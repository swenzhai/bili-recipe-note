from __future__ import annotations

from .output_layout import iter_output_folders

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
from .ai_review_workbench import render_review_workbench
from .ai_review_dashboard import (
    STATUS_LABELS, preferred_review_batch, render_ai_review_dashboard, render_batch_status as _render_batch_status,
)
from .file_cache import read_json_cached
from .mobile_sync import recipe_visibility_states
from .review_policy import (
    AI_REVIEW_FILE_NAME,
    get_review_state,
    list_review_history,
    set_ai_review_visibility,
)


CLASSIFICATION_LABELS = {
    "recipe": "菜谱",
    "technique": "烹饪技巧",
    "non_recipe": "非菜谱",
    "uncertain": "无法确认",
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
    for folder in sorted(iter_output_folders(root)):
        path = folder / "recipe.json"
        if not path.is_file() or path.is_symlink() or path.parent.is_symlink():
            continue
        try:
            recipe = read_json_cached(path)
            if not isinstance(recipe, dict):
                raise ValueError("recipe.json 必须是 JSON 对象")
            error = ""
        except (OSError, UnicodeError, ValueError) as exc:
            recipe, error = {}, str(exc)
        state = get_review_state(path.parent) if not error else {}
        review = state.get("ai_review")
        records.append({
            "folder": path.parent,
            "title": str(recipe.get("title") or path.parent.name),
            "recipe": recipe,
            "review": review,
            "review_state": state,
            "error": error,
            "stale": review is None and (path.parent / AI_REVIEW_FILE_NAME).exists(),
        })
    visibility = recipe_visibility_states(root, folders=[item["folder"] for item in records])
    for item in records:
        item["visibility"] = visibility.get(str(item["folder"]), {})
        item["hidden"] = bool(item["visibility"].get("hidden"))
    return records


def _notice(st, message: str) -> None:
    st.session_state["_ai_review_notice"] = message
    st.rerun()


def _render_review_history(st, folder: Path) -> None:
    with st.expander("审核历史"):
        try:
            snapshots = list_review_history(folder)
        except (OSError, ValueError) as exc:
            st.warning(f"审核历史暂不可用：{exc}")
            return
        if not snapshots:
            st.caption("还没有历史版本。再次审核或修改人工决定时，会保留此前记录。")
            return
        events = {
            "ai_rereview": "AI 复审", "manual_visibility": "人工展示决定",
            "manual_classification": "人工分类", "manual_item": "逐项人工核对",
            "manual_complete": "完成人工确认",
        }
        st.caption("这里保存的是每次变更前的记录，仅供核对和下载。")
        by_id = {item["history_id"]: item for item in snapshots}
        selection = st.selectbox(
            "历史版本", list(by_id), key=f"ai_review_history_{folder.name}",
            format_func=lambda key: f"{by_id[key].get('archived_at', '')} · {events.get(by_id[key].get('event'), '审核变更')}前",
        )
        snapshot = by_id[selection]
        st.json(snapshot["review"])
        st.download_button(
            "下载所选历史记录", json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=f"recipe.ai-review.{selection}.json", mime="application/json",
            key=f"ai_review_history_download_{folder.name}",
        )


def render_ai_review_page(st, config: UIConfig) -> None:
    st.subheader("AI 审核归类")
    st.caption(
        "逐条对照字幕审核用料和步骤，保存分类、标签、证据与待确认项。"
        "审核结果独立保存，原始报告、字幕和已有菜谱正文保持原样。"
        "明确的非菜谱或技巧资料默认隐藏；不确定内容保留展示，供人工复核。"
    )
    render_ai_review_dashboard(st, config.out_dir)
    notice = st.session_state.pop("_ai_review_notice", None)
    if notice:
        st.success(notice)
    if not Path(config.out_dir).expanduser().is_dir():
        st.info("输出目录还不存在。请先生成菜谱，或在侧栏选择已有输出目录。")
        return
    try:
        records = _review_records(config.out_dir)
        batches = list_ai_review_batches(config.out_dir)
        latest = preferred_review_batch(batches)
    except Exception as exc:
        st.error(f"审核数据读取失败：{exc}")
        return
    active = bool(latest and latest.get("active"))
    reviewed = [item for item in records if item["review"]]
    metrics = st.columns(4)
    metrics[0].metric("生成版菜谱", len(records))
    metrics[1].metric("已有有效审核", len(reviewed))
    metrics[2].metric("待人工确认", sum(bool(item["review_state"].get("needs_confirmation")) for item in records))
    metrics[3].metric("已隐藏", sum(item["hidden"] for item in records))
    st.caption("后台逐项保存进度，关闭浏览器后继续运行。可刷新查看最新结果，暂停后可继续未完成项。")

    scope_folders = st.session_state.get("_ai_review_scope_folders", [])
    allowed = {str(item["folder"]) for item in records}
    scope_folders = [str(Path(folder).resolve()) for folder in scope_folders if str(Path(folder).resolve()) in allowed]
    scope_options = ["全部生成菜谱"] + (["当前批次结果"] if scope_folders else [])
    scope = st.selectbox("审核范围", scope_options, index=len(scope_options) - 1, key="ai_review_scope")
    only_pending = st.checkbox("跳过已有有效 AI 审核的菜谱", value=True, key="ai_review_only_pending")
    limit = int(st.number_input("本轮最多审核条数（0 表示全部）", min_value=0, value=0, step=10, key="ai_review_limit"))
    with st.expander("失败恢复设置"):
        max_attempts = int(st.number_input("短暂错误最多尝试次数", min_value=1, max_value=5, value=3, key="ai_review_max_attempts"))
        failure_limit = int(st.number_input("连续失败多少条后自动暂停", min_value=1, max_value=100, value=5, key="ai_review_failure_limit"))
        st.caption("超时等短暂错误会退避重试；配置错误会保留原因，避免重复调用。")
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
                max_attempts=max_attempts, consecutive_failure_limit=failure_limit,
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
            st.session_state["ai_review_batch_select"] = latest["batch_id"] if latest else batch_ids[0]
        batch_id = st.selectbox("审核任务", batch_ids, key="ai_review_batch_select")
        batch = get_ai_review_batch(config.out_dir, batch_id)
        if batch:
            if latest and batch_id != latest["batch_id"]:
                with st.expander("所选历史任务进度", expanded=True):
                    _render_batch_status(st, batch)
            st.caption(f"以下操作作用于任务 {batch_id} · {STATUS_LABELS.get(batch.get('status'), batch.get('status'))}")
            retry_config = st.radio(
                "继续或重试时使用的配置", ["当前侧栏配置", "原任务配置"],
                horizontal=True, key="ai_review_retry_config",
            )
            retry_options = _llm_options(config) if retry_config == "当前侧栏配置" else {}
            invalid_retry_config = retry_config == "当前侧栏配置" and config.llm_provider == "none"
            controls = st.columns(3)
            if controls[0].button("完成当前条后暂停", key="ai_review_stop", disabled=not batch.get("active")):
                try:
                    request_stop_ai_review_batch(config.out_dir, batch_id)
                except Exception as exc:
                    st.error(f"暂停失败：{exc}")
                else:
                    _notice(st, "已请求暂停，当前条审核完成后停止。")
            resumable = batch.get("status") in {"paused", "interrupted", "failed"}
            if controls[1].button("继续未完成审核", key="ai_review_resume", disabled=active or not resumable or invalid_retry_config):
                try:
                    resume_ai_review_batch(config.out_dir, batch_id, **retry_options)
                except Exception as exc:
                    st.error(f"继续失败：{exc}")
                else:
                    _notice(st, "已继续审核未完成的条目。")
            if controls[2].button("重试失败项", key="ai_review_retry", disabled=active or not batch.get("failed") or invalid_retry_config):
                try:
                    resume_ai_review_batch(config.out_dir, batch_id, retry_failed=True, **retry_options)
                except Exception as exc:
                    st.error(f"重试失败：{exc}")
                else:
                    _notice(st, "已启动失败项重试。")

    st.divider()
    next_filter = st.session_state.pop("_ai_review_next_filter", None)
    if next_filter:
        st.session_state["ai_review_result_filter"] = next_filter
    status_filter = st.radio(
        "结果范围", ["全部", "待审核", "待人工确认", "已人工确认", "已隐藏", "菜谱", "烹饪技巧", "非菜谱"],
        horizontal=True, key="ai_review_result_filter",
    )
    if st.session_state.pop("_ai_review_clear_search", False):
        st.session_state["ai_review_search"] = ""
    query = st.text_input("搜索审核结果", placeholder="菜名、视频标题、标签或目录", key="ai_review_search").strip().casefold()
    visible = []
    for item in records:
        review = item["review"] or {}
        if status_filter == "待审核" and review:
            continue
        if status_filter == "待人工确认" and not item["review_state"].get("needs_confirmation"):
            continue
        if status_filter == "已人工确认" and not item["review_state"].get("manual_completed"):
            continue
        if status_filter == "已隐藏" and not item["hidden"]:
            continue
        if status_filter in {"菜谱", "烹饪技巧", "非菜谱"} and CLASSIFICATION_LABELS.get(item["visibility"].get("classification")) != status_filter:
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
    state = selected["review_state"]
    stored = state.get("record") or {}
    visibility = selected["visibility"]
    if selected["error"]:
        st.error(f"菜谱读取失败：{selected['error']}")
    if selected["stale"]:
        st.warning(
            "旧版审核缺少字幕与规则版本记录，需要重新审核升级；已有人工决定仍然保留。"
            if state.get("requires_upgrade") else
            "当前没有有效的 AI 审核结果，可重新审核；已有人工分类和展示决定仍然保留。"
        )
    if st.button(
        "AI 审核这一条", key="ai_review_single",
        disabled=active or bool(selected["error"]) or config.llm_provider == "none",
    ):
        try:
            batch = start_ai_review_batch(
                config.out_dir, folders=[selected_folder], only_pending=False, **_llm_options(config),
                max_attempts=max_attempts, consecutive_failure_limit=failure_limit,
            )
        except Exception as exc:
            st.error(f"启动审核失败：{exc}")
        else:
            st.session_state["ai_review_batch_select"] = batch["batch_id"]
            _notice(st, "已开始审核所选菜谱。")
    st.write(
        f"内容分类：{CLASSIFICATION_LABELS.get(visibility.get('classification'), '无法确认')} · "
        f"{'内容已隐藏' if selected['hidden'] else '内容可展示'}"
    )
    st.caption("展示依据：" + str(visibility.get("content_reason") or visibility.get("reason") or "暂无审核决定"))
    if visibility.get("published") is False:
        st.info("手机菜单仍处于手动下架状态；恢复内容展示后，可到“手机客户端”页面重新上架。")
    if review:
        st.caption(f"AI 判断：{CLASSIFICATION_LABELS.get(review['classification'], review['classification'])} · 置信度：{review['confidence']:.0%}")
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
    else:
        st.info("尚无当前版本的有效 AI 审核结果。")
    if not selected["error"]:
        if stored.get("visibility_override"):
            st.caption("已使用人工展示决定；内容变化和再次 AI 审核均保留该决定。")
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
            "清除人工展示覆盖", key="ai_review_clear_override", disabled=not stored.get("visibility_override"),
        ):
            try:
                set_ai_review_visibility(selected_folder, None)
            except Exception as exc:
                st.error(f"恢复建议失败：{exc}")
            else:
                _notice(st, "已清除人工展示覆盖，按人工分类、来源分类与有效 AI 判断决定展示。")
    if stored:
        st.download_button(
            "下载当前审核记录", json.dumps(stored, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=AI_REVIEW_FILE_NAME, mime="application/json", key="ai_review_download",
        )
    if not selected["error"]:
        next_folder = next((str(item["folder"]) for item in records if item["folder"] != selected["folder"] and item["review_state"].get("needs_confirmation")), None)
        render_review_workbench(st, selected, next_folder=next_folder)
    with st.expander("只读查看生成版菜谱"):
        st.json(selected["recipe"])
    _render_review_history(st, selected["folder"])
