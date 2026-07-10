from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from bili_recipe_notes import downloader


def test_base_ydl_opts_adds_fingerprint_cookie_file_without_user_cookie_file(monkeypatch) -> None:
    monkeypatch.setattr(downloader, "_fingerprint_loaded", False)
    monkeypatch.setattr(downloader, "_fingerprint_cookies", None)
    monkeypatch.setattr(downloader, "_fetch_fingerprint_cookies", lambda: {"buvid3": "a", "buvid4": "b"})

    opts = downloader._base_ydl_opts()

    cookiefile = opts["cookiefile"]
    assert "Cookie" not in opts["http_headers"]
    assert "User-Agent" in opts["http_headers"]
    assert "buvid3\ta" in downloader.Path(cookiefile).read_text(encoding="utf-8")
    assert "buvid4\tb" in downloader.Path(cookiefile).read_text(encoding="utf-8")

    downloader._cleanup_ydl_opts(opts)
    assert not downloader.Path(cookiefile).exists()


def test_base_ydl_opts_uses_cookie_file_without_cookie_header(monkeypatch) -> None:
    monkeypatch.setattr(downloader, "_fetch_fingerprint_cookies", lambda: {"buvid3": "a", "buvid4": "b"})

    opts = downloader._base_ydl_opts("cookies.txt")

    assert opts["cookiefile"] == "cookies.txt"
    assert "Cookie" not in opts["http_headers"]
    assert "_temp_cookiefile" not in opts


def test_bilibili_412_gets_friendly_error() -> None:
    with pytest.raises(RuntimeError, match="Bilibili returned HTTP 412"):
        downloader._raise_friendly_error(Exception("[BiliBili] Unable to download JSON metadata: HTTP Error 412"))


def test_failed_download_does_not_reuse_stale_audio(monkeypatch, tmp_path) -> None:
    old_audio = tmp_path / "audio.m4a"
    old_audio.write_bytes(b"old")

    class _NoOutputYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def download(self, _urls):
            return None

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_NoOutputYDL))

    with pytest.raises(FileNotFoundError, match="no usable file"):
        downloader.download_audio("https://example.com/video", tmp_path, cookies="cookies.txt")

    assert old_audio.read_bytes() == b"old"


def test_lowres_video_prefers_video_only_and_atomically_replaces_old_file(monkeypatch, tmp_path) -> None:
    old_video = tmp_path / "video.webm"
    old_video.write_bytes(b"old")
    captured = {}

    class _WritingYDL:
        def __init__(self, opts):
            captured.update(opts)
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def download(self, _urls):
            Path(self.opts["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"new-video")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_WritingYDL))

    result = downloader.download_lowres_video("https://example.com/video", tmp_path, cookies="cookies.txt")

    assert result == tmp_path / "video.mp4"
    assert result.read_bytes() == b"new-video"
    assert not old_video.exists()
    assert captured["format"].startswith("worstvideo/")
    assert "+" not in captured["format"]
