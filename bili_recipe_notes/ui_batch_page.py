"""批量处理 page; shared UI callbacks are supplied by the application shell."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from pathlib import Path
from datetime import datetime, timezone

from .config import UIConfig
from . import batch_queue, batch_runner, mobile_sync, obsidian_archive, pipeline
from . import config as app_config


@dataclass(frozen=True)
class BatchPageHelpers:
    """Shared rendering and navigation callbacks, without importing the main UI."""

    archive_approved_knowledge: Callable[..., Any]
    archive_output: Callable[..., Any]
    batch_item_row: Callable[..., Any]
    batch_progress_summary: Callable[..., Any]
    check_batch_overlaps: Callable[..., Any]
    clean_error: Callable[..., Any]
    creator_link_document_label: Callable[..., Any]
    creator_name_from_document: Callable[..., Any]
    load_batch_urls: Callable[..., Any]
    navigate_to_record: Callable[..., Any]
    optional_text: Callable[..., Any]
    paged_values: Callable[..., Any]
    preferred_batch_id: Callable[..., Any]
    record_key: Callable[..., Any]
    render_batch_pause_control: Callable[..., Any]
    render_rating_controls: Callable[..., Any]
    rerun_with_notice: Callable[..., Any]
    saved_creator_link_documents: Callable[..., Any]
    vault_path: Callable[..., Any]


def render_batch_page(st, config: UIConfig, helpers: BatchPageHelpers) -> None:
    st.subheader("批量处理")
    try:
        batch_source_store = mobile_sync.MobileSyncStore(Path.cwd(), out_dir=config.out_dir)
    except Exception as exc:  # noqa: BLE001
        batch_source_store = None
        st.warning(f"视频来源分类数据库暂不可用：{helpers.clean_error(exc)}")
    if st.button("打开后台任务仪表盘", type="primary", key="batch_open_dashboard"):
        st.session_state["_next_page"] = "任务仪表盘"
        st.rerun()
    saved_link_documents = helpers.saved_creator_link_documents(config.out_dir)
    saved_link_options = [""] + [str(path) for path in saved_link_documents]
    if st.session_state.get("batch_saved_creator_links") not in saved_link_options:
        st.session_state["batch_saved_creator_links"] = saved_link_options[1] if saved_link_documents else ""
    selected_saved_links = st.selectbox(
        "已保存的 UP 主链接清单",
        saved_link_options,
        format_func=helpers.creator_link_document_label,
        key="batch_saved_creator_links",
        help="默认选择最近一次抓取的 UP 主清单；创建新批次时会直接读取，不需要粘贴 URL。",
    )
    if selected_saved_links:
        selected_count = len(helpers.load_batch_urls("", "", selected_saved_links))
        st.success(f"已导入最近保存的链接清单，共 {selected_count} 条。")
    elif not saved_link_documents:
        st.info("还没有已保存的 UP 主链接清单；可先到“UP 主链接”页面抓取，或在下面手动输入。")
    with st.expander("手动添加 URL 或导入其他文件（可选）", expanded=not bool(selected_saved_links)):
        links_text = st.text_area("视频 URL，每行一个", height=180)
        links_file = st.text_input("其他链接文件路径", placeholder="outputs/creator_video_links.txt")
    suggested_creator_name = helpers.creator_name_from_document(selected_saved_links)
    creator_name_input = st.text_input(
        "本批次 UP 主名称（选填）",
        placeholder=f"留空则使用视频信息{f'，当前识别为：{suggested_creator_name}' if suggested_creator_name else ''}",
        help="用于统一同一位 UP 主的名称，并随菜谱写入数据库；不会覆盖视频原始作者字段。",
    )
    batch_creator_name = creator_name_input.strip() or suggested_creator_name
    target_label = st.radio(
        "运行到目标阶段",
        ["仅形成原始版", "生成完整菜谱版"],
        horizontal=True,
        help="完整菜谱版会自动补齐缺失的原始版；已有原始版不会重复抓字幕。",
    )
    target_stage = "raw" if target_label == "仅形成原始版" else "recipe"
    skip_existing = st.checkbox("已生成则跳过", value=True)
    st.caption("批量任务在独立后台进程中运行并持续保存进度；刷新页面、关闭浏览器或重启 UI 后仍可恢复查看。")
    try:
        batch_states = batch_queue.list_batch_states()
    except Exception as exc:  # noqa: BLE001
        cached_states = st.session_state.get("_batch_states_cache")
        batch_states = cached_states if isinstance(cached_states, list) else []
        if batch_states:
            st.warning(f"本次读取批次状态失败，暂时显示上次成功读取的数据：{helpers.clean_error(exc)}")
        else:
            st.error(f"读取批次状态失败：{helpers.clean_error(exc)}")
    else:
        st.session_state["_batch_states_cache"] = batch_states
    batch_by_id = {state.batch_id: state for state in batch_states}
    runtime_by_id = {
        state.batch_id: batch_runner.get_background_batch_status(state.batch_id)
        for state in batch_states
    }
    running_batch_ids = [
        state.batch_id
        for state in batch_states
        if runtime_by_id.get(state.batch_id) and runtime_by_id[state.batch_id].status == "running"
    ]
    active_batch_ids = [
        state.batch_id
        for state in batch_states
        if runtime_by_id.get(state.batch_id) and runtime_by_id[state.batch_id].status in {"running", "paused"}
    ]
    if running_batch_ids:
        st.success("正在运行的批次：" + "、".join(running_batch_ids))
    paused_batch_ids = [batch_id for batch_id in active_batch_ids if batch_id not in running_batch_ids]
    if paused_batch_ids:
        st.info("已暂停的批次：" + "、".join(paused_batch_ids))
    overlapping_running_pairs = []
    for index, batch_id in enumerate(running_batch_ids):
        left = batch_by_id[batch_id]
        left_urls = {item.url for item in left.items}
        for other_id in running_batch_ids[index + 1 :]:
            right = batch_by_id[other_id]
            overlap = len(left_urls.intersection(item.url for item in right.items))
            if overlap:
                overlapping_running_pairs.append(f"{batch_id} 与 {other_id} 重复 {overlap} 条")
    if overlapping_running_pairs:
        st.warning(
            "检测到同时运行的批次包含重复链接，可能争用同一输出目录："
            + "；".join(overlapping_running_pairs)
            + "。请先观察现有任务，不要再次启动相同批次。"
        )
    next_batch_select = st.session_state.pop("_next_batch_select", None)
    preferred_batch_id = helpers.preferred_batch_id(
        batch_states,
        runtime_by_id,
        str(next_batch_select) if next_batch_select else None,
        str(st.session_state.get("batch_select") or ""),
    )
    if preferred_batch_id:
        st.session_state["batch_select"] = preferred_batch_id
    else:
        st.session_state.pop("batch_select", None)
    def batch_label(value: str) -> str:
        state = batch_by_id.get(value)
        if state:
            summary = helpers.batch_progress_summary(state)
            runtime = runtime_by_id.get(value)
            runtime_label = {
                "running": "运行中",
                "paused": "已暂停",
                "done": "已完成",
                "done_with_errors": "完成但有失败",
                "failed": "后台失败",
                "stopped": "已停止（可继续）",
            }.get(runtime.status, runtime.status) if runtime else "历史批次"
            return (
                f"{value} | {runtime_label} | {summary['completed']}/{summary['total']} 已完成"
                f" | {summary['failed']} 失败"
            )
        return value
    if batch_by_id:
        selected_batch_value = st.selectbox(
            "已有批次",
            list(batch_by_id),
            format_func=batch_label,
            key="batch_select",
        )
        selected_batch_id = str(selected_batch_value).split(" | ", 1)[0]
    else:
        selected_batch_id = None
        st.info("还没有持久化批次。创建后即使刷新或重启网页，也会自动显示最新运行状态。")
    selected_runtime = runtime_by_id.get(selected_batch_id)
    selected_active = bool(selected_runtime and selected_runtime.status in {"running", "paused"})
    if selected_batch_id:
        helpers.render_batch_pause_control(st, selected_batch_id, selected_runtime, key_prefix="batch")
    col_new, col_resume, col_retry = st.columns(3)
    run_mode = None
    with col_new:
        if st.button("创建新批次并运行", type="primary"):
            run_mode = "new-queue"
    with col_resume:
        if st.button("继续未完成", disabled=not selected_batch_id or selected_active):
            run_mode = "resume-unfinished"
    with col_retry:
        if st.button("只重试失败", disabled=not selected_batch_id or selected_active):
            run_mode = "retry-failed"
    if st.button("开始批量生成", type="primary"):
        run_mode = "new-direct"
    if run_mode:
        try:
            if run_mode in {"resume-unfinished", "retry-failed"} and selected_batch_id:
                urls = []
            else:
                urls = helpers.load_batch_urls(links_text, links_file, selected_saved_links)
                if batch_source_store is not None:
                    known_non_recipes = batch_source_store.known_non_recipe_urls(urls)
                    if known_non_recipes:
                        urls = [url for url in urls if url not in known_non_recipes]
                        st.warning(
                            f"已自动排除 {len(known_non_recipes)} 条曾标记为非菜谱的视频，不会重复处理。"
                        )
            if not urls and run_mode not in {"resume-unfinished", "retry-failed"}:
                raise ValueError("没有可处理的视频；请添加链接，或检查它们是否已归类为非菜谱。")
            if run_mode in {"resume-unfinished", "retry-failed"} and selected_batch_id:
                selected_urls = [
                    item.url for item in batch_queue.selectable_items(batch_by_id[selected_batch_id], run_mode, target_stage)
                ]
                helpers.check_batch_overlaps(selected_urls, active_batch_ids, batch_by_id, exclude=selected_batch_id)
            else:
                helpers.check_batch_overlaps(urls, active_batch_ids, batch_by_id)
            app_config.save_config(config)
            batch_id: str | None = None
            resume_mode = "new"
            if run_mode in {"new-queue", "new-direct"}:
                batch_id = batch_queue.create_batch_id()
            elif run_mode in {"resume-unfinished", "retry-failed"}:
                batch_id = selected_batch_id
                resume_mode = run_mode
            options = pipeline.BatchJobOptions(
                urls=urls,
                cookies=helpers.optional_text(config.cookies),
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
                llm_cli_extra_instructions=config.llm_cli_extra_instructions,
                max_recipe_steps=config.max_recipe_steps,
                max_step_images=config.max_step_images,
                enable_recipe_review=config.enable_recipe_review,
                skip_existing=skip_existing,
                batch_id=batch_id,
                resume_mode=resume_mode,
                target_stage=target_stage,
                creator_name=batch_creator_name or None,
                source_database_path=str(batch_source_store.database_path) if batch_source_store else None,
            )
            if run_mode in {"new-queue", "new-direct"}:
                options_snapshot = {key: value for key, value in options.__dict__.items() if key != "urls"}
                batch_queue.create_batch_state(urls, options_snapshot, batch_id=batch_id)
                options.urls = []
                options.resume_mode = "resume-unfinished"
            def post_process(result) -> None:
                if not config.auto_archive_after_generation:
                    return
                completed_folders = [
                    item.output_folder for item in result.items if item.output_folder and item.status == "done"
                ]
                if completed_folders:
                    obsidian_archive.archive_recipe_batch(completed_folders, helpers.vault_path(config))
                    if config.archive_knowledge_with_recipe:
                        helpers.archive_approved_knowledge(config)
            background = batch_runner.start_background_batch(options, on_complete=post_process)
        except Exception as exc:  # noqa: BLE001 - batch failures must not take down the UI
            st.error(f"批量处理失败：{helpers.clean_error(exc)}")
        else:
            st.session_state["_next_batch_select"] = batch_id
            helpers.rerun_with_notice(st, f"批次已在后台启动：{background.batch_id}")
    if selected_batch_id:
        selected_state = batch_by_id.get(selected_batch_id)
        if selected_state:
            st.markdown("#### 批次状态")
            background = runtime_by_id.get(selected_batch_id)
            if background:
                status_text = {
                    "running": "后台运行中",
                    "paused": "后台已暂停（点击“继续任务”接着处理）",
                    "done": "后台运行完成",
                    "done_with_errors": "后台运行完成（有失败项）",
                    "failed": "后台运行失败",
                    "stopped": "后台已停止（可继续未完成项）",
                }.get(background.status, background.status)
                st.info(f"{status_text}；启动时间：{background.started_at}")
                if background.error:
                    st.error(f"后台状态：{background.error}")
            summary = helpers.batch_progress_summary(selected_state)
            progress_columns = st.columns(5)
            progress_columns[0].metric("总数", summary["total"])
            progress_columns[1].metric("已完成", summary["completed"])
            progress_columns[2].metric(
                "暂停中" if background and background.status == "paused" else "执行中", summary["running"]
            )
            progress_columns[3].metric("待处理", summary["pending"])
            progress_columns[4].metric("失败", summary["failed"])
            st.progress(
                summary["completed"] / summary["total"] if summary["total"] else 1.0,
                text=f"完成进度 {summary['completed']}/{summary['total']}",
            )
            if st.button("刷新批次进度", key=f"refresh_batch_{selected_batch_id}"):
                st.session_state["_next_batch_select"] = selected_batch_id
                st.rerun()
            visible_batch_items, _ = helpers.paged_values(
                st,
                selected_state.items,
                key=f"batch_table_page_{selected_batch_id}",
            )
            st.dataframe([helpers.batch_item_row(item) for item in visible_batch_items], width="stretch")
            markable_items = [
                item for item in selected_state.items
                if item.status not in {"raw_running", "recipe_running", "running", "non_recipe", "technique"}
            ]
            if markable_items and batch_source_store is not None:
                with st.expander("归类来源：非菜谱或烹饪技巧"):
                    st.caption(
                        "两类来源都会保留并从点餐菜单移除；烹饪技巧视频可在知识库页面继续提取和沉淀学习内容。"
                    )
                    source_classification = st.radio(
                        "归类类型",
                        ["non_recipe", "technique"],
                        format_func={
                            "non_recipe": "非菜谱 / 广告 / 无用信息",
                            "technique": "烹饪技巧 / 学习资料",
                        }.get,
                        horizontal=True,
                        key=f"batch_source_classification_{selected_batch_id}",
                    )
                    non_recipe_urls = st.multiselect(
                        "选择视频",
                        [item.url for item in markable_items],
                        key=f"batch_classification_urls_{selected_batch_id}",
                    )
                    if st.button(
                        "确认归类",
                        disabled=not non_recipe_urls,
                        key=f"batch_mark_source_{selected_batch_id}",
                    ):
                        creator = str(selected_state.options.get("creator_name") or "").strip() or None
                        batch_source_store.set_video_classifications(
                            non_recipe_urls,
                            source_classification,
                            creator_name=creator,
                            batch_id=selected_batch_id,
                        )
                        stamp = datetime.now(timezone.utc).isoformat()
                        selected_urls = set(non_recipe_urls)
                        for item in selected_state.items:
                            if item.url not in selected_urls:
                                continue
                            item.status = source_classification
                            item.error = (
                                "人工归类为烹饪技巧"
                                if source_classification == "technique"
                                else "人工归类为非菜谱"
                            )
                            item.finished_at = stamp
                            for stage in item.stages.values():
                                stage.status = "done"
                                stage.error = None
                                stage.finished_at = stamp
                        batch_queue.save_batch_state(selected_state)
                        batch_source_store.index_recipes()
                        helpers.rerun_with_notice(
                            st,
                            f"已将 {len(non_recipe_urls)} 条视频归类为"
                            f"{'烹饪技巧学习资料' if source_classification == 'technique' else '非菜谱'}，"
                            "并从点餐菜单中移除。",
                        )
            batch_log = batch_runner.read_batch_log(selected_batch_id)
            if batch_log:
                st.text_area("后台运行日志（最新）", value=batch_log, height=220, disabled=True)
            completed_batch_items = [
                item
                for item in selected_state.items
                if item.status in {"done", "skipped"}
                and item.output_folder
                and (Path(item.output_folder) / "recipe.json").is_file()
            ]
            if completed_batch_items:
                st.markdown("#### 批量结果后处理")
                st.caption("每条结果都是独立草稿，可以逐条编辑、审核或归档；批量生成不会锁死后续修改。")
                if st.button("AI 逐项审核本批次", key=f"batch_ai_review_{selected_state.batch_id}"):
                    st.session_state["_ai_review_scope_folders"] = [
                        str(Path(item.output_folder).resolve()) for item in completed_batch_items
                    ]
                    st.session_state["_next_page"] = "AI 审核归类"
                    st.rerun()
                batch_item_labels = {
                    f"{Path(item.output_folder).name} | {item.status}": item for item in completed_batch_items
                }
                batch_item = batch_item_labels[
                    st.selectbox("选择一条结果", list(batch_item_labels), key=f"batch_post_{selected_state.batch_id}")
                ]
                batch_folder = Path(str(batch_item.output_folder))
                force_batch_overwrite = st.checkbox(
                    "确认覆盖 Vault 中这条笔记的手写修改",
                    key=f"batch_force_archive_{selected_state.batch_id}",
                )
                batch_ratings = helpers.render_rating_controls(
                    st,
                    batch_folder / "recipe.json",
                    key_prefix=f"batch_{selected_state.batch_id}_{helpers.record_key(batch_folder)}_rating_",
                )
                col_detail, col_quick, col_edit, col_review, col_archive = st.columns(5)
                with col_detail:
                    if st.button("查看这条", key=f"batch_detail_{selected_state.batch_id}"):
                        helpers.navigate_to_record(st, "菜谱详情", batch_folder)
                with col_quick:
                    if st.button("快速审核", key=f"batch_quick_{selected_state.batch_id}"):
                        st.session_state["_quick_review_preferred_folder"] = str(batch_folder)
                        st.session_state["_next_page"] = "快速审核"
                        st.rerun()
                with col_edit:
                    if st.button("编辑这条", key=f"batch_edit_{selected_state.batch_id}"):
                        helpers.navigate_to_record(st, "编辑修复", batch_folder)
                with col_review:
                    if st.button("审核这条", key=f"batch_review_{selected_state.batch_id}"):
                        helpers.navigate_to_record(st, "审核确认", batch_folder)
                with col_archive:
                    if st.button("直接归档这条", type="primary", key=f"batch_archive_{selected_state.batch_id}"):
                        try:
                            archived, archived_knowledge, knowledge_error = helpers.archive_output(
                                batch_folder,
                                config,
                                overwrite=force_batch_overwrite,
                                ratings=batch_ratings,
                            )
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"归档失败：{helpers.clean_error(exc)}")
                        else:
                            message = f"已归档：{archived.note_path}"
                            if knowledge_error:
                                message += f"；技巧同步失败：{helpers.clean_error(knowledge_error)}"
                            helpers.rerun_with_notice(st, message)
                if st.button("归档本批次全部已完成草稿", key=f"batch_archive_all_{selected_state.batch_id}"):
                    folders = [Path(str(item.output_folder)) for item in completed_batch_items]
                    archived_batch = obsidian_archive.archive_recipe_batch(
                        folders,
                        helpers.vault_path(config),
                        conflict="overwrite" if force_batch_overwrite else "update",
                    )
                    knowledge_results, knowledge_error = (
                        helpers.archive_approved_knowledge(config, overwrite=force_batch_overwrite)
                        if config.archive_knowledge_with_recipe
                        else ((), None)
                    )
                    knowledge_message = f"；同步 {len(knowledge_results)} 条技巧" if knowledge_results else ""
                    if knowledge_error:
                        knowledge_message += f"；技巧同步失败：{helpers.clean_error(knowledge_error)}"
                    helpers.rerun_with_notice(
                        st,
                        f"批量归档完成：成功 {archived_batch.archived_count}，"
                        f"跳过 {archived_batch.skipped_count}，失败 {archived_batch.failed_count}"
                        f"{knowledge_message}。",
                    )
    if batch_source_store is not None:
        excluded_sources = [
            item
            for item in batch_source_store.list_video_sources()
            if item.get("classification") in {"non_recipe", "technique"}
        ]
        if excluded_sources:
            with st.expander(f"已归档的非菜谱/烹饪技巧来源（{len(excluded_sources)}）"):
                st.caption("这里保留来源去重记录；烹饪技巧可前往知识库提取干货，误判时可恢复为菜谱。")
                st.dataframe(
                    [
                        {
                            "UP 主": item.get("creator_name") or "",
                            "视频": item.get("title") or item.get("source_url") or "",
                            "来源": item.get("source_url") or "",
                            "归类": "烹饪技巧" if item.get("classification") == "technique" else "非菜谱",
                            "更新时间": item.get("updated_at") or "",
                        }
                        for item in excluded_sources[:200]
                    ],
                    hide_index=True,
                    width="stretch",
                )
                restore_urls = st.multiselect(
                    "恢复为菜谱来源",
                    [str(item["source_url"]) for item in excluded_sources],
                    key="batch_restore_excluded_urls",
                )
                if st.button(
                    "恢复所选来源",
                    disabled=not restore_urls,
                    key="batch_restore_excluded",
                ):
                    batch_source_store.set_video_classifications(restore_urls, "recipe")
                    batch_source_store.index_recipes()
                    helpers.rerun_with_notice(st, f"已恢复 {len(restore_urls)} 条来源，可重新加入批处理。")

