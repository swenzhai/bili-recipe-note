"""Live progress shared by the review page and the background task dashboard."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ai_review_runner import list_ai_review_batches


STATUS_LABELS = {
    "queued": "等待启动", "running": "审核中", "paused": "已暂停",
    "completed": "已完成", "completed_with_errors": "已结束，部分失败",
    "interrupted": "任务中断，可续跑", "failed": "失败", "pending": "待审核", "skipped": "已跳过",
}


def preferred_review_batch(batches: list[dict]) -> dict | None:
    active = next((batch for batch in batches if batch.get("active")), None)
    # A resumed older task remains the most relevant task after it finishes.
    return active or max(batches, key=lambda batch: str(batch.get("updated_at") or batch.get("created_at") or ""), default=None)


def _timestamp(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "等待数据"
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分 {seconds} 秒" if minutes else f"{seconds} 秒"


def review_batch_summary(batch: dict, *, now: datetime | None = None) -> dict:
    """Estimate speed from this run only, excluding preexisting and skipped work."""
    now = now or datetime.now(timezone.utc)
    counts = {key: max(0, int(batch.get(key) or 0)) for key in ("total", "completed", "failed", "skipped")}
    processed = counts["completed"] + counts["failed"] + counts["skipped"]
    remaining = max(0, counts["total"] - processed)
    token = batch.get("run_token")
    run = next((run for run in reversed(batch.get("runs") or []) if run.get("run_token") == token), {})
    started = _timestamp(run.get("started_at") or batch.get("started_at") or batch.get("created_at"))
    end = now if batch.get("active") else _timestamp(batch.get("finished_at") or batch.get("updated_at"))
    elapsed = max(0.0, (end - started).total_seconds()) if started and end else None
    samples = 0
    for item in batch.get("items") or []:
        item_started, item_finished = _timestamp(item.get("started_at")), _timestamp(item.get("finished_at"))
        if item.get("status") not in {"completed", "failed"} or not item_started or not item_finished or not started:
            continue
        if not (started <= item_started <= item_finished <= (end or now)):
            continue
        attempts = item.get("attempts") or []
        if token and attempts and not any(attempt.get("run_token") == token for attempt in attempts):
            continue
        samples += 1
    average = elapsed / samples if elapsed and samples else None
    eta = average * remaining if batch.get("active") and average is not None else None
    return {
        **counts, "processed": processed, "remaining": remaining,
        "progress": min(1.0, processed / counts["total"]) if counts["total"] else 0.0,
        "elapsed": elapsed, "speed_per_hour": 3600 / average if average else None,
        "eta": eta, "estimated_finish": now + timedelta(seconds=eta) if eta is not None else None,
    }


def render_batch_status(st, batch: dict[str, Any]) -> None:
    summary = review_batch_summary(batch)
    st.caption(f"任务 {batch['batch_id']} · {STATUS_LABELS.get(batch.get('status'), batch.get('status'))}")
    counts = st.columns(5)
    for column, label, field in zip(counts, ("审核总数", "审核完成", "审核失败", "审核跳过", "审核剩余"),
                                    ("total", "completed", "failed", "skipped", "remaining")):
        column.metric(label, summary[field])
    st.progress(summary["progress"], text=f"已处理 {summary['processed']}/{summary['total']} 条（包含失败和跳过）")
    timing = st.columns(4)
    timing[0].metric("本轮已运行", _duration(summary["elapsed"]))
    speed = summary["speed_per_hour"]
    timing[1].metric("平均速度", f"{speed:.1f} 条/小时" if speed is not None else "等待数据")
    timing[2].metric("预计剩余", _duration(summary["eta"]) if batch.get("active") else "—")
    finish = summary["estimated_finish"]
    timing[3].metric("预计完成", finish.astimezone().strftime("%m-%d %H:%M") if finish else "—")
    if batch.get("current_folder"):
        current = str(batch["current_folder"])
        st.info("正在审核：" + current)
        item = next((item for item in batch.get("items", []) if item.get("folder") == current), {})
        started = _timestamp(item.get("started_at"))
        attempts = [attempt for attempt in item.get("attempts", []) if attempt.get("run_token") == batch.get("run_token")]
        details = []
        if started:
            details.append("本条用时 " + _duration((datetime.now(timezone.utc) - started).total_seconds()))
        if attempts:
            details.append(f"本轮尝试 {len(attempts)}/{batch.get('max_attempts', 3)}")
            if attempts[-1].get("status") == "failed" and attempts[-1].get("retry_delay_seconds") is not None:
                details.append("短暂错误后等待重试")
        if details:
            st.caption(" · ".join(details))
    if batch.get("stop_requested") and batch.get("active"):
        st.caption("已请求暂停，正在等待当前条目结束。")
    if batch.get("error"):
        st.warning(str(batch["error"]))
    updated = _timestamp(batch.get("updated_at"))
    if updated:
        st.caption("最近任务进度更新：" + updated.astimezone().strftime("%Y-%m-%d %H:%M:%S"))
    failures = [item for item in batch.get("items", []) if item.get("status") == "failed"]
    if failures:
        with st.expander(f"失败明细（{len(failures)} 条）"):
            st.dataframe([
                {"目录": str(item["folder"]), "错误": item.get("error", "")}
                for item in failures
            ], hide_index=True, width="stretch")


def render_ai_review_dashboard(st, out_dir: str | Path, *, key_prefix: str = "ai_review",
                               refresh_seconds: int = 5, show_open_button: bool = False) -> None:
    st.markdown("### AI 审核进度")

    # Keep polling even when initially idle: another tab or a resumed older
    # batch can start work without causing a full rerun of this page.
    @st.fragment(run_every=f"{refresh_seconds}s" if refresh_seconds else None)
    def live_progress() -> None:
        st.button("刷新审核仪表盘", key=f"{key_prefix}_dashboard_refresh")
        if refresh_seconds:
            st.caption(f"每 {refresh_seconds} 秒自动刷新，优先显示正在运行的审核任务。")
        try:
            batches = list_ai_review_batches(out_dir) if Path(out_dir).expanduser().is_dir() else []
        except Exception as exc:
            st.error(f"读取审核进度失败：{exc}")
            return
        batch = preferred_review_batch(batches)
        signature = (str(out_dir), batch.get("batch_id"), batch.get("run_token"), batch.get("status"),
                     bool(batch.get("active"))) if batch else (str(out_dir), None)
        state_key = f"{key_prefix}_dashboard_state"
        previous = st.session_state.get(state_key)
        st.session_state[state_key] = signature
        if previous is not None and previous != signature:
            # Refresh action buttons and the result list when a task starts,
            # pauses or finishes; ordinary count updates stay in this fragment.
            st.rerun()
        if not batch:
            st.info("还没有 AI 审核任务。可在“AI 审核归类”中开始审核。")
            return
        with st.container(border=True):
            render_batch_status(st, batch)
            if show_open_button and st.button("查看审核详情", key=f"{key_prefix}_open_review"):
                st.session_state["ai_review_batch_select"] = batch["batch_id"]
                st.session_state["_next_page"] = "AI 审核归类"
                st.rerun()

    live_progress()
