from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ai_review_runner import (
    get_ai_review_batch,
    list_ai_review_batches,
    request_stop_ai_review_batch,
    resume_ai_review_batch,
    start_ai_review_batch,
)
from .config import UIConfig
from .review_policy import (
    AI_REVIEW_FILE_NAME,
    ai_review_is_hidden,
    load_ai_review,
    set_ai_review_visibility,
)


CLASSIFICATION_LABELS = {
    "recipe": "菜谱",
    "technique": "烹饪技巧",
    "non_recipe": "非菜谱",
    "uncertain": "无法确认",
}
STATUS_LABELS = {
    "queued": "等待启动",
    "running": "审核中",
    "paused": "已暂停",
    "completed": "已完成",
    "completed_with_errors": "已结束，部分失败",
    "interrupted": "任务中断，可续跑",
    "failed": "失败",
    "pending": "待审核",
    "skipped": "已跳过",
}


def _llm_options(config: UIConfig) -> dict[str, Any]:
    return {
        "provider": config.llm_provider,
        "openai_model": config.openai_model,
        "local_llm_command": config.local_llm_command,
        "codex_model": config.codex_model,
        "codex_profile": config.codex_profile,
        "cli_extra_instructions": config.llm_cli_extra_instructions,
    }


def _review_records(out_dir: str | Path) -> list[dict[str, Any]]:
    """Read generated recipes without creating or refreshing source reports."""
    root = Path(out_dir).expanduser().resolve()
    records = []
    for path in sorted(root.glob("*/recipe.json")):
        if path.is_symlink() or path.parent.is_symlink():
            continue
        try:
            recipe = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(recipe, dict):
                raise ValueError("recipe.json 必须是 JSON 对象")
            error = ""
        except (OSError, UnicodeError, ValueError) as exc:
            recipe, error = {}, str(exc)
        review = load_ai_review(path.parent)
        records.append({
            "folder": path.parent,
            "title": str(recipe.get("title") or path.parent.name),
            "recipe": recipe,
            "review": review,
            "error": error,
            "stale": review is None and (path.parent / AI_REVIEW_FILE_NAME).exists(),
            "hidden": ai_review_is_hidden(review),
        })
    return records


def _notice(st, message: str) -> None:
    st.session_state["_ai_review_notice"] = message
    st.rerun()


def _render_batch_status(st, batch: dict[str, Any]) -> None:
    completed = int(batch.get("completed") or 0)
    failed = int(batch.get("failed") or 0)
    skipped = int(batch.get("skipped") or 0)
    total = int(batch.get("total") or 0)
    st.caption(f"任务 {batch['batch_id']} · {STATUS_LABELS.get(batch.get('status'), batch.get('status'))}")
    st.progress(
        min(1.0, (completed + failed + skipped) / total) if total else 1.0,
        text=f"成功 {completed} · 失败 {failed} · 跳过 {skipped} · 共 {total} 条",
    )
    if batch.get("current_folder"):
        st.caption("当前：" + Path(batch["current_folder"]).name)
    if batch.get("stop_requested") and batch.get("active"):
        st.caption("已请求暂停，正在等待当前条目结束。")
    if batch.get("error"):
        st.warning(str(batch["error"]))
    failures = [item for item in batch.get("items", []) if item.get("status") == "failed"]
    if failures:
        with st.expander(f"失败明细（{len(failures)} 条）"):
            st.dataframe([
                {"目录": Path(item["folder"]).name, "错误": item.get("error", "")}
                for item in failures
            ], hide_index=True, width="stretch")


def render_ai_review_page(st, config: UIConfig) -> None:
    st.subheader("AI 审核归类")
    st.caption(
        "逐条对照字幕审核用料和步骤，保存分类、标签、证据与待确认项。"
        "审核结果独立保存，原始报告、字幕和已有菜谱正文保持原样。"
        "明确的非菜谱或技巧资料默认隐藏；不确定内容保留展示，供人工复核。"
    )
    notice = st.session_state.pop("_ai_review_notice", None)
    if notice:
        st.success(notice)
    if not Path(config.out_dir).expanduser().is_dir():
        st.info("输出目录还不存在。请先生成菜谱，或在侧栏选择已有输出目录。")
        return
    try:
        records = _review_records(config.out_dir)
        batches = list_ai_review_batches(config.out_dir)
        latest = get_ai_review_batch(config.out_dir)
    except Exception as exc:
        st.error(f"审核数据读取失败：{exc}")
        return
    active = bool(latest and latest.get("active"))
    reviewed = [item for item in records if item["review"]]
    metrics = st.columns(4)
    metrics[0].metric("生成版菜谱", len(records))
    metrics[1].metric("已有有效审核", len(reviewed))
    metrics[2].metric("待人工确认", sum(item["review"].get("status") == "needs_review" for item in reviewed))
    metrics[3].metric("已隐藏", sum(item["hidden"] for item in records))
    st.caption("后台逐项保存进度，关闭浏览器后继续运行。可刷新查看最新结果，暂停后可继续未完成项。")

    scope_folders = st.session_state.get("_ai_review_scope_folders", [])
    allowed = {str(item["folder"]) for item in records}
    scope_folders = [str(Path(folder).resolve()) for folder in scope_folders if str(Path(folder).resolve()) in allowed]
    scope_options = ["全部生成菜谱"] + (["当前批次结果"] if scope_folders else [])
    scope = st.selectbox("审核范围", scope_options, index=len(scope_options) - 1, key="ai_review_scope")
    only_pending = st.checkbox("跳过已有有效审核的菜谱", value=True, key="ai_review_only_pending")
    limit = int(st.number_input("本轮最多审核条数（0 表示全部）", min_value=0, value=0, step=10, key="ai_review_limit"))
    if config.llm_provider == "none":
        st.info("请先在侧栏选择 LLM 服务，再启动 AI 审核。")
    start_col, refresh_col = st.columns(2)
    if start_col.button(
        "开始 AI 批量审核", type="primary", key="ai_review_start",
        disabled=active or not records or config.llm_provider == "none",
    ):
        try:
            batch = start_ai_review_batch(
                config.out_dir, folders=scope_folders if scope == "当前批次结果" else None,
                limit=limit or None, only_pending=only_pending, **_llm_options(config),
            )
        except Exception as exc:
            st.error(f"启动审核失败：{exc}")
        else:
            st.session_state["ai_review_batch_select"] = batch["batch_id"]
            _notice(st, "AI 审核任务已在后台启动。" if batch.get("total", 1) else "当前范围没有需要审核的菜谱。")
    if refresh_col.button("刷新审核进度与结果", key="ai_review_refresh"):
        st.rerun()

    if batches:
        batch_ids = [item["batch_id"] for item in batches]
        if st.session_state.get("ai_review_batch_select") not in batch_ids:
            st.session_state["ai_review_batch_select"] = batch_ids[0]
        batch_id = st.selectbox("审核任务", batch_ids, key="ai_review_batch_select")
        batch = get_ai_review_batch(config.out_dir, batch_id)
        if batch:
            @st.fragment(run_every="5s" if batch.get("active") else None)
            def live_progress():
                current = get_ai_review_batch(config.out_dir, batch_id)
                if current is None:
                    return
                if batch.get("active") and not current.get("active"):
                    st.rerun()
                _render_batch_status(st, current)

            live_progress()
            controls = st.columns(3)
            if controls[0].button("完成当前条后暂停", key="ai_review_stop", disabled=not batch.get("active")):
                try:
                    request_stop_ai_review_batch(config.out_dir, batch_id)
                except Exception as exc:
                    st.error(f"暂停失败：{exc}")
                else:
                    _notice(st, "已请求暂停，当前条审核完成后停止。")
            resumable = batch.get("status") in {"paused", "interrupted", "failed"}
            if controls[1].button("继续未完成审核", key="ai_review_resume", disabled=active or not resumable):
                try:
                    resume_ai_review_batch(config.out_dir, batch_id)
                except Exception as exc:
                    st.error(f"继续失败：{exc}")
                else:
                    _notice(st, "已继续审核未完成的条目。")
            if controls[2].button("重试失败项", key="ai_review_retry", disabled=active or not batch.get("failed")):
                try:
                    resume_ai_review_batch(config.out_dir, batch_id, retry_failed=True)
                except Exception as exc:
                    st.error(f"重试失败：{exc}")
                else:
                    _notice(st, "已启动失败项重试。")

    st.divider()
    status_filter = st.radio(
        "结果范围", ["全部", "待审核", "待人工确认", "已隐藏", "菜谱", "烹饪技巧", "非菜谱"],
        horizontal=True, key="ai_review_result_filter",
    )
    query = st.text_input("搜索审核结果", placeholder="菜名、视频标题、标签或目录", key="ai_review_search").strip().casefold()
    visible = []
    for item in records:
        review = item["review"] or {}
        if status_filter == "待审核" and review:
            continue
        if status_filter == "待人工确认" and review.get("status") != "needs_review":
            continue
        if status_filter == "已隐藏" and not item["hidden"]:
            continue
        if status_filter in {"菜谱", "烹饪技巧", "非菜谱"} and CLASSIFICATION_LABELS.get(review.get("classification")) != status_filter:
            continue
        search_text = " ".join([
            item["title"], item["folder"].name,
            str(item["recipe"].get("video_title") or ""), " ".join(review.get("tags") or []),
        ]).casefold()
        if not query or query in search_text:
            visible.append(item)
    if not visible:
        st.info("当前范围没有记录。")
        return
    st.caption(f"匹配 {len(visible)} 条；隐藏内容始终可以在此查看和恢复。")
    by_folder = {str(item["folder"]): item for item in visible}
    focus = st.session_state.pop("_ai_review_focus_folder", None)
    if focus in by_folder:
        st.session_state["ai_review_record"] = focus
    if st.session_state.get("ai_review_record") not in by_folder:
        st.session_state["ai_review_record"] = next(iter(by_folder))
    selected_folder = st.selectbox(
        "查看审核记录", list(by_folder), key="ai_review_record",
        format_func=lambda folder: f"{by_folder[folder]['title']} · {Path(folder).name}",
    )
    selected = by_folder[selected_folder]
    review = selected["review"]
    if selected["error"]:
        st.error(f"菜谱读取失败：{selected['error']}")
    if selected["stale"]:
        st.warning("已有审核文件已失效或损坏，请重新审核当前版本。")
    if st.button(
        "AI 审核这一条", key="ai_review_single",
        disabled=active or bool(selected["error"]) or config.llm_provider == "none",
    ):
        try:
            batch = start_ai_review_batch(
                config.out_dir, folders=[selected_folder], only_pending=False, **_llm_options(config),
            )
        except Exception as exc:
            st.error(f"启动审核失败：{exc}")
        else:
            st.session_state["ai_review_batch_select"] = batch["batch_id"]
            _notice(st, "已开始审核所选菜谱。")
    if review:
        st.write(
            f"分类：{CLASSIFICATION_LABELS.get(review['classification'], review['classification'])} · "
            f"置信度：{review['confidence']:.0%} · "
            f"{'已隐藏' if selected['hidden'] else '正常展示'}"
        )
        st.write(review.get("reason", ""))
        st.caption("标签：" + "、".join(review.get("tags") or []))
        for evidence in review.get("evidence", []):
            start = evidence.get("start")
            st.write(f"依据{f'（{start:g} 秒）' if isinstance(start, (int, float)) else ''}：{evidence.get('text', '')}")
        checks = review.get("item_checks") or []
        if checks:
            st.dataframe([
                {"审核项": check.get("id"), "结论": "有依据" if check.get("verdict") == "supported" else "待确认", "说明": check.get("reason")}
                for check in checks
            ], hide_index=True, width="stretch")
        if review.get("visibility_override"):
            st.caption("已使用人工展示决定；再次 AI 审核会保留该决定。")
        visibility_controls = st.columns(2)
        desired_visibility = "visible" if selected["hidden"] else "hidden"
        if visibility_controls[0].button(
            "人工恢复展示" if selected["hidden"] else "人工隐藏", key="ai_review_visibility",
        ):
            try:
                set_ai_review_visibility(selected_folder, desired_visibility)
            except Exception as exc:
                st.error(f"保存展示决定失败：{exc}")
            else:
                _notice(st, "展示决定已保存，手机菜单会在下一次同步时更新。")
        if visibility_controls[1].button(
            "恢复 AI 展示建议", key="ai_review_clear_override", disabled=not review.get("visibility_override"),
        ):
            try:
                set_ai_review_visibility(selected_folder, None)
            except Exception as exc:
                st.error(f"恢复建议失败：{exc}")
            else:
                _notice(st, "已恢复 AI 的展示建议。")
        st.download_button(
            "下载当前审核记录", json.dumps(review, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=AI_REVIEW_FILE_NAME, mime="application/json", key="ai_review_download",
        )
    else:
        st.info("尚无当前版本的有效 AI 审核结果。")
    with st.expander("只读查看生成版菜谱"):
        st.json(selected["recipe"])
