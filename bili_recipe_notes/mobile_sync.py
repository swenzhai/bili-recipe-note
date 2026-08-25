from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import mimetypes
import secrets
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import parse_qs, urlparse

from .config import CONFIG_DIR_NAME, load_config
from .cover_policy import has_manually_approved_cover
from .storage import atomic_write_json


SCHEMA_VERSION = 1
DATABASE_SCHEMA_VERSION = 7
PROTOCOL_VERSION = 2
MAX_SYNC_OPERATIONS = 100
MAX_SYNC_CHANGES = 200
MAX_PRACTICE_PHOTO_BYTES = 5 * 1024 * 1024
PAIRING_TTL_MINUTES = 10
DATABASE_FILE_NAME = "mobile-sync.sqlite3"
MEDIA_DIR_NAME = "mobile-media"
SYNC_META_FILE_NAME = "sync-meta.json"
RECIPE_NAMESPACE = uuid.UUID("f7e7b2d5-96dd-43c6-a769-83e95f72bd39")
ALLOWED_OUTCOMES = {"", "success", "partial", "failed"}
LEGACY_CAPABILITIES = {"recipe", "practice_log"}
SUPPORTED_CAPABILITIES = LEGACY_CAPABILITIES | {
    "meal_plan", "meal_order", "meal_selection", "meal_dish_state"
}


class MobileSyncError(RuntimeError):
    pass


class AuthenticationError(MobileSyncError):
    pass


class ValidationError(MobileSyncError):
    pass


@dataclass(frozen=True)
class PairingCredential:
    server_id: str
    base_url: str
    pairing_token: str
    expires_at: str

    def qr_payload(self) -> str:
        return json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "server_id": self.server_id,
                "base_url": self.base_url.rstrip("/"),
                "pairing_token": self.pairing_token,
                "expires_at": self.expires_at,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_private_client(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True  # ASGI test clients use a name instead of a numeric address.
    return bool(address.is_private or address.is_loopback or address.is_link_local)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_source_url(value: str) -> str:
    """Normalize a video URL for durable recipe/non-recipe classification."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.netloc:
        return raw.rstrip("/")
    query = parse_qs(parsed.query)
    page = str((query.get("p") or [""])[0]).strip()
    normalized = f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    return f"{normalized}?p={page}" if page else normalized


class MobileSyncStore:
    """Server-side source of truth for mobile pairing and offline synchronization."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        out_dir: str | Path | None = None,
        database_path: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).expanduser().resolve()
        configured_out = out_dir if out_dir is not None else load_config(self.project_root).out_dir
        output_path = Path(configured_out).expanduser()
        self.out_dir = output_path.resolve() if output_path.is_absolute() else (self.project_root / output_path).resolve()
        config_dir = self.project_root / CONFIG_DIR_NAME
        config_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = Path(database_path).resolve() if database_path else config_dir / DATABASE_FILE_NAME
        self.media_dir = config_dir / MEDIA_DIR_NAME
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def _write_connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > DATABASE_SCHEMA_VERSION:
                raise MobileSyncError(f"Unsupported mobile sync database version: {version}")
            if 0 < version < DATABASE_SCHEMA_VERSION:
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_path = self.database_path.with_name(
                    f"{self.database_path.stem}.before-v{version}-to-v{DATABASE_SCHEMA_VERSION}-{timestamp}.bak"
                )
                with sqlite3.connect(backup_path) as backup:
                    connection.backup(backup)
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS pairing_tokens (
                    token_hash TEXT PRIMARY KEY, expires_at TEXT NOT NULL, used_at TEXT
                );
                CREATE TABLE IF NOT EXISTS recipes (
                    id TEXT PRIMARY KEY, output_folder TEXT NOT NULL, payload_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, revision INTEGER NOT NULL,
                    published INTEGER NOT NULL DEFAULT 1, recommended INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS video_sources (
                    source_key TEXT PRIMARY KEY, source_url TEXT NOT NULL,
                    creator_name TEXT, title TEXT, classification TEXT NOT NULL,
                    recipe_id TEXT, batch_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_video_sources_classification
                    ON video_sources(classification, updated_at DESC);
                CREATE TABLE IF NOT EXISTS assets (
                    sha256 TEXT PRIMARY KEY, recipe_id TEXT, path TEXT NOT NULL, mime_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS practice_logs (
                    id TEXT PRIMARY KEY, recipe_id TEXT NOT NULL, device_id TEXT NOT NULL,
                    cooked_on TEXT NOT NULL, outcome TEXT, rating INTEGER, notes TEXT NOT NULL,
                    photo_sha256 TEXT, version INTEGER NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, deleted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS conflicts (
                    id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, device_id TEXT NOT NULL,
                    incoming_json TEXT NOT NULL, server_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    resolved_at TEXT, resolution TEXT
                );
                CREATE TABLE IF NOT EXISTS operation_receipts (
                    op_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS change_log (
                    revision INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                    action TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_changes_entity ON change_log(entity_type, entity_id);
                CREATE INDEX IF NOT EXISTS idx_practice_recipe ON practice_logs(recipe_id, cooked_on DESC);
                CREATE INDEX IF NOT EXISTS idx_conflicts_open ON conflicts(resolved_at, created_at);
                CREATE TABLE IF NOT EXISTS meal_plans (
                    id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, version INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS meal_orders (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, version INTEGER NOT NULL, epoch INTEGER NOT NULL,
                    created_at TEXT NOT NULL, completed_at TEXT, phase TEXT NOT NULL DEFAULT 'ordering',
                    chef_device_id TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_single_active_meal
                    ON meal_orders(status) WHERE status='active';
                CREATE TABLE IF NOT EXISTS meal_selections (
                    order_id TEXT NOT NULL, device_id TEXT NOT NULL, recipe_id TEXT NOT NULL,
                    quantity REAL NOT NULL, note TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(order_id, device_id, recipe_id),
                    FOREIGN KEY(order_id) REFERENCES meal_orders(id),
                    FOREIGN KEY(device_id) REFERENCES devices(id)
                );
                CREATE TABLE IF NOT EXISTS meal_dish_states (
                    order_id TEXT NOT NULL, recipe_id TEXT NOT NULL, sort_order INTEGER NOT NULL,
                    completed INTEGER NOT NULL, updated_at TEXT NOT NULL,
                    prep_completed INTEGER NOT NULL DEFAULT 0,
                    cook_completed INTEGER NOT NULL DEFAULT 0,
                    served INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(order_id, recipe_id), FOREIGN KEY(order_id) REFERENCES meal_orders(id)
                );
                CREATE INDEX IF NOT EXISTS idx_meal_selections_order ON meal_selections(order_id, recipe_id);
                """
            )
            recipe_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(recipes)")}
            if "published" not in recipe_columns:
                connection.execute("ALTER TABLE recipes ADD COLUMN published INTEGER NOT NULL DEFAULT 1")
            if "recommended" not in recipe_columns:
                connection.execute("ALTER TABLE recipes ADD COLUMN recommended INTEGER NOT NULL DEFAULT 0")
            order_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(meal_orders)")}
            if "phase" not in order_columns:
                connection.execute("ALTER TABLE meal_orders ADD COLUMN phase TEXT NOT NULL DEFAULT 'ordering'")
            if "chef_device_id" not in order_columns:
                connection.execute("ALTER TABLE meal_orders ADD COLUMN chef_device_id TEXT")
            dish_state_columns = {
                str(row["name"]) for row in connection.execute("PRAGMA table_info(meal_dish_states)")
            }
            if "prep_completed" not in dish_state_columns:
                connection.execute(
                    "ALTER TABLE meal_dish_states ADD COLUMN prep_completed INTEGER NOT NULL DEFAULT 0"
                )
            if "cook_completed" not in dish_state_columns:
                connection.execute(
                    "ALTER TABLE meal_dish_states ADD COLUMN cook_completed INTEGER NOT NULL DEFAULT 0"
                )
                connection.execute("UPDATE meal_dish_states SET cook_completed=completed")
            if "served" not in dish_state_columns:
                connection.execute("ALTER TABLE meal_dish_states ADD COLUMN served INTEGER NOT NULL DEFAULT 0")
            connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
            connection.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('revision', '0')")
            connection.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('server_id', ?)", (str(uuid.uuid4()),))
            connection.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('self_join_enabled', '1')")
        self._migrate_legacy_meal_plans()

    def _migrate_legacy_meal_plans(self) -> None:
        path = self.project_root / CONFIG_DIR_NAME / "meal-plans.json"
        if not path.is_file():
            return
        with self._write_connection() as connection:
            if connection.execute("SELECT 1 FROM meal_plans LIMIT 1").fetchone():
                return
            source = _read_object(path)
            plans = source.get("plans") if source.get("schema_version") == 1 else None
            if not isinstance(plans, list):
                return
            now = utc_now()
            migrated = 0
            for raw in plans:
                if not isinstance(raw, dict):
                    continue
                plan_id = str(raw.get("id") or "").strip()
                name = str(raw.get("name") or "").strip()
                if not plan_id or not name:
                    continue
                created_at = str(raw.get("created_at") or now)
                updated_at = str(raw.get("updated_at") or created_at)
                payload = {**raw, "id": plan_id, "version": 1, "deleted_at": None}
                connection.execute(
                    "INSERT INTO meal_plans VALUES (?,?,?,?,?,NULL)",
                    (plan_id, _canonical_json(payload), 1, created_at, updated_at),
                )
                self._record_change(connection, "meal_plan", plan_id, "upsert", payload)
                migrated += 1
        if migrated:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = path.with_name(f"meal-plans.migrated-{stamp}.json.bak")
            if not backup.exists():
                shutil.copy2(path, backup)

    @property
    def server_id(self) -> str:
        with self._connect() as connection:
            return str(connection.execute("SELECT value FROM meta WHERE key='server_id'").fetchone()[0])

    def current_revision(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def _record_change(
        self,
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> int:
        revision = int(connection.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]) + 1
        now = utc_now()
        connection.execute("UPDATE meta SET value=? WHERE key='revision'", (str(revision),))
        connection.execute(
            "INSERT INTO change_log VALUES (?, ?, ?, ?, ?, ?)",
            (revision, entity_type, entity_id, action, _canonical_json(payload), now),
        )
        return revision

    # Pairing and device management
    def self_join_enabled(self) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key='self_join_enabled'").fetchone()
        return row is None or str(row["value"]) == "1"

    def set_self_join_enabled(self, enabled: bool) -> None:
        with self._write_connection() as connection:
            connection.execute(
                "INSERT INTO meta(key,value) VALUES ('self_join_enabled',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if enabled else "0",),
            )

    def _register_device(self, device_name: str, requested_device_id: str | None = None) -> dict[str, Any]:
        name = device_name.strip()[:80]
        if not name:
            raise ValidationError("device_name is required")
        device_id = requested_device_id.strip() if requested_device_id else str(uuid.uuid4())
        try:
            uuid.UUID(device_id)
        except ValueError as exc:
            raise ValidationError("device_id must be a UUID") from exc
        now = utc_now()
        access_token = secrets.token_urlsafe(48)
        with self._write_connection() as connection:
            connection.execute(
                "INSERT INTO devices VALUES (?, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, token_hash=excluded.token_hash, "
                "last_seen_at=excluded.last_seen_at, revoked_at=NULL",
                (device_id, name, token_hash(access_token), now, now),
            )
        return {
            "schema_version": SCHEMA_VERSION,
            "server_id": self.server_id,
            "device_id": device_id,
            "device_name": name,
            "access_token": access_token,
        }

    def join_device(self, device_name: str, requested_device_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if not self.self_join_enabled():
                raise AuthenticationError("New device joining is currently disabled")
            return self._register_device(device_name, requested_device_id)

    def issue_pairing_credential(self, base_url: str) -> PairingCredential:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(minutes=PAIRING_TTL_MINUTES)
        with self._write_connection() as connection:
            connection.execute("DELETE FROM pairing_tokens WHERE expires_at < ? OR used_at IS NOT NULL", (utc_now(),))
            connection.execute(
                "INSERT INTO pairing_tokens VALUES (?, ?, NULL)", (token_hash(token), expires.isoformat())
            )
        return PairingCredential(self.server_id, base_url.rstrip("/"), token, expires.isoformat())

    def pair_device(self, pairing_token: str, device_name: str, requested_device_id: str | None = None) -> dict[str, Any]:
        if not device_name.strip():
            raise ValidationError("device_name is required")
        if requested_device_id:
            try:
                uuid.UUID(requested_device_id.strip())
            except ValueError as exc:
                raise ValidationError("device_id must be a UUID") from exc
        now = utc_now()
        with self._write_connection() as connection:
            row = connection.execute(
                "SELECT expires_at, used_at FROM pairing_tokens WHERE token_hash=?", (token_hash(pairing_token),)
            ).fetchone()
            if not row or row["used_at"] or str(row["expires_at"]) < now:
                raise AuthenticationError("Pairing token is invalid or expired")
            connection.execute("UPDATE pairing_tokens SET used_at=? WHERE token_hash=?", (now, token_hash(pairing_token)))
        return self._register_device(device_name, requested_device_id)

    def authenticate(self, access_token: str) -> dict[str, str]:
        candidate = token_hash(access_token)
        with self._write_connection() as connection:
            rows = connection.execute("SELECT * FROM devices WHERE revoked_at IS NULL").fetchall()
            row = next((item for item in rows if hmac.compare_digest(str(item["token_hash"]), candidate)), None)
            if row is None:
                raise AuthenticationError("Invalid or revoked device token")
            connection.execute("UPDATE devices SET last_seen_at=? WHERE id=?", (utc_now(), row["id"]))
            return {"id": str(row["id"]), "name": str(row["name"])}

    def list_devices(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id,name,created_at,last_seen_at,revoked_at FROM devices ORDER BY created_at DESC"
            )]

    def revoke_device(self, device_id: str) -> None:
        with self._write_connection() as connection:
            connection.execute("UPDATE devices SET revoked_at=? WHERE id=?", (utc_now(), device_id))

    # Recipe indexing
    def _source_identity(self, recipe: dict[str, Any], job: dict[str, Any]) -> str | None:
        bvid = str(job.get("bvid") or "").strip().lower()
        cid = str(job.get("cid") or "").strip()
        part_id = str(job.get("part_id") or "").strip()
        part_label = str(job.get("part_label") or "").strip().lower()
        source_url = str(recipe.get("source_url") or job.get("source_url") or "").strip()
        if bvid and cid:
            return f"bilibili:{bvid}:cid:{cid}"
        if bvid and part_id:
            label = part_label if part_label in {"p", "cid"} else "p"
            return f"bilibili:{bvid}:{label}:{part_id}"
        if bvid:
            page = (parse_qs(urlparse(source_url).query).get("p") or [None])[0]
            return f"bilibili:{bvid}:p:{page}" if page else f"bilibili:{bvid}"
        if source_url:
            parsed = urlparse(source_url)
            return f"url:{parsed._replace(fragment='').geturl().rstrip('/')}"
        return None

    def _recipe_id(self, folder: Path, recipe: dict[str, Any], job: dict[str, Any]) -> str:
        metadata_path = folder / SYNC_META_FILE_NAME
        metadata = _read_object(metadata_path)
        existing = str(metadata.get("recipe_id") or "").strip()
        try:
            if existing:
                return str(uuid.UUID(existing))
        except ValueError:
            pass
        identity = self._source_identity(recipe, job)
        recipe_id = str(uuid.uuid5(RECIPE_NAMESPACE, identity)) if identity else str(uuid.uuid4())
        atomic_write_json(
            metadata_path,
            {
                "schema_version": SCHEMA_VERSION,
                "recipe_id": recipe_id,
                "source_identity": identity or "persisted-random",
            },
        )
        return recipe_id

    def _assets_for_recipe(self, folder: Path, payload: dict[str, Any], recipe_id: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        root = folder.resolve()
        payload.pop("cover_image_sha256", None)
        cover_raw = (
            str(payload.get("cover_image_path") or "").strip()
            if has_manually_approved_cover(payload)
            else ""
        )
        if cover_raw and "://" not in cover_raw:
            cover_path = (folder / cover_raw).resolve()
            try:
                cover_path.relative_to(root)
            except ValueError:
                cover_path = Path()
            if cover_path.is_file():
                digest = _sha256_file(cover_path)
                payload["cover_image_sha256"] = digest
                found.append(
                    {
                        "sha256": digest,
                        "recipe_id": recipe_id,
                        "kind": "recipe_cover",
                        "mime_type": mimetypes.guess_type(cover_path.name)[0] or "application/octet-stream",
                        "byte_size": cover_path.stat().st_size,
                        "_path": str(cover_path),
                    }
                )
        for index, step in enumerate(payload.get("steps") or []):
            if not isinstance(step, dict):
                continue
            raw = str(step.get("screenshot_path") or "").strip()
            if not raw or "://" in raw:
                continue
            path = (folder / raw).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if not path.is_file():
                continue
            digest = _sha256_file(path)
            step["image_sha256"] = digest
            found.append(
                {
                    "sha256": digest,
                    "recipe_id": recipe_id,
                    "kind": "recipe_image",
                    "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    "byte_size": path.stat().st_size,
                    "step_index": index,
                    "_path": str(path),
                }
            )
        return found

    def index_recipes(self) -> dict[str, Any]:
        candidates: dict[str, list[tuple[float, Path, dict[str, Any], list[dict[str, Any]], str]]] = {}
        with self._connect() as connection:
            excluded_source_keys = {
                str(row["source_key"])
                for row in connection.execute(
                    "SELECT source_key FROM video_sources WHERE classification IN ('non_recipe','technique')"
                )
            }
        if self.out_dir.exists():
            for folder in self.out_dir.iterdir():
                recipe_path = folder / "recipe.json"
                if not folder.is_dir() or not recipe_path.is_file():
                    continue
                recipe = _read_object(recipe_path)
                if not recipe or not isinstance(recipe.get("steps"), list):
                    continue
                if normalize_source_url(str(recipe.get("source_url") or "")) in excluded_source_keys:
                    continue
                recipe_id = self._recipe_id(folder, recipe, _read_object(folder / "job.json"))
                payload = {**recipe, "id": recipe_id, "schema_version": SCHEMA_VERSION}
                assets = self._assets_for_recipe(folder, payload, recipe_id)
                public_assets = [{key: value for key, value in item.items() if key != "_path"} for item in assets]
                payload["assets"] = public_assets
                content_hash = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
                candidates.setdefault(recipe_id, []).append(
                    (recipe_path.stat().st_mtime, folder, payload, assets, content_hash)
                )
        chosen = {key: max(items, key=lambda item: item[0]) for key, items in candidates.items()}
        duplicates = [
            {"recipe_id": key, "folders": [str(item[1]) for item in items]}
            for key, items in candidates.items() if len(items) > 1
        ]
        changed = deleted = 0
        with self._write_connection() as connection:
            existing = {str(row["id"]): row for row in connection.execute("SELECT * FROM recipes")}
            now = utc_now()
            for recipe_id, (_, folder, payload, assets, content_hash) in chosen.items():
                old = existing.get(recipe_id)
                source_url = str(payload.get("source_url") or "").strip()
                source_key = normalize_source_url(source_url)
                if source_key:
                    creator_name = str(payload.get("creator_name") or payload.get("uploader") or "").strip() or None
                    connection.execute(
                        "INSERT INTO video_sources(source_key,source_url,creator_name,title,classification,recipe_id,batch_id,created_at,updated_at) "
                        "VALUES (?,?,?,?, 'recipe', ?, NULL, ?, ?) ON CONFLICT(source_key) DO UPDATE SET "
                        "source_url=excluded.source_url,creator_name=COALESCE(excluded.creator_name,video_sources.creator_name),"
                        "title=excluded.title,classification='recipe',recipe_id=excluded.recipe_id,updated_at=excluded.updated_at",
                        (source_key, source_url, creator_name, str(payload.get("video_title") or payload.get("title") or ""), recipe_id, now, now),
                    )
                if old and old["content_hash"] == content_hash and old["deleted_at"] is None:
                    continue
                published = bool(old["published"]) if old is not None else True
                recommended = bool(old["recommended"]) if old is not None else False
                public_payload = {**payload, "published": published, "recommended": recommended}
                revision = (
                    self._record_change(connection, "recipe", recipe_id, "upsert", public_payload)
                    if published else int(old["revision"])
                )
                connection.execute(
                    "INSERT INTO recipes("
                    "id,output_folder,payload_json,content_hash,updated_at,deleted_at,revision,published,recommended"
                    ") VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                    "output_folder=excluded.output_folder,payload_json=excluded.payload_json,"
                    "content_hash=excluded.content_hash,updated_at=excluded.updated_at,deleted_at=NULL,revision=excluded.revision",
                    (
                        recipe_id, str(folder), _canonical_json(payload), content_hash, now, revision,
                        int(published), int(recommended),
                    ),
                )
                for asset in assets:
                    connection.execute(
                        "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(sha256) DO UPDATE SET "
                        "recipe_id=excluded.recipe_id,path=excluded.path,mime_type=excluded.mime_type,"
                        "byte_size=excluded.byte_size,kind=excluded.kind",
                        (
                            asset["sha256"], recipe_id, asset["_path"], asset["mime_type"],
                            asset["byte_size"], asset["kind"], now,
                        ),
                    )
                changed += 1
            for recipe_id, old in existing.items():
                if recipe_id in chosen or old["deleted_at"] is not None:
                    continue
                revision = int(old["revision"])
                if bool(old["published"]):
                    payload = {"schema_version": SCHEMA_VERSION, "id": recipe_id, "deleted_at": now}
                    revision = self._record_change(connection, "recipe", recipe_id, "delete", payload)
                connection.execute(
                    "UPDATE recipes SET deleted_at=?,updated_at=?,revision=? WHERE id=?",
                    (now, now, revision, recipe_id),
                )
                deleted += 1
        return {"indexed": len(chosen), "changed": changed, "deleted": deleted, "duplicates": duplicates}

    def known_non_recipe_urls(self, urls: Iterable[str]) -> set[str]:
        return self.known_video_urls(urls, classifications={"non_recipe", "technique"})

    def known_video_urls(
        self,
        urls: Iterable[str],
        *,
        classifications: set[str] | frozenset[str] | None = None,
    ) -> set[str]:
        keyed = {normalize_source_url(url): str(url) for url in urls if normalize_source_url(url)}
        if not keyed:
            return set()
        selected_classifications = classifications or {"non_recipe", "technique"}
        if not selected_classifications:
            return set()
        placeholders = ",".join("?" for _ in keyed)
        classification_placeholders = ",".join("?" for _ in selected_classifications)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT source_key FROM video_sources "
                f"WHERE classification IN ({classification_placeholders}) AND source_key IN ({placeholders})",
                (*selected_classifications, *keyed),
            ).fetchall()
        return {keyed[str(row["source_key"])] for row in rows}

    def set_video_classifications(
        self,
        urls: Iterable[str],
        classification: str,
        *,
        creator_name: str | None = None,
        batch_id: str | None = None,
    ) -> int:
        if classification not in {"recipe", "non_recipe", "technique"}:
            raise ValidationError("classification must be recipe, non_recipe, or technique")
        values = [(normalize_source_url(url), str(url).strip()) for url in urls]
        values = [(key, url) for key, url in values if key and url]
        now = utc_now()
        with self._write_connection() as connection:
            for source_key, source_url in values:
                connection.execute(
                    "INSERT INTO video_sources(source_key,source_url,creator_name,title,classification,recipe_id,batch_id,created_at,updated_at) "
                    "VALUES (?,?,?,NULL,?,NULL,?,?,?) ON CONFLICT(source_key) DO UPDATE SET "
                    "source_url=excluded.source_url,creator_name=COALESCE(excluded.creator_name,video_sources.creator_name),"
                    "classification=excluded.classification,batch_id=COALESCE(excluded.batch_id,video_sources.batch_id),"
                    "recipe_id=CASE WHEN excluded.classification='non_recipe' THEN NULL ELSE video_sources.recipe_id END,"
                    "updated_at=excluded.updated_at",
                    (source_key, source_url, creator_name, classification, batch_id, now, now),
                )
        return len(values)

    def list_video_sources(self, classification: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE classification=?" if classification else ""
        parameters = (classification,) if classification else ()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT video_sources.*, recipes.output_folder AS output_folder "
                "FROM video_sources LEFT JOIN recipes ON recipes.id = video_sources.recipe_id "
                f"{where} ORDER BY video_sources.updated_at DESC",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def set_recipe_publications(self, updates: dict[str, bool]) -> int:
        return self.set_recipe_menu_states({recipe_id: {"published": value} for recipe_id, value in updates.items()})

    def set_recipe_recommendations(self, updates: dict[str, bool]) -> int:
        return self.set_recipe_menu_states({recipe_id: {"recommended": value} for recipe_id, value in updates.items()})

    def set_recipe_menu_states(self, updates: dict[str, dict[str, bool]]) -> int:
        changed = 0
        with self._write_connection() as connection:
            now = utc_now()
            for recipe_id, state in updates.items():
                row = connection.execute(
                    "SELECT * FROM recipes WHERE id=? AND deleted_at IS NULL", (str(recipe_id),)
                ).fetchone()
                if row is None:
                    continue
                published = bool(state.get("published", row["published"]))
                recommended = bool(state.get("recommended", row["recommended"]))
                if "published" in state and not published:
                    recommended = False
                elif recommended:
                    published = True
                if bool(row["published"]) == published and bool(row["recommended"]) == recommended:
                    continue
                payload = json.loads(str(row["payload_json"]))
                payload["published"] = published
                payload["recommended"] = recommended
                revision = self._record_change(connection, "recipe", str(recipe_id), "upsert", payload)
                connection.execute(
                    "UPDATE recipes SET published=?,recommended=?,updated_at=?,revision=? WHERE id=?",
                    (int(published), int(recommended), now, revision, str(recipe_id)),
                )
                changed += 1
        return changed

    # Binary assets
    def store_asset(self, digest: str, content: bytes, mime_type: str) -> dict[str, Any]:
        if len(content) > MAX_PRACTICE_PHOTO_BYTES:
            raise ValidationError("Practice photo exceeds 5 MiB")
        suffixes = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
        if mime_type not in suffixes:
            raise ValidationError("Unsupported practice photo type")
        actual = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(actual, digest.lower()):
            raise ValidationError("Asset SHA-256 does not match content")
        path = self.media_dir / f"{actual}{suffixes[mime_type]}"
        if not path.exists():
            path.write_bytes(content)
        with self._write_connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO assets VALUES (?, NULL, ?, ?, ?, 'practice_photo', ?)",
                (actual, str(path), mime_type, len(content), utc_now()),
            )
        return {"sha256": actual, "mime_type": mime_type, "byte_size": len(content)}

    def asset_path(self, digest: str) -> tuple[Path, str] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT path,mime_type FROM assets WHERE sha256=?", (digest.lower(),)).fetchone()
        if not row:
            return None
        path = Path(str(row["path"]))
        return (path, str(row["mime_type"])) if path.is_file() else None

    # Practice logs and synchronization
    @staticmethod
    def _practice_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": str(row["id"]), "recipe_id": str(row["recipe_id"]),
            "cooked_on": str(row["cooked_on"]), "outcome": row["outcome"],
            "rating": row["rating"], "notes": str(row["notes"]),
            "photo_sha256": row["photo_sha256"], "version": int(row["version"]),
            "created_at": str(row["created_at"]), "updated_at": str(row["updated_at"]),
            "deleted_at": row["deleted_at"],
        }

    def _clean_practice(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            log_id = str(uuid.UUID(str(payload.get("id") or "")))
            recipe_id = str(uuid.UUID(str(payload.get("recipe_id") or "")))
        except ValueError as exc:
            raise ValidationError("Practice log id and recipe_id must be UUIDs") from exc
        cooked_on = str(payload.get("cooked_on") or "")
        try:
            datetime.strptime(cooked_on, "%Y-%m-%d")
        except ValueError as exc:
            raise ValidationError("cooked_on must use YYYY-MM-DD") from exc
        outcome = str(payload.get("outcome") or "")
        if outcome not in ALLOWED_OUTCOMES:
            raise ValidationError("Invalid practice outcome")
        rating = payload.get("rating")
        if rating is not None and (not isinstance(rating, int) or not 1 <= rating <= 5):
            raise ValidationError("rating must be between 1 and 5")
        notes = str(payload.get("notes") or "").strip()
        if not notes or len(notes) > 5000:
            raise ValidationError("notes must contain 1 to 5000 characters")
        photo = str(payload.get("photo_sha256") or "").lower() or None
        if photo and (len(photo) != 64 or any(char not in "0123456789abcdef" for char in photo)):
            raise ValidationError("photo_sha256 must be a SHA-256 digest")
        return {
            "id": log_id, "recipe_id": recipe_id, "cooked_on": cooked_on,
            "outcome": outcome or None, "rating": rating, "notes": notes, "photo_sha256": photo,
        }

    @staticmethod
    def _order_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": str(row["id"]),
            "status": str(row["status"]),
            "phase": str(row["phase"]),
            "chef_device_id": row["chef_device_id"],
            "version": int(row["version"]),
            "epoch": int(row["epoch"]),
            "created_at": str(row["created_at"]),
            "completed_at": row["completed_at"],
        }

    @staticmethod
    def _selection_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "order_id": str(row["order_id"]),
            "device_id": str(row["device_id"]),
            "device_name": str(row["device_name"]),
            "recipe_id": str(row["recipe_id"]),
            "quantity": float(row["quantity"]),
            "note": str(row["note"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _dish_state_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "order_id": str(row["order_id"]),
            "recipe_id": str(row["recipe_id"]),
            "sort_order": int(row["sort_order"]),
            "prep_completed": bool(row["prep_completed"]),
            "cook_completed": bool(row["cook_completed"]),
            "served": bool(row["served"]),
            "completed": bool(row["cook_completed"]),
            "updated_at": str(row["updated_at"]),
        }

    def _active_order_row(self, connection: sqlite3.Connection) -> sqlite3.Row | None:
        return connection.execute("SELECT * FROM meal_orders WHERE status='active' LIMIT 1").fetchone()

    def _create_order(self, connection: sqlite3.Connection) -> sqlite3.Row:
        order_id = str(uuid.uuid4())
        now = utc_now()
        connection.execute(
            "INSERT INTO meal_orders(id,status,version,epoch,created_at,completed_at,phase) "
            "VALUES (?, 'active', 1, 1, ?, NULL, 'ordering')",
            (order_id, now),
        )
        row = connection.execute("SELECT * FROM meal_orders WHERE id=?", (order_id,)).fetchone()
        self._record_change(connection, "meal_order", order_id, "upsert", self._order_payload(row))
        return row

    def current_meal_order(self, *, create: bool = True) -> dict[str, Any] | None:
        with self._write_connection() as connection:
            order = self._active_order_row(connection)
            if order is None and create:
                order = self._create_order(connection)
            if order is None:
                return None
            return self._meal_snapshot(connection, order)

    def _meal_snapshot(self, connection: sqlite3.Connection, order: sqlite3.Row) -> dict[str, Any]:
        selections = connection.execute(
            "SELECT s.*,d.name AS device_name FROM meal_selections s JOIN devices d ON d.id=s.device_id "
            "WHERE s.order_id=? ORDER BY s.updated_at,s.recipe_id",
            (order["id"],),
        ).fetchall()
        states = connection.execute(
            "SELECT * FROM meal_dish_states WHERE order_id=? ORDER BY sort_order,recipe_id",
            (order["id"],),
        ).fetchall()
        return {
            "order": self._order_payload(order),
            "selections": [self._selection_payload(row) for row in selections],
            "dish_states": [self._dish_state_payload(row) for row in states],
        }

    def _validate_meal_target(
        self, connection: sqlite3.Connection, operation: dict[str, Any]
    ) -> tuple[sqlite3.Row | None, dict[str, Any] | None]:
        active = self._active_order_row(connection)
        requested_id = str(operation.get("order_id") or "")
        try:
            requested_epoch = int(operation.get("epoch"))
        except (TypeError, ValueError):
            raise ValidationError("Meal operations require an integer epoch")
        if active is None:
            return None, {"status": "conflict", "reason": "order_completed", "message": "本餐已结束"}
        if requested_id != str(active["id"]) or requested_epoch != int(active["epoch"]):
            requested = connection.execute("SELECT status FROM meal_orders WHERE id=?", (requested_id,)).fetchone()
            if requested is not None and str(requested["status"]) == "completed":
                return None, {
                    "status": "conflict", "reason": "order_completed",
                    "message": "本餐已结束，请在新本餐中重新选择",
                    "current_order": self._order_payload(active),
                }
            return None, {
                "status": "conflict",
                "reason": "stale_order",
                "message": "本餐已清空或结束，请刷新后重新选择",
                "current_order": self._order_payload(active),
            }
        requested_phase = operation.get("phase")
        if requested_phase is not None and str(requested_phase) != str(active["phase"]):
            return None, {
                "status": "conflict",
                "reason": "meal_phase_changed",
                "message": "本餐已进入下一阶段，请刷新后继续",
                "current_order": self._order_payload(active),
            }
        return active, None

    def _meal_selection_row(
        self, connection: sqlite3.Connection, order_id: str, device_id: str, recipe_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT s.*,d.name AS device_name FROM meal_selections s JOIN devices d ON d.id=s.device_id "
            "WHERE s.order_id=? AND s.device_id=? AND s.recipe_id=?",
            (order_id, device_id, recipe_id),
        ).fetchone()

    def _record_selection_delete(
        self, connection: sqlite3.Connection, order_id: str, device_id: str, recipe_id: str
    ) -> None:
        entity_id = f"{order_id}:{device_id}:{recipe_id}"
        self._record_change(
            connection,
            "meal_selection",
            entity_id,
            "delete",
            {"order_id": order_id, "device_id": device_id, "recipe_id": recipe_id},
        )

    def _ensure_dish_state(self, connection: sqlite3.Connection, order_id: str, recipe_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM meal_dish_states WHERE order_id=? AND recipe_id=?", (order_id, recipe_id)
        ).fetchone():
            return
        next_order = int(connection.execute(
            "SELECT COALESCE(MAX(sort_order),-1)+1 FROM meal_dish_states WHERE order_id=?", (order_id,)
        ).fetchone()[0])
        now = utc_now()
        connection.execute(
            "INSERT INTO meal_dish_states("
            "order_id,recipe_id,sort_order,completed,updated_at,prep_completed,cook_completed,served"
            ") VALUES (?,?,?,?,?,?,?,?)",
            (order_id, recipe_id, next_order, 0, now, 0, 0, 0),
        )
        row = connection.execute(
            "SELECT * FROM meal_dish_states WHERE order_id=? AND recipe_id=?", (order_id, recipe_id)
        ).fetchone()
        self._record_change(
            connection, "meal_dish_state", f"{order_id}:{recipe_id}", "upsert", self._dish_state_payload(row)
        )

    def _apply_meal_operation(
        self, connection: sqlite3.Connection, device_id: str, operation: dict[str, Any], op_id: str
    ) -> dict[str, Any]:
        action = str(operation.get("action") or "")
        allowed = {
            "add_quantity", "set_quantity", "set_note", "remove_selection",
            "set_dish_completed", "set_dish_stage_completed", "advance_meal_phase",
            "clear_order", "complete_order", "load_plan",
        }
        if action not in allowed:
            raise ValidationError(f"Unsupported meal action: {action}")
        order, conflict = self._validate_meal_target(connection, operation)
        if conflict:
            return {"op_id": op_id, **conflict}
        assert order is not None
        order_id = str(order["id"])
        epoch = int(order["epoch"])
        phase = str(order["phase"])
        chef_device_id = str(order["chef_device_id"] or "")
        now = utc_now()

        if action == "advance_meal_phase":
            next_phase = {
                "ordering": "prep",
                "prep": "cooking",
                "cooking": "serving",
            }.get(phase)
            if next_phase is None:
                return {
                    "op_id": op_id, "status": "conflict", "reason": "meal_phase_changed",
                    "message": "当前阶段不能继续推进",
                }
            if phase != "ordering" and chef_device_id != device_id:
                return {
                    "op_id": op_id, "status": "conflict", "reason": "chef_only",
                    "message": "只有本餐主厨可以推进烹饪阶段",
                }
            dish_count = int(connection.execute(
                "SELECT COUNT(DISTINCT recipe_id) FROM meal_selections WHERE order_id=?", (order_id,)
            ).fetchone()[0])
            if dish_count == 0:
                return {
                    "op_id": op_id, "status": "conflict", "reason": "meal_empty",
                    "message": "请先选择至少一道菜",
                }
            completion_column = {"prep": "prep_completed", "cooking": "cook_completed"}.get(phase)
            if completion_column is not None:
                incomplete = int(connection.execute(
                    f"SELECT COUNT(*) FROM (SELECT DISTINCT recipe_id FROM meal_selections WHERE order_id=?) s "
                    f"LEFT JOIN meal_dish_states d ON d.order_id=? AND d.recipe_id=s.recipe_id "
                    f"WHERE COALESCE(d.{completion_column},0)=0",
                    (order_id, order_id),
                ).fetchone()[0])
                if incomplete:
                    return {
                        "op_id": op_id, "status": "conflict", "reason": "phase_incomplete",
                        "message": f"还有 {incomplete} 道菜未完成当前阶段",
                    }
            if phase == "ordering":
                connection.execute(
                    "UPDATE meal_orders SET phase=?,chef_device_id=?,version=version+1 WHERE id=?",
                    (next_phase, device_id, order_id),
                )
            else:
                connection.execute(
                    "UPDATE meal_orders SET phase=?,version=version+1 WHERE id=?", (next_phase, order_id)
                )
            updated = connection.execute("SELECT * FROM meal_orders WHERE id=?", (order_id,)).fetchone()
            payload = self._order_payload(updated)
            self._record_change(connection, "meal_order", order_id, "upsert", payload)
            return {"op_id": op_id, "status": "accepted", "order": payload}

        if action == "clear_order":
            if phase != "ordering":
                return {
                    "op_id": op_id, "status": "conflict", "reason": "meal_phase_locked",
                    "message": "烹饪已经开始，不能再清空本餐",
                }
            old_selections = connection.execute(
                "SELECT device_id,recipe_id FROM meal_selections WHERE order_id=?", (order_id,)
            ).fetchall()
            old_states = connection.execute(
                "SELECT recipe_id FROM meal_dish_states WHERE order_id=?", (order_id,)
            ).fetchall()
            for row in old_selections:
                self._record_selection_delete(connection, order_id, str(row["device_id"]), str(row["recipe_id"]))
            for row in old_states:
                recipe_id = str(row["recipe_id"])
                self._record_change(
                    connection, "meal_dish_state", f"{order_id}:{recipe_id}", "delete",
                    {"order_id": order_id, "recipe_id": recipe_id},
                )
            connection.execute("DELETE FROM meal_selections WHERE order_id=?", (order_id,))
            connection.execute("DELETE FROM meal_dish_states WHERE order_id=?", (order_id,))
            connection.execute(
                "UPDATE meal_orders SET epoch=epoch+1,version=version+1,phase='ordering',chef_device_id=NULL "
                "WHERE id=?", (order_id,)
            )
            updated = connection.execute("SELECT * FROM meal_orders WHERE id=?", (order_id,)).fetchone()
            self._record_change(connection, "meal_order", order_id, "upsert", self._order_payload(updated))
            return {"op_id": op_id, "status": "accepted", "order": self._order_payload(updated)}

        if action == "complete_order":
            if chef_device_id != device_id:
                return {
                    "op_id": op_id, "status": "conflict", "reason": "chef_only",
                    "message": "只有本餐主厨可以完成并归档本餐",
                }
            if phase != "serving":
                return {
                    "op_id": op_id, "status": "conflict", "reason": "meal_phase_locked",
                    "message": "请按顺序完成备餐、烹饪和上桌",
                }
            incomplete = int(connection.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT recipe_id FROM meal_selections WHERE order_id=?) s "
                "LEFT JOIN meal_dish_states d ON d.order_id=? AND d.recipe_id=s.recipe_id "
                "WHERE COALESCE(d.served,0)=0",
                (order_id, order_id),
            ).fetchone()[0])
            if incomplete:
                return {
                    "op_id": op_id, "status": "conflict", "reason": "phase_incomplete",
                    "message": f"还有 {incomplete} 道菜未上桌",
                }
            connection.execute(
                "UPDATE meal_orders SET status='completed',version=version+1,completed_at=? WHERE id=?", (now, order_id)
            )
            updated = connection.execute("SELECT * FROM meal_orders WHERE id=?", (order_id,)).fetchone()
            self._record_change(connection, "meal_order", order_id, "upsert", self._order_payload(updated))
            return {"op_id": op_id, "status": "accepted", "order": self._order_payload(updated)}

        if phase != "ordering" and action in {
            "add_quantity", "set_quantity", "set_note", "remove_selection", "load_plan",
        }:
            return {
                "op_id": op_id, "status": "conflict", "reason": "meal_phase_locked",
                "message": "本餐已开始烹饪，点餐内容已经锁定",
            }

        if action == "load_plan":
            plan_id = str(operation.get("plan_id") or "")
            plan = connection.execute(
                "SELECT payload_json FROM meal_plans WHERE id=? AND deleted_at IS NULL", (plan_id,)
            ).fetchone()
            if plan is None:
                return {
                    "op_id": op_id, "status": "conflict", "reason": "plan_unavailable",
                    "message": "套餐已删除或不可用",
                }
            items = json.loads(str(plan["payload_json"])).get("items") or []
            loaded = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                recipe_id = str(item.get("recipe_id") or "")
                if not recipe_id:
                    continue
                recipe = connection.execute(
                    "SELECT 1 FROM recipes WHERE id=? AND deleted_at IS NULL AND published=1", (recipe_id,)
                ).fetchone()
                if recipe is None:
                    continue
                try:
                    quantity = max(0.25, min(20.0, float(item.get("servings_multiplier") or 1)))
                except (TypeError, ValueError):
                    continue
                note = str(item.get("note") or "")[:500]
                connection.execute(
                    "INSERT INTO meal_selections VALUES (?,?,?,?,?,?) ON CONFLICT(order_id,device_id,recipe_id) "
                    "DO UPDATE SET quantity=excluded.quantity,note=excluded.note,updated_at=excluded.updated_at",
                    (order_id, device_id, recipe_id, quantity, note, now),
                )
                row = self._meal_selection_row(connection, order_id, device_id, recipe_id)
                self._record_change(
                    connection, "meal_selection", f"{order_id}:{device_id}:{recipe_id}", "upsert",
                    self._selection_payload(row),
                )
                self._ensure_dish_state(connection, order_id, recipe_id)
                loaded += 1
            skipped = len(items) - loaded
            return {
                "op_id": op_id, "status": "accepted", "loaded": loaded, "skipped": skipped,
                "message": f"已跳过 {skipped} 道下架或失效菜品" if skipped else None,
                "order_id": order_id, "epoch": epoch,
            }

        recipe_id = str(operation.get("recipe_id") or "")
        recipe_row = connection.execute(
            "SELECT published FROM recipes WHERE id=? AND deleted_at IS NULL", (recipe_id,)
        ).fetchone() if recipe_id else None
        target_device = str(operation.get("device_id") or device_id)
        target = connection.execute(
            "SELECT revoked_at FROM devices WHERE id=?", (target_device,)
        ).fetchone()
        if target is None:
            return {
                "op_id": op_id, "status": "conflict", "reason": "device_unavailable",
                "message": "点餐设备已不存在",
            }
        existing = self._meal_selection_row(connection, order_id, target_device, recipe_id)
        if target["revoked_at"] and existing is None:
            return {
                "op_id": op_id, "status": "conflict", "reason": "device_unavailable",
                "message": "点餐设备已被撤销",
            }
        dish_selected = connection.execute(
            "SELECT 1 FROM meal_selections WHERE order_id=? AND recipe_id=? LIMIT 1", (order_id, recipe_id)
        ).fetchone() is not None
        if recipe_row is None:
            can_manage_existing = dish_selected and action in {
                "set_dish_completed", "set_dish_stage_completed", "remove_selection",
            }
            if not can_manage_existing and existing is None:
                return {
                    "op_id": op_id, "status": "conflict", "reason": "recipe_unavailable",
                    "message": "这道菜已被删除，没有提交该操作",
                }
        if (
            recipe_row is not None
            and not bool(recipe_row["published"])
            and existing is None
            and action in {"add_quantity", "set_quantity"}
        ):
            return {
                "op_id": op_id, "status": "conflict", "reason": "recipe_unpublished",
                "message": "这道菜已下架，没有加入本餐",
            }

        if action in {"set_dish_completed", "set_dish_stage_completed"}:
            if chef_device_id != device_id:
                return {
                    "op_id": op_id, "status": "conflict", "reason": "chef_only",
                    "message": "只有本餐主厨可以更新烹饪进度",
                }
            stage = "cooking" if action == "set_dish_completed" else str(operation.get("stage") or "")
            stage_column = {
                "prep": "prep_completed",
                "cooking": "cook_completed",
                "serving": "served",
            }.get(stage)
            if stage_column is None:
                raise ValidationError("Dish stage must be prep, cooking, or serving")
            if phase != stage:
                return {
                    "op_id": op_id, "status": "conflict", "reason": "meal_phase_changed",
                    "message": "只能更新本餐当前阶段的进度",
                }
            if connection.execute(
                "SELECT 1 FROM meal_selections WHERE order_id=? AND recipe_id=? LIMIT 1",
                (order_id, recipe_id),
            ).fetchone() is None:
                return {
                    "op_id": op_id, "status": "conflict", "reason": "selection_missing",
                    "message": "这道菜已不在本餐中",
                }
            completed = bool(operation.get("completed"))
            self._ensure_dish_state(connection, order_id, recipe_id)
            if stage == "cooking":
                connection.execute(
                    "UPDATE meal_dish_states SET cook_completed=?,completed=?,updated_at=? "
                    "WHERE order_id=? AND recipe_id=?",
                    (int(completed), int(completed), now, order_id, recipe_id),
                )
            else:
                connection.execute(
                    f"UPDATE meal_dish_states SET {stage_column}=?,updated_at=? "
                    "WHERE order_id=? AND recipe_id=?",
                    (int(completed), now, order_id, recipe_id),
                )
            row = connection.execute(
                "SELECT * FROM meal_dish_states WHERE order_id=? AND recipe_id=?", (order_id, recipe_id)
            ).fetchone()
            payload = self._dish_state_payload(row)
            self._record_change(connection, "meal_dish_state", f"{order_id}:{recipe_id}", "upsert", payload)
            return {"op_id": op_id, "status": "accepted", "dish_state": payload}

        if action == "remove_selection":
            if existing:
                connection.execute(
                    "DELETE FROM meal_selections WHERE order_id=? AND device_id=? AND recipe_id=?",
                    (order_id, target_device, recipe_id),
                )
                self._record_selection_delete(connection, order_id, target_device, recipe_id)
            remaining = connection.execute(
                "SELECT 1 FROM meal_selections WHERE order_id=? AND recipe_id=? LIMIT 1", (order_id, recipe_id)
            ).fetchone()
            if not remaining:
                connection.execute(
                    "DELETE FROM meal_dish_states WHERE order_id=? AND recipe_id=?", (order_id, recipe_id)
                )
                self._record_change(
                    connection, "meal_dish_state", f"{order_id}:{recipe_id}", "delete",
                    {"order_id": order_id, "recipe_id": recipe_id},
                )
            return {"op_id": op_id, "status": "accepted", "removed": bool(existing)}

        current_quantity = float(existing["quantity"]) if existing else 0.0
        if action == "add_quantity":
            quantity = current_quantity + float(operation.get("quantity") or 0)
        elif action == "set_quantity":
            quantity = float(operation.get("quantity") or 0)
        else:
            quantity = current_quantity
        if action == "set_note" and existing is None:
            return {
                "op_id": op_id, "status": "conflict", "reason": "selection_missing",
                "message": "对应点餐已不存在，备注没有提交",
            }
        if quantity <= 0 and action != "set_note":
            return self._apply_meal_operation(
                connection, device_id,
                {**operation, "action": "remove_selection", "device_id": target_device}, op_id,
            )
        if quantity > 20:
            return {
                "op_id": op_id, "status": "conflict", "reason": "invalid_quantity",
                "message": "单人单道菜最多为 20 份",
            }
        note = str(operation.get("note") if action == "set_note" else (existing["note"] if existing else ""))[:500]
        connection.execute(
            "INSERT INTO meal_selections VALUES (?,?,?,?,?,?) ON CONFLICT(order_id,device_id,recipe_id) "
            "DO UPDATE SET quantity=excluded.quantity,note=excluded.note,updated_at=excluded.updated_at",
            (order_id, target_device, recipe_id, quantity, note, now),
        )
        row = self._meal_selection_row(connection, order_id, target_device, recipe_id)
        payload = self._selection_payload(row)
        self._record_change(
            connection, "meal_selection", f"{order_id}:{target_device}:{recipe_id}", "upsert", payload
        )
        self._ensure_dish_state(connection, order_id, recipe_id)
        return {"op_id": op_id, "status": "accepted", "selection": payload}

    def _apply_meal_plan_operation(
        self, connection: sqlite3.Connection, device_id: str, operation: dict[str, Any], op_id: str
    ) -> dict[str, Any]:
        del device_id
        action = str(operation.get("action") or "")
        if action not in {"upsert", "delete"}:
            raise ValidationError("Meal plan action must be upsert or delete")
        payload = dict(operation.get("payload") or {})
        entity_id = str(payload.get("id") or operation.get("entity_id") or "").strip()
        if not entity_id or len(entity_id) > 80:
            raise ValidationError("Meal plan id is required")
        existing = connection.execute("SELECT * FROM meal_plans WHERE id=?", (entity_id,)).fetchone()
        base_version = int(operation.get("base_version") or 0)
        if existing is not None and int(existing["version"]) != base_version:
            return {
                "op_id": op_id, "status": "conflict", "reason": "version_mismatch",
                "server": json.loads(str(existing["payload_json"])),
            }
        if existing is None and base_version:
            return {"op_id": op_id, "status": "conflict", "reason": "missing_server_record"}
        now = utc_now()
        version = base_version + 1
        if action == "delete":
            if existing is None:
                return {"op_id": op_id, "status": "accepted", "entity_id": entity_id, "version": 0}
            deleted = {**json.loads(str(existing["payload_json"])), "version": version, "deleted_at": now}
            connection.execute(
                "UPDATE meal_plans SET payload_json=?,version=?,updated_at=?,deleted_at=? WHERE id=?",
                (_canonical_json(deleted), version, now, now, entity_id),
            )
            self._record_change(connection, "meal_plan", entity_id, "delete", deleted)
            return {"op_id": op_id, "status": "accepted", "entity_id": entity_id, "version": version}
        name = str(payload.get("name") or "").strip()
        items = payload.get("items")
        if not name or not isinstance(items, list) or not items:
            raise ValidationError("Meal plan requires a name and at least one item")
        created_at = str(existing["created_at"]) if existing else now
        clean = {
            **payload, "id": entity_id, "name": name[:120], "items": items,
            "version": version, "created_at": created_at, "updated_at": now, "deleted_at": None,
        }
        connection.execute(
            "INSERT INTO meal_plans VALUES (?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET "
            "payload_json=excluded.payload_json,version=excluded.version,updated_at=excluded.updated_at,deleted_at=NULL",
            (entity_id, _canonical_json(clean), version, created_at, now),
        )
        self._record_change(connection, "meal_plan", entity_id, "upsert", clean)
        return {"op_id": op_id, "status": "accepted", "entity_id": entity_id, "version": version}

    def _apply_operation(self, connection: sqlite3.Connection, device_id: str, operation: dict[str, Any]) -> dict[str, Any]:
        try:
            op_id = str(uuid.UUID(str(operation.get("op_id") or "")))
        except ValueError as exc:
            raise ValidationError("op_id must be a UUID") from exc
        receipt = connection.execute(
            "SELECT device_id,result_json FROM operation_receipts WHERE op_id=?", (op_id,)
        ).fetchone()
        if receipt:
            if str(receipt["device_id"]) != device_id:
                raise ValidationError("op_id was already used by another device")
            return json.loads(str(receipt["result_json"]))
        entity_type = str(operation.get("entity_type") or "")
        if entity_type in {"meal_order", "meal_selection", "meal_dish_state"}:
            result = self._apply_meal_operation(connection, device_id, operation, op_id)
            connection.execute(
                "INSERT INTO operation_receipts VALUES (?,?,?,?)",
                (op_id, device_id, _canonical_json(result), utc_now()),
            )
            return result
        if entity_type == "meal_plan":
            result = self._apply_meal_plan_operation(connection, device_id, operation, op_id)
            connection.execute(
                "INSERT INTO operation_receipts VALUES (?,?,?,?)",
                (op_id, device_id, _canonical_json(result), utc_now()),
            )
            return result
        if operation.get("entity_type") != "practice_log":
            raise ValidationError("Unsupported entity_type")
        action = str(operation.get("action") or "")
        if action not in {"upsert", "delete", "resolve_conflict"}:
            raise ValidationError("action must be upsert, delete, or resolve_conflict")
        payload = dict(operation.get("payload") or {})
        try:
            entity_id = str(uuid.UUID(str(payload.get("id") or operation.get("entity_id") or "")))
        except ValueError as exc:
            raise ValidationError("entity_id must be a UUID") from exc
        if action == "resolve_conflict":
            try:
                conflict_id = str(uuid.UUID(str(payload.get("conflict_id") or "")))
            except ValueError as exc:
                raise ValidationError("conflict_id must be a UUID") from exc
            resolution = str(payload.get("resolution") or "")
            if resolution != "server":
                raise ValidationError("A resolve_conflict operation only accepts the server resolution")
            conflict = connection.execute(
                "SELECT device_id,entity_id,resolved_at FROM conflicts WHERE id=?",
                (conflict_id,),
            ).fetchone()
            if (
                conflict is None
                or str(conflict["device_id"]) != device_id
                or str(conflict["entity_id"]) != entity_id
            ):
                raise ValidationError("Conflict not found or owned by another device")
            connection.execute(
                "UPDATE conflicts SET resolved_at=?,resolution='server' "
                "WHERE id=? AND entity_id=? AND device_id=? AND resolved_at IS NULL",
                (utc_now(), conflict_id, entity_id, device_id),
            )
            result = {
                "op_id": op_id,
                "status": "accepted",
                "entity_id": entity_id,
                "conflict_id": conflict_id,
            }
            connection.execute(
                "INSERT INTO operation_receipts VALUES (?,?,?,?)",
                (op_id, device_id, _canonical_json(result), utc_now()),
            )
            return result
        base_version = int(operation.get("base_version") or 0)
        existing = connection.execute("SELECT * FROM practice_logs WHERE id=?", (entity_id,)).fetchone()
        if existing is not None and int(existing["version"]) != base_version:
            conflict_id = str(uuid.uuid4())
            server = self._practice_payload(existing)
            incoming = {**payload, "id": entity_id, "action": action, "base_version": base_version}
            connection.execute(
                "INSERT INTO conflicts(id,entity_id,device_id,incoming_json,server_json,created_at) VALUES (?,?,?,?,?,?)",
                (conflict_id, entity_id, device_id, _canonical_json(incoming), _canonical_json(server), utc_now()),
            )
            result = {"op_id": op_id, "status": "conflict", "conflict_id": conflict_id, "server": server}
        elif existing is None and base_version != 0:
            result = {"op_id": op_id, "status": "conflict", "message": "Server record no longer exists"}
        elif action == "delete":
            if existing is None:
                result = {"op_id": op_id, "status": "accepted", "entity_id": entity_id, "version": 0}
            else:
                now = utc_now()
                version = int(existing["version"]) + 1
                connection.execute(
                    "UPDATE practice_logs SET version=?,updated_at=?,deleted_at=? WHERE id=?",
                    (version, now, now, entity_id),
                )
                row = connection.execute("SELECT * FROM practice_logs WHERE id=?", (entity_id,)).fetchone()
                self._record_change(connection, "practice_log", entity_id, "delete", self._practice_payload(row))
                result = {"op_id": op_id, "status": "accepted", "entity_id": entity_id, "version": version}
        else:
            clean = self._clean_practice({**payload, "id": entity_id})
            if connection.execute(
                "SELECT 1 FROM recipes WHERE id=? AND deleted_at IS NULL", (clean["recipe_id"],)
            ).fetchone() is None:
                raise ValidationError("Practice log references an unknown recipe")
            if clean["photo_sha256"] and connection.execute(
                "SELECT 1 FROM assets WHERE sha256=? AND kind='practice_photo'", (clean["photo_sha256"],)
            ).fetchone() is None:
                raise ValidationError("Practice photo must be uploaded before the log")
            now = utc_now()
            version = base_version + 1
            created = str(existing["created_at"]) if existing else now
            connection.execute(
                "INSERT INTO practice_logs VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET "
                "recipe_id=excluded.recipe_id,device_id=excluded.device_id,cooked_on=excluded.cooked_on,"
                "outcome=excluded.outcome,rating=excluded.rating,notes=excluded.notes,photo_sha256=excluded.photo_sha256,"
                "version=excluded.version,updated_at=excluded.updated_at,deleted_at=NULL",
                (entity_id, clean["recipe_id"], device_id, clean["cooked_on"], clean["outcome"], clean["rating"],
                 clean["notes"], clean["photo_sha256"], version, created, now),
            )
            row = connection.execute("SELECT * FROM practice_logs WHERE id=?", (entity_id,)).fetchone()
            self._record_change(connection, "practice_log", entity_id, "upsert", self._practice_payload(row))
            result = {"op_id": op_id, "status": "accepted", "entity_id": entity_id, "version": version}
        resolved_conflict_id = str(payload.get("_resolved_conflict_id") or "")
        if result["status"] == "accepted" and resolved_conflict_id:
            try:
                resolved_conflict_id = str(uuid.UUID(resolved_conflict_id))
            except ValueError as exc:
                raise ValidationError("_resolved_conflict_id must be a UUID") from exc
            resolution = str(payload.get("_conflict_resolution") or "incoming")
            if resolution not in {"incoming", "merged"}:
                raise ValidationError("_conflict_resolution must be incoming or merged")
            conflict = connection.execute(
                "SELECT device_id,entity_id,resolved_at FROM conflicts WHERE id=?",
                (resolved_conflict_id,),
            ).fetchone()
            if (
                conflict is None
                or str(conflict["device_id"]) != device_id
                or str(conflict["entity_id"]) != entity_id
            ):
                raise ValidationError("Conflict not found or owned by another device")
            connection.execute(
                "UPDATE conflicts SET resolved_at=?,resolution=? "
                "WHERE id=? AND entity_id=? AND device_id=? AND resolved_at IS NULL",
                (utc_now(), resolution, resolved_conflict_id, entity_id, device_id),
            )
        connection.execute(
            "INSERT INTO operation_receipts VALUES (?,?,?,?)", (op_id, device_id, _canonical_json(result), utc_now())
        )
        return result

    def sync(
        self,
        device_id: str,
        cursor: int,
        operations: Iterable[dict[str, Any]],
        capabilities: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        operation_list = list(operations)
        if len(operation_list) > MAX_SYNC_OPERATIONS:
            raise ValidationError(f"A sync request accepts at most {MAX_SYNC_OPERATIONS} operations")
        if cursor < 0:
            raise ValidationError("cursor cannot be negative")
        requested = set(capabilities) if capabilities is not None else set(LEGACY_CAPABILITIES)
        unknown = requested - SUPPORTED_CAPABILITIES
        if unknown:
            raise ValidationError(f"Unsupported capabilities: {', '.join(sorted(unknown))}")
        with self._write_connection() as connection:
            if requested & {"meal_order", "meal_selection", "meal_dish_state"}:
                if self._active_order_row(connection) is None:
                    self._create_order(connection)
            results = [self._apply_operation(connection, device_id, operation) for operation in operation_list]
            if requested & {"meal_order", "meal_selection", "meal_dish_state"}:
                if self._active_order_row(connection) is None:
                    self._create_order(connection)
            bootstrap = cursor == 0 and capabilities is not None and "recipe" in requested
            if bootstrap:
                revision = int(connection.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
                changes: list[dict[str, Any]] = []

                def add_snapshot(entity_type: str, payload: dict[str, Any]) -> None:
                    changes.append({
                        "revision": revision,
                        "entity_type": entity_type,
                        "entity_id": str(payload.get("id") or payload.get("order_id") or ""),
                        "action": "upsert",
                        "payload": payload,
                    })

                if "recipe" in requested:
                    for row in connection.execute(
                        "SELECT payload_json,published,recommended FROM recipes "
                        "WHERE deleted_at IS NULL ORDER BY updated_at DESC"
                    ):
                        payload = json.loads(str(row["payload_json"]))
                        payload["published"] = bool(row["published"])
                        payload["recommended"] = bool(row["recommended"])
                        add_snapshot("recipe", payload)
                if "practice_log" in requested:
                    for row in connection.execute("SELECT * FROM practice_logs WHERE deleted_at IS NULL ORDER BY cooked_on DESC,created_at DESC"):
                        add_snapshot("practice_log", self._practice_payload(row))
                if "meal_plan" in requested:
                    for row in connection.execute("SELECT payload_json FROM meal_plans WHERE deleted_at IS NULL ORDER BY updated_at DESC"):
                        add_snapshot("meal_plan", json.loads(str(row["payload_json"])))
                visible = changes
                has_more = False
                next_cursor = revision
            else:
                placeholders = ",".join("?" for _ in requested)
                rows = connection.execute(
                    f"SELECT * FROM change_log WHERE revision>? AND entity_type IN ({placeholders}) "
                    "ORDER BY revision LIMIT ?",
                    (cursor, *sorted(requested), MAX_SYNC_CHANGES + 1),
                ).fetchall() if requested else []
                visible = [
                    {
                        "revision": int(row["revision"]), "entity_type": str(row["entity_type"]),
                        "entity_id": str(row["entity_id"]), "action": str(row["action"]),
                        "payload": json.loads(str(row["payload_json"])),
                    }
                    for row in rows[:MAX_SYNC_CHANGES]
                ]
                has_more = len(rows) > MAX_SYNC_CHANGES
                next_cursor = int(visible[-1]["revision"]) if visible else int(
                    connection.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
                )
            response = {
                "schema_version": SCHEMA_VERSION,
                "operation_results": results,
                "changes": visible,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "capabilities": sorted(requested),
                "capability_key": hashlib.sha256(",".join(sorted(requested)).encode()).hexdigest()[:16],
            }
            if bootstrap:
                response["bootstrap"] = True
            if requested & {"meal_order", "meal_selection", "meal_dish_state"}:
                order = self._active_order_row(connection)
                response["meal"] = self._meal_snapshot(connection, order) if order else None
            return response

    def list_meal_plans(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        where = "" if include_deleted else "WHERE deleted_at IS NULL"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM meal_plans {where} ORDER BY updated_at DESC"
            ).fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]

    def list_meal_history(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(100, int(limit)))
        with self._connect() as connection:
            orders = connection.execute(
                "SELECT * FROM meal_orders WHERE status='completed' "
                "ORDER BY completed_at DESC,created_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
            return [self._meal_snapshot(connection, order) for order in orders]

    def list_practice_logs(self, recipe_id: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if recipe_id:
            clauses.append("recipe_id=?")
            params.append(recipe_id)
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM practice_logs {where} ORDER BY cooked_on DESC,created_at DESC", params
            ).fetchall()
        return [self._practice_payload(row) for row in rows]

    def list_indexed_recipes(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        where = "" if include_deleted else "WHERE deleted_at IS NULL"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT id,payload_json,updated_at,deleted_at,published,recommended FROM recipes {where}"
            ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            result.append({
                "id": str(row["id"]),
                "title": str(payload.get("title") or row["id"]),
                "category": str(payload.get("category") or ""),
                "updated_at": str(row["updated_at"]),
                "deleted_at": row["deleted_at"],
                "published": bool(row["published"]),
                "recommended": bool(row["recommended"]),
            })
        return sorted(result, key=lambda item: item["title"])

    def list_recipe_cover_reviews(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,output_folder,payload_json,published FROM recipes "
                "WHERE deleted_at IS NULL ORDER BY updated_at DESC"
            ).fetchall()
        reviews = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            status = str(payload.get("cover_image_status") or "").strip()
            reviews.append(
                {
                    "id": str(row["id"]),
                    "title": str(payload.get("title") or row["id"]),
                    "output_folder": str(row["output_folder"]),
                    "published": bool(row["published"]),
                    "cover_status": status or "unreviewed",
                    "cover_image_path": str(payload.get("cover_image_path") or ""),
                    "cover_image_time": payload.get("cover_image_time"),
                    "cover_source_kind": payload.get("cover_source_kind"),
                    "cover_source_label": payload.get("cover_source_label"),
                    "cover_source_url": payload.get("cover_source_url"),
                    "cover_source_step_index": payload.get("cover_source_step_index"),
                    "cover_original_size": payload.get("cover_original_size"),
                    "cover_crop_box": payload.get("cover_crop_box"),
                    "cover_selected_at": payload.get("cover_selected_at"),
                    "source_url": str(payload.get("source_url") or ""),
                    "steps": payload.get("steps") if isinstance(payload.get("steps"), list) else [],
                }
            )
        return sorted(reviews, key=lambda item: item["title"])

    def admin_update_practice(
        self, log_id: str, updates: dict[str, Any], *, delete: bool = False
    ) -> dict[str, Any]:
        with self._write_connection() as connection:
            existing = connection.execute("SELECT * FROM practice_logs WHERE id=?", (log_id,)).fetchone()
            if existing is None:
                raise ValidationError("Practice log not found")
            now = utc_now()
            version = int(existing["version"]) + 1
            if delete:
                connection.execute(
                    "UPDATE practice_logs SET version=?,updated_at=?,deleted_at=? WHERE id=?",
                    (version, now, now, log_id),
                )
                action = "delete"
            else:
                clean = self._clean_practice({**self._practice_payload(existing), **updates, "id": log_id})
                connection.execute(
                    "UPDATE practice_logs SET cooked_on=?,outcome=?,rating=?,notes=?,photo_sha256=?,"
                    "version=?,updated_at=?,deleted_at=NULL WHERE id=?",
                    (clean["cooked_on"], clean["outcome"], clean["rating"], clean["notes"],
                     clean["photo_sha256"], version, now, log_id),
                )
                action = "upsert"
            row = connection.execute("SELECT * FROM practice_logs WHERE id=?", (log_id,)).fetchone()
            payload = self._practice_payload(row)
            self._record_change(connection, "practice_log", log_id, action, payload)
            return payload

    def list_recipes(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        where = "" if include_deleted else "WHERE deleted_at IS NULL"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload_json,published,recommended FROM recipes {where} ORDER BY updated_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            payload["published"] = bool(row["published"])
            payload["recommended"] = bool(row["recommended"])
            result.append(payload)
        return result

    def list_conflicts(self, open_only: bool = True) -> list[dict[str, Any]]:
        where = "WHERE resolved_at IS NULL" if open_only else ""
        with self._connect() as connection:
            rows = connection.execute(f"SELECT * FROM conflicts {where} ORDER BY created_at DESC").fetchall()
        return [
            {**dict(row), "incoming": json.loads(str(row["incoming_json"])), "server": json.loads(str(row["server_json"]))}
            for row in rows
        ]

    def admin_save_practice_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Save the current Mac-admin version without creating avoidable conflicts."""

        entity_id = str(payload.get("id") or "")
        with self._write_connection() as connection:
            current = connection.execute("SELECT version FROM practice_logs WHERE id=?", (entity_id,)).fetchone()
            operation = {
                "op_id": str(uuid.uuid4()),
                "entity_type": "practice_log",
                "entity_id": entity_id,
                "action": "upsert",
                "base_version": int(current["version"]) if current else 0,
                "payload": payload,
            }
            return self._apply_operation(connection, "mac-admin", operation)

    def admin_delete_practice_log(self, entity_id: str) -> dict[str, Any]:
        with self._write_connection() as connection:
            current = connection.execute("SELECT version FROM practice_logs WHERE id=?", (entity_id,)).fetchone()
            return self._apply_operation(
                connection,
                "mac-admin",
                {
                    "op_id": str(uuid.uuid4()),
                    "entity_type": "practice_log",
                    "entity_id": entity_id,
                    "action": "delete",
                    "base_version": int(current["version"]) if current else 0,
                    "payload": {"id": entity_id},
                },
            )

    def resolve_conflict(
        self, conflict_id: str, resolution: str, merged_payload: dict[str, Any] | None = None
    ) -> None:
        if resolution not in {"server", "incoming", "merged"}:
            raise ValidationError("resolution must be server, incoming, or merged")
        with self._write_connection() as connection:
            conflict = connection.execute(
                "SELECT * FROM conflicts WHERE id=? AND resolved_at IS NULL", (conflict_id,)
            ).fetchone()
            if conflict is None:
                raise ValidationError("Conflict not found or already resolved")
            if resolution in {"incoming", "merged"}:
                incoming = (
                    dict(merged_payload or {})
                    if resolution == "merged"
                    else json.loads(str(conflict["incoming_json"]))
                )
                current = connection.execute("SELECT * FROM practice_logs WHERE id=?", (conflict["entity_id"],)).fetchone()
                self._apply_operation(
                    connection,
                    str(conflict["device_id"]),
                    {
                        "op_id": str(uuid.uuid4()), "entity_type": "practice_log",
                        "entity_id": str(conflict["entity_id"]), "action": incoming.pop("action", "upsert"),
                        "base_version": int(current["version"]) if current else 0, "payload": incoming,
                    },
                )
            connection.execute(
                "UPDATE conflicts SET resolved_at=?,resolution=? WHERE id=?", (utc_now(), resolution, conflict_id)
            )
