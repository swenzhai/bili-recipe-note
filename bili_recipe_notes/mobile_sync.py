from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import mimetypes
import secrets
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
from .storage import atomic_write_json


SCHEMA_VERSION = 1
PROTOCOL_VERSION = 1
MAX_SYNC_OPERATIONS = 100
MAX_SYNC_CHANGES = 200
MAX_PRACTICE_PHOTO_BYTES = 5 * 1024 * 1024
PAIRING_TTL_MINUTES = 10
DATABASE_FILE_NAME = "mobile-sync.sqlite3"
MEDIA_DIR_NAME = "mobile-media"
SYNC_META_FILE_NAME = "sync-meta.json"
RECIPE_NAMESPACE = uuid.UUID("f7e7b2d5-96dd-43c6-a769-83e95f72bd39")
ALLOWED_OUTCOMES = {"", "success", "partial", "failed"}


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
            if version > SCHEMA_VERSION:
                raise MobileSyncError(f"Unsupported mobile sync database version: {version}")
            if 0 < version < SCHEMA_VERSION:
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_path = self.database_path.with_name(
                    f"{self.database_path.stem}.before-v{version}-to-v{SCHEMA_VERSION}-{timestamp}.bak"
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
                    content_hash TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, revision INTEGER NOT NULL
                );
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
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('revision', '0')")
            connection.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('server_id', ?)", (str(uuid.uuid4()),))

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
            row = connection.execute(
                "SELECT expires_at, used_at FROM pairing_tokens WHERE token_hash=?", (token_hash(pairing_token),)
            ).fetchone()
            if not row or row["used_at"] or str(row["expires_at"]) < now:
                raise AuthenticationError("Pairing token is invalid or expired")
            connection.execute("UPDATE pairing_tokens SET used_at=? WHERE token_hash=?", (now, token_hash(pairing_token)))
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
            "access_token": access_token,
        }

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
        if self.out_dir.exists():
            for folder in self.out_dir.iterdir():
                recipe_path = folder / "recipe.json"
                if not folder.is_dir() or not recipe_path.is_file():
                    continue
                recipe = _read_object(recipe_path)
                if not recipe or not isinstance(recipe.get("steps"), list):
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
                if old and old["content_hash"] == content_hash and old["deleted_at"] is None:
                    continue
                revision = self._record_change(connection, "recipe", recipe_id, "upsert", payload)
                connection.execute(
                    "INSERT INTO recipes VALUES (?, ?, ?, ?, ?, NULL, ?) ON CONFLICT(id) DO UPDATE SET "
                    "output_folder=excluded.output_folder,payload_json=excluded.payload_json,"
                    "content_hash=excluded.content_hash,updated_at=excluded.updated_at,deleted_at=NULL,revision=excluded.revision",
                    (recipe_id, str(folder), _canonical_json(payload), content_hash, now, revision),
                )
                for asset in assets:
                    connection.execute(
                        "INSERT INTO assets VALUES (?, ?, ?, ?, ?, 'recipe_image', ?) ON CONFLICT(sha256) DO UPDATE SET "
                        "recipe_id=excluded.recipe_id,path=excluded.path,mime_type=excluded.mime_type,byte_size=excluded.byte_size",
                        (asset["sha256"], recipe_id, asset["_path"], asset["mime_type"], asset["byte_size"], now),
                    )
                changed += 1
            for recipe_id, old in existing.items():
                if recipe_id in chosen or old["deleted_at"] is not None:
                    continue
                payload = {"schema_version": SCHEMA_VERSION, "id": recipe_id, "deleted_at": now}
                revision = self._record_change(connection, "recipe", recipe_id, "delete", payload)
                connection.execute(
                    "UPDATE recipes SET deleted_at=?,updated_at=?,revision=? WHERE id=?",
                    (now, now, revision, recipe_id),
                )
                deleted += 1
        return {"indexed": len(chosen), "changed": changed, "deleted": deleted, "duplicates": duplicates}

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
        if operation.get("entity_type") != "practice_log":
            raise ValidationError("Only practice_log operations are supported")
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

    def sync(self, device_id: str, cursor: int, operations: Iterable[dict[str, Any]]) -> dict[str, Any]:
        operation_list = list(operations)
        if len(operation_list) > MAX_SYNC_OPERATIONS:
            raise ValidationError(f"A sync request accepts at most {MAX_SYNC_OPERATIONS} operations")
        if cursor < 0:
            raise ValidationError("cursor cannot be negative")
        with self._write_connection() as connection:
            results = [self._apply_operation(connection, device_id, operation) for operation in operation_list]
            rows = connection.execute(
                "SELECT * FROM change_log WHERE revision>? ORDER BY revision LIMIT ?",
                (cursor, MAX_SYNC_CHANGES + 1),
            ).fetchall()
            visible = rows[:MAX_SYNC_CHANGES]
            changes = [
                {
                    "revision": int(row["revision"]), "entity_type": str(row["entity_type"]),
                    "entity_id": str(row["entity_id"]), "action": str(row["action"]),
                    "payload": json.loads(str(row["payload_json"])),
                }
                for row in visible
            ]
            return {
                "schema_version": SCHEMA_VERSION,
                "operation_results": results,
                "changes": changes,
                "next_cursor": int(visible[-1]["revision"]) if visible else cursor,
                "has_more": len(rows) > MAX_SYNC_CHANGES,
            }

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
                f"SELECT id,payload_json,updated_at,deleted_at FROM recipes {where}"
            ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            result.append({
                "id": str(row["id"]),
                "title": str(payload.get("title") or row["id"]),
                "updated_at": str(row["updated_at"]),
                "deleted_at": row["deleted_at"],
            })
        return sorted(result, key=lambda item: item["title"])

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
            rows = connection.execute(f"SELECT payload_json FROM recipes {where} ORDER BY updated_at DESC").fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]

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
