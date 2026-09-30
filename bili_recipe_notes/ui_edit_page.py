"""编辑修复 page; shared UI callbacks are supplied by the application shell."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import json

from .config import UIConfig
from . import history, optimizer, pipeline, recipe_extractor, storage

INGREDIENT_COLUMNS = ("name", "amount", "note")

STEP_COLUMNS = (
    "title",
    "start_time",
    "end_time",
    "action",
    "heat",
    "duration",
    "tips",
    "screenshot_path",
)


@dataclass(frozen=True)
class EditPageHelpers:
    """Shared rendering and navigation callbacks, without importing the main UI."""

    archive_output: Callable[..., Any]
    backup_files: Callable[..., Any]
    backup_summary: Callable[..., Any]
    clean_error: Callable[..., Any]
    display_quality: Callable[..., Any]
    dump_model: Callable[..., Any]
    editor_table: Callable[..., Any]
    history_options: Callable[..., Any]
    local_markdown_image: Callable[..., Any]
    merge_editor_rows: Callable[..., Any]
    nonnegative_float: Callable[..., Any]
    optimize_options: Callable[..., Any]
    optional_text: Callable[..., Any]
    rating_option_label: Callable[..., Any]
    read_text: Callable[..., Any]
    record_key: Callable[..., Any]
    regenerate_note_kwargs: Callable[..., Any]
    rerun_with_notice: Callable[..., Any]
    safe_recipe_to_data: Callable[..., Any]
    select_history_item: Callable[..., Any]
    transcript_duration: Callable[..., Any]
    validate_recipe: Callable[..., Any]


def render_edit_page(st, config: UIConfig, helpers: EditPageHelpers) -> None:
    st.subheader("编辑与修复")
    items = history.scan_history(config.out_dir)
    editable = [item for item in items if item.recipe_path]
    if not editable:
        st.info("没有可编辑的菜谱。")
    else:
        edit_options = helpers.history_options(editable)
        selected = helpers.select_history_item(st, "选择菜谱", edit_options, "edit_select")
        record_key = helpers.record_key(selected.output_folder)
        state_prefix = f"edit_{record_key}_"
        st.markdown("#### 质量报告")
        helpers.display_quality(st, selected.output_folder)
        confirm_overwrite = st.checkbox(
            "确认覆盖当前记录的已有文件",
            key=f"{state_prefix}confirm_overwrite",
        )
        st.caption("优化、重写、保存、重新抽取和截图都会覆盖已有文件；每次操作前会创建带时间戳的备份。")
        if st.button(
            "一键优化当前笔记",
            disabled=not selected.note_path or not confirm_overwrite,
            key=f"{state_prefix}optimize",
        ):
            try:
                backups = helpers.backup_files([selected.note_path], "optimize")
                optimized = optimizer.optimize_existing_note(selected.output_folder, helpers.optimize_options(config))
            except Exception as exc:  # noqa: BLE001
                st.error(f"优化失败：{helpers.clean_error(exc)}")
            else:
                helpers.rerun_with_notice(
                    st,
                    f"优化完成：{optimized.quality_before.score} -> {optimized.quality_after.score}"
                    f"{helpers.backup_summary(backups)}",
                    clear_prefix=f"{state_prefix}confirm_",
                )
        if st.button(
            "仅重写当前 note.md（不下载视频）",
            disabled=not selected.recipe_path or not confirm_overwrite,
            key=f"{state_prefix}rewrite_note",
        ):
            try:
                backups = helpers.backup_files([selected.note_path], "rewrite")
                result = pipeline.regenerate_note_from_recipe(selected.output_folder, **helpers.regenerate_note_kwargs(config))
            except Exception as exc:  # noqa: BLE001
                st.error(f"重写失败：{helpers.clean_error(exc)}")
            else:
                message = f"已重写：{result.note_path}{helpers.backup_summary(backups)}"
                if result.stage_errors:
                    message += f"；提示：{'；'.join(result.stage_errors)}"
                helpers.rerun_with_notice(st, message, clear_prefix=f"{state_prefix}confirm_")
        recipe_data, recipe_error = helpers.safe_recipe_to_data(selected.recipe_path)  # type: ignore[arg-type]
        if recipe_error or recipe_data is None:
            st.error(f"当前 recipe.json 已损坏，已跳过结构编辑：{recipe_error}")
            st.caption(f"文件：{selected.recipe_path}。可先从备份恢复，其他功能页仍可继续使用。")
            with st.expander("查看损坏的 recipe.json"):
                st.code(helpers.read_text(selected.recipe_path), language="json")
        else:
            recipe_data = helpers.dump_model(recipe_extractor.normalize_recipe_taxonomy(helpers.validate_recipe(recipe_data)))
            st.markdown("#### 菜谱结构")
            recipe_data["title"] = st.text_input(
                "标题",
                value=recipe_data.get("title", ""),
                key=f"{state_prefix}title",
            )
            recipe_data["servings"] = st.text_input(
                "份量",
                value=recipe_data.get("servings") or "",
                key=f"{state_prefix}servings",
            )
            recipe_data["total_time"] = st.text_input(
                "总耗时",
                value=recipe_data.get("total_time") or "",
                key=f"{state_prefix}total_time",
            )
            recipe_data["difficulty"] = st.text_input(
                "难度",
                value=recipe_data.get("difficulty") or "",
                key=f"{state_prefix}difficulty",
            )
            rating_col_taste, rating_col_difficulty, rating_col_time = st.columns(3)
            with rating_col_taste:
                recipe_data["taste_rating"] = st.selectbox(
                    "个人喜爱度",
                    [None, 1, 2, 3, 4, 5],
                    index=int(recipe_data.get("taste_rating") or 0),
                    format_func=helpers.rating_option_label,
                    key=f"{state_prefix}taste_rating",
                )
            with rating_col_difficulty:
                recipe_data["difficulty_rating"] = st.selectbox(
                    "烹饪难度评级",
                    [1, 2, 3, 4, 5],
                    index=int(recipe_data.get("difficulty_rating") or 1) - 1,
                    format_func=helpers.rating_option_label,
                    key=f"{state_prefix}difficulty_rating",
                    help="系统根据步骤和技法自动给出初值，可手动修改。",
                )
            with rating_col_time:
                recipe_data["time_rating"] = st.selectbox(
                    "时间投入评级",
                    [1, 2, 3, 4, 5],
                    index=int(recipe_data.get("time_rating") or 1) - 1,
                    format_func=helpers.rating_option_label,
                    key=f"{state_prefix}time_rating",
                    help="系统根据总耗时自动给出初值，可手动修改。",
                )
            category_options = list(dict.fromkeys([recipe_data.get("category") or "未分类", *recipe_extractor.RECIPE_CATEGORIES]))
            recipe_data["category"] = st.selectbox(
                "归档分类",
                category_options,
                key=f"{state_prefix}category",
                help="用于 Obsidian 菜谱目录，例如中餐、汤羹、西餐、糕点。",
            )
            cuisine_options = list(dict.fromkeys([recipe_data.get("cuisine") or "未分类", *recipe_extractor.RECIPE_CUISINES]))
            recipe_data["cuisine"] = st.selectbox(
                "菜系",
                cuisine_options,
                key=f"{state_prefix}cuisine",
            )
            recipe_data["tags"] = [
                tag.strip().lstrip("#")
                for tag in st.text_input(
                    "检索标签，用逗号分隔",
                    value=", ".join(recipe_data.get("tags") or []),
                    key=f"{state_prefix}tags",
                ).split(",")
                if tag.strip().lstrip("#")
            ]
            original_ingredients = recipe_data.get("ingredients")
            ingredient_editor = st.data_editor(
                helpers.editor_table(original_ingredients, INGREDIENT_COLUMNS),
                num_rows="dynamic",
                column_order=list(INGREDIENT_COLUMNS),
                column_config={"name": "名称", "amount": "用量", "note": "备注"},
                key=f"{state_prefix}ingredients",
            )
            recipe_data["ingredients"] = helpers.merge_editor_rows(
                ingredient_editor, INGREDIENT_COLUMNS, original_ingredients
            )
            original_seasonings = recipe_data.get("seasonings")
            seasoning_editor = st.data_editor(
                helpers.editor_table(original_seasonings, INGREDIENT_COLUMNS),
                num_rows="dynamic",
                column_order=list(INGREDIENT_COLUMNS),
                column_config={"name": "名称", "amount": "用量", "note": "备注"},
                key=f"{state_prefix}seasonings",
            )
            recipe_data["seasonings"] = helpers.merge_editor_rows(
                seasoning_editor, INGREDIENT_COLUMNS, original_seasonings
            )
            recipe_data["tools"] = [
                item.strip()
                for item in st.text_area(
                    "工具，每行一个",
                    value="\n".join(recipe_data.get("tools") or []),
                    key=f"{state_prefix}tools",
                ).splitlines()
                if item.strip()
            ]
            recipe_data["shopping_list"] = [
                item.strip()
                for item in st.text_area(
                    "购物清单，每行一个",
                    value="\n".join(recipe_data.get("shopping_list") or []),
                    key=f"{state_prefix}shopping_list",
                ).splitlines()
                if item.strip()
            ]
            recipe_data["prep_items"] = [
                item.strip()
                for item in st.text_area(
                    "备菜清单，每行一个",
                    value="\n".join(recipe_data.get("prep_items") or []),
                    key=f"{state_prefix}prep_items",
                ).splitlines()
                if item.strip()
            ]
            recipe_data["summary_tips"] = [
                item.strip()
                for item in st.text_area(
                    "关键点速查，每行一个",
                    value="\n".join(recipe_data.get("summary_tips") or []),
                    key=f"{state_prefix}summary_tips",
                ).splitlines()
                if item.strip()
            ]
            original_steps = recipe_data.get("steps")
            steps_editor = st.data_editor(
                helpers.editor_table(original_steps, STEP_COLUMNS),
                num_rows="dynamic",
                column_order=list(STEP_COLUMNS),
                column_config={
                    "title": "步骤标题",
                    "start_time": st.column_config.NumberColumn("开始（秒）", min_value=0.0),
                    "end_time": st.column_config.NumberColumn("结束（秒）", min_value=0.0),
                    "action": "操作",
                    "heat": "火候",
                    "duration": "时长",
                    "tips": "提示",
                    "screenshot_path": "截图路径",
                },
                key=f"{state_prefix}steps",
            )
            recipe_data["steps"] = helpers.merge_editor_rows(steps_editor, STEP_COLUMNS, original_steps)
            if st.button(
                "保存菜谱并重新生成 note.md",
                disabled=not confirm_overwrite,
                key=f"{state_prefix}save_recipe",
            ):
                try:
                    recipe = helpers.validate_recipe(recipe_data)
                    backups = helpers.backup_files([selected.recipe_path, selected.note_path], "recipe-edit")
                    storage.atomic_write_json(selected.recipe_path, helpers.dump_model(recipe))  # type: ignore[arg-type]
                    result = pipeline.regenerate_note_from_recipe(
                        selected.output_folder,
                        **helpers.regenerate_note_kwargs(config),
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"保存失败：{helpers.clean_error(exc)}")
                else:
                    helpers.rerun_with_notice(
                        st,
                        f"已保存：{result.note_path}{helpers.backup_summary(backups)}",
                        clear_prefix=state_prefix,
                    )
        st.markdown("#### 最终 Markdown 手动编辑")
        st.caption("这里保存的是归档时使用的最终正文；以后点击“从结构重新生成”会覆盖这些手写修改。")
        final_markdown = st.text_area(
            "note.md",
            value=helpers.read_text(selected.note_path),
            height=420,
            key=f"{state_prefix}final_markdown",
        )
        force_edit_archive = st.checkbox(
            "归档时覆盖 Vault 中这篇笔记的手写修改",
            key=f"{state_prefix}force_archive",
        )
        col_save_markdown, col_save_archive = st.columns(2)
        with col_save_markdown:
            save_final_markdown = st.button(
                "保存最终 Markdown",
                disabled=not selected.note_path or not confirm_overwrite or not final_markdown.strip(),
                key=f"{state_prefix}save_final_markdown",
            )
        with col_save_archive:
            save_and_archive = st.button(
                "保存并归档到 Obsidian",
                type="primary",
                disabled=not selected.note_path or not confirm_overwrite or not final_markdown.strip(),
                key=f"{state_prefix}save_and_archive",
            )
        if save_final_markdown or save_and_archive:
            try:
                backups = helpers.backup_files([selected.note_path], "markdown-edit")
                storage.atomic_write_text(selected.note_path, final_markdown.strip() + "\n")  # type: ignore[arg-type]
                archived = None
                knowledge_results = ()
                knowledge_error = None
                if save_and_archive:
                    edited_ratings = None
                    if recipe_data is not None:
                        edited_ratings = {
                            "taste_rating": recipe_data.get("taste_rating"),
                            "difficulty_rating": recipe_data.get("difficulty_rating"),
                            "time_rating": recipe_data.get("time_rating"),
                        }
                    archived, knowledge_results, knowledge_error = helpers.archive_output(
                        selected.output_folder,
                        config,
                        overwrite=force_edit_archive,
                        ratings=edited_ratings,
                    )
            except Exception as exc:  # noqa: BLE001
                st.error(f"保存或归档失败：{helpers.clean_error(exc)}")
            else:
                message = f"最终 Markdown 已保存{helpers.backup_summary(backups)}"
                if archived:
                    message += f"；已归档：{archived.note_path}"
                if knowledge_results:
                    message += f"；同步 {len(knowledge_results)} 条技巧"
                if knowledge_error:
                    message += f"；技巧同步失败：{helpers.clean_error(knowledge_error)}"
                helpers.rerun_with_notice(st, message)
        st.markdown("#### transcript 修正")
        transcript_text = helpers.read_text(selected.transcript_path)
        edited_transcript = st.text_area(
            "transcript.json",
            value=transcript_text,
            height=240,
            key=f"{state_prefix}transcript",
        )
        if st.button(
            "保存 transcript 并重新抽取菜谱",
            disabled=not selected.transcript_path or not confirm_overwrite,
            key=f"{state_prefix}save_transcript",
        ):
            try:
                json.loads(edited_transcript)
                backups = helpers.backup_files(
                    [selected.transcript_path, selected.recipe_path, selected.note_path],
                    "transcript-edit",
                )
                storage.atomic_write_text(selected.transcript_path, edited_transcript)  # type: ignore[arg-type]
                result = pipeline.regenerate_recipe_from_transcript(selected.output_folder)
            except Exception as exc:  # noqa: BLE001
                st.error(f"重新抽取失败：{helpers.clean_error(exc)}")
            else:
                helpers.rerun_with_notice(
                    st,
                    f"已重新生成：{result.note_path}{helpers.backup_summary(backups)}",
                    clear_prefix=state_prefix,
                )
        st.markdown("#### 步骤图片")
        st.caption("自动截图会比较多个时间点；候选图只临时保存在当前会话，最终每道菜最多保留 4 张压缩图片。")
        steps = recipe_data.get("steps", []) if recipe_data else []
        if not isinstance(steps, list) or not steps:
            st.info("当前菜谱没有可重截的步骤。")
        else:
            pending_image_count = sum(
                isinstance(step, dict) and step.get("screenshot_status") == "needs_review"
                for step in steps
            )
            if pending_image_count:
                st.warning(f"有 {pending_image_count} 个步骤的自动候选质量不足，建议人工确认；也可以明确选择不配图。")
            step_indexes = list(range(1, len(steps) + 1))
            def screenshot_step_label(index: int) -> str:
                step = steps[index - 1]
                status = str(step.get("screenshot_status") or "")
                marker = "待选图" if status == "needs_review" else "已有图" if step.get("screenshot_path") else "无图"
                return f"{index}. {step.get('title') or '未命名步骤'} · {marker}"
            step_index = st.selectbox(
                "选择步骤",
                step_indexes,
                format_func=screenshot_step_label,
                key=f"{state_prefix}screenshot_step",
            )
            current_step = steps[int(step_index) - 1]
            initial_timestamp = helpers.nonnegative_float(current_step.get("start_time"))
            current_image = helpers.local_markdown_image(
                selected.output_folder,
                str(current_step.get("screenshot_path") or ""),
            )
            screenshot_status = str(current_step.get("screenshot_status") or "")
            screenshot_score = current_step.get("screenshot_score")
            if current_image and current_image.is_file():
                st.image(str(current_image), caption="当前保存图片", width=420)
            if screenshot_status == "needs_review":
                st.warning("自动候选质量不足，因此没有强行配图。可以查看候选、精确重截、上传图片或选择不配图。")
            elif screenshot_status == "none":
                st.info("此步骤已明确设置为不配图。")
            elif screenshot_score is not None:
                st.caption(f"当前图片质量评分：{float(screenshot_score) * 100:.0f}%")
            duration = helpers.transcript_duration(selected.transcript_path)
            known_times = [
                helpers.nonnegative_float(value)
                for step in steps
                if isinstance(step, dict)
                for value in (step.get("start_time"), step.get("end_time"))
                if isinstance(value, (int, float))
            ]
            max_timestamp = max([initial_timestamp, duration or 0.0, *known_times], default=initial_timestamp)
            col_time, col_video = st.columns([1, 3])
            with col_time:
                timestamp = st.number_input(
                    "时间点（秒）",
                    min_value=0.0,
                    max_value=max_timestamp if max_timestamp > 0 else None,
                    value=initial_timestamp,
                    step=0.5,
                    key=f"{state_prefix}screenshot_time_{step_index}",
                )
            with col_video:
                video_path = st.text_input(
                    "视频文件路径（可留空自动下载/复用 media）",
                    key=f"{state_prefix}screenshot_video",
                )
            end_time = current_step.get("end_time")
            available_range = (
                f"视频可用范围 0–{max_timestamp:.1f} 秒"
                if max_timestamp > 0
                else "未能读取视频总时长，请输入非负时间点"
            )
            if isinstance(end_time, (int, float)):
                st.caption(
                    f"当前步骤约为 {initial_timestamp:.1f}–{helpers.nonnegative_float(end_time):.1f} 秒；"
                    f"{available_range}。"
                )
            else:
                st.caption(f"当前步骤从 {initial_timestamp:.1f} 秒开始；{available_range}。")
            candidate_key = f"{state_prefix}screenshot_candidates_{step_index}"
            candidate_action, clear_action = st.columns(2)
            with candidate_action:
                if st.button(
                    "自动查找候选图",
                    key=f"{state_prefix}suggest_screenshots_{step_index}",
                    use_container_width=True,
                ):
                    try:
                        with st.spinner("正在截取并比较多个候选画面..."):
                            for session_key in list(st.session_state):
                                if str(session_key).startswith(f"{state_prefix}screenshot_candidates_"):
                                    st.session_state.pop(session_key, None)
                            st.session_state[candidate_key] = pipeline.suggest_step_screenshots(
                                selected.output_folder,
                                int(step_index),
                                cookies=helpers.optional_text(config.cookies),
                                video_path=helpers.optional_text(video_path),
                                keep_video=config.keep_media,
                            )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"候选图生成失败：{helpers.clean_error(exc)}")
            with clear_action:
                if st.button(
                    "此步骤不配图",
                    disabled=not confirm_overwrite,
                    key=f"{state_prefix}clear_screenshot_{step_index}",
                    use_container_width=True,
                ):
                    try:
                        backups = helpers.backup_files(
                            [current_image, selected.recipe_path, selected.note_path],
                            "remove-screenshot",
                        )
                        pipeline.clear_step_screenshot(selected.output_folder, int(step_index))
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"移除图片失败：{helpers.clean_error(exc)}")
                    else:
                        helpers.rerun_with_notice(
                            st,
                            f"此步骤已设为不配图{helpers.backup_summary(backups)}",
                            clear_prefix=state_prefix,
                        )
            candidates = st.session_state.get(candidate_key, [])
            if candidates:
                st.markdown("##### 候选图片（质量较高者优先）")
                candidate_columns = st.columns(3)
                for candidate_index, candidate in enumerate(candidates):
                    with candidate_columns[candidate_index % len(candidate_columns)]:
                        st.image(candidate.content, width="stretch")
                        timestamp_label = (
                            f"{candidate.timestamp:.1f} 秒" if candidate.timestamp is not None else "本地图片"
                        )
                        st.caption(f"{timestamp_label} · 质量 {candidate.score * 100:.0f}%")
                        if st.button(
                            "采用这张",
                            disabled=not confirm_overwrite,
                            key=f"{state_prefix}use_candidate_{step_index}_{candidate_index}",
                            use_container_width=True,
                        ):
                            try:
                                target_image = selected.output_folder / "images" / f"step_{int(step_index):02d}.jpg"
                                backups = helpers.backup_files(
                                    [target_image, selected.recipe_path, selected.note_path],
                                    "choose-screenshot",
                                )
                                image_path = pipeline.save_step_screenshot_candidate(
                                    selected.output_folder,
                                    int(step_index),
                                    candidate,
                                )
                            except Exception as exc:  # noqa: BLE001
                                st.error(f"保存候选图失败：{helpers.clean_error(exc)}")
                            else:
                                helpers.rerun_with_notice(
                                    st,
                                    f"已采用：{image_path}{helpers.backup_summary(backups)}",
                                    clear_prefix=state_prefix,
                                )
            elif candidate_key in st.session_state:
                st.warning("这个步骤范围内没有截到可读取的候选画面，可以扩大时间点精确重截、上传图片或不配图。")
            uploaded_image = st.file_uploader(
                "上传替代图片",
                type=["jpg", "jpeg", "png", "webp"],
                key=f"{state_prefix}uploaded_screenshot_{step_index}",
                help="图片会转成压缩 JPEG 后保存，原文件不会复制进菜谱目录。",
            )
            if st.button(
                "采用上传图片",
                disabled=uploaded_image is None or not confirm_overwrite,
                key=f"{state_prefix}use_uploaded_screenshot_{step_index}",
            ):
                try:
                    uploaded_content = uploaded_image.getvalue()
                    if len(uploaded_content) > 10 * 1024 * 1024:
                        raise ValueError("上传图片不能超过 10 MB")
                    target_image = selected.output_folder / "images" / f"step_{int(step_index):02d}.jpg"
                    backups = helpers.backup_files(
                        [target_image, selected.recipe_path, selected.note_path],
                        "upload-screenshot",
                    )
                    image_path = pipeline.save_uploaded_step_screenshot(
                        selected.output_folder,
                        int(step_index),
                        uploaded_content,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"上传图片保存失败：{helpers.clean_error(exc)}")
                else:
                    helpers.rerun_with_notice(
                        st,
                        f"已采用上传图片：{image_path}{helpers.backup_summary(backups)}",
                        clear_prefix=state_prefix,
                    )
            st.markdown("##### 按时间点精确重截")
            if st.button(
                "重新截图",
                disabled=not confirm_overwrite,
                key=f"{state_prefix}recapture",
            ):
                try:
                    target_image = selected.output_folder / "images" / f"step_{int(step_index):02d}.jpg"
                    backups = helpers.backup_files(
                        [target_image, selected.recipe_path, selected.note_path],
                        "recapture",
                    )
                    image_path = pipeline.recapture_step_screenshot(
                        selected.output_folder,
                        int(step_index),
                        float(timestamp),
                        cookies=helpers.optional_text(config.cookies),
                        video_path=helpers.optional_text(video_path),
                        keep_video=config.keep_media,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"截图失败：{helpers.clean_error(exc)}")
                else:
                    helpers.rerun_with_notice(
                        st,
                        f"已更新：{image_path}{helpers.backup_summary(backups)}",
                        clear_prefix=state_prefix,
                    )

