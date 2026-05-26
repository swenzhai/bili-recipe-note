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

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Python 3.10+。

### ffmpeg 安装提示

需要系统可执行 `ffmpeg`，例如：

- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`
- Windows: `winget install ffmpeg`

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
- `--no-llm-summary`
- `--creator-home`（输入博主主页链接，提取全部视频链接）
- `--creator-links-file all_links.txt`（提取结果文件名，默认 `creator_video_links.txt`）

提取博主全部视频链接示例：

```bash
python -m bili_recipe_notes "https://space.bilibili.com/123456/video" --creator-home --out outputs
```

执行后会在 `outputs/creator_video_links.txt` 生成该主页下全部视频 URL，便于后续批量整理菜谱。

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

## 二进制版本发布（含 Windows）

仓库已提供 GitHub Actions 工作流 `.github/workflows/release-binaries.yml`：

- 打 `v*` tag（如 `v0.2.0`）会自动构建 Linux / macOS / Windows 可执行文件。
- 构建后会自动附加到 GitHub Release。
- 本地也可手动构建：`pyinstaller --onefile --name bili-recipe-notes -m bili_recipe_notes`。

## 后续计划

- 接入 `extract_recipe_with_llm(transcript, metadata)`。
- 后续可支持 OpenAI API 或本地大模型做更高质量结构化抽取。

## 示例数据

`examples/sample_transcript.json` 提供了最小 transcript 样例。
