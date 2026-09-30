"""Human decisions kept alongside AI reviews, without editing recipe sources."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .file_cache import read_json_cached
from .review_policy import (
    complete_manual_ai_review,
    set_ai_review_classification,
    set_ai_review_item_decision,
)

CLASSIFICATION_LABELS = {
    "recipe": "菜谱", "technique": "烹饪技巧", "non_recipe": "非菜谱", "uncertain": "无法确认",
}
SECTION_LABELS = {"ingredients": "主料", "seasonings": "调料", "steps": "步骤"}


def _notice(st, message: str) -> None:
    st.session_state["_ai_review_notice"] = message
    st.rerun()


def video_evidence_url(source_url: str, seconds: float | None = None) -> str | None:
    try:
        parsed = urlsplit(source_url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if seconds is None or not math.isfinite(seconds):
        return source_url
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "t"]
    query.append(("t", str(max(0, int(seconds)))))
    return urlunsplit(parsed._replace(query=urlencode(query)))


def review_items(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for section in SECTION_LABELS:
        values = recipe.get(section)
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                continue
            result.append({
                "id": f"{section}:{index}", "value": value,
                "label": f"{SECTION_LABELS[section]} {index + 1} · {value.get('name') or value.get('title') or value.get('action') or '未命名'}",
            })
    return result


def _transcript(folder: Path) -> tuple[list[dict[str, Any]], str | None]:
    try:
        value = read_json_cached(folder / "transcript.json")
        if not isinstance(value, list) or any(not isinstance(segment, dict) for segment in value):
            raise ValueError("字幕不是有效的片段数组")
        return value, None
    except (OSError, UnicodeError, ValueError) as exc:
        return [], str(exc)


def render_review_workbench(st, selected: dict[str, Any], *, next_folder: str | None) -> None:
    folder = selected["folder"]
    recipe = selected["recipe"]
    state = selected["review_state"]
    stored = state.get("record") or {}
    review = state.get("ai_review") or {}
    identity = hashlib.sha256(str(folder).encode()).hexdigest()[:12]
    content_key = str(state.get("content_fingerprint") or "unavailable")[:12]
    prefix = f"ai_manual_{identity}_{content_key}"
    st.markdown("#### 人工核对")
    st.caption("人工分类、逐项意见与完成状态单独保存，不改写菜谱或字幕。")
    error_key = f"{prefix}_error"
    if st.session_state.get(error_key):
        st.error(st.session_state.pop(error_key))
    if state.get("manual_completed"):
        st.success("当前内容已完成人工确认。")
    elif state.get("needs_confirmation"):
        st.info("仍有内容需要人工核对。")

    source_url = str(recipe.get("source_url") or "")
    video_url = video_evidence_url(source_url)
    if video_url:
        st.link_button("打开原视频核对", video_url)

    classifications = ["", "recipe", "technique", "non_recipe", "uncertain"]
    current_classification = stored.get("classification_override") or ""
    if current_classification not in classifications:
        current_classification = ""

    def save_classification():
        try:
            set_ai_review_classification(
                folder, st.session_state[f"{prefix}_classification"] or None,
                comment=st.session_state[f"{prefix}_classification_comment"],
            )
        except Exception as exc:
            st.session_state[error_key] = f"保存分类失败：{exc}"
        else:
            st.session_state["_ai_review_notice"] = "人工分类已保存。"

    with st.form(f"{prefix}_classification_form"):
        st.selectbox(
            "人工内容分类", classifications, index=classifications.index(current_classification),
            format_func=lambda value: CLASSIFICATION_LABELS.get(value, "沿用 AI 判断"),
            key=f"{prefix}_classification",
        )
        st.text_input(
            "分类依据", value=str(stored.get("classification_override_comment") or ""), key=f"{prefix}_classification_comment",
        )
        st.form_submit_button("保存人工分类", on_click=save_classification)

    items = review_items(recipe)
    decisions = state.get("item_decisions") or {}
    checks = {item["id"]: item for item in review.get("item_checks", [])}
    transcript, transcript_error = _transcript(folder)
    if transcript_error:
        st.warning(f"字幕暂不可用，可打开原视频核对：{transcript_error}")

    if items:
        by_id = {item["id"]: item for item in items}
        item_ids = list(by_id)
        unresolved = [key for key in item_ids if decisions.get(key, {}).get("verdict") != "supported"]
        preferred = st.session_state.pop(f"{prefix}_next_item", None)
        item_key = f"{prefix}_item"
        if preferred in by_id:
            st.session_state[item_key] = preferred
        if st.session_state.get(item_key) not in by_id:
            st.session_state[item_key] = (unresolved or item_ids)[0]
        item_id = st.selectbox(
            "核对用料或步骤", item_ids, key=item_key,
            format_func=lambda key: f"{'已确认' if decisions.get(key, {}).get('verdict') == 'supported' else '待确认'} · {by_id[key]['label']}",
        )
        current = by_id[item_id]
        check = checks.get(item_id, {})
        decision = decisions.get(item_id, {})
        saved_evidence = decision.get("evidence") or []
        saved_quote = str(saved_evidence[0].get("text") or "") if saved_evidence and isinstance(saved_evidence[0], dict) else ""
        columns = st.columns([1, 1.3, 1])
        with columns[0]:
            st.markdown("**生成版原文**")
            st.json(current["value"])
        with columns[1]:
            st.markdown("**字幕证据**")
            query = st.text_input("检索字幕", key=f"{prefix}_{item_id}_query", placeholder="食材、火候或操作关键词")
            found = [segment for segment in transcript if not query.strip() or query.strip().casefold() in str(segment.get("text") or "").casefold()]
            page_count = max(1, (len(found) + 11) // 12)
            page_key = f"{prefix}_{item_id}_transcript_page"
            if st.session_state.get(page_key, 1) > page_count:
                st.session_state[page_key] = 1
            page = int(st.number_input("字幕页", min_value=1, max_value=page_count, value=1, step=1, key=page_key))
            st.caption(f"匹配 {len(found)} 段，共 {page_count} 页")
            for segment in found[(page - 1) * 12:page * 12]:
                timestamp = segment.get("start")
                text = str(segment.get("text") or "")
                st.write(f"{timestamp:g} 秒 · {text}" if isinstance(timestamp, (int, float)) else text)
                if isinstance(timestamp, (int, float)) and video_url:
                    st.link_button(f"跳转至 {timestamp:g} 秒", video_evidence_url(source_url, timestamp))
            if not found:
                st.caption("没有匹配字幕；不能凭经验补齐未说明的信息。")
        with columns[2]:
            st.markdown("**审核意见**")
            st.write(check.get("reason") or "此项尚无当前版本的 AI 审核意见。")
            if check:
                st.caption("AI：有字幕依据" if check.get("verdict") == "supported" else "AI：待核对")
            if decision:
                st.write("人工备注：" + str(decision.get("comment") or "无"))

        def save_item():
            try:
                quote = st.session_state[f"{prefix}_{item_id}_quote"].strip()
                set_ai_review_item_decision(
                    folder, item_id, st.session_state[f"{prefix}_{item_id}_verdict"],
                    comment=st.session_state[f"{prefix}_{item_id}_comment"],
                    evidence=saved_evidence if quote and quote == saved_quote else ([{"text": quote}] if quote else []),
                    expected_content_fingerprint=state.get("content_fingerprint"),
                )
            except Exception as exc:
                st.session_state[error_key] = f"保存核对意见失败：{exc}"
            else:
                remaining = [key for key in unresolved if key != item_id]
                if remaining:
                    st.session_state[f"{prefix}_next_item"] = remaining[0]
                st.session_state["_ai_review_notice"] = "人工核对意见已保存。"

        with st.form(f"{prefix}_decision_form"):
            verdicts = ["supported", "needs_review"]
            st.radio(
                "人工核对结论", verdicts,
                index=verdicts.index(decision.get("verdict")) if decision.get("verdict") in verdicts else 1,
                format_func={"supported": "已核实，有依据", "needs_review": "仍有疑问"}.get,
                horizontal=True, key=f"{prefix}_{item_id}_verdict",
            )
            st.text_area("核对说明", value=str(decision.get("comment") or ""), key=f"{prefix}_{item_id}_comment")
            st.text_area("引用字幕原文（可选）", value=saved_quote, key=f"{prefix}_{item_id}_quote", help="如填写，将核对是否存在于字幕。通过视频核对时，请在说明中记录依据。")
            st.form_submit_button("保存并核对下一项", on_click=save_item)
        st.caption(f"已确认 {len(item_ids) - len(unresolved)} / {len(item_ids)} 项")
    else:
        st.caption("这条内容没有用料或步骤，可先核实内容分类。")

    actions = st.columns(2)
    if actions[0].button("完成人工确认", key=f"{prefix}_complete", disabled=bool(state.get("manual_completed"))):
        try:
            complete_manual_ai_review(folder, expected_content_fingerprint=state.get("content_fingerprint"))
        except Exception as exc:
            st.error(f"暂不能完成：{exc}")
        else:
            if next_folder:
                st.session_state["_ai_review_focus_folder"] = next_folder
                st.session_state["_ai_review_next_filter"] = "待人工确认"
                st.session_state["_ai_review_clear_search"] = True
            _notice(st, "已完成人工确认。" + ("已进入下一条待确认内容。" if next_folder else ""))
    if actions[1].button("下一条待人工确认", key=f"{prefix}_next_recipe", disabled=next_folder is None):
        st.session_state["_ai_review_focus_folder"] = next_folder
        st.session_state["_ai_review_next_filter"] = "待人工确认"
        st.session_state["_ai_review_clear_search"] = True
        st.rerun()
