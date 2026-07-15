from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

from .recipe_extractor import Recipe, RecipeIngredient


NUMBER_PATTERN = r"(?:\d+\s+\d+\/\d+|\d+\/\d+|\d+(?:\.\d+)?|[\u00bc\u00bd\u00be\u2153\u2154\u215b\u215c\u215d\u215e])"
AMOUNT_RE = re.compile(
    rf"^\s*(?P<prefix>约|大约|约为|约需|≈|~)?\s*"
    rf"(?P<low>{NUMBER_PATTERN})"
    rf"(?:\s*(?P<separator>[-–—~～至到])\s*(?P<high>{NUMBER_PATTERN}))?"
    r"\s*(?P<unit>[^\d]*)\s*$",
    re.IGNORECASE,
)
SERVING_RANGE_RE = re.compile(
    rf"(?P<low>{NUMBER_PATTERN})(?:\s*[-–—~～至到]\s*(?P<high>{NUMBER_PATTERN}))?"
)

UNICODE_FRACTIONS = {
    "¼": 1 / 4,
    "½": 1 / 2,
    "¾": 3 / 4,
    "⅓": 1 / 3,
    "⅔": 2 / 3,
    "⅛": 1 / 8,
    "⅜": 3 / 8,
    "⅝": 5 / 8,
    "⅞": 7 / 8,
}


@dataclass(frozen=True)
class UnitDefinition:
    dimension: str
    base_multiplier: float


UNIT_DEFINITIONS = {
    "kg": UnitDefinition("mass", 1000.0),
    "千克": UnitDefinition("mass", 1000.0),
    "公斤": UnitDefinition("mass", 1000.0),
    "g": UnitDefinition("mass", 1.0),
    "克": UnitDefinition("mass", 1.0),
    "mg": UnitDefinition("mass", 0.001),
    "毫克": UnitDefinition("mass", 0.001),
    "斤": UnitDefinition("mass", 500.0),
    "两": UnitDefinition("mass", 50.0),
    "l": UnitDefinition("volume", 1000.0),
    "升": UnitDefinition("volume", 1000.0),
    "ml": UnitDefinition("volume", 1.0),
    "毫升": UnitDefinition("volume", 1.0),
    "汤匙": UnitDefinition("volume", 15.0),
    "大勺": UnitDefinition("volume", 15.0),
    "tbsp": UnitDefinition("volume", 15.0),
    "茶匙": UnitDefinition("volume", 5.0),
    "小勺": UnitDefinition("volume", 5.0),
    "tsp": UnitDefinition("volume", 5.0),
    "杯": UnitDefinition("volume", 240.0),
}


@dataclass(frozen=True)
class AmountConversion:
    text: str
    converted: bool
    reason: str = ""


@dataclass(frozen=True)
class ShoppingItem:
    name: str
    amount: str
    note: str = ""
    category: str = "主料"
    converted: bool = False

    @property
    def label(self) -> str:
        text = f"{self.name}：{self.amount}"
        if self.note and self.note != "未说明":
            text += f"（{self.note}）"
        return text


def _parse_number(value: str) -> float:
    text = value.strip()
    if text in UNICODE_FRACTIONS:
        return UNICODE_FRACTIONS[text]
    if " " in text and "/" in text:
        whole, fraction = text.split(None, 1)
        numerator, denominator = fraction.split("/", 1)
        return float(whole) + float(numerator) / float(denominator)
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        denominator_value = float(denominator)
        if denominator_value == 0:
            raise ValueError("amount denominator cannot be zero")
        return float(numerator) / denominator_value
    return float(text)


def _format_number(value: float) -> str:
    if math.isclose(value, round(value), rel_tol=0, abs_tol=1e-9):
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _normalized_unit(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def parse_servings(value: str | None) -> float | None:
    """Parse a serving label; a range uses its midpoint as the scaling baseline."""

    text = str(value or "").strip()
    if not text:
        return None
    match = SERVING_RANGE_RE.search(text)
    if not match:
        return None
    try:
        low = _parse_number(match.group("low"))
        high_text = match.group("high")
        high = _parse_number(high_text) if high_text else low
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if low <= 0 or high <= 0:
        return None
    return (low + high) / 2


def serving_scale(servings: str | None, target_servings: float) -> float:
    baseline = parse_servings(servings)
    if baseline is None:
        raise ValueError("原菜谱份量无法识别，请直接设置用量倍率")
    if not math.isfinite(target_servings) or target_servings <= 0:
        raise ValueError("目标份量必须大于 0")
    return target_servings / baseline


def _metric_amount(value: float, unit: UnitDefinition) -> tuple[float, str]:
    base_value = value * unit.base_multiplier
    if unit.dimension == "mass":
        if base_value >= 1000:
            return base_value / 1000, "千克"
        if 0 < base_value < 1:
            return base_value * 1000, "毫克"
        return base_value, "克"
    if base_value >= 1000:
        return base_value / 1000, "升"
    return base_value, "毫升"


def convert_amount(amount: str | None, factor: float = 1.0, unit_system: str = "original") -> AmountConversion:
    """Scale one amount and optionally normalize known mass/volume units to metric."""

    original = str(amount or "").strip()
    if not original:
        return AmountConversion("用量待确认", False, "未提供用量")
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("用量倍率必须大于 0")
    if unit_system not in {"original", "metric"}:
        raise ValueError(f"unsupported unit system: {unit_system}")

    match = AMOUNT_RE.fullmatch(original)
    if not match:
        return AmountConversion(original, False, "用量不是可安全换算的数字格式")
    try:
        low = _parse_number(match.group("low")) * factor
        high_text = match.group("high")
        high = _parse_number(high_text) * factor if high_text else None
    except (TypeError, ValueError, ZeroDivisionError):
        return AmountConversion(original, False, "数字格式无法识别")

    prefix = match.group("prefix") or ""
    raw_unit = (match.group("unit") or "").strip()
    normalized_unit = _normalized_unit(raw_unit)
    definition = UNIT_DEFINITIONS.get(normalized_unit)
    display_unit = raw_unit
    if unit_system == "metric" and definition:
        low, display_unit = _metric_amount(low, definition)
        if high is not None:
            high, high_unit = _metric_amount(high, definition)
            if high_unit != display_unit:
                # Keep both range endpoints in one base unit around promotion boundaries.
                if definition.dimension == "mass":
                    low = low * (1000 if display_unit == "千克" else 0.001 if display_unit == "毫克" else 1)
                    high = high * (1000 if high_unit == "千克" else 0.001 if high_unit == "毫克" else 1)
                    display_unit = "克"
                else:
                    low = low * (1000 if display_unit == "升" else 1)
                    high = high * (1000 if high_unit == "升" else 1)
                    display_unit = "毫升"

    quantity = _format_number(low)
    if high is not None:
        quantity += f"–{_format_number(high)}"
    converted = not math.isclose(factor, 1.0) or bool(unit_system == "metric" and definition)
    return AmountConversion(f"{prefix}{quantity}{display_unit}", converted)


def _structured_shopping_items(
    items: Iterable[RecipeIngredient],
    *,
    category: str,
    factor: float,
    unit_system: str,
) -> list[ShoppingItem]:
    results: list[ShoppingItem] = []
    for item in items:
        name = item.name.strip()
        if not name:
            continue
        converted = convert_amount(item.amount, factor=factor, unit_system=unit_system)
        results.append(
            ShoppingItem(
                name=name,
                amount=converted.text,
                note=str(item.note or "").strip(),
                category=category,
                converted=converted.converted,
            )
        )
    return results


def _manual_shopping_item(text: str, *, factor: float, unit_system: str) -> ShoppingItem:
    colon_parts = re.split(r"\s*[：:]\s*", text, maxsplit=1)
    if len(colon_parts) == 2 and colon_parts[0].strip():
        name, amount = colon_parts[0].strip(), colon_parts[1].strip()
    else:
        amount_match = re.search(NUMBER_PATTERN, text)
        if amount_match and text[: amount_match.start()].strip(" ：:-"):
            name = text[: amount_match.start()].strip(" ：:-")
            amount = text[amount_match.start() :].strip()
        else:
            return ShoppingItem(name=text, amount="按需", category="其他")
    converted = convert_amount(amount, factor=factor, unit_system=unit_system)
    return ShoppingItem(
        name=name,
        amount=converted.text,
        category="其他",
        converted=converted.converted,
    )


def build_shopping_list(
    recipe: Recipe,
    *,
    factor: float = 1.0,
    unit_system: str = "original",
) -> list[ShoppingItem]:
    """Build a deduplicated checklist, preferring structured ingredient amounts."""

    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("用量倍率必须大于 0")
    structured = [
        *_structured_shopping_items(
            recipe.ingredients,
            category="主料",
            factor=factor,
            unit_system=unit_system,
        ),
        *_structured_shopping_items(
            recipe.seasonings,
            category="调料",
            factor=factor,
            unit_system=unit_system,
        ),
    ]
    results: list[ShoppingItem] = []
    seen_structured: set[tuple[str, str, str]] = set()
    for item in structured:
        identity = (item.name.casefold(), item.amount.casefold(), item.note.casefold())
        if identity in seen_structured:
            continue
        seen_structured.add(identity)
        results.append(item)
    known_names = {item.name.casefold() for item in results}
    for raw_item in recipe.shopping_list:
        text = str(raw_item).strip()
        if not text or any(name and name in text.casefold() for name in known_names):
            continue
        results.append(_manual_shopping_item(text, factor=factor, unit_system=unit_system))
    return results


def shopping_list_markdown(recipe: Recipe, items: list[ShoppingItem], factor: float) -> str:
    lines = [f"# {recipe.title}购物清单", "", f"- 用量倍率：{_format_number(factor)}×"]
    if recipe.servings:
        lines.append(f"- 原菜谱份量：{recipe.servings}")
    lines.append("")
    current_category = ""
    for item in items:
        if item.category != current_category:
            current_category = item.category
            lines.extend([f"## {current_category}", ""])
        lines.append(f"- [ ] {item.label}")
    if not items:
        lines.append("- [ ] 暂无可用配料，请先在菜谱中补充")
    return "\n".join(lines).strip() + "\n"
