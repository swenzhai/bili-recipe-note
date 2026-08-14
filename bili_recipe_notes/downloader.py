from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TypeVar
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from .config import CONFIG_DIR_NAME
from .storage import atomic_write_bytes

FINGERPRINT_URL = "https://api.bilibili.com/x/frontend/finger/spi"
LOGIN_STATUS_URL = "https://api.bilibili.com/x/web-interface/nav"
BILIBILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
BILIBILI_412_MAX_ATTEMPTS = 5
BILIBILI_412_BASE_WAIT_SECONDS = 30
BILIBILI_412_MAX_WAIT_SECONDS = 300

_fingerprint_cookies: dict[str, str] | None = None
_fingerprint_loaded = False
T = TypeVar("T")


@dataclass
class CreatorVideo:
    bvid: str
    title: str
    url: str


@dataclass
class CreatorCrawlResult:
    uid: str
    uploader: str
    videos: list[CreatorVideo]
    complete: bool = True
    warnings: list[str] = field(default_factory=list)


def imported_cookie_path(project_root: str | Path | None = None) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    return root / CONFIG_DIR_NAME / "cookies" / "bilibili-edge.txt"


def validate_bilibili_cookie_file(cookie_path: str | Path) -> bool:
    """Verify a Netscape cookie file against Bilibili without exposing cookie values."""
    from yt_dlp.cookies import YoutubeDLCookieJar

    jar = YoutubeDLCookieJar(str(cookie_path))
    jar.load(ignore_discard=True, ignore_expires=True)
    opener = build_opener(HTTPCookieProcessor(jar))
    request = Request(LOGIN_STATUS_URL, headers=BILIBILI_HEADERS)
    with opener.open(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") if isinstance(payload, dict) else None
    return bool(payload.get("code") == 0 and isinstance(data, dict) and data.get("isLogin") is True)


def import_edge_cookies(
    project_root: str | Path | None = None,
    profile: str | None = None,
) -> Path:
    """Import only live Bilibili cookies from Edge into a private Netscape file."""
    from yt_dlp import YoutubeDL
    from yt_dlp.cookies import YoutubeDLCookieJar

    with YoutubeDL({"quiet": True, "cookiesfrombrowser": ("edge", profile, None, None)}) as ydl:
        source_jar = ydl.cookiejar

    filtered = YoutubeDLCookieJar()
    now = time.time()
    for cookie in source_jar:
        domain = (cookie.domain or "").lower().lstrip(".")
        if not (domain == "bilibili.com" or domain.endswith(".bilibili.com")):
            continue
        if cookie.expires is not None and cookie.expires <= now:
            continue
        filtered.set_cookie(cookie)
    if not filtered:
        raise RuntimeError("Edge 中没有找到可用的 Bilibili Cookie，请先确认已经登录。")

    destination = imported_cookie_path(project_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(destination.parent, 0o700)
    except OSError:
        pass

    descriptor, temporary_name = tempfile.mkstemp(prefix=".edge-cookie-", suffix=".txt")
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        filtered.filename = str(temporary_path)
        # Expired cookies were filtered above; yt-dlp encodes live session cookies
        # with expires=0, which requires ignore_expires=True while saving.
        filtered.save(ignore_discard=True, ignore_expires=True)
        if not validate_bilibili_cookie_file(temporary_path):
            raise RuntimeError("Edge 中的 Bilibili 登录态无效或已经过期，请重新登录后再刷新。")
        atomic_write_bytes(destination, temporary_path.read_bytes(), backup=False)
        try:
            os.chmod(destination, 0o600)
        except OSError:
            pass
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def remove_imported_cookies(project_root: str | Path | None = None) -> None:
    imported_cookie_path(project_root).unlink(missing_ok=True)


def _fetch_fingerprint_cookies() -> dict[str, str] | None:
    req = Request(FINGERPRINT_URL, headers=BILIBILI_HEADERS)
    with urlopen(req, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data") or {}
    buvid3 = data.get("b_3")
    buvid4 = data.get("b_4")
    if not buvid3 or not buvid4:
        return None
    return {"buvid3": buvid3, "buvid4": buvid4}


def _get_fingerprint_cookies() -> dict[str, str] | None:
    global _fingerprint_cookies, _fingerprint_loaded
    if _fingerprint_loaded:
        return _fingerprint_cookies
    _fingerprint_loaded = True
    try:
        _fingerprint_cookies = _fetch_fingerprint_cookies()
    except Exception:
        _fingerprint_cookies = None
    return _fingerprint_cookies


def _reset_fingerprint_cookies() -> None:
    global _fingerprint_cookies, _fingerprint_loaded
    _fingerprint_cookies = None
    _fingerprint_loaded = False


def _write_temp_cookie_file(cookies: dict[str, str]) -> Path:
    cookie_file = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".cookies.txt", delete=False)
    with cookie_file:
        cookie_file.write("# Netscape HTTP Cookie File\n")
        for name, value in cookies.items():
            cookie_file.write(f".bilibili.com\tTRUE\t/\tFALSE\t2147483647\t{name}\t{value}\n")
    return Path(cookie_file.name)


def _base_ydl_opts(cookies: str | None = None) -> dict:
    opts = {"quiet": True, "http_headers": dict(BILIBILI_HEADERS)}
    if cookies:
        opts["cookiefile"] = cookies
    else:
        fingerprint_cookies = _get_fingerprint_cookies()
        if fingerprint_cookies:
            cookie_file = _write_temp_cookie_file(fingerprint_cookies)
            opts["cookiefile"] = str(cookie_file)
            opts["_temp_cookiefile"] = str(cookie_file)
    return opts


def _yt_dlp_opts(opts: dict) -> dict:
    ydl_opts = dict(opts)
    ydl_opts.pop("_temp_cookiefile", None)
    return ydl_opts


def _cleanup_ydl_opts(opts: dict) -> None:
    temp_cookiefile = opts.get("_temp_cookiefile")
    if temp_cookiefile:
        Path(temp_cookiefile).unlink(missing_ok=True)


def _raise_friendly_error(exc: Exception) -> None:
    if _is_bilibili_412(exc):
        raise RuntimeError(
            "Bilibili returned HTTP 412 Precondition Failed. "
            f"The request was still rejected after {BILIBILI_412_MAX_ATTEMPTS} attempts. "
            "Refresh yt-dlp or use a fresh cookies.txt exported from your browser."
        ) from exc
    raise exc


def _is_bilibili_412(exc: BaseException) -> bool:
    """Recognize yt-dlp wrappers as well as the underlying HTTPError."""
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if getattr(current, "code", None) == 412:
            return True
        message = str(current)
        if re.search(r"HTTP(?: Error)?\s+412\b", message, flags=re.IGNORECASE):
            return True
        current = current.__cause__ or current.__context__
    return False


def _run_with_bilibili_412_retry(operation: Callable[[], T]) -> T:
    """Retry a Bilibili operation after refreshing its anonymous fingerprint."""
    max_attempts = max(1, BILIBILI_412_MAX_ATTEMPTS)
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if not _is_bilibili_412(exc):
                raise
            if attempt >= max_attempts:
                _raise_friendly_error(exc)

            _reset_fingerprint_cookies()
            delay = min(
                BILIBILI_412_BASE_WAIT_SECONDS * (2 ** (attempt - 1)),
                BILIBILI_412_MAX_WAIT_SECONDS,
            )
            print(
                "Bilibili returned HTTP 412; "
                f"waiting {delay:g}s before retry {attempt + 1}/{max_attempts}...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)

    raise AssertionError("unreachable")


def _completed_downloads(
    staging_dir: Path,
    stem: str,
    allowed_suffixes: set[str] | None = None,
) -> list[Path]:
    completed: list[Path] = []
    for path in sorted(staging_dir.glob(f"{stem}.*")):
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        if path.suffix.lower() in {".part", ".temp", ".tmp", ".ytdl"}:
            continue
        if allowed_suffixes is not None and path.suffix.lower() not in allowed_suffixes:
            continue
        completed.append(path)
    return completed


def _promote_downloads(staging_files: list[Path], output_dir: Path, stem: str) -> list[Path]:
    """Replace prior artifacts only after a fresh download was validated."""
    for old_path in output_dir.glob(f"{stem}.*"):
        if old_path.is_file() or old_path.is_symlink():
            old_path.unlink(missing_ok=True)

    promoted: list[Path] = []
    for staging_file in staging_files:
        destination = output_dir / staging_file.name
        staging_file.replace(destination)
        promoted.append(destination)
    return promoted


def _download_to_staging(
    url: str,
    output_dir: Path,
    stem: str,
    opts: dict,
    *,
    allowed_suffixes: set[str] | None = None,
) -> list[Path]:
    """Download in an isolated directory so stale media cannot look successful."""
    output_dir.mkdir(parents=True, exist_ok=True)
    from yt_dlp import YoutubeDL

    with tempfile.TemporaryDirectory(prefix=f".{stem}-download-", dir=output_dir) as temp_dir:
        staging_dir = Path(temp_dir)
        staged_opts = dict(opts)
        staged_opts["outtmpl"] = str(staging_dir / f"{stem}.%(ext)s")
        with YoutubeDL(_yt_dlp_opts(staged_opts)) as ydl:
            ydl.download([url])

        staging_files = _completed_downloads(staging_dir, stem, allowed_suffixes)
        if not staging_files:
            raise FileNotFoundError(f"{stem.capitalize()} download produced no usable file")
        return _promote_downloads(staging_files, output_dir, stem)


def fetch_video_info(url: str, cookies: str | None = None) -> dict:
    from yt_dlp import YoutubeDL

    def operation() -> dict:
        opts = _base_ydl_opts(cookies)
        opts["skip_download"] = True
        try:
            with YoutubeDL(_yt_dlp_opts(opts)) as ydl:
                return ydl.extract_info(url, download=False)
        finally:
            _cleanup_ydl_opts(opts)

    return _run_with_bilibili_412_retry(operation)


def crawl_creator_videos(home_url: str, cookies: str | None = None) -> CreatorCrawlResult:
    """Recursively crawl creator uploads and nested hidden-mode collections."""
    uid_match = re.search(r"space\.bilibili\.com/(\d+)", home_url)
    if not uid_match:
        raise ValueError("请输入有效的 Bilibili UP 主空间链接。")
    uid = uid_match.group(1)
    from yt_dlp import YoutubeDL

    def bvid_from_entry(entry: dict) -> str | None:
        for candidate in (entry.get("id"), entry.get("url"), entry.get("webpage_url")):
            match = re.search(r"(BV[0-9A-Za-z]{10})", str(candidate or ""), flags=re.IGNORECASE)
            if match:
                return "BV" + match.group(1)[2:]
        return None

    def operation() -> CreatorCrawlResult:
        opts = _base_ydl_opts(cookies)
        opts.update({
            "quiet": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "playlistend": None,
        })
        warnings: list[str] = []
        complete = True
        videos: list[CreatorVideo] = []
        seen_bvids: set[str] = set()
        visited_playlists: set[str] = set()

        try:
            with YoutubeDL(_yt_dlp_opts(opts)) as ydl:
                info = ydl.extract_info(home_url, download=False)
                uploader = str(info.get("uploader") or "").strip()

                def walk_entries(container: dict, depth: int = 0) -> None:
                    nonlocal complete, uploader
                    if depth > 8:
                        complete = False
                        warnings.append("合集嵌套层级超过安全上限，已停止继续展开。")
                        return
                    raw_entries = container.get("entries") or []
                    iterator = iter(raw_entries)
                    while True:
                        try:
                            entry = next(iterator)
                        except StopIteration:
                            break
                        except Exception as exc:  # noqa: BLE001
                            if _is_bilibili_412(exc):
                                raise
                            complete = False
                            warnings.append(f"读取投稿分页失败：{exc}")
                            break
                        if not isinstance(entry, dict):
                            continue
                        uploader = uploader or str(entry.get("uploader") or "").strip()
                        bvid = bvid_from_entry(entry)
                        if bvid:
                            if bvid not in seen_bvids:
                                seen_bvids.add(bvid)
                                videos.append(
                                    CreatorVideo(
                                        bvid=bvid,
                                        title=str(entry.get("title") or bvid).strip(),
                                        url=f"https://www.bilibili.com/video/{bvid}",
                                    )
                                )
                            continue

                        nested_url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
                        if not nested_url or nested_url in visited_playlists:
                            continue
                        if "space.bilibili.com" not in nested_url:
                            continue
                        visited_playlists.add(nested_url)
                        try:
                            nested = ydl.extract_info(nested_url, download=False)
                        except Exception as exc:  # noqa: BLE001
                            if _is_bilibili_412(exc):
                                raise
                            complete = False
                            warnings.append(f"合集展开失败：{nested_url}：{exc}")
                            continue
                        if isinstance(nested, dict):
                            walk_entries(nested, depth + 1)

                visited_playlists.add(home_url)
                walk_entries(info)
        finally:
            _cleanup_ydl_opts(opts)

        return CreatorCrawlResult(
            uid=uid,
            uploader=uploader or uid,
            videos=videos,
            complete=complete,
            warnings=warnings,
        )

    return _run_with_bilibili_412_retry(operation)


def extract_creator_video_links(home_url: str, cookies: str | None = None) -> list[str]:
    """Compatibility wrapper returning all canonical creator video URLs."""
    return [video.url for video in crawl_creator_videos(home_url, cookies=cookies).videos]


def download_subtitles(url: str, output_dir: Path, language: str = "zh", cookies: str | None = None) -> list[Path]:
    def operation() -> list[Path]:
        opts = _base_ydl_opts(cookies)
        opts.update({
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [language, f"{language}-CN", "zh-Hans", "zh"],
            "subtitlesformat": "vtt/srt/json3",
            "quiet": True,
        })
        try:
            return _download_to_staging(
                url,
                output_dir,
                "subtitle",
                opts,
                allowed_suffixes={".vtt", ".srt", ".json3"},
            )
        finally:
            _cleanup_ydl_opts(opts)

    return _run_with_bilibili_412_retry(operation)


def download_audio(url: str, output_dir: Path, cookies: str | None = None) -> Path:
    def operation() -> Path:
        opts = _base_ydl_opts(cookies)
        opts.update({
            "format": "bestaudio/best",
            "quiet": True,
        })
        try:
            files = _download_to_staging(url, output_dir, "audio", opts)
            return files[0]
        finally:
            _cleanup_ydl_opts(opts)

    return _run_with_bilibili_412_retry(operation)


def download_lowres_video(url: str, output_dir: Path, cookies: str | None = None) -> Path:
    def operation() -> Path:
        opts = _base_ydl_opts(cookies)
        opts.update({
            # Screenshots do not need an audio stream.  Prefer a video-only stream,
            # while retaining a combined-stream fallback for sites without DASH.
            "format": "worstvideo/bestvideo/worst",
            "quiet": True,
        })
        try:
            files = _download_to_staging(url, output_dir, "video", opts)
            return files[0]
        finally:
            _cleanup_ydl_opts(opts)

    return _run_with_bilibili_412_retry(operation)
