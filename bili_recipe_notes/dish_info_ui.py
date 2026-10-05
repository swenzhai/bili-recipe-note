"""Admin controls for optional menu descriptions and source references."""
from __future__ import annotations

from pathlib import Path

from .ai_review_dashboard import _duration, preferred_review_batch, review_batch_summary
from .config import UIConfig
from .dish_info_runner import (
    list_dish_info_batches, resume_dish_info_batch, start_dish_info_batch, stop_dish_info_batch,
)

_STATUS = {
    "queued": "等待启动", "running": "生成中", "paused": "已暂停", "completed": "已完成",
    "completed_with_errors": "已结束，部分失败", "interrupted": "任务中断，可继续", "failed": "启动失败",
}


def _options(config: UIConfig) -> dict:
    return {"provider": config.llm_provider, "openai_model": config.openai_model,
            "local_llm_command": config.local_llm_command, "codex_model": config.codex_model,
            "codex_profile": config.codex_profile, "cli_extra_instructions": config.llm_cli_extra_instructions}


def _notice(st, message: str) -> None:
    st.session_state["_dish_info_notice"] = message
    st.rerun()


def render_dish_info_dashboard(st, out_dir: str | Path, *, key_prefix: str = "dish_info",
                               config: UIConfig | None = None, refresh_seconds: int = 5) -> None:
    st.markdown("### 菜品介绍任务进度")

    @st.fragment(run_every=f"{refresh_seconds}s" if refresh_seconds else None)
    def live() -> None:
        st.button("刷新介绍任务", key=f"{key_prefix}_refresh")
        try:
            batches = list_dish_info_batches(out_dir) if Path(out_dir).expanduser().is_dir() else []
        except Exception as exc:
            st.error(f"读取介绍任务失败：{exc}")
            return
        batch = preferred_review_batch(batches)
        if not batch:
            st.caption("尚无菜品介绍任务。可在“菜品介绍”中按需生成。")
            return
        if config is not None:
            by_id = {item["batch_id"]: item for item in batches}
            selection_key = f"{key_prefix}_batch_select"
            if st.session_state.get(selection_key) not in by_id:
                st.session_state[selection_key] = batch["batch_id"]
            selected = st.selectbox("介绍任务", list(by_id), key=selection_key,
                                    format_func=lambda value: f"{value} · {_STATUS.get(by_id[value]['status'], by_id[value]['status'])}")
            batch = by_id[selected]
        summary = review_batch_summary(batch)
        with st.container(border=True):
            st.caption(f"{batch['batch_id']} · {_STATUS.get(batch.get('status'), batch.get('status'))} · 每 {refresh_seconds} 秒刷新")
            for column, label, field in zip(st.columns(5), ("菜品总数", "完成", "失败", "跳过", "剩余"),
                                            ("total", "completed", "failed", "skipped", "remaining")):
                column.metric(label, summary[field])
            progress = summary["progress"] if summary["total"] else (1.0 if batch["status"] == "completed" else 0.0)
            st.progress(progress, text=f"已处理 {summary['processed']}/{summary['total']} 道菜")
            timing = st.columns(2)
            timing[0].metric("本轮已运行", _duration(summary["elapsed"]))
            timing[1].metric("预计剩余", _duration(summary["eta"]) if batch.get("active") else "—")
            if batch.get("current_folder"):
                st.info("正在补充：" + str(batch["current_folder"]))
            if batch.get("stop_requested") and batch.get("active"):
                st.caption("已请求暂停，当前菜品处理结束后暂停。")
            if batch.get("error"):
                st.warning(str(batch["error"]))
            search_failed = sum(item.get("search_status") == "failed" for item in batch.get("items", []))
            if search_failed:
                st.warning(f"{search_failed} 道菜的联网检索失败，已保留本菜简介与做法。再次开启联网补充时会重试。")
            failures = [item for item in batch.get("items", []) if item.get("status") == "failed"]
            if failures:
                with st.expander(f"失败原因（{len(failures)} 道菜）"):
                    st.dataframe([{"菜品目录": item["folder"], "原因": item.get("error", "")} for item in failures],
                                 hide_index=True, width="stretch")
            if config is None:
                if st.button("管理菜品介绍", key=f"{key_prefix}_open"):
                    st.session_state["_next_page"] = "菜品介绍"
                    st.rerun()
                return
            active = any(item.get("active") for item in batches)
            use_current = st.checkbox("继续或重试时使用当前侧栏模型配置", value=True, key=f"{key_prefix}_current_config")
            disabled = active or (use_current and config.llm_provider == "none")
            resume_options = _options(config) if use_current else {}
            actions = st.columns(3)
            try:
                if actions[0].button("处理完当前菜品后暂停", key=f"{key_prefix}_stop", disabled=not batch.get("active")):
                    stop_dish_info_batch(out_dir, batch["batch_id"])
                    _notice(st, "已请求暂停；当前条目结束后保存进度。")
                if actions[1].button("继续未完成项", key=f"{key_prefix}_resume", disabled=disabled or not summary["remaining"]):
                    resume_dish_info_batch(out_dir, batch["batch_id"], **resume_options)
                    _notice(st, "菜品介绍任务已继续。")
                if actions[2].button("重试失败并继续", key=f"{key_prefix}_retry", disabled=disabled or not summary["failed"]):
                    resume_dish_info_batch(out_dir, batch["batch_id"], retry_failed=True, **resume_options)
                    _notice(st, "已重试失败项并继续未完成项。")
            except Exception as exc:
                st.error(f"操作失败：{exc}")

    live()


def render_dish_info_page(st, config: UIConfig) -> None:
    st.subheader("菜品介绍")
    st.caption("AI 根据已有菜谱和字幕概括简介与做法，可选联网查找背景和起源。结果独立保存，自动同步到点餐详情。")
    notice = st.session_state.pop("_dish_info_notice", None)
    if notice:
        st.success(notice)
    if not Path(config.out_dir).expanduser().is_dir():
        st.info("请先在侧栏选择已有菜谱输出目录。")
        return
    st.write("处理范围：当前可展示且已上架的菜品。任务在后台运行，关闭页面后继续；可暂停、续跑和重试失败项。")
    with st.form("dish_info_start_form"):
        limit = int(st.number_input("本轮最多补充菜品数（0 表示全部）", min_value=0, value=10, step=10, key="dish_info_limit"))
        only_pending = st.checkbox("跳过已有有效介绍的菜品", value=True, key="dish_info_only_pending")
        web_search = st.checkbox("联网查找背景与起源（可选）", value=False, key="dish_info_web_search")
        st.caption("联网时向 Bing 搜索发送菜名和部分主料名称，仅使用搜索摘要。网上资料可能同名不同菜，详情会标明候选匹配、来源链接和未经核实；无可靠对应片段时不补写起源。")
        st.caption(f"使用侧栏模型配置：{config.llm_provider}。首次建议先处理 10 道菜。")
        started = st.form_submit_button("开始 AI 补充菜品介绍", type="primary", disabled=config.llm_provider == "none")
    if config.llm_provider == "none":
        st.info("请先在侧栏选择 LLM 服务。")
    if started:
        try:
            with st.spinner("正在选择菜品并创建后台任务…"):
                batch = start_dish_info_batch(config.out_dir, limit=limit or None, only_pending=only_pending,
                                             web_search=web_search, **_options(config))
            st.session_state["dish_info_batch_select"] = batch["batch_id"]
            _notice(st, "没有需要补充的已上架菜品。" if not batch.get("total") else f"任务 {batch['batch_id']} 已启动，可在下方查看进度。")
        except Exception as exc:
            st.error(f"启动菜品介绍任务失败：{exc}")
    render_dish_info_dashboard(st, config.out_dir, config=config)
