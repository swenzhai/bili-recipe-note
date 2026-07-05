from __future__ import annotations

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
