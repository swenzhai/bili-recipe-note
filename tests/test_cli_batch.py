from __future__ import annotations

from dataclasses import asdict
from io import StringIO
from pathlib import Path

import pytest

from bili_recipe_notes import cli
from bili_recipe_notes.batch_queue import create_batch_state, load_batch_state, save_batch_state
from bili_recipe_notes.pipeline import BatchJobItemResult, BatchJobResult


def test_cli_creates_pending_batch_from_file_without_running(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    links = tmp_path / "links.txt"
    links.write_text(
        "# UP 主链接\nhttps://example.com/BV1\n\nhttps://example.com/BV2\nhttps://example.com/BV1\n",
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(
        [
            "--batch",
            "--batch-file",
            str(links),
            "--batch-id",
            "cli-pending",
            "--target-stage",
            "raw",
            "--create-only",
        ]
    )

    assert cli.run(args) == 0
    state = load_batch_state("cli-pending", project_root=tmp_path)
    assert [item.url for item in state.items] == ["https://example.com/BV1", "https://example.com/BV2"]
    assert all(item.status == "pending" for item in state.items)
    assert state.options["target_stage"] == "raw"


def test_cli_runs_new_batch_and_passes_processing_options(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_run_batch(options, log=None):
        captured["options"] = options
        state = create_batch_state(options.urls, asdict(options), batch_id=options.batch_id)
        results = []
        for item in state.items:
            item.status = "raw_ready"
            item.stages["raw"].status = "done"
            results.append(BatchJobItemResult(item.url, "raw_ready"))
        save_batch_state(state)
        return BatchJobResult(results)

    monkeypatch.setattr(cli, "run_batch", fake_run_batch)
    args = cli.build_parser().parse_args(
        [
            "https://example.com/BV1",
            "--batch",
            "--batch-url",
            "https://example.com/BV2",
            "--batch-id",
            "cli-run",
            "--target-stage",
            "raw",
            "--whisper-model",
            "medium",
            "--cookies",
            "cookies.txt",
            "--no-screenshot",
        ]
    )

    assert cli.run(args) == 0
    options = captured["options"]
    assert options.urls == ["https://example.com/BV1", "https://example.com/BV2"]
    assert options.batch_id == "cli-run"
    assert options.target_stage == "raw"
    assert options.whisper_model == "medium"
    assert options.cookies == "cookies.txt"
    assert options.no_screenshot is True


def test_cli_resumes_existing_batch_without_new_urls(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    state = create_batch_state(["https://example.com/BV1"], {}, batch_id="resume-me")
    captured = {}

    def fake_run_batch(options, log=None):
        captured["options"] = options
        state.items[0].status = "done"
        state.items[0].stages["raw"].status = "done"
        state.items[0].stages["recipe"].status = "done"
        save_batch_state(state)
        return BatchJobResult([BatchJobItemResult(state.items[0].url, "done")])

    monkeypatch.setattr(cli, "run_batch", fake_run_batch)
    args = cli.build_parser().parse_args(
        ["--resume-batch", "resume-me", "--target-stage", "recipe", "--llm-provider", "none"]
    )

    assert cli.run(args) == 0
    assert captured["options"].urls == []
    assert captured["options"].batch_id == "resume-me"
    assert captured["options"].resume_mode == "resume-unfinished"
    assert captured["options"].target_stage == "recipe"


def test_cli_returns_nonzero_when_batch_item_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_run_batch(options, log=None):
        state = create_batch_state(options.urls, {}, batch_id=options.batch_id)
        state.items[0].status = "failed"
        state.items[0].error = "network failed"
        state.items[0].stages["raw"].status = "failed"
        save_batch_state(state)
        return BatchJobResult([BatchJobItemResult(state.items[0].url, "failed", error="network failed")])

    monkeypatch.setattr(cli, "run_batch", fake_run_batch)
    args = cli.build_parser().parse_args(
        ["https://example.com/BV1", "--batch", "--batch-id", "cli-failed"]
    )

    assert cli.run(args) == 1


def test_cli_reads_batch_urls_from_stdin_and_deduplicates(monkeypatch) -> None:
    monkeypatch.setattr(
        cli.sys,
        "stdin",
        StringIO("# comment\nhttps://example.com/BV1\nhttps://example.com/BV1\nhttps://example.com/BV2\n"),
    )
    args = cli.build_parser().parse_args(["--batch", "--batch-file", "-"])

    assert cli._read_batch_urls(args) == ["https://example.com/BV1", "https://example.com/BV2"]


def test_cli_rejects_batch_only_flags_without_batch_mode() -> None:
    args = cli.build_parser().parse_args(["https://example.com/BV1", "--create-only"])

    with pytest.raises(ValueError, match="Batch-only"):
        cli.run(args)

