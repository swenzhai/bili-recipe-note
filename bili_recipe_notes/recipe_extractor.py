from __future__ import annotations

from typing import Any, Iterable

try:
    from pydantic import BaseModel
except Exception:  # pragma: no cover
    class BaseModel:  # type: ignore[override]
        def __init__(self, **data: Any):
            for k, v in data.items():
                setattr(self, k, v)

        def model_dump(self) -> dict[str, Any]:
            return self.__dict__

        def model_dump_json(self, indent: int | None = None) -> str:
            import json

            return json.dumps(self.model_dump(), ensure_ascii=False, indent=indent)


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class RecipeIngredient(BaseModel):
    name: str
    amount: str | None = None
    note: str | None = None


class RecipeStep(BaseModel):
    title: str
    start_time: float
    end_time: float | None = None
    action: str
    heat: str | None = None
    duration: str | None = None
    tips: str | None = None
    screenshot_path: str | None = None


class Recipe(BaseModel):
    title: str
    source_url: str
    video_title: str | None = None
    uploader: str | None = None
    ingredients: list[RecipeIngredient]
    seasonings: list[RecipeIngredient]
    tools: list[str]
    steps: list[RecipeStep]
    summary_tips: list[str]
    uncertain_points: list[str]


STEP_KEYWORDS = ["先", "然后", "接着", "再", "最后", "下锅", "焯水", "翻炒", "炖", "煮", "蒸", "烤", "出锅"]
INGREDIENT_KEYWORDS = ["鸡肉", "牛肉", "猪肉", "鱼", "虾", "鸡蛋", "番茄", "土豆", "洋葱", "青椒", "豆腐", "米饭", "面条"]
SEASONING_KEYWORDS = ["盐", "糖", "生抽", "老抽", "料酒", "蚝油", "醋", "胡椒粉", "辣椒", "花椒", "姜", "蒜", "葱"]
TOOL_KEYWORDS = ["锅", "炒锅", "蒸锅", "烤箱", "空气炸锅"]


def _contains_keyword(text: str, keywords: Iterable[str]) -> bool:
    return any(k in text for k in keywords)


def extract_recipe_rule_based(transcript: list[TranscriptSegment], metadata: dict) -> Recipe:
    ingredients = [RecipeIngredient(name=i, amount=None, note="未说明") for i in INGREDIENT_KEYWORDS if any(i in s.text for s in transcript)]
    seasonings = [RecipeIngredient(name=i, amount=None, note="未说明") for i in SEASONING_KEYWORDS if any(i in s.text for s in transcript)]
    tools_set = {k for k in TOOL_KEYWORDS if any(k in s.text for s in transcript)}
    if not tools_set:
        tools_set.add("炒锅")

    steps: list[RecipeStep] = []
    for seg in transcript:
        if _contains_keyword(seg.text, STEP_KEYWORDS) or not steps:
            idx = len(steps) + 1
            steps.append(RecipeStep(title=f"步骤{idx}", start_time=seg.start, end_time=seg.end, action=seg.text.strip()))
        else:
            steps[-1].action = f"{steps[-1].action} {seg.text.strip()}".strip()
            steps[-1].end_time = seg.end

    return Recipe(
        title=metadata.get("recipe_title") or metadata.get("video_title") or "未命名菜谱",
        source_url=metadata.get("source_url", ""),
        video_title=metadata.get("video_title"),
        uploader=metadata.get("uploader"),
        ingredients=ingredients,
        seasonings=seasonings,
        tools=sorted(tools_set),
        steps=steps,
        summary_tips=["用量可能未在视频中明确说明，建议边看边记。"],
        uncertain_points=[
            *([] if ingredients else ["未能稳定识别食材，请手动补充"]),
            *([] if seasonings else ["未能稳定识别调料，请手动补充"]),
        ],
    )


def extract_recipe_with_llm(transcript: list[TranscriptSegment], metadata: dict) -> Recipe:
    raise NotImplementedError("LLM extraction is not implemented in MVP yet.")
