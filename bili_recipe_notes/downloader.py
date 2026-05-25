from __future__ import annotations

from pathlib import Path



def fetch_video_info(url: str, cookies: str | None = None) -> dict:
    opts = {"quiet": True, "skip_download": True}
    if cookies:
        opts["cookiefile"] = cookies
    from yt_dlp import YoutubeDL
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


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
