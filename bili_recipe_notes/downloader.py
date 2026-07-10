from __future__ import annotations

import json
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

FINGERPRINT_URL = "https://api.bilibili.com/x/frontend/finger/spi"
BILIBILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_fingerprint_cookies: dict[str, str] | None = None
_fingerprint_loaded = False


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
    message = str(exc)
    if "BiliBili" in message and "HTTP Error 412" in message:
        raise RuntimeError(
            "Bilibili returned HTTP 412 Precondition Failed. "
            "This usually means the request fingerprint or cookies were rejected. "
            "Try again first; if it still fails, refresh yt-dlp and use a fresh cookies.txt exported from your browser."
        ) from exc
    raise exc


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
    opts = _base_ydl_opts(cookies)
    opts["skip_download"] = True
    from yt_dlp import YoutubeDL
    try:
        with YoutubeDL(_yt_dlp_opts(opts)) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as exc:
        _raise_friendly_error(exc)
    finally:
        _cleanup_ydl_opts(opts)


def extract_creator_video_links(home_url: str, cookies: str | None = None) -> list[str]:
    """Extract all video URLs from a Bilibili creator page.

    This relies on yt-dlp playlist extraction against creator spaces.
    """
    opts = _base_ydl_opts(cookies)
    opts.update({
        "quiet": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": None,
    })

    from yt_dlp import YoutubeDL

    try:
        with YoutubeDL(_yt_dlp_opts(opts)) as ydl:
            info = ydl.extract_info(home_url, download=False)
    except Exception as exc:
        _raise_friendly_error(exc)
    finally:
        _cleanup_ydl_opts(opts)

    entries = info.get("entries") or []
    links: list[str] = []
    for entry in entries:
        if not entry:
            continue
        webpage_url = entry.get("webpage_url")
        if webpage_url:
            links.append(webpage_url)
            continue
        bvid = entry.get("id")
        if bvid:
            links.append(f"https://www.bilibili.com/video/{bvid}")

    # de-dup while preserving order
    seen: set[str] = set()
    unique_links: list[str] = []
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        unique_links.append(link)
    return unique_links


def download_subtitles(url: str, output_dir: Path, language: str = "zh", cookies: str | None = None) -> list[Path]:
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
    except Exception as exc:
        _raise_friendly_error(exc)
    finally:
        _cleanup_ydl_opts(opts)


def download_audio(url: str, output_dir: Path, cookies: str | None = None) -> Path:
    opts = _base_ydl_opts(cookies)
    opts.update({
        "format": "bestaudio/best",
        "quiet": True,
    })
    try:
        files = _download_to_staging(url, output_dir, "audio", opts)
        return files[0]
    except Exception as exc:
        _raise_friendly_error(exc)
    finally:
        _cleanup_ydl_opts(opts)


def download_lowres_video(url: str, output_dir: Path, cookies: str | None = None) -> Path:
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
    except Exception as exc:
        _raise_friendly_error(exc)
    finally:
        _cleanup_ydl_opts(opts)
