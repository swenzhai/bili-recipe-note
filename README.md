# bili-recipe-notes

本项目是一个本地 Python CLI 小工具，用于从 Bilibili 视频生成**个人学习用**菜谱笔记。

## 合规边界

- 本工具用于个人学习笔记。
- 默认优先提取字幕，而不是完整下载视频。
- 不支持去水印。
- 不鼓励批量下载、公开搬运、二次发布。
- 如果视频没有字幕，工具可以提取音频用于本地转写。
- 截图仅用于个人笔记中的关键步骤配图。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.10+。

### ffmpeg 安装提示

需要系统可执行 `ffmpeg`，例如：

- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`

## 使用方式

```bash
python -m bili_recipe_notes "https://www.bilibili.com/video/BVxxxx"
```

可选参数：

- `--cookies cookies.txt`
- `--out outputs`
- `--no-screenshot`
- `--whisper-model small`
- `--language zh`
- `--keep-media`

### cookies.txt 说明

部分视频需登录态。可将浏览器导出的 Netscape 格式 cookies 保存为 `cookies.txt`，通过 `--cookies` 传入。

## 输出示例

```text
outputs/视频标题/
├── recipe.json
├── note.md
├── transcript.json
└── images/
    ├── step_01.jpg
    └── ...
```

`note.md` 为最终单一文档：会优先通过 opencode 重写为固定结构（配料信息 → 备菜 → 烹饪），并保留步骤配图。

## 后续计划

- 接入 `extract_recipe_with_llm(transcript, metadata)`。
- 后续可支持 OpenAI API 或本地大模型做更高质量结构化抽取。

## 示例数据

`examples/sample_transcript.json` 提供了最小 transcript 样例。
