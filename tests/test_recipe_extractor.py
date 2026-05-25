from bili_recipe_notes.recipe_extractor import TranscriptSegment, extract_recipe_rule_based
from bili_recipe_notes.subtitle import parse_srt, parse_vtt


def test_parse_srt_and_vtt() -> None:
    srt = """1\n00:00:01,000 --> 00:00:03,000\n先准备鸡蛋和番茄\n\n2\n00:00:03,000 --> 00:00:05,000\n然后下锅翻炒加盐\n"""
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n先准备鸡蛋和番茄\n"
    srt_seg = parse_srt(srt)
    vtt_seg = parse_vtt(vtt)
    assert len(srt_seg) == 2
    assert srt_seg[0].start == 1.0
    assert len(vtt_seg) == 1


def test_extract_recipe_rule_based() -> None:
    transcript = [
        TranscriptSegment(start=0, end=2, text="先准备鸡蛋和番茄"),
        TranscriptSegment(start=3, end=5, text="然后下锅翻炒加盐和葱"),
    ]
    recipe = extract_recipe_rule_based(
        transcript,
        {"source_url": "u", "video_title": "番茄炒蛋", "uploader": "up"},
    )
    assert recipe.title == "番茄炒蛋"
    assert any(i.name == "鸡蛋" for i in recipe.ingredients)
    assert any(s.name == "盐" for s in recipe.seasonings)
    assert len(recipe.steps) >= 2
