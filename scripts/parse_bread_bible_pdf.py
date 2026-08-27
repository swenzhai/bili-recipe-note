from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from rapidocr_onnxruntime import RapidOCR


RECIPES: list[tuple[str, str, int]] = [
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "惊喜肉桂面包", 42),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "蔓越莓香蕉核桃快速面包", 44),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "胡萝卜面包", 46),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "巧克力面包", 48),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "美国西南部玉米奶蛋面包", 50),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "经典玉米麦芬", 52),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "玉米手指", 54),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "蓝莓麦芬", 56),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "大号香蕉麦芬", 58),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "比斯吉", 60),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "黄油比斯吉", 61),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "优雅之触比斯吉", 63),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "轻盈天使比斯吉", 65),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "红薯比斯吉", 67),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "司康薄饼", 69),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "浓郁奶油生姜司康", 71),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "爱尔兰苏打面包", 74),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "利维贝果", 76),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "比亚利碎洋葱面包", 83),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "英式麦芬", 86),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "普雷结碱水包", 89),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "英式烤面饼", 91),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "黄油脆薄空心松饼", 92),
    ("第二章 快速面包、迷你快速面包、迷你酵母面包和面糊面包", "荷兰宝贝薄松饼", 94),
    ("第三章 扁平面包", "完美比萨饼面团", 99),
    ("第三章 扁平面包", "土豆扁平比萨饼", 104),
    ("第三章 扁平面包", "多人份的土豆扁平比萨饼", 108),
    ("第三章 扁平面包", "迷迭香佛卡夏", 109),
    ("第三章 扁平面包", "新鲜香草佛卡夏", 111),
    ("第三章 扁平面包", "烧烤佛卡夏", 115),
    ("第三章 扁平面包", "西西里蔬菜比萨卷", 118),
    ("第三章 扁平面包", "皮塔饼", 120),
    ("第三章 扁平面包", "地中海逾越节薄饼", 122),
    ("第三章 扁平面包", "印度煎饼", 124),
    ("第四章 三明治软面包和餐包", "基础三明治白面包", 133),
    ("第四章 三明治软面包和餐包", "黄油餐包", 136),
    ("第四章 三明治软面包和餐包", "普尔曼三明治面包", 140),
    ("第四章 三明治软面包和餐包", "肉桂葡萄干面包", 142),
    ("第四章 三明治软面包和餐包", "土豆三明治面包", 146),
    ("第四章 三明治软面包和餐包", "轻盈香蕉面包", 149),
    ("第四章 三明治软面包和餐包", "红薯面包", 152),
    ("第四章 三明治软面包和餐包", "切达干酪面包", 155),
    ("第四章 三明治软面包和餐包", "意大利乳清干酪面包", 158),
    ("第四章 三明治软面包和餐包", "碎小麦面包", 161),
    ("第四章 三明治软面包和餐包", "亚麻籽面包", 164),
    ("第五章 炉火面包", "基本炉火面包", 170),
    ("第五章 炉火面包", "经典小麦面包", 173),
    ("第五章 炉火面包", "小圆面包", 176),
    ("第五章 炉火面包", "土豆酪乳面包", 179),
    ("第五章 炉火面包", "利维传统犹太黑麦面包", 182),
    ("第五章 炉火面包", "标准粗黑麦面包", 185),
    ("第五章 炉火面包", "法棍", 189),
    ("第五章 炉火面包", "辛辣香草面包棒", 193),
    ("第五章 炉火面包", "布琳娜的普格利泽", 195),
    ("第五章 炉火面包", "托斯卡纳低盐面包", 198),
    ("第五章 炉火面包", "夏巴塔", 200),
    ("第五章 炉火面包", "普格利泽", 203),
    ("第五章 炉火面包", "金色粗粒小麦面包", 205),
    ("第五章 炉火面包", "意大利熏火腿面包圈", 208),
    ("第五章 炉火面包", "啤酒面包", 211),
    ("第五章 炉火面包", "曼图亚橄榄油面包", 213),
    ("第五章 炉火面包", "橄榄面包", 216),
    ("第五章 炉火面包", "蘑菇面包", 219),
    ("第五章 炉火面包", "提洛尔谷物面包", 222),
    ("第五章 炉火面包", "瑞典黑麦面包", 225),
    ("第五章 炉火面包", "葡萄干山核桃面包", 228),
    ("第五章 炉火面包", "新西兰杏仁无花果面包", 232),
    ("第五章 炉火面包", "核桃叶子形面包", 235),
    ("第五章 炉火面包", "核桃洋葱面包", 238),
    ("第六章 酸面团面包", "基本酸面团面包", 250),
    ("第六章 酸面团面包", "酸面团黑麦面包", 254),
    ("第六章 酸面团面包", "酸面团粗黑麦面包", 258),
    ("第六章 酸面团面包", "酸面团小麦谷物面包", 263),
    ("第六章 酸面团面包", "低风险酸面团面包", 267),
    ("第六章 酸面团面包", "法式乡村酸面团圆面包", 270),
    ("第七章 布里欧修家族", "基本布里欧修", 276),
    ("第七章 布里欧修家族", "焦糖黏面包卷", 282),
    ("第七章 布里欧修家族", "巧克力黏面包卷", 286),
    ("第七章 布里欧修家族", "猴子面包", 288),
    ("第七章 布里欧修家族", "栗子潘妮托尼", 291),
    ("第七章 布里欧修家族", "传统哈拉", 294),
    ("第七章 布里欧修家族", "巧克力杏仁旋涡咕咕霍夫", 297),
    ("第七章 布里欧修家族", "图钉麦芬", 301),
    ("第七章 布里欧修家族", "小麦可颂", 304),
]


def _slug(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", value).strip("-") or "recipe"


def _ocr_page(path: Path) -> tuple[str, str]:
    result, _ = RapidOCR()(str(path))
    text = "\n".join(str(item[1]).strip() for item in (result or []) if str(item[1]).strip())
    return path.stem, text + "\n"


def _section(text: str, names: tuple[str, ...]) -> str:
    positions = [text.find(name) for name in names if text.find(name) >= 0]
    if not positions:
        return ""
    start = min(positions)
    return text[start:]


def _recipe_body(text: str, title: str) -> str:
    position = text.find(title)
    return text[position:] if position >= 0 else text


def _lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _numbered_steps(value: str) -> list[str]:
    lines = _lines(value)
    steps: list[str] = []
    current = ""
    for line in lines:
        if re.match(r"^(?:步骤\s*)?\d+[.、．)]", line):
            if current:
                steps.append(current)
            current = re.sub(r"^(?:步骤\s*)?\d+[.、．)]\s*", "", line)
        elif current:
            current += " " + line
    if current:
        steps.append(current)
    return steps or lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=70)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    pdf = args.pdf.resolve()
    output = args.output.resolve()
    pages_dir = output / "ocr" / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    page_count = 367
    existing = {path.stem for path in pages_dir.glob("page-*.txt")}
    missing = [number for number in range(1, page_count + 1) if f"page-{number:03d}" not in existing]
    if missing:
        image_dir = output / ".rendered"
        image_dir.mkdir(parents=True, exist_ok=True)
        first, last = min(missing), max(missing)
        subprocess.run(
            ["pdftoppm", "-f", str(first), "-l", str(last), "-r", str(args.dpi), "-jpeg", "-jpegopt", "quality=68", str(pdf), str(image_dir / "page")],
            check=True,
        )
        image_paths = {int(path.stem.rsplit("-", 1)[-1]): path for path in image_dir.glob("page-*.jpg")}
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            for stem, text in executor.map(_ocr_page, [image_paths[number] for number in missing]):
                (pages_dir / f"{stem}.txt").write_text(text, encoding="utf-8")
        shutil.rmtree(image_dir, ignore_errors=True)
    all_pages = {int(path.stem.rsplit("-", 1)[-1]): path.read_text(encoding="utf-8") for path in pages_dir.glob("page-*.txt")}
    full_text = "\n".join(f"\n===== PDF PAGE {number} =====\n{text}" for number, text in sorted(all_pages.items()))
    (output / "ocr" / "full_text.txt").write_text(full_text, encoding="utf-8")
    records: list[dict[str, Any]] = []
    for index, (chapter, title, printed_page) in enumerate(RECIPES):
        next_printed = RECIPES[index + 1][2] if index + 1 < len(RECIPES) else 308
        pdf_start = printed_page + 15
        pdf_end = next_printed + 14
        importable_dir = output.parent / f"面包圣经-{_slug(title)}"
        existing_importable_path = importable_dir / "recipe.json"
        if existing_importable_path.is_file():
            try:
                existing_importable = json.loads(existing_importable_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                existing_importable = {}
        else:
            existing_importable = {}
        if existing_importable.get("review_status") == "reviewed":
            reviewed_ingredients = existing_importable.get("ingredients") or []
            reviewed_steps = existing_importable.get("steps") or []
            reviewed_tips = existing_importable.get("summary_tips") or []
            record = {
                "id": _slug(title),
                "title": title,
                "chapter": chapter,
                "category": existing_importable.get("category") or "面包烘焙",
                "source_pdf": str(pdf),
                "printed_pages": {"start": printed_page, "end": next_printed - 1},
                "pdf_pages": {"start": pdf_start, "end": min(pdf_end, page_count)},
                "yield_and_conditions": existing_importable.get("servings") or "",
                "ingredients_raw": "\n".join(
                    f"{item.get('name', '')}：{item.get('amount', '')}" for item in reviewed_ingredients if isinstance(item, dict)
                ),
                "ingredients": [item.get("name", "") for item in reviewed_ingredients if isinstance(item, dict)],
                "steps_raw": "\n".join(item.get("action", "") for item in reviewed_steps if isinstance(item, dict)),
                "steps": [item.get("action", "") for item in reviewed_steps if isinstance(item, dict)],
                "tips_raw": "\n".join(str(item) for item in reviewed_tips),
                "source_text": str(existing_importable.get("ocr_source_text") or ""),
                "ocr_complete": True,
                "review_status": "reviewed",
                "extraction_method": "pdf_manual_review",
            }
            records.append(record)
            recipe_dir = output / "recipes" / _slug(title)
            recipe_dir.mkdir(parents=True, exist_ok=True)
            (recipe_dir / "recipe.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (recipe_dir / "recipe.md").write_text(
                f"# {title}\n\n- 章节：{chapter}\n- 原书页码：{printed_page}–{next_printed - 1}\n- PDF 页码：{pdf_start}–{min(pdf_end, page_count)}\n- 审核状态：已人工审核\n\n"
                "## 原材料\n\n" + record["ingredients_raw"] + "\n\n## 制作过程\n\n" + record["steps_raw"] + "\n\n## 要点\n\n" + record["tips_raw"] + "\n",
                encoding="utf-8",
            )
            continue
        source_text = "\n".join(all_pages.get(page, "") for page in range(pdf_start, min(pdf_end, page_count) + 1)).strip()
        body = _recipe_body(source_text, title)
        ingredients_raw = _section(body, ("原材料", "原材料（", "面糊", "面团"))[:7000]
        steps_raw = _section(body, ("·制作过程", "制作过程", "制作步骤", "制作方法"))[:10000]
        if not steps_raw.strip():
            steps_raw = body[:10000]
        tips_raw = _section(body, ("Tips:", "小知识:", "小知识"))[:5000]
        record = {
            "id": _slug(title),
            "title": title,
            "chapter": chapter,
            "category": "面包烘焙",
            "source_pdf": str(pdf),
            "printed_pages": {"start": printed_page, "end": next_printed - 1},
            "pdf_pages": {"start": pdf_start, "end": min(pdf_end, page_count)},
            "yield_and_conditions": _section(body, ("·准备工作", "准备工作"))[:1800],
            "ingredients_raw": ingredients_raw,
            "ingredients": _lines(ingredients_raw),
            "steps_raw": steps_raw,
            "steps": _numbered_steps(steps_raw),
            "tips_raw": tips_raw,
            "source_text": source_text,
            "ocr_complete": True,
            "review_status": "draft",
            "extraction_method": "pdf_ocr",
        }
        records.append(record)
        recipe_dir = output / "recipes" / _slug(title)
        recipe_dir.mkdir(parents=True, exist_ok=True)
        (recipe_dir / "recipe.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (recipe_dir / "recipe.md").write_text(
            f"# {title}\n\n- 章节：{chapter}\n- 原书页码：{printed_page}–{next_printed - 1}\n- PDF 页码：{pdf_start}–{min(pdf_end, page_count)}\n\n"
            "## 准备工作与条件\n\n" + record["yield_and_conditions"] + "\n\n## 原材料（OCR 原文）\n\n" + record["ingredients_raw"] + "\n\n## 制作过程（OCR 原文）\n\n" + record["steps_raw"] + "\n\n## Tips 与补充\n\n" + record["tips_raw"] + "\n", encoding="utf-8"
        )
        importable_dir.mkdir(parents=True, exist_ok=True)
        importable = {
            "title": title,
            "source_url": f"pdf://{pdf}#page={printed_page}",
            "video_title": "《面包圣经》扫描 PDF",
            "uploader": "罗丝·利维·贝兰堡（原书）",
            "creator_name": "罗丝·利维·贝兰堡",
            "category": "面包烘焙",
            "cuisine": "西式烘焙",
            "tags": ["面包圣经", chapter, "OCR待复核"],
            "servings": None,
            "total_time": None,
            "difficulty": None,
            "ingredients": [
                {"name": line, "amount": None, "note": "OCR 原文行，需按原页表格复核。", "evidence": line, "confidence": 0.65}
                for line in record["ingredients"]
            ],
            "seasonings": [],
            "tools": [],
            "prep_items": [],
            "shopping_list": [],
            "steps": [
                {"title": f"步骤 {step_index}", "start_time": 0, "action": line, "evidence": line, "confidence": 0.65}
                for step_index, line in enumerate(record["steps"], start=1)
            ],
            "summary_tips": _lines(record["tips_raw"]),
            "uncertain_points": ["本文件由扫描 PDF OCR 生成；请以对应 PDF 页面的表格和原文核对数字、单位、温度与时间。"],
            "extraction_method": "pdf_ocr",
            "source_pdf": str(pdf),
            "printed_pages": record["printed_pages"],
            "pdf_pages": record["pdf_pages"],
            "ocr_source_text": record["source_text"],
            "ocr_ingredients_raw": record["ingredients_raw"],
            "ocr_steps_raw": record["steps_raw"],
            "review_status": "draft",
        }
        (importable_dir / "recipe.json").write_text(json.dumps(importable, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (importable_dir / "note.md").write_text(
            f"# {title}\n\n来源：`{pdf}`，原书第 {printed_page} 页，PDF 第 {pdf_start}–{min(pdf_end, page_count)} 页。\n\n"
            "这是扫描 PDF 的 OCR 结构化初稿，完整 OCR 原文和复核提示保存在 `recipe.json`。纳入知识库前请按 PDF 原页逐项校对。\n",
            encoding="utf-8",
        )
    (output / "recipes.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    review_queue = [
        {
            "id": record["id"],
            "title": record["title"],
            "printed_pages": record["printed_pages"],
            "pdf_pages": record["pdf_pages"],
            "review_status": record.get("review_status", "draft"),
            "recipe_path": f"../面包圣经-{record['id']}/recipe.json",
        }
        for record in records
    ]
    (output / "review_queue.json").write_text(json.dumps(review_queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "README.md").write_text(
        "# 《面包圣经》结构化菜谱档案\n\n"
        f"来源：`{pdf}`\n\n"
        f"共识别 {len(records)} 个目录配方，PDF 共 {page_count} 页。所有页面均保存在 `ocr/pages/`，完整 OCR 文本在 `ocr/full_text.txt`；每个配方同时提供 `recipe.json` 和 `recipe.md`。\n\n"
        "页码按目录中的印刷页码记录，PDF 页码按本扫描文件实际页码记录。OCR 原文保留在每个 JSON 的 `source_text`，后续知识库导入时应以原文核对数量、单位和步骤。\n\n"
        "## 逐个审核流程\n\n"
        "每个可导入目录 `outputs2/面包圣经-*/recipe.json` 都带有 `review_status`：`draft` 表示 OCR 草稿，`reviewed` 表示已对照 PDF 原页逐项确认。当前已完成 `惊喜肉桂面包`（PDF 第 57–58 页）的人工审核。审核其他菜谱后，将修订文件保存回同一目录并设为 `reviewed`；再次运行本脚本会自动保留人工版本，不会覆盖。\n\n"
        "`review_queue.json` 按目录顺序列出 84 个菜谱、印刷页/PDF 页映射和当前状态，可作为逐个处理清单。\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
