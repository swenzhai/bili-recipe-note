from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from .batch_queue import create_batch_id, list_batch_states
    from .config import UIConfig, load_config, save_config
    from .content_analysis import ContentAnalysisOptions, analyze_video_content
    from .environment import run_environment_checks
    from .exports import export_note
    from .history import HistoryItem, scan_history
    from .optimizer import OptimizeOptions, optimize_existing_note
    from .pipeline import (
        BatchJobOptions,
        RecipeJobOptions,
        extract_creator_links,
        generate_recipe_note,
        recapture_step_screenshot,
        regenerate_note_from_recipe,
        regenerate_recipe_from_transcript,
        run_batch,
    )
    from .quality import analyze_recipe_quality
    from .recipe_extractor import Recipe
except ImportError:  # pragma: no cover - supports direct streamlit script execution
    from bili_recipe_notes.batch_queue import create_batch_id, list_batch_states
    from bili_recipe_notes.config import UIConfig, load_config, save_config
    from bili_recipe_notes.content_analysis import ContentAnalysisOptions, analyze_video_content
    from bili_recipe_notes.environment import run_environment_checks
    from bili_recipe_notes.exports import export_note
    from bili_recipe_notes.history import HistoryItem, scan_history
    from bili_recipe_notes.optimizer import OptimizeOptions, optimize_existing_note
    from bili_recipe_notes.pipeline import (
        BatchJobOptions,
        RecipeJobOptions,
        extract_creator_links,
        generate_recipe_note,
        recapture_step_screenshot,
        regenerate_note_from_recipe,
        regenerate_recipe_from_transcript,
        run_batch,
    )
    from bili_recipe_notes.quality import analyze_recipe_quality
    from bili_recipe_notes.recipe_extractor import Recipe


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
LLM_PROVIDERS = ["opencode", "codex", "openai", "local", "none"]
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]


def _optional_text(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _clean_error(exc: Exception) -> str:
    return ANSI_RE.sub("", str(exc)).strip()


def _render_paths(paths: list[Path]) -> str:
    return "\n".join(str(path) for path in paths)


def _read_text(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _recipe_to_data(recipe_path: Path) -> dict:
    return json.loads(recipe_path.read_text(encoding="utf-8"))


def _validate_recipe(data: dict) -> Recipe:
    if hasattr(Recipe, "model_validate"):
        return Recipe.model_validate(data)
    return Recipe(**data)


def _dump_model(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.__dict__


def _open_folder(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _history_options(items: list[HistoryItem]) -> dict[str, HistoryItem]:
    return {f"{item.title} | {item.output_folder.name}": item for item in items}


def _job_options(url: str, config: UIConfig) -> RecipeJobOptions:
    return RecipeJobOptions(
        url=url.strip(),
        cookies=_optional_text(config.cookies),
        out=config.out_dir,
        no_screenshot=not config.enable_screenshot,
        whisper_model=config.whisper_model,
        language=config.language,
        keep_media=config.keep_media,
        no_llm_summary=not config.enable_llm_summary or config.llm_provider == "none",
        llm_provider=config.llm_provider,
        openai_model=config.openai_model,
        local_llm_command=config.local_llm_command,
        codex_model=config.codex_model,
        codex_profile=config.codex_profile,
    )


def _optimize_options(config: UIConfig) -> OptimizeOptions:
    return OptimizeOptions(
        llm_provider=config.llm_provider,
        openai_model=config.openai_model,
        local_llm_command=config.local_llm_command,
        codex_model=config.codex_model,
        codex_profile=config.codex_profile,
        no_llm_summary=not config.enable_llm_summary or config.llm_provider == "none",
    )


def _content_analysis_options(config: UIConfig, output_filename: str) -> ContentAnalysisOptions:
    return ContentAnalysisOptions(
        llm_provider=config.llm_provider,
        openai_model=config.openai_model,
        local_llm_command=config.local_llm_command,
        codex_model=config.codex_model,
        codex_profile=config.codex_profile,
        output_filename=_markdown_filename(output_filename),
    )


def _markdown_filename(filename: str) -> str:
    name = Path(filename.strip() or "extra_analysis.md").name
    if not name.lower().endswith(".md"):
        name += ".md"
    return name


def _display_quality(st, output_folder: Path) -> None:
    report = analyze_recipe_quality(output_folder)
    st.metric("质量评分", report.score)
    st.caption(report.summary)
    if report.issues:
        st.dataframe(
            [
                {
                    "级别": issue.severity,
                    "问题": issue.message,
                    "建议": issue.suggestion,
                }
                for issue in report.issues
            ],
            use_container_width=True,
        )


def _render_sidebar(st, config: UIConfig) -> UIConfig:
    st.sidebar.header("默认配置")
    config.out_dir = st.sidebar.text_input("输出目录", value=config.out_dir)
    config.cookies = _optional_text(st.sidebar.text_input("cookies 文件路径", value=config.cookies or ""))
    config.language = st.sidebar.text_input("字幕/转写语言", value=config.language)
    config.whisper_model = st.sidebar.selectbox(
        "Whisper 模型",
        WHISPER_MODELS,
        index=WHISPER_MODELS.index(config.whisper_model)
        if config.whisper_model in WHISPER_MODELS
        else 2,
    )
    config.enable_screenshot = st.sidebar.checkbox("生成步骤截图", value=config.enable_screenshot)
    config.enable_llm_summary = st.sidebar.checkbox("使用 LLM 重写", value=config.enable_llm_summary)
    config.keep_media = st.sidebar.checkbox("保留临时媒体文件", value=config.keep_media)
    config.llm_provider = st.sidebar.selectbox(
        "LLM provider",
        LLM_PROVIDERS,
        index=LLM_PROVIDERS.index(config.llm_provider)
        if config.llm_provider in LLM_PROVIDERS
        else 0,
    )
    if config.llm_provider == "codex":
        config.codex_model = _optional_text(st.sidebar.text_input("Codex 模型", value=config.codex_model or ""))
        config.codex_profile = _optional_text(st.sidebar.text_input("Codex profile", value=config.codex_profile or ""))
    if config.llm_provider == "openai":
        config.openai_model = st.sidebar.text_input("OpenAI 模型", value=config.openai_model)
    if config.llm_provider == "local":
        config.local_llm_command = _optional_text(
            st.sidebar.text_input("本地 LLM 命令", value=config.local_llm_command or "")
        )
    if st.sidebar.button("保存默认配置"):
        path = save_config(config)
        st.sidebar.success(f"已保存：{path}")
    return config


def _log_box(st, height: int = 220):
    log_lines: list[str] = []
    box = st.empty()

    def log(message: str) -> None:
        log_lines.append(message)
        box.text_area("运行日志", value="\n".join(log_lines), height=height)

    return log


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Bili Recipe Notes", layout="wide")
    st.title("Bili Recipe Notes")

    config = _render_sidebar(st, load_config())

    tabs = st.tabs(["单视频生成", "历史记录", "批量处理", "编辑修复", "二次分析", "环境检查", "UP 主链接"])

    with tabs[0]:
        st.subheader("生成菜谱笔记")
        url = st.text_input("视频 URL", placeholder="https://www.bilibili.com/video/BV...")
        if st.button("开始生成", type="primary", disabled=not url.strip()):
            save_config(config)
            log = _log_box(st)
            try:
                with st.spinner("正在生成菜谱笔记..."):
                    result = generate_recipe_note(_job_options(url, config), log=log)
            except Exception as exc:  # noqa: BLE001
                error_message = _clean_error(exc)
                log(error_message)
                st.error(f"生成失败：{error_message}")
            else:
                st.success(f"生成完成：{result.output_folder}")
                if result.stage_errors:
                    st.warning("\n".join(result.stage_errors))
                st.markdown("#### 笔记预览")
                st.markdown(result.final_note)
                st.markdown("#### 输出文件")
                st.code(
                    _render_paths([result.output_folder, result.note_path, result.recipe_path, result.transcript_path]),
                    language="text",
                )
                st.download_button("下载 note.md", data=result.final_note, file_name="note.md", mime="text/markdown")

    with tabs[1]:
        st.subheader("历史记录")
        items = scan_history(config.out_dir)
        query = st.text_input("搜索", placeholder="标题、UP 主、URL")
        filtered = [
            item
            for item in items
            if not query.strip()
            or query.lower() in item.title.lower()
            or query.lower() in (item.uploader or "").lower()
            or query.lower() in item.source_url.lower()
        ]
        st.caption(f"共 {len(filtered)} 条")
        if filtered:
            st.dataframe(
                [
                    {
                        "标题": item.title,
                        "UP主": item.uploader or "",
                        "状态": item.status,
                        "质量分": item.quality_score if item.quality_score is not None else "",
                        "完成时间": item.finished_at or "",
                        "目录": str(item.output_folder),
                    }
                    for item in filtered
                ],
                use_container_width=True,
            )
            options = _history_options(filtered)
            selected = options[st.selectbox("选择记录", list(options))]
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                if st.button("打开输出目录"):
                    _open_folder(selected.output_folder)
            with col_b:
                if st.button("重新生成", disabled=not selected.source_url):
                    log = _log_box(st)
                    try:
                        result = generate_recipe_note(_job_options(selected.source_url, config), log=log)
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"重新生成失败：{_clean_error(exc)}")
                    else:
                        st.success(f"重新生成完成：{result.output_folder}")
            with col_c:
                export_kind = st.selectbox("导出格式", ["obsidian", "pdf", "docx"])
                if st.button("导出", disabled=not selected.note_path):
                    try:
                        exported = export_note(selected.note_path, export_kind)  # type: ignore[arg-type]
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"导出失败：{_clean_error(exc)}")
                    else:
                        st.success(f"已导出：{exported}")
            st.markdown("#### 质量报告")
            _display_quality(st, selected.output_folder)
            if st.button("一键优化笔记", disabled=not selected.note_path or not selected.recipe_path):
                try:
                    optimized = optimize_existing_note(selected.output_folder, _optimize_options(config))
                except Exception as exc:  # noqa: BLE001
                    st.error(f"优化失败：{_clean_error(exc)}")
                else:
                    st.success(
                        f"优化完成：{optimized.quality_before.score} -> {optimized.quality_after.score}，备份：{optimized.backup_path}"
                    )
            note = _read_text(selected.note_path)
            if note:
                st.markdown("#### note.md 预览")
                st.markdown(note)
        else:
            st.info("还没有历史记录，或搜索没有匹配结果。")

    with tabs[2]:
        st.subheader("批量处理")
        links_text = st.text_area("视频 URL，每行一个", height=180)
        links_file = st.text_input("或读取链接文件路径", placeholder="outputs/creator_video_links.txt")
        skip_existing = st.checkbox("已生成则跳过", value=True)
        use_queue = st.checkbox("保存为可续跑批次", value=True)
        batch_states = list_batch_states()
        batch_labels = [f"{state.batch_id} | {state.updated_at} | {len(state.items)} 条" for state in batch_states]
        selected_batch_label = st.selectbox("已有批次", [""] + batch_labels)
        selected_batch_id = selected_batch_label.split(" | ", 1)[0] if selected_batch_label else None

        col_new, col_resume, col_retry = st.columns(3)
        run_mode = None
        with col_new:
            if st.button("创建新批次并运行", type="primary"):
                run_mode = "new-queue"
        with col_resume:
            if st.button("继续未完成", disabled=not selected_batch_id):
                run_mode = "resume-unfinished"
        with col_retry:
            if st.button("只重试失败", disabled=not selected_batch_id):
                run_mode = "retry-failed"

        if st.button("开始批量生成", type="primary"):
            run_mode = "new-direct"

        if run_mode:
            urls = [line.strip() for line in links_text.splitlines() if line.strip()]
            file_path = Path(links_file) if links_file.strip() else None
            if file_path and file_path.exists():
                urls.extend([line.strip() for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()])
            if run_mode in {"resume-unfinished", "retry-failed"} and selected_batch_id:
                urls = []
            if not urls and run_mode not in {"resume-unfinished", "retry-failed"}:
                st.warning("请先输入 URL 或提供链接文件。")
            else:
                save_config(config)
                log = _log_box(st, height=260)
                batch_id = None
                resume_mode = "new"
                if run_mode == "new-queue":
                    batch_id = create_batch_id()
                elif run_mode in {"resume-unfinished", "retry-failed"}:
                    batch_id = selected_batch_id
                    resume_mode = run_mode
                elif use_queue:
                    batch_id = create_batch_id()
                result = run_batch(
                    BatchJobOptions(
                        urls=urls,
                        cookies=_optional_text(config.cookies),
                        out=config.out_dir,
                        no_screenshot=not config.enable_screenshot,
                        whisper_model=config.whisper_model,
                        language=config.language,
                        keep_media=config.keep_media,
                        no_llm_summary=not config.enable_llm_summary or config.llm_provider == "none",
                        llm_provider=config.llm_provider,
                        openai_model=config.openai_model,
                        local_llm_command=config.local_llm_command,
                        codex_model=config.codex_model,
                        codex_profile=config.codex_profile,
                        skip_existing=skip_existing,
                        batch_id=batch_id,
                        resume_mode=resume_mode,
                    ),
                    log=log,
                )
                st.dataframe([item.__dict__ for item in result.items], use_container_width=True)
                if batch_id:
                    st.success(f"批次状态已保存：{batch_id}")

        if selected_batch_id:
            selected_state = next((state for state in batch_states if state.batch_id == selected_batch_id), None)
            if selected_state:
                st.markdown("#### 批次状态")
                st.dataframe([item.__dict__ for item in selected_state.items], use_container_width=True)

    with tabs[3]:
        st.subheader("编辑与修复")
        items = scan_history(config.out_dir)
        editable = [item for item in items if item.recipe_path]
        if not editable:
            st.info("没有可编辑的菜谱。")
        else:
            selected = _history_options(editable)[st.selectbox("选择菜谱", list(_history_options(editable)), key="edit_select")]
            st.markdown("#### 质量报告")
            _display_quality(st, selected.output_folder)
            if st.button("一键优化当前笔记", disabled=not selected.note_path):
                try:
                    optimized = optimize_existing_note(selected.output_folder, _optimize_options(config))
                except Exception as exc:  # noqa: BLE001
                    st.error(f"优化失败：{_clean_error(exc)}")
                else:
                    st.success(
                        f"优化完成：{optimized.quality_before.score} -> {optimized.quality_after.score}，备份：{optimized.backup_path}"
                    )
            recipe_data = _recipe_to_data(selected.recipe_path)  # type: ignore[arg-type]
            st.markdown("#### 菜谱结构")
            recipe_data["title"] = st.text_input("标题", value=recipe_data.get("title", ""))
            recipe_data["servings"] = st.text_input("份量", value=recipe_data.get("servings") or "")
            recipe_data["total_time"] = st.text_input("总耗时", value=recipe_data.get("total_time") or "")
            recipe_data["difficulty"] = st.text_input("难度", value=recipe_data.get("difficulty") or "")
            recipe_data["ingredients"] = st.data_editor(recipe_data.get("ingredients") or [], num_rows="dynamic")
            recipe_data["seasonings"] = st.data_editor(recipe_data.get("seasonings") or [], num_rows="dynamic")
            recipe_data["tools"] = [
                item.strip()
                for item in st.text_area("工具，每行一个", value="\n".join(recipe_data.get("tools") or [])).splitlines()
                if item.strip()
            ]
            recipe_data["shopping_list"] = [
                item.strip()
                for item in st.text_area("购物清单，每行一个", value="\n".join(recipe_data.get("shopping_list") or [])).splitlines()
                if item.strip()
            ]
            recipe_data["prep_items"] = [
                item.strip()
                for item in st.text_area("备菜清单，每行一个", value="\n".join(recipe_data.get("prep_items") or [])).splitlines()
                if item.strip()
            ]
            recipe_data["steps"] = st.data_editor(recipe_data.get("steps") or [], num_rows="dynamic")
            if st.button("保存菜谱并重新生成 note.md"):
                try:
                    recipe = _validate_recipe(recipe_data)
                    selected.recipe_path.write_text(json.dumps(_dump_model(recipe), ensure_ascii=False, indent=2), encoding="utf-8")  # type: ignore[union-attr]
                    result = regenerate_note_from_recipe(
                        selected.output_folder,
                        no_llm_summary=not config.enable_llm_summary or config.llm_provider == "none",
                        llm_provider=config.llm_provider,
                        openai_model=config.openai_model,
                        local_llm_command=config.local_llm_command,
                        codex_model=config.codex_model,
                        codex_profile=config.codex_profile,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"保存失败：{_clean_error(exc)}")
                else:
                    st.success(f"已保存：{result.note_path}")

            st.markdown("#### transcript 修正")
            transcript_text = _read_text(selected.transcript_path)
            edited_transcript = st.text_area("transcript.json", value=transcript_text, height=240)
            if st.button("保存 transcript 并重新抽取菜谱", disabled=not selected.transcript_path):
                try:
                    json.loads(edited_transcript)
                    selected.transcript_path.write_text(edited_transcript, encoding="utf-8")  # type: ignore[union-attr]
                    result = regenerate_recipe_from_transcript(selected.output_folder)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"重新抽取失败：{_clean_error(exc)}")
                else:
                    st.success(f"已重新生成：{result.note_path}")

            st.markdown("#### 截图重截")
            col_step, col_time, col_video = st.columns([1, 1, 3])
            with col_step:
                step_index = st.number_input("步骤序号", min_value=1, value=1, step=1)
            with col_time:
                timestamp = st.number_input("时间点（秒）", min_value=0.0, value=0.0, step=0.5)
            with col_video:
                video_path = st.text_input("视频文件路径（可留空自动下载/复用 media）")
            if st.button("重新截图"):
                try:
                    image_path = recapture_step_screenshot(
                        selected.output_folder,
                        int(step_index),
                        float(timestamp),
                        cookies=_optional_text(config.cookies),
                        video_path=_optional_text(video_path),
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"截图失败：{_clean_error(exc)}")
                else:
                    st.success(f"已更新：{image_path}")
                    st.image(str(image_path))

    with tabs[4]:
        st.subheader("LLM 二次分析")
        items = scan_history(config.out_dir)
        analyzable = [item for item in items if item.note_path or item.transcript_path or item.recipe_path]
        if not analyzable:
            st.info("没有可分析的视频记录。")
        else:
            options = _history_options(analyzable)
            selected = options[st.selectbox("选择视频", list(options), key="analysis_select")]
            request = st.text_area(
                "想额外分析的内容",
                value="提取视频中提到的通用烹饪技巧",
                height=100,
                key="analysis_request",
            )
            output_filename = st.text_input("输出文件名", value="extra_analysis.md")
            existing_analysis = selected.output_folder / _markdown_filename(output_filename)
            if existing_analysis.exists():
                st.markdown("#### 已有分析")
                st.markdown(existing_analysis.read_text(encoding="utf-8"))
            if st.button("开始二次分析", type="primary", disabled=config.llm_provider == "none"):
                try:
                    with st.spinner("正在分析视频内容..."):
                        result = analyze_video_content(
                            selected.output_folder,
                            request=request,
                            options=_content_analysis_options(config, output_filename),
                        )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"二次分析失败：{_clean_error(exc)}")
                else:
                    st.success(f"已生成：{result.analysis_path}")
                    st.markdown("#### 分析结果")
                    st.markdown(result.markdown)
                    st.download_button(
                        "下载分析 Markdown",
                        data=result.markdown,
                        file_name=result.analysis_path.name,
                        mime="text/markdown",
                    )

    with tabs[5]:
        st.subheader("环境检查")
        checks = run_environment_checks()
        st.dataframe(
            [
                {
                    "项目": check.name,
                    "状态": "OK" if check.ok else "需要处理",
                    "详情": check.detail,
                    "建议": check.suggestion or "",
                }
                for check in checks
            ],
            use_container_width=True,
        )

    with tabs[6]:
        st.subheader("提取 UP 主视频链接")
        home_url = st.text_input("UP 主主页 URL", placeholder="https://space.bilibili.com/123456/video")
        filename = st.text_input("链接文件名", value="creator_video_links.txt")
        if st.button("开始提取", type="primary", disabled=not home_url.strip()):
            log = _log_box(st, height=180)
            try:
                with st.spinner("正在提取视频链接..."):
                    links_path = extract_creator_links(
                        url=home_url.strip(),
                        cookies=_optional_text(config.cookies),
                        out=config.out_dir,
                        filename=filename.strip() or "creator_video_links.txt",
                        log=log,
                    )
            except Exception as exc:  # noqa: BLE001
                error_message = _clean_error(exc)
                log(error_message)
                st.error(f"提取失败：{error_message}")
            else:
                content = links_path.read_text(encoding="utf-8") if links_path.exists() else ""
                st.success(f"已写入：{links_path}")
                st.text_area("链接预览", value=content, height=240)
                st.download_button("下载链接文件", data=content, file_name=links_path.name, mime="text/plain")


if __name__ == "__main__":
    main()
