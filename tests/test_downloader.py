from __future__ import annotations

import sys
import types
from http.cookiejar import Cookie, CookieJar
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


def test_412_waits_refreshes_fingerprint_and_retries(monkeypatch) -> None:
    fingerprints = iter([
        {"buvid3": "first", "buvid4": "first-4"},
        {"buvid3": "second", "buvid4": "second-4"},
    ])
    cookie_contents: list[str] = []
    waits: list[float] = []
    extract_calls = 0

    class _YDL:
        def __init__(self, opts):
            cookie_contents.append(Path(opts["cookiefile"]).read_text(encoding="utf-8"))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            nonlocal extract_calls
            extract_calls += 1
            if extract_calls == 1:
                raise RuntimeError("[BiliBili] HTTP Error 412: Precondition Failed")
            return {"id": "BV1success"}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_YDL))
    monkeypatch.setattr(downloader, "_fingerprint_loaded", False)
    monkeypatch.setattr(downloader, "_fingerprint_cookies", None)
    monkeypatch.setattr(downloader, "_fetch_fingerprint_cookies", lambda: next(fingerprints))
    monkeypatch.setattr(downloader.time, "sleep", waits.append)

    result = downloader.fetch_video_info("https://www.bilibili.com/video/BV1success")

    assert result == {"id": "BV1success"}
    assert extract_calls == 2
    assert waits == [30]
    assert "buvid3\tfirst" in cookie_contents[0]
    assert "buvid3\tsecond" in cookie_contents[1]


def test_412_retry_is_bounded_and_raises_friendly_error(monkeypatch) -> None:
    calls = 0
    waits: list[float] = []

    def operation():
        nonlocal calls
        calls += 1
        raise RuntimeError("HTTP Error 412: Precondition Failed")

    monkeypatch.setattr(downloader, "BILIBILI_412_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(downloader, "BILIBILI_412_BASE_WAIT_SECONDS", 2)
    monkeypatch.setattr(downloader.time, "sleep", waits.append)

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        downloader._run_with_bilibili_412_retry(operation)

    assert calls == 3
    assert waits == [2, 4]


def test_non_412_error_is_not_retried(monkeypatch) -> None:
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise RuntimeError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(downloader.time, "sleep", lambda _delay: pytest.fail("must not wait"))

    with pytest.raises(RuntimeError, match="403"):
        downloader._run_with_bilibili_412_retry(operation)

    assert calls == 1


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


def test_creator_crawl_recursively_expands_collections_and_deduplicates(monkeypatch) -> None:
    home = "https://space.bilibili.com/123/video"
    collection = "https://space.bilibili.com/123/lists/99?type=season"
    payloads = {
        home: {
            "entries": [
                {"id": "BV1xx411c7mD", "title": "菜谱一", "uploader": "厨师"},
                {"url": collection, "title": "隐藏合集"},
            ]
        },
        collection: {
            "entries": [
                {"id": "BV1xx411c7mD", "title": "重复"},
                {"webpage_url": "https://www.bilibili.com/video/BV1ab411c7mE", "title": "菜谱二"},
            ]
        },
    }

    class _YDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, url, download=False):
            return payloads[url]

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_YDL))

    result = downloader.crawl_creator_videos(home, cookies="cookies.txt")

    assert result.complete is True
    assert result.uploader == "厨师"
    assert [video.bvid for video in result.videos] == ["BV1xx411c7mD", "BV1ab411c7mE"]


def test_creator_crawl_marks_nested_failure_incomplete(monkeypatch) -> None:
    home = "https://space.bilibili.com/123/video"
    collection = "https://space.bilibili.com/123/lists/99?type=season"

    class _YDL:
        def __init__(self, _opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, url, download=False):
            if url == home:
                return {"entries": [{"url": collection}]}
            raise RuntimeError("blocked")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_YDL))

    result = downloader.crawl_creator_videos(home, cookies="cookies.txt")

    assert result.complete is False
    assert "blocked" in result.warnings[0]


def test_creator_crawl_retries_when_nested_collection_returns_412(monkeypatch) -> None:
    home = "https://space.bilibili.com/123/video"
    collection = "https://space.bilibili.com/123/lists/99?type=season"
    attempts = 0
    waits: list[float] = []

    class _YDL:
        def __init__(self, _opts):
            nonlocal attempts
            attempts += 1
            self.attempt = attempts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, url, download=False):
            if url == home:
                return {"uploader": "厨师", "entries": [{"url": collection}]}
            if self.attempt == 1:
                raise RuntimeError("[BiliBili] HTTP Error 412: Precondition Failed")
            return {"entries": [{"id": "BV1xx411c7mD", "title": "菜谱一"}]}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_YDL))
    monkeypatch.setattr(downloader.time, "sleep", waits.append)

    result = downloader.crawl_creator_videos(home, cookies="cookies.txt")

    assert attempts == 2
    assert waits == [30]
    assert result.complete is True
    assert [video.bvid for video in result.videos] == ["BV1xx411c7mD"]


def _cookie(name: str, value: str, domain: str) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
    )


def test_import_edge_cookies_filters_domains_and_sets_private_mode(monkeypatch, tmp_path) -> None:
    import yt_dlp

    jar = CookieJar()
    jar.set_cookie(_cookie("SESSDATA", "secret", ".bilibili.com"))
    jar.set_cookie(_cookie("other", "do-not-save", ".example.com"))

    class _YDL:
        def __init__(self, _opts):
            self.cookiejar = jar

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _YDL)
    monkeypatch.setattr(downloader, "validate_bilibili_cookie_file", lambda _path: True)

    path = downloader.import_edge_cookies(project_root=tmp_path)
    content = path.read_text(encoding="utf-8")

    assert "bilibili.com" in content
    assert "example.com" not in content
    assert path.stat().st_mode & 0o777 == 0o600


def test_failed_edge_cookie_validation_preserves_previous_file(monkeypatch, tmp_path) -> None:
    import yt_dlp

    destination = downloader.imported_cookie_path(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.write_text("previous-cookie-file", encoding="utf-8")
    jar = CookieJar()
    jar.set_cookie(_cookie("SESSDATA", "new-secret", ".bilibili.com"))

    class _YDL:
        def __init__(self, _opts):
            self.cookiejar = jar

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _YDL)
    monkeypatch.setattr(downloader, "validate_bilibili_cookie_file", lambda _path: False)

    with pytest.raises(RuntimeError, match="登录态无效"):
        downloader.import_edge_cookies(project_root=tmp_path)

    assert destination.read_text(encoding="utf-8") == "previous-cookie-file"
