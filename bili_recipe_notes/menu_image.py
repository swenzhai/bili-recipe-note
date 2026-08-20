from __future__ import annotations

import hashlib
import io
import math
import re
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps


MenuImageFormat = Literal["share", "print"]
AssetResolver = Callable[[str], tuple[Path, str] | None]

INK = "#2A201B"
MUTED = "#766A61"
PAPER = "#F6F0E5"
CARD = "#FFFDF8"
RED = "#9D3D31"
DARK_RED = "#65271F"
GOLD = "#C69A52"
LINE = "#DED2C2"

CATEGORY_ORDER = ("主厨推荐", "海鲜", "面条", "主食", "糕点", "汤羹", "饮品", "小吃", "素菜", "肉类", "主菜", "其他")


@dataclass(frozen=True)
class MenuImageOptions:
    title: str = "Chef Zhai · 本周菜单"
    subtitle: str = "主厨精选 · 新鲜现做"
    footer: str = "请直接回复菜名预订 · 菜品以当日供应为准"
    image_format: MenuImageFormat = "share"
    include_photos: bool = True


@dataclass(frozen=True)
class MenuImageFile:
    name: str
    content: bytes
    mime_type: str
    width: int
    height: int


@dataclass(frozen=True)
class MenuImageResult:
    files: tuple[MenuImageFile, ...]
    recipe_count: int
    category_count: int


_FONT_CANDIDATES = {
    "sans": (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ),
    "bold": (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ),
    "serif": (
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/System/Library/Fonts/Songti.ttc",
        "C:/Windows/Fonts/simsun.ttc",
    ),
}


@lru_cache(maxsize=96)
def _font(size: int, style: str = "sans") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES.get(style, _FONT_CANDIDATES["sans"]):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default(size=size)


def menu_category(recipe: dict[str, Any]) -> str:
    category = str(recipe.get("category") or "")
    title = str(recipe.get("title") or "")
    ingredients = recipe.get("ingredients") if isinstance(recipe.get("ingredients"), list) else []
    tags = recipe.get("tags") if isinstance(recipe.get("tags"), list) else []
    text = " ".join(
        [title, category, *(str(item) for item in tags), *(str(item.get("name") or "") for item in ingredients if isinstance(item, dict))]
    )
    seafood = re.compile(r"鱼|虾|蟹|贝|蚝|螺|鱿|鳝|海鲜|海胆|鲍|瑶柱|带子|龙虾|多宝鱼|生蚝")
    meat = re.compile(r"猪|牛|羊|鸡|鸭|鹅|鸽|排骨|肉|叉烧|肥肠|生肠|牛杂|牛腩|牛展|乳鸽|猪手")
    noodle = re.compile(r"炒面|汤面|拌面|伊面|面条|河粉|米粉|粉面|云吞|粉皮|牛河")
    staple = re.compile(r"炒饭|煲仔饭|牛肉饭|米饭|砂锅粥|白粥|云吞|炒面|汤面|拌面|伊面|面条|河粉|米粉|牛河")
    pastry = re.compile(r"蛋挞|拿破仑酥|糖沙翁|玉米糕|沙琪玛|糕点|点心|酥皮")
    vegetable = re.compile(r"菜心|通菜|油麦菜|土豆丝|豆芽|韭菜|四季豆|菜花|凉瓜|节瓜|豆腐|茄子|青菜|蔬菜")
    if noodle.search(text) and "面粉" not in title:
        return "面条"
    if category == "主食" or staple.search(title):
        return "主食"
    if category == "糕点" or pastry.search(text):
        return "糕点"
    if category == "汤羹" or re.search(r"汤|羹|炖汤|糖水", title):
        return "汤羹"
    if category == "饮品":
        return "饮品"
    if category == "小吃":
        return "小吃"
    if seafood.search(text):
        return "海鲜"
    if category == "中餐" and vegetable.search(text) and not meat.search(text):
        return "素菜"
    if meat.search(text):
        return "肉类"
    if category == "中餐":
        return "主菜"
    return "其他"


def _group_recipes(recipes: Iterable[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict((category, []) for category in CATEGORY_ORDER)
    for recipe in recipes:
        grouped[menu_category(recipe)].append(recipe)
    for items in grouped.values():
        items.sort(key=lambda item: (not bool(item.get("recommended")), str(item.get("title") or "")))
    return [(category, items) for category, items in grouped.items() if items]


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
    return float(draw.textlength(text, font=font))


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
    clean = " ".join(str(text).split())
    if _text_width(draw, clean, font) <= width:
        return clean
    while clean and _text_width(draw, f"{clean}…", font) > width:
        clean = clean[:-1]
    return f"{clean}…" if clean else "…"


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int, lines: int) -> list[str]:
    remaining = " ".join(str(text).split())
    result: list[str] = []
    while remaining and len(result) < lines:
        if _text_width(draw, remaining, font) <= width:
            result.append(remaining)
            remaining = ""
            break
        end = 1
        while end <= len(remaining) and _text_width(draw, remaining[:end], font) <= width:
            end += 1
        split = max(1, end - 1)
        result.append(remaining[:split].rstrip())
        remaining = remaining[split:].lstrip()
    if remaining and result:
        result[-1] = _fit_text(draw, f"{result[-1]}{remaining}", font, width)
    return result


def _draw_header(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    options: MenuImageOptions,
    recipe_count: int,
    *,
    height: int,
    compact: bool = False,
) -> None:
    width = image.width
    draw.rectangle((0, 0, width, height), fill=DARK_RED)
    draw.ellipse((width - 370, -190, width + 150, 330), outline="#8A4E42", width=3)
    draw.ellipse((width - 270, -90, width + 45, 225), outline="#8A4E42", width=2)
    margin = 110 if width > 1500 else 52
    brand_size = 29 if width > 1500 else 20
    draw.text((margin, 54 if compact else 70), "C H E F   Z H A I", font=_font(brand_size, "bold"), fill=GOLD)
    title_size = 78 if width > 1500 else 52
    title_y = 100 if compact else (130 if width > 1500 else 120)
    max_title_width = width - margin * 2 - (250 if width > 1500 else 80)
    title = _fit_text(draw, options.title or "Chef Zhai 常用菜单", _font(title_size, "serif"), max_title_width)
    draw.text((margin, title_y), title, font=_font(title_size, "serif"), fill="#FFF8EC")
    if not compact:
        subtitle_size = 31 if width > 1500 else 26
        subtitle = _fit_text(draw, options.subtitle, _font(subtitle_size), max_title_width)
        draw.text((margin, title_y + title_size + 28), subtitle, font=_font(subtitle_size), fill="#E7D3C0")
        badge_text = f"当季常用菜单  ·  {recipe_count} 道"
        badge_font = _font(28 if width > 1500 else 21, "bold")
        badge_width = int(_text_width(draw, badge_text, badge_font)) + 46
        badge_y = height - (90 if width > 1500 else 72)
        draw.rounded_rectangle((margin, badge_y, margin + badge_width, badge_y + 48), radius=24, fill=GOLD)
        draw.text((margin + 23, badge_y + 8), badge_text, font=badge_font, fill=DARK_RED)


def _draw_category_header(draw: ImageDraw.ImageDraw, category: str, y: int, width: int, margin: int, *, large: bool) -> int:
    font = _font(42 if large else 30, "serif")
    line_y = y + (27 if large else 19)
    draw.rounded_rectangle((margin, line_y - 4, margin + (54 if large else 36), line_y + 4), radius=4, fill=RED)
    draw.text((margin + (76 if large else 52), y), category, font=font, fill=INK)
    return y + (76 if large else 58)


def _recipe_digest(recipe: dict[str, Any]) -> str:
    if recipe.get("cover_image_status") == "no_suitable":
        return ""
    if recipe.get("cover_image_sha256"):
        return str(recipe["cover_image_sha256"])
    for asset in recipe.get("assets") or []:
        if isinstance(asset, dict) and asset.get("kind") == "recipe_cover" and asset.get("sha256"):
            return str(asset["sha256"])
    for step in reversed(recipe.get("steps") or []):
        if isinstance(step, dict) and step.get("image_sha256"):
            return str(step["image_sha256"])
    for asset in recipe.get("assets") or []:
        if isinstance(asset, dict) and asset.get("sha256"):
            return str(asset["sha256"])
    return ""


def _fallback_photo(size: tuple[int, int], title: str) -> Image.Image:
    digest = hashlib.sha256(title.encode("utf-8")).digest()
    palette = ((158, 66, 50), (94, 106, 72), (174, 116, 57), (94, 70, 63), (65, 103, 111))
    base = palette[digest[0] % len(palette)]
    image = Image.new("RGB", size, base)
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.ellipse((width * 0.55, -height * 0.4, width * 1.18, height * 0.55), outline=tuple(min(255, channel + 30) for channel in base), width=max(2, width // 120))
    draw.ellipse((-width * 0.2, height * 0.48, width * 0.48, height * 1.25), outline=tuple(max(0, channel - 22) for channel in base), width=max(2, width // 120))
    initial = title[:1] or "菜"
    font = _font(max(32, min(size) // 3), "serif")
    bounds = draw.textbbox((0, 0), initial, font=font)
    draw.text(((width - (bounds[2] - bounds[0])) / 2, (height - (bounds[3] - bounds[1])) / 2 - bounds[1]), initial, font=font, fill="#FFF2DD")
    return image


def _recipe_photo(
    recipe: dict[str, Any],
    size: tuple[int, int],
    resolver: AssetResolver,
    *,
    include_photos: bool,
) -> Image.Image:
    title = str(recipe.get("title") or "菜")
    if include_photos:
        digest = _recipe_digest(recipe)
        found = resolver(digest) if digest else None
        if found:
            try:
                with Image.open(found[0]) as source:
                    return ImageOps.fit(source.convert("RGB"), size, method=Image.Resampling.LANCZOS)
            except (OSError, ValueError):
                pass
    return _fallback_photo(size, title)


def _draw_share_card(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    recipe: dict[str, Any],
    box: tuple[int, int, int, int],
    resolver: AssetResolver,
    include_photos: bool,
) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=18, fill=CARD, outline=LINE, width=2)
    photo_width = 166
    photo = _recipe_photo(recipe, (photo_width, bottom - top), resolver, include_photos=include_photos)
    mask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, photo.width + 18, photo.height), radius=18, fill=255)
    image.paste(photo, (left, top), mask)
    text_left = left + photo_width + 18
    title_font = _font(27, "bold")
    title_lines = _wrap_text(
        draw, str(recipe.get("title") or "未命名菜品"), title_font, right - text_left - 16, 2
    )
    for line_index, line in enumerate(title_lines):
        draw.text((text_left, top + 27 + line_index * 38), line, font=title_font, fill=INK)
    meta = " · ".join(part for part in (menu_category(recipe), str(recipe.get("total_time") or "")) if part)
    draw.text((text_left, top + 111), _fit_text(draw, meta, _font(19), right - text_left - 16), font=_font(19), fill=MUTED)
    if recipe.get("recommended"):
        label = "CHEF 推荐"
        label_font = _font(17, "bold")
        label_width = int(_text_width(draw, label, label_font)) + 24
        draw.rounded_rectangle((text_left, bottom - 45, text_left + label_width, bottom - 14), radius=15, fill=GOLD)
        draw.text((text_left + 12, bottom - 40), label, font=label_font, fill=DARK_RED)


def _draw_print_card(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    recipe: dict[str, Any],
    box: tuple[int, int, int, int],
    resolver: AssetResolver,
    include_photos: bool,
) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=26, fill=CARD, outline=LINE, width=3)
    photo_height = 205
    photo = _recipe_photo(recipe, (right - left, photo_height), resolver, include_photos=include_photos)
    mask = Image.new("L", photo.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, photo.width, photo.height + 26), radius=26, fill=255)
    image.paste(photo, (left, top), mask)
    if recipe.get("recommended"):
        label_font = _font(25, "bold")
        draw.rounded_rectangle((left + 18, top + 18, left + 190, top + 62), radius=22, fill=GOLD)
        draw.text((left + 39, top + 24), "CHEF 推荐", font=label_font, fill=DARK_RED)
    title = _fit_text(draw, str(recipe.get("title") or "未命名菜品"), _font(42, "bold"), right - left - 42)
    draw.text((left + 22, top + photo_height + 24), title, font=_font(42, "bold"), fill=INK)
    meta = " · ".join(part for part in (menu_category(recipe), str(recipe.get("total_time") or "")) if part)
    draw.text((left + 22, bottom - 50), _fit_text(draw, meta, _font(25), right - left - 42), font=_font(25), fill=MUTED)


def _draw_footer(draw: ImageDraw.ImageDraw, width: int, height: int, text: str, *, page: int | None = None) -> None:
    margin = 110 if width > 1500 else 52
    y = height - (118 if width > 1500 else 105)
    draw.line((margin, y, width - margin, y), fill=LINE, width=2)
    footer_font = _font(25 if width > 1500 else 20)
    draw.text((margin, y + 29), _fit_text(draw, text, footer_font, width - margin * 2 - 100), font=footer_font, fill=MUTED)
    if page is not None:
        page_text = f"{page:02d}"
        draw.text((width - margin - int(_text_width(draw, page_text, footer_font)), y + 29), page_text, font=footer_font, fill=RED)


def _jpeg_file(image: Image.Image, name: str, *, dpi: int) -> MenuImageFile:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=91, optimize=True, progressive=True, dpi=(dpi, dpi), subsampling=0)
    return MenuImageFile(name=name, content=buffer.getvalue(), mime_type="image/jpeg", width=image.width, height=image.height)


def _share_image(
    groups: list[tuple[str, list[dict[str, Any]]]],
    resolver: AssetResolver,
    options: MenuImageOptions,
) -> MenuImageFile:
    width = 1080
    margin = 52
    gap = 22
    card_height = 184
    section_gap = 34
    header_height = 338
    content_height = sum(58 + math.ceil(len(items) / 2) * (card_height + gap) + section_gap for _, items in groups)
    height = header_height + 38 + content_height + 132
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    recipe_count = sum(len(items) for _, items in groups)
    _draw_header(image, draw, options, recipe_count, height=header_height)
    y = header_height + 38
    card_width = (width - margin * 2 - gap) // 2
    for category, items in groups:
        y = _draw_category_header(draw, category, y, width, margin, large=False)
        for index, recipe in enumerate(items):
            column = index % 2
            row = index // 2
            left = margin + column * (card_width + gap)
            top = y + row * (card_height + gap)
            _draw_share_card(image, draw, recipe, (left, top, left + card_width, top + card_height), resolver, options.include_photos)
        y += math.ceil(len(items) / 2) * (card_height + gap) + section_gap
    _draw_footer(draw, width, height, options.footer)
    return _jpeg_file(image, "chef-zhai-menu-share.jpg", dpi=144)


def _print_sections(groups: list[tuple[str, list[dict[str, Any]]]]) -> list[list[tuple[str, list[dict[str, Any]]]]]:
    pages: list[list[tuple[str, list[dict[str, Any]]]]] = []
    remaining = [(category, list(items)) for category, items in groups]
    continued_categories: set[str] = set()
    page_index = 0
    while remaining:
        y = 470 if page_index == 0 else 270
        bottom = 3350
        sections: list[tuple[str, list[dict[str, Any]]]] = []
        while remaining:
            category, items = remaining[0]
            available_rows = (bottom - y - 82) // 376
            if available_rows <= 0:
                break
            take = min(len(items), available_rows * 3)
            selected = items[:take]
            label = f"{category} · 续" if category in continued_categories else category
            sections.append((label, selected))
            rows = math.ceil(take / 3)
            y += 82 + rows * 376 + 28
            if take == len(items):
                remaining.pop(0)
            else:
                remaining[0] = (category, items[take:])
                continued_categories.add(category)
                break
        if not sections:
            category, items = remaining.pop(0)
            sections.append((category, items[:3]))
            if len(items) > 3:
                remaining.insert(0, (category, items[3:]))
                continued_categories.add(category)
        pages.append(sections)
        page_index += 1
    return pages


def _print_images(
    groups: list[tuple[str, list[dict[str, Any]]]],
    resolver: AssetResolver,
    options: MenuImageOptions,
) -> tuple[MenuImageFile, ...]:
    width, height = 2480, 3508
    margin = 110
    gap = 34
    card_width = (width - margin * 2 - gap * 2) // 3
    card_height = 342
    pages = _print_sections(groups)
    files: list[MenuImageFile] = []
    recipe_count = sum(len(items) for _, items in groups)
    for page_index, sections in enumerate(pages, start=1):
        image = Image.new("RGB", (width, height), PAPER)
        draw = ImageDraw.Draw(image)
        header_height = 420 if page_index == 1 else 225
        _draw_header(image, draw, options, recipe_count, height=header_height, compact=page_index > 1)
        y = header_height + 48
        for category, items in sections:
            y = _draw_category_header(draw, category, y, width, margin, large=True)
            for index, recipe in enumerate(items):
                column = index % 3
                row = index // 3
                left = margin + column * (card_width + gap)
                top = y + row * (card_height + gap)
                _draw_print_card(image, draw, recipe, (left, top, left + card_width, top + card_height), resolver, options.include_photos)
            y += math.ceil(len(items) / 3) * (card_height + gap) + 28
        _draw_footer(draw, width, height, options.footer, page=page_index)
        files.append(_jpeg_file(image, f"chef-zhai-menu-a4-{page_index:02d}.jpg", dpi=300))
    return tuple(files)


def generate_menu_images(
    recipes: Iterable[dict[str, Any]],
    resolver: AssetResolver,
    options: MenuImageOptions | None = None,
) -> MenuImageResult:
    selected_options = options or MenuImageOptions()
    published = [dict(recipe) for recipe in recipes if recipe.get("published", True)]
    if not published:
        raise ValueError("当前没有已上架菜品，无法生成菜单图片")
    groups = _group_recipes(published)
    files = (
        (_share_image(groups, resolver, selected_options),)
        if selected_options.image_format == "share"
        else _print_images(groups, resolver, selected_options)
    )
    return MenuImageResult(files=files, recipe_count=len(published), category_count=len(groups))


def menu_image_zip(result: MenuImageResult) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in result.files:
            archive.writestr(file.name, file.content)
    return buffer.getvalue()
