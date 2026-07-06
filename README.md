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

### 命令行

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

### 本地 UI

安装依赖后可启动本地 Web UI：

```powershell
python -m streamlit run bili_recipe_notes/ui.py
```

也可以直接双击项目根目录下的快捷启动文件：

- Windows: `start-ui-windows.bat`
- macOS: `start-ui-mac.command`

macOS 如果提示没有执行权限，首次运行前执行一次：

```bash
chmod +x start-ui-mac.command
```

UI 会在浏览器中打开，支持：

- 输入 Bilibili 视频链接并生成菜谱笔记
- 查看运行日志和 `note.md` 预览
- 设置 cookies 文件路径、输出目录、语言、Whisper 模型
- 开关步骤截图、opencode 重写、临时媒体保留
- 提取 UP 主主页下的视频链接
- 扫描历史记录、搜索并预览已生成的菜谱
- 批量读取多行 URL 或链接文件，失败不中断，已生成内容可跳过，并支持断点续跑
- 检查本地环境：依赖包、ffmpeg、yt-dlp Bilibili 支持、opencode、Codex CLI、Whisper
- 编辑 `recipe.json` / `transcript.json` 后重新生成笔记
- 单步截图重截
- 显示菜谱质量评分、问题和改进建议
- 对已有笔记一键优化，优化前会备份为 `note.before-optimize.md`
- 对已有视频做 LLM 二次分析，例如提取通用烹饪技巧、食材替换建议、常见失败点
- 导出 Obsidian Markdown、PDF、Word

UI 仅面向本机个人使用，不做账号登录管理。输出仍写入 `outputs/` 或界面中指定的目录。

UI 默认配置会保存到：

```text
.bili-recipe-notes/config.json
```

支持保存的默认项包括输出目录、cookies 路径、语言、Whisper 模型、截图/LLM/保留媒体开关、LLM provider。

批量任务状态会保存到：

```text
.bili-recipe-notes/batches/
```

生成和修复后的质量报告会保存到每个菜谱输出目录：

```text
outputs/视频标题/quality.json
```

### LLM provider

默认 provider 是 `opencode`。UI 也提供：

- `none`：不重写，直接使用规则版 Markdown
- `codex`：使用本机 Codex CLI 的 `codex exec` 非交互模式，需要本机已安装并登录 Codex CLI
- `openai`：使用 OpenAI Responses API，需要设置环境变量 `OPENAI_API_KEY`
- `local`：把提示词通过 stdin 传给本地命令

Codex 模式可在 UI 里填写模型名和 profile；两者都可留空，留空时使用 Codex CLI 默认配置。OpenAI 模式的模型名可在 UI 里修改，默认值保存在本地配置中。

### cookies.txt 说明

部分视频需登录态。可将浏览器导出的 Netscape 格式 cookies 保存为 `cookies.txt`，通过 `--cookies` 传入。

如果遇到 Bilibili `HTTP Error 412: Precondition Failed`：

- 先关闭 UI 重新双击启动文件，启动脚本会自动检查并更新 `yt-dlp` 的 Bilibili 支持。
- 也可以手动更新依赖：`python -m pip install -U -r requirements.txt`。
- 如果视频需要登录态，重新从浏览器导出最新 `cookies.txt`，并在 CLI/UI 中传入。

## 输出示例

```text
outputs/视频标题/
├── recipe.json
├── note.md
├── extra_analysis.md
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

本地 UI 打包脚本：

- Windows: `package-ui-windows.bat`，输出 `dist/BiliRecipeNotesUI.exe`
- macOS: `package-ui-mac.command`，输出 `dist/Bili Recipe Notes.app`

macOS 首次运行打包脚本前可能需要：

```bash
chmod +x package-ui-mac.command
```

## 后续计划

- 更细的阶段级重试。
- 更高质量的 LLM 结构化抽取。
- 更完整的桌面安装包。

## 示例数据

`examples/sample_transcript.json` 提供了最小 transcript 样例。
