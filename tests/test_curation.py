from __future__ import annotations

import csv
import json
from pathlib import Path

from bili_recipe_notes.curation import (
    build_curation_review,
    curation_decision_conflicts,
    load_curation_decisions,
    load_curation_review,
    save_curation_decision,
    save_curation_decisions,
    suggested_curation_decision,
)


def _write_recipe(
    root: Path,
    folder_name: str,
    *,
    title: str,
    video_title: str,
    bvid: str,
    duration: float,
    transcript: list[str],
    materials: list[str],
    step_count: int,
    quality_score: int = 70,
) -> None:
    folder = root / folder_name
    folder.mkdir(parents=True)
    (folder / "recipe.json").write_text(
        json.dumps(
            {
                "title": title,
                "source_url": f"https://www.bilibili.com/video/{bvid}",
                "video_title": video_title,
                "ingredients": [{"name": name} for name in materials],
                "seasonings": [],
                "steps": [
                    {"action": f"步骤 {index}", "evidence": f"证据 {index}"}
                    for index in range(step_count)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (folder / "source.json").write_text(
        json.dumps(
            {
                "bvid": bvid,
                "duration": duration,
                "video_title": video_title,
                "source_url": f"https://www.bilibili.com/video/{bvid}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (folder / "transcript.json").write_text(
        json.dumps(
            [{"start": index, "end": index + 1, "text": text} for index, text in enumerate(transcript)],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (folder / "quality.json").write_text(
        json.dumps({"score": quality_score}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_curation_review_recommends_primary_and_flags_short_clip_and_promotion(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    full_transcript = ["鸡肉切丁上浆", "花椒辣椒炒香", "放入鸡丁翻炒", "最后加入花生米"]
    _write_recipe(
        outputs,
        "宫保鸡丁--BV1full",
        title="宫保鸡丁",
        video_title="传统宫保鸡丁完整做法",
        bvid="BV1full",
        duration=480,
        transcript=full_transcript,
        materials=["鸡腿肉", "花生米", "花椒", "辣椒"],
        step_count=8,
    )
    _write_recipe(
        outputs,
        "宫保鸡丁--BV1clip",
        title="宫保鸡丁",
        video_title="百年宫保鸡丁精剪版",
        bvid="BV1clip",
        duration=60,
        transcript=full_transcript[1:3],
        materials=["鸡腿肉", "花椒"],
        step_count=4,
    )
    _write_recipe(
        outputs,
        "宫保鸡丁--BV1promo",
        title="宫保鸡丁",
        video_title="一袋宫保酱汁，年夜饭当甩手掌柜",
        bvid="BV1promo",
        duration=35,
        transcript=["倒入宫保酱汁调味包", "翻炒出锅"],
        materials=["鸡肉", "酱汁调味包"],
        step_count=2,
    )
    _write_recipe(
        outputs,
        "宫保鸡丁--BV1showcase",
        title="宫保鸡丁",
        video_title="国宴大师vs家庭主厨",
        bvid="BV1showcase",
        duration=900,
        transcript=["两位厨师分别制作宫保鸡丁"],
        materials=["鸡腿肉", "花生米", "花椒", "辣椒"],
        step_count=8,
    )

    result = build_curation_review(outputs, tmp_path / "review")

    with result.csv_path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    roles = {row["bvid"]: row["suggested_role"] for row in rows}
    assert roles == {
        "BV1full": "primary_candidate",
        "BV1clip": "short_clip_candidate",
        "BV1promo": "exclude_candidate",
        "BV1showcase": "variant_candidate",
    }
    assert result.duplicate_name_groups == 1
    assert result.review_item_count == 4
    showcase = next(row for row in rows if row["bvid"] == "BV1showcase")
    assert showcase["suggested_content_type"] == "showcase"


def test_curation_review_reports_cautious_similar_name_candidates(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    for title, bvid in (("小吊梨汤", "BV1a"), ("小调梨汤", "BV1b"), ("酸梅汤", "BV1c")):
        _write_recipe(
            outputs,
            f"{title}--{bvid}",
            title=title,
            video_title=title,
            bvid=bvid,
            duration=180,
            transcript=[f"制作{title}"],
            materials=["水"],
            step_count=4,
        )

    result = build_curation_review(outputs, tmp_path / "review")
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))

    assert result.similar_name_pairs == 1
    assert result.review_item_count == 2
    assert result.primary_candidate_count == 0
    with result.csv_path.open(encoding="utf-8-sig", newline="") as file:
        assert {row["suggested_role"] for row in csv.DictReader(file)} == {"name_review_candidate"}
    assert payload["similar_name_candidates"][0]["title"] == "小吊梨汤"
    assert payload["similar_name_candidates"][0]["candidate_title"] == "小调梨汤"


def test_curation_decisions_persist_separately_and_report_primary_conflicts(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    for bvid in ("BV1a", "BV1b"):
        _write_recipe(
            outputs,
            f"红烧肉--{bvid}",
            title="红烧肉",
            video_title=f"红烧肉 {bvid}",
            bvid=bvid,
            duration=300,
            transcript=["五花肉炒糖色后炖熟"],
            materials=["五花肉", "冰糖"],
            step_count=6,
        )
    review_dir = tmp_path / "review"
    build_curation_review(outputs, review_dir)
    report = load_curation_review(review_dir)
    item_ids = [item["item_id"] for item in report["groups"][0]["items"]]

    save_curation_decision(
        review_dir,
        item_ids[0],
        decision="keep_primary",
        final_title="红烧肉",
        review_notes="完整版本",
    )
    save_curation_decisions(
        review_dir,
        [
            {
                "item_id": item_ids[1],
                "decision": "keep_primary",
                "final_title": "红烧肉",
            }
        ],
    )
    decisions = load_curation_decisions(review_dir)

    assert decisions["items"][item_ids[0]]["review_notes"] == "完整版本"
    assert curation_decision_conflicts(report, decisions) == [
        f"最终菜名“红烧肉”存在 2 个主版本：{'、'.join(item_ids)}"
    ]
    assert suggested_curation_decision("short_clip_candidate") == "merge_clip"
