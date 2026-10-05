"""Streamlit controls for independent, local-model food frame selection."""
from __future__ import annotations

from pathlib import Path

from .ai_review_dashboard import _duration, _timestamp, preferred_review_batch, review_batch_summary
from .config import UIConfig
from .frame_selection import DEFAULT_REQUIREMENT
from .frame_selection_runner import (
    list_frame_selection_batches,
    resume_frame_selection_batch,
    start_frame_selection_batch,
    stop_frame_selection_batch,
)
from .frame_selection_service import load_frame_candidate


PAGE_NAME = "AI 菜品选图"
_STATUS = {
    "queued": "等待启动", "running": "选图中", "paused": "已暂停", "interrupted": "任务中断，可继续",
    "completed": "已完成", "completed_with_errors": "已结束，部分失败", "failed": "启动失败",
    "pending": "待处理", "skipped": "已跳过", "not_found": "未找到匹配画面",
}
_PHASE = {
    "preparing": "准备原视频", "waiting_model": "等待本地视觉模型", "downloading": "下载原视频", "loading_model": "加载本地视觉模型",
    "loading": "加载本地视觉模型", "coarse": "粗筛候选帧", "fine": "精筛菜品特写",
    "validating": "校验候选图片", "completed": "候选已保存",
}


def _notice(st, message: str) -> None:
    st.session_state["_frame_selection_notice"] = message
    st.rerun()


def _go_to_review(st, folder: Path) -> None:
    st.session_state["_quick_review_preferred_folder"] = str(folder.resolve())
    st.session_state["quick_review_filter"] = "全部"
    st.session_state["_next_page"] = "快速审核"
    st.rerun()


def _item_label(item: dict) -> str:
    state = item.get("status", "pending")
    if state == "completed":
        label = "未找到匹配画面" if item.get("result_status") in {"not_found", "no_match"} else "已有候选"
    else:
        label = _STATUS.get(state, state)
    return f"{item.get('title') or item.get('folder') or '未命名菜品'} · {label}"


def _render_frame_progress(st, batch: dict) -> None:
    progress = batch.get("current_progress") or {}
    phase = str(progress.get("phase") or "")
    if phase:
        st.write("当前阶段：" + _PHASE.get(phase, phase))
    total = max(0, int(progress.get("phase_total") or progress.get("total_frames") or 0))
    completed = max(0, int(progress.get("phase_completed") or progress.get("completed_frames") or 0))
    if total:
        st.progress(min(1.0, completed / total), text=f"{_PHASE.get(phase, '候选帧')}：{completed}/{total} 帧")
    elif phase in {"preparing", "waiting_model", "downloading", "loading_model", "loading"}:
        st.caption("正在准备本条视频，获得候选帧后会显示帧进度。")
    details = []
    timestamp = progress.get("current_timestamp", progress.get("timestamp"))
    if timestamp is not None:
        details.append(f"当前视频时间 {float(timestamp):.2f} 秒")
    for field, label in (("coarse_total", "粗筛"), ("fine_total", "精筛")):
        if progress.get(field) is not None:
            details.append(f"{label} {progress[field]} 帧")
    if progress.get("frames_total"):
        details.append(f"候选帧累计 {progress.get('frames_completed', 0)}/{progress['frames_total']}")
    if details:
        st.caption(" · ".join(details))


def _render_batch_status(st, batch: dict) -> dict:
    # A no-match result is completed work and contributes to the time estimate.
    summary = review_batch_summary({**batch, "items": [
        {**item, "status": "completed"} if item.get("status") == "not_found" else item
        for item in batch.get("items", [])
    ]})
    not_found = max(0, int(batch.get("not_found") or 0))
    found = max(0, summary["completed"] - not_found)
    st.caption(f"任务 {batch['batch_id']} · {_STATUS.get(batch.get('status'), batch.get('status'))}")
    values = (summary["total"], found, not_found, summary["failed"], summary["skipped"], summary["remaining"])
    for column, label, value in zip(st.columns(6), ("菜品总数", "找到候选", "无匹配", "失败", "跳过", "剩余"), values):
        column.metric(label, value)
    overall = summary["progress"] if summary["total"] else float(batch.get("status") == "completed")
    st.progress(overall, text=f"已处理 {summary['processed']}/{summary['total']} 道菜（含无匹配、失败和跳过）")
    timing = st.columns(2)
    timing[0].metric("本轮已运行", _duration(summary["elapsed"]))
    timing[1].metric("预计剩余", _duration(summary["eta"]) if batch.get("active") else "—")
    if batch.get("active") and summary["eta"] is None:
        st.caption("完成首道菜后再估算剩余时间；视频长度和候选帧数会影响耗时。")
    if batch.get("current_folder"):
        st.info("正在处理：" + str(batch["current_folder"]))
        _render_frame_progress(st, batch)
    if batch.get("active") and batch.get("stop_requested"):
        st.caption("已请求暂停，当前菜品处理结束后保存进度并暂停。")
    if batch.get("error"):
        st.warning(str(batch["error"]))
    if not_found:
        st.info(f"{not_found} 道菜未找到符合要求的画面，这是选图结果，不属于运行失败。可调整要求后新建任务并取消“跳过已有有效选图结果”。")
    failures = [item for item in batch.get("items", []) if item.get("status") == "failed"]
    if failures:
        with st.expander(f"失败原因（{len(failures)} 道菜）"):
            st.dataframe([{"菜品目录": item.get("folder", ""), "原因": item.get("error", "")} for item in failures],
                         hide_index=True, width="stretch")
    updated = _timestamp(batch.get("updated_at"))
    if updated:
        st.caption("最近进度更新：" + updated.astimezone().strftime("%Y-%m-%d %H:%M:%S"))
    return summary


def _render_results(st, out_dir: str | Path, batch: dict, key_prefix: str) -> None:
    items = [item for item in batch.get("items", []) if item.get("folder")]
    if not items:
        return
    st.markdown("#### 查看选图结果")
    by_folder = {str(item["folder"]): item for item in items}
    key = f"{key_prefix}_result_select"
    if st.session_state.get(key) not in by_folder:
        st.session_state[key] = next((str(item["folder"]) for item in items if item.get("status") == "completed"), next(iter(by_folder)))
    selected = st.selectbox("选择菜品查看候选", list(by_folder), key=key,
                            format_func=lambda value: _item_label(by_folder[value]))
    item = by_folder[selected]
    root = Path(out_dir).expanduser().resolve()
    folder = Path(selected)
    folder = folder.resolve() if folder.is_absolute() else (root / folder).resolve()
    if not folder.is_relative_to(root):
        st.error("菜品目录超出当前菜谱库，无法预览。")
        return
    try:
        candidate = load_frame_candidate(root, folder, include_stale=True)
    except Exception as exc:
        st.error(f"读取候选结果失败：{exc}")
        return
    if not candidate:
        if item.get("status") == "failed":
            st.warning("本条选图失败：" + str(item.get("error") or "请查看任务日志"))
        elif item.get("status") == "skipped":
            st.caption("本条已跳过，可能已有人工确认封面或有效选图结果。")
        elif item.get("status") in {"completed", "not_found"}:
            st.warning("此前的选图结果已过期或缺失，请重新选图后再确认。")
        else:
            st.caption("当前尚无可预览的选图结果。")
        return
    status = str(candidate.get("status") or "")
    if status in {"stale", "expired"} or candidate.get("valid") is False:
        st.warning("候选结果已过期或不可用，请重新选图后再确认。")
        if candidate.get("reason"):
            st.caption(str(candidate["reason"]))
        return
    if status in {"not_found", "no_match"}:
        st.info("未找到符合要求的菜品特写。可以调整选图要求或增加采样帧数后重试。")
        if candidate.get("reason"):
            st.write(str(candidate["reason"]))
        return
    image_path = candidate.get("image_path")
    if not image_path:
        st.warning("候选图片缺失，请重新选图。")
        return
    st.image(str(image_path), width="stretch")
    timestamp = candidate.get("timestamp")
    if timestamp is not None:
        st.caption(f"原视频 {float(timestamp):.2f} 秒 · PNG 原帧候选")
    if candidate.get("reason"):
        st.write("选择理由：" + str(candidate["reason"]))
    st.caption("候选图尚未应用到菜单，请在快速审核中确认画面并裁剪。")
    if st.button("去快速审核确认与裁剪", key=f"{key_prefix}_confirm_candidate", type="primary"):
        _go_to_review(st, folder)


def render_frame_selection_dashboard(st, out_dir: str | Path, *, key_prefix: str = "frame_selection",
                                     show_open_button: bool = False, refresh_seconds: int = 5,
                                     config: UIConfig | None = None, resume_options: dict | None = None) -> None:
    st.markdown("### AI 菜品选图进度")

    @st.fragment(run_every=f"{refresh_seconds}s" if refresh_seconds else None)
    def live() -> None:
        st.button("刷新选图进度", key=f"{key_prefix}_refresh")
        if refresh_seconds:
            st.caption(f"每 {refresh_seconds} 秒自动刷新；关闭浏览器后后台任务继续运行。")
        try:
            batches = list_frame_selection_batches(out_dir) if Path(out_dir).expanduser().is_dir() else []
        except Exception as exc:
            st.error(f"读取选图进度失败：{exc}")
            return
        preferred = preferred_review_batch(batches)
        signature = (str(out_dir), preferred.get("batch_id"), preferred.get("run_token"),
                     preferred.get("status"), bool(preferred.get("active"))) if preferred else (str(out_dir), None)
        state_key = f"{key_prefix}_dashboard_state"
        previous = st.session_state.get(state_key)
        st.session_state[state_key] = signature
        if previous is not None and previous != signature:
            st.rerun()
        if not preferred:
            st.info("还没有选图任务。可在“AI 菜品选图”中开始。")
            if show_open_button and st.button("打开 AI 菜品选图", key=f"{key_prefix}_open"):
                st.session_state["_next_page"] = PAGE_NAME
                st.rerun()
            return
        batch = preferred
        if config is not None:
            by_id = {item["batch_id"]: item for item in batches}
            select_key = f"{key_prefix}_batch_select"
            if st.session_state.get(select_key) not in by_id:
                st.session_state[select_key] = preferred["batch_id"]
            selected = st.selectbox("选图任务", list(by_id), key=select_key,
                                    format_func=lambda value: f"{value} · {_STATUS.get(by_id[value].get('status'), by_id[value].get('status'))}")
            batch = by_id[selected]
            if preferred.get("active") and selected != preferred["batch_id"]:
                with st.container(border=True):
                    st.markdown("**当前运行中的任务**")
                    _render_batch_status(st, preferred)
        with st.container(border=True):
            summary = _render_batch_status(st, batch)
            if config is not None:
                active = any(item.get("active") for item in batches)
                current = st.checkbox("继续或重试时使用本页选图参数", value=False, key=f"{key_prefix}_current_config")
                options = dict(resume_options or {}) if current else {}
                # Cookies are credentials for this machine, never a persisted
                # model setting from an earlier run or a different device.
                options["cookies"] = config.cookies
                actions = st.columns(3)
                try:
                    if actions[0].button("处理完当前菜品后暂停", key=f"{key_prefix}_stop", disabled=not batch.get("active") or bool(batch.get("stop_requested"))):
                        stop_frame_selection_batch(out_dir, batch["batch_id"])
                        _notice(st, "已请求暂停；当前菜品结束后保存结果。")
                    if actions[1].button("继续未完成项", key=f"{key_prefix}_resume", disabled=active or not summary["remaining"]):
                        resume_frame_selection_batch(out_dir, batch["batch_id"], **options)
                        _notice(st, "选图任务已继续。")
                    if actions[2].button("重试失败并继续", key=f"{key_prefix}_retry", disabled=active or not summary["failed"]):
                        resume_frame_selection_batch(out_dir, batch["batch_id"], retry_failed=True, **options)
                        _notice(st, "已重试失败项并继续未完成项。")
                except Exception as exc:
                    st.error(f"操作失败：{exc}")
            if show_open_button and st.button("打开 AI 菜品选图", key=f"{key_prefix}_open"):
                st.session_state["frame_selection_batch_select"] = batch["batch_id"]
                st.session_state["_next_page"] = PAGE_NAME
                st.rerun()
        if config is not None:
            _render_results(st, out_dir, batch, key_prefix)

    live()


def render_frame_selection_page(st, config: UIConfig) -> None:
    st.subheader(PAGE_NAME)
    st.caption("从原视频中选择制作完成的菜品特写，生成独立 PNG 候选。请人工确认与裁剪后再作为菜单封面。")
    notice = st.session_state.pop("_frame_selection_notice", None)
    if notice:
        st.success(notice)
    warning = st.session_state.pop("_frame_selection_warning", None)
    if warning:
        st.warning(warning)
    if not Path(config.out_dir).expanduser().is_dir():
        st.info("请先在侧栏选择已有菜谱输出目录。")
        return
    st.info("使用独立的本地视觉模型，不使用侧栏的文本 LLM。此前 153 秒视频、粗筛 24 帧与精筛 8 帧在本机 CPU 用时约 12 分钟；实际耗时取决于视频、采样参数和硬件。任务在后台继续，可完成当前菜后暂停、续跑或重试失败项。")
    scoped = st.session_state.get("_frame_selection_scope_folders") or []
    if isinstance(scoped, (str, Path)):
        scoped = [scoped]
    scoped = list(dict.fromkeys(str(folder) for folder in scoped))
    scope_signature = tuple(scoped)
    scope_changed = st.session_state.get("_frame_selection_defaults_scope") != scope_signature
    if scope_changed:
        st.session_state["_frame_selection_defaults_scope"] = scope_signature
        if scoped:
            st.session_state["frame_selection_scope"] = "当前选中的菜品"
    for key in ("only_pending", "published_only", "skip_confirmed"):
        widget_key = f"frame_selection_{key}"
        # Streamlit removes widget state after navigating away, while the
        # remembered scope remains. Restore defaults on return as well.
        if scope_changed or widget_key not in st.session_state:
            st.session_state[widget_key] = not bool(scoped)
    try:
        active = any(batch.get("active") for batch in list_frame_selection_batches(config.out_dir))
    except Exception as exc:
        st.error(f"读取任务状态失败：{exc}")
        active = True
    with st.container(border=True):
        if scoped:
            scope = st.radio("选图范围", ["当前选中的菜品", "当前菜谱库"], key="frame_selection_scope")
            st.caption(f"已带入 {len(scoped)} 道菜品；默认允许重新选图，生成候选后仍需人工确认。也可切换为当前菜谱库。")
        else:
            scope = "当前菜谱库"
            st.caption("选图范围：当前菜谱库；默认仅处理可展示、已上架且没有人工确认封面的菜品。")
        limit = int(st.number_input("本轮最多处理菜品数（0 表示全部）", min_value=0, value=10, step=10, key="frame_selection_limit"))
        only_pending = st.checkbox("跳过已有有效选图结果", key="frame_selection_only_pending")
        published_only = st.checkbox("仅处理已上架菜品", key="frame_selection_published_only")
        skip_confirmed = st.checkbox("保留已有人工确认封面的菜品", key="frame_selection_skip_confirmed")
        requirement = st.text_area("选图要求（可附加菜名）", value=DEFAULT_REQUIREMENT, key="frame_selection_requirement")
        with st.expander("本地模型与采样参数"):
            model = st.text_input("本地视觉模型目录（留空使用默认）", key="frame_selection_model")
            python_executable = st.text_input("选图 Python 解释器（留空使用独立环境）", key="frame_selection_python")
            device = st.selectbox("运行设备", ["cpu", "cuda"], key="frame_selection_device")
            st.caption("当前机器只验证了 CPU；其他设备需选图环境和模型支持。")
            threads = int(st.number_input("CPU 线程数", min_value=1, max_value=128, value=16, key="frame_selection_threads"))
            interval = float(st.number_input("粗筛采样间隔（秒）", min_value=0.1, value=2.0, step=0.5, key="frame_selection_interval"))
            coarse_limit = int(st.number_input("粗筛最多帧数", min_value=2, value=24, key="frame_selection_coarse_limit"))
            fine_limit = int(st.number_input("精筛最多帧数", min_value=1, value=8, key="frame_selection_fine_limit"))
            top_k = int(st.number_input("进入精筛的候选数", min_value=1, value=3, key="frame_selection_top_k"))
            fine_fps = float(st.number_input("精筛采样频率（帧/秒）", min_value=0.1, value=5.0, step=1.0, key="frame_selection_fine_fps"))
            keep_video = st.checkbox("保留本次下载的视频", value=False, key="frame_selection_keep_video")
        started = st.button("开始 AI 菜品选图", key="frame_selection_start", type="primary", disabled=active)
    options = {
        "requirement": requirement.strip() or DEFAULT_REQUIREMENT, "model": model.strip() or None,
        "python_executable": python_executable.strip() or None, "device": device, "threads": threads,
        "coarse_limit": coarse_limit, "fine_limit": fine_limit, "top_k": top_k, "interval": interval,
        "fine_fps": fine_fps, "keep_video": keep_video,
    }
    if active:
        st.caption("已有选图任务正在运行，请先等待或暂停当前任务。")
    if started:
        try:
            with st.spinner("正在选择菜品并创建后台选图任务…"):
                batch = start_frame_selection_batch(
                    config.out_dir, limit=limit or None, only_pending=only_pending, published_only=published_only,
                    skip_confirmed=skip_confirmed, folders=scoped if scope == "当前选中的菜品" else None,
                    cookies=config.cookies, **options,
                )
            st.session_state["frame_selection_batch_select"] = batch["batch_id"]
            if batch.get("already_running"):
                st.session_state["_frame_selection_warning"] = "已有选图任务正在运行，本次未新建任务。请先等待或暂停当前任务后再开始。"
                st.rerun()
            _notice(st, "没有需要处理的菜品。" if not batch.get("total") else f"任务 {batch['batch_id']} 已启动，可在下方查看进度。")
        except Exception as exc:
            st.error(f"启动选图任务失败：{exc}")
    render_frame_selection_dashboard(st, config.out_dir, config=config, resume_options=options)
