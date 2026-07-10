from __future__ import annotations

import subprocess

from bili_recipe_notes.llm import (
    apply_cli_extra_instructions,
    append_missing_image_links,
    build_summary_prompt,
    clean_llm_markdown_output,
    ensure_recipe_summary_section,
    ensure_source_attribution,
    extract_markdown_image_links,
    finalize_rewritten_note,
    get_last_llm_error,
    markdown_has_image_links,
    normalize_markdown_image_paths,
    summarize_note,
    summarize_note_with_codex_cli,
    summarize_note_with_openai,
    summarize_note_with_opencode,
)


def test_summarize_note_with_opencode_success(monkeypatch) -> None:
    def _run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="总结内容\n", stderr="")

    monkeypatch.setattr("bili_recipe_notes.llm._opencode_command", lambda: "opencode.cmd")
    monkeypatch.setattr(subprocess, "run", _run)
    assert summarize_note_with_opencode("# note") == "总结内容"


def test_summarize_note_with_opencode_failure(monkeypatch) -> None:
    def _run(*args, **kwargs):
        raise FileNotFoundError("opencode not found")

    monkeypatch.setattr("bili_recipe_notes.llm._opencode_command", lambda: "opencode.cmd")
    monkeypatch.setattr(subprocess, "run", _run)
    assert summarize_note_with_opencode("# note") is None
    assert "FileNotFoundError" in (get_last_llm_error() or "")


def test_summarize_note_with_opencode_uses_windows_launcher_and_stdin(monkeypatch) -> None:
    calls = []

    def _run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="stdin总结\n", stderr="")

    monkeypatch.setattr("bili_recipe_notes.llm._opencode_command", lambda: "C:/bin/opencode.cmd")
    monkeypatch.setattr(subprocess, "run", _run)

    assert summarize_note_with_opencode("# note") == "stdin总结"
    assert calls[0][0][0][:2] == ["C:/bin/opencode.cmd", "run"]
    assert "--dir" in calls[0][0][0]
    assert calls[0][1]["cwd"]
    assert "菜谱笔记" in calls[0][1]["input"]
    assert calls[0][1]["encoding"] == "utf-8"


def test_summarize_note_with_opencode_reads_temp_markdown_file(monkeypatch) -> None:
    def _run(*args, **kwargs):
        output_path = kwargs["cwd"] / "note.md"
        output_path.write_text("```markdown\n## 配料信息\n\n- 鸡蛋\n```", encoding="utf-8")
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="已输出到 note.md", stderr="")

    monkeypatch.setattr("bili_recipe_notes.llm._opencode_command", lambda: "opencode.cmd")
    monkeypatch.setattr(subprocess, "run", _run)

    assert summarize_note_with_opencode("# note") == "## 配料信息\n\n- 鸡蛋"


def test_normalize_markdown_image_paths() -> None:
    md = "\n".join(
        [
            "![](./images/step_01.jpg)",
            "![](/tmp/step_02.png)",
            "![x](https://example.com/a.jpg)",
        ]
    )
    normalized = normalize_markdown_image_paths(md)
    assert "![](images/step_01.jpg)" in normalized
    assert "![](images/step_02.png)" in normalized
    assert "![x](https://example.com/a.jpg)" in normalized
    assert normalize_markdown_image_paths(r"![](images\step_03.jpg)") == "![](images/step_03.jpg)"


def test_clean_llm_markdown_output_unwraps_full_markdown_fence() -> None:
    cleaned = clean_llm_markdown_output("```markdown\n# 标题\n\n![](/tmp/step.jpg)\n```")

    assert cleaned == "# 标题\n\n![](images/step.jpg)"


def test_markdown_has_image_links() -> None:
    assert markdown_has_image_links("![](images/step_01.jpg)")
    assert not markdown_has_image_links("no image")


def test_append_missing_image_links() -> None:
    source_links = extract_markdown_image_links("![](images/step_01.jpg)\n![](images/step_02.jpg)")
    merged = append_missing_image_links("## 烹饪\n\n步骤文字", source_links)
    assert "## 步骤配图补全" in merged
    assert "![](images/step_01.jpg)" in merged
    assert "![](images/step_02.jpg)" in merged


def test_build_summary_prompt_requires_recipe_summary() -> None:
    prompt = build_summary_prompt("# note")

    assert "## 关键点速查" in prompt
    assert "不要输出证据、置信度" in prompt


def test_ensure_recipe_summary_section_appends_missing_summary() -> None:
    source = "\n".join(
        [
            "# 番茄炒蛋",
            "",
            "## 总结要点",
            "",
            "- 火不要太大",
            "",
            "## 不确定信息",
            "",
            "- 盐量需要确认",
        ]
    )

    merged = ensure_recipe_summary_section("## 烹饪\n\n炒熟出锅", source)

    assert "## 关键点速查" in merged
    assert "火不要太大" in merged
    assert "盐量需要确认" not in merged


def test_ensure_recipe_summary_section_keeps_existing_summary() -> None:
    markdown = "## 菜谱总结\n\n- 已有总结\n"

    assert ensure_recipe_summary_section(markdown, "# source") == "## 关键点速查\n\n- 已有总结\n"


def test_summarize_note_with_codex_cli_success(monkeypatch) -> None:
    calls = []

    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        output_path = cmd[cmd.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("## 配料信息\n\n- 鸡蛋\n")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)

    result = summarize_note_with_codex_cli("# note", model="gpt-test", profile="work")

    assert result == "## 配料信息\n\n- 鸡蛋"
    cmd, kwargs = calls[0]
    assert cmd[0].lower().endswith(("codex.cmd", "codex.exe", "codex"))
    assert cmd[1] == "exec"
    assert "service_tier" not in " ".join(cmd)
    assert "--skip-git-repo-check" in cmd
    assert "--ephemeral" in cmd
    assert "--ignore-rules" in cmd
    assert "--cd" in cmd
    assert ["--sandbox", "read-only"] == cmd[cmd.index("--sandbox") : cmd.index("--sandbox") + 2]
    assert ["--model", "gpt-test"] == cmd[cmd.index("--model") : cmd.index("--model") + 2]
    assert ["--profile", "work"] == cmd[cmd.index("--profile") : cmd.index("--profile") + 2]
    assert cmd[-1] == "-"
    assert "菜谱笔记" in kwargs["input"]
    assert kwargs["timeout"] == 300
    assert kwargs["cwd"]


def test_finalize_rewritten_note_normalizes_summary_and_preserves_source() -> None:
    source = "# 番茄炒蛋\n\n原视频：https://www.bilibili.com/video/BV1TEST\n视频标题：家常番茄炒蛋\nUP主：测试UP\n\n![](images/step_01.jpg)\n\n## 菜谱总结\n\n- 原总结\n"
    rewritten = "# 番茄炒蛋\n\n## 菜谱总结（注意事项）\n\n- 新总结\n"

    result = finalize_rewritten_note(rewritten, source)

    assert result.count("## 关键点速查") == 1
    assert "https://www.bilibili.com/video/BV1TEST" in result
    assert "## 来源" in result
    assert "![](images/step_01.jpg)" in result


def test_ensure_source_attribution_does_not_duplicate_existing_url() -> None:
    source = "原视频：https://example.com/video\n视频标题：Demo\nUP主：UP\n"
    markdown = "# Demo\n\n来源：https://example.com/video\n"

    assert ensure_source_attribution(markdown, source).count("https://example.com/video") == 1


def test_unknown_provider_is_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        summarize_note("# note", provider="typo")


def test_cli_advanced_instructions_are_delimited_and_keep_contract() -> None:
    prompt = apply_cli_extra_instructions("BASE CONTRACT", "重点提取温度和成熟判断。")

    assert prompt.startswith("BASE CONTRACT")
    assert "<user_advanced_instructions>" in prompt
    assert "重点提取温度和成熟判断" in prompt
    assert "不得覆盖既定 JSON/Markdown 输出格式" in prompt


def test_complete_prompt_applies_advanced_instructions_only_to_cli(monkeypatch) -> None:
    captured = {}

    def _opencode(prompt: str):
        captured["cli"] = prompt
        return "ok"

    def _openai(prompt: str, model: str):
        captured["api"] = prompt
        return "ok"

    monkeypatch.setattr("bili_recipe_notes.llm._complete_prompt_with_opencode", _opencode)
    monkeypatch.setattr("bili_recipe_notes.llm._complete_prompt_with_openai", _openai)

    from bili_recipe_notes.llm import complete_markdown_prompt

    complete_markdown_prompt("base", provider="opencode", cli_extra_instructions="自定义")
    complete_markdown_prompt("base", provider="openai", cli_extra_instructions="自定义")

    assert "自定义" in captured["cli"]
    assert captured["api"] == "base"


def test_summarize_note_with_codex_cli_failure(monkeypatch) -> None:
    def _run(*args, **kwargs):
        raise FileNotFoundError("codex not found")

    monkeypatch.setattr(subprocess, "run", _run)

    assert summarize_note_with_codex_cli("# note") is None
    assert "codex not found" in (get_last_llm_error() or "")


def test_summarize_note_with_openai_records_exception(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def _urlopen(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr("bili_recipe_notes.llm.urlopen", _urlopen)

    assert summarize_note_with_openai("# note") is None
    assert "api down" in (get_last_llm_error() or "")


def test_summarize_note_routes_to_codex(monkeypatch) -> None:
    captured = {}

    def _codex(markdown_note, model=None, profile=None):
        captured.update({"markdown": markdown_note, "model": model, "profile": profile})
        return "codex result"

    monkeypatch.setattr("bili_recipe_notes.llm.summarize_note_with_codex_cli", _codex)

    assert summarize_note("# note", provider="codex", codex_model="m", codex_profile="p") == "codex result"
    assert captured == {"markdown": "# note", "model": "m", "profile": "p"}


def test_codex_command_prefers_windows_launcher(monkeypatch) -> None:
    from bili_recipe_notes import llm

    monkeypatch.setattr(llm.os, "name", "nt")
    monkeypatch.setattr(llm.shutil, "which", lambda name: f"C:/bin/{name}" if name == "codex.cmd" else None)

    assert llm._codex_command() == "C:/bin/codex.cmd"


def test_opencode_command_prefers_windows_launcher(monkeypatch) -> None:
    from bili_recipe_notes import llm

    monkeypatch.setattr(llm.os, "name", "nt")
    monkeypatch.setattr(llm.shutil, "which", lambda name: f"C:/bin/{name}" if name == "opencode.cmd" else None)

    assert llm._opencode_command() == "C:/bin/opencode.cmd"
