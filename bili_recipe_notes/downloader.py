from __future__ import annotations

from pathlib import Path



def fetch_video_info(url: str, cookies: str | None = None) -> dict:
    opts = {"quiet": True, "skip_download": True}
    if cookies:
        opts["cookiefile"] = cookies
    from yt_dlp import YoutubeDL
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def extract_creator_video_links(home_url: str, cookies: str | None = None) -> list[str]:
    """Extract all video URLs from a Bilibili creator page.

    This relies on yt-dlp playlist extraction against creator spaces.
    """
    opts = {
        "quiet": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": None,
    }
    if cookies:
        opts["cookiefile"] = cookies

    from yt_dlp import YoutubeDL

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(home_url, download=False)

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
    opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [language, f"{language}-CN", "zh-Hans", "zh"],
        "subtitlesformat": "vtt/srt/json3",
        "outtmpl": str(output_dir / "subtitle.%(ext)s"),
        "quiet": True,
    }
    if cookies:
        opts["cookiefile"] = cookies
    from yt_dlp import YoutubeDL
    with YoutubeDL(opts) as ydl:
        ydl.download([url])
    return sorted(output_dir.glob("subtitle.*"))


def download_audio(url: str, output_dir: Path, cookies: str | None = None) -> Path:
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "audio.%(ext)s"),
        "quiet": True,
    }
    if cookies:
        opts["cookiefile"] = cookies
    from yt_dlp import YoutubeDL
    with YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = list(output_dir.glob("audio.*"))
    if not files:
        raise FileNotFoundError("Audio download failed")
    return files[0]


def download_lowres_video(url: str, output_dir: Path, cookies: str | None = None) -> Path:
    opts = {
        "format": "worstvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": str(output_dir / "video.%(ext)s"),
        "quiet": True,
    }
    if cookies:
        opts["cookiefile"] = cookies
    from yt_dlp import YoutubeDL
    with YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = list(output_dir.glob("video.*"))
    if not files:
        raise FileNotFoundError("Video download failed")
    return files[0]
