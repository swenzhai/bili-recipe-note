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

开发、运行测试或构建二进制时改用 `pip install -r requirements-dev.txt`。

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
- `--max-steps 10`（最终版保留 4–12 个关键阶段，默认最多 10 个）
- `--max-images 4`（只保存 1–6 张关键步骤图片，默认最多 4 张）
- `--review`（额外生成逐项审核文件）
- `--whisper-model small`
- `--language zh`
- `--keep-media`
- `--no-llm-summary`
- `--llm-provider opencode|codex|openai|local|none`
- `--codex-model MODEL` / `--codex-profile PROFILE`
- `--local-llm-command "COMMAND ..."`
- `--llm-extra-instructions "附加提示词"`
- `--llm-extra-instructions-file prompt.txt`（从 UTF-8 文件载入高级提示词）
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
python -m streamlit run bili_recipe_notes/ui.py --server.address=127.0.0.1 --browser.serverAddress=127.0.0.1
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
- 开关步骤截图、LLM 重写、临时媒体保留，并设置最终步骤/关键图片上限
- 可选生成逐项审核版，在“审核确认”中采用、修改后采用或跳过每一项
- 提取 UP 主主页下的视频链接
- 扫描历史记录、搜索并预览已生成的菜谱
- 在历史记录中可选择“仅重写 note.md”，只基于本地 `recipe.json` 重试 LLM，不重新下载视频
- 批量读取多行 URL 或链接文件，失败不中断，已生成内容可跳过，并支持断点续跑
- 检查本地环境：依赖包、ffmpeg、yt-dlp Bilibili 支持、opencode、Codex CLI、Whisper
- 编辑 `recipe.json` / `transcript.json` 后重新生成笔记
- 单步截图重截
- 显示菜谱质量评分、问题和改进建议
- 对已有笔记一键优化，优化前会备份为 `note.before-optimize.md`
- 从历史视频中提取通用烹饪技巧、原理、避坑经验，沉淀到独立知识库
- 编辑、删除、搜索、去重合并知识库条目
- 复习知识卡片，记录掌握状态和下次复习时间
- 为知识点添加自己的实践记录
- 批量从历史视频沉淀知识，并可把相关知识写回菜谱 `note.md`
- 全量或按分类导出知识库为 Markdown、CSV 或 Anki TSV
- 对已有视频做 LLM 二次分析，例如提取通用烹饪技巧、食材替换建议、常见失败点
- 导出 Obsidian Markdown、PDF、Word

UI 仅面向本机个人使用，所有启动入口都只监听 `127.0.0.1`，目前不做账号登录管理。若未来需要远程或多人使用，必须先完成 [TODO.md](TODO.md) 中的认证、权限和数据隔离事项。输出仍写入 `outputs/` 或界面中指定的目录。

UI 默认配置会保存到：

```text
.bili-recipe-notes/config.json
```

支持保存的默认项包括输出目录、cookies 路径、语言、Whisper 模型、截图/LLM/保留媒体开关、LLM provider、步骤/图片上限和审核版开关。

批量任务状态会保存到：

```text
.bili-recipe-notes/batches/
```

个人厨艺知识库会保存到：

```text
.bili-recipe-notes/knowledge_base.json
```

知识库独立于单个视频输出目录，用于长期积累从不同视频中提炼出的通用技巧、原理、适用场景和视频依据。
相似知识会尽量自动合并，并保留多个来源视频。你也可以在 UI 中手动编辑、合并、复习和记录实践。

配置、批次、知识库和质量报告采用原子替换写入；覆盖前会把上一版保留为同目录下的 `*.bak`。如果 JSON 已损坏，程序会明确报错并停止覆盖。此时应先检查并恢复对应的 `.bak`，不要用空文件覆盖原数据。

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

#### LLM CLI 高级提示词

当 provider 为 `opencode`、`codex` 或 `local` 时，侧栏会显示“LLM CLI 高级提示词”。可以载入“严格证据、家庭实用、专业厨房”预设，也可以填写自己的附加指令。该指令会用于：

- 结构化菜谱抽取
- 手动重写/优化笔记
- 二次分析
- 知识库提取

高级提示词只作为附加指令，不会替换程序内置的 JSON/Markdown 格式、来源保留和不杜撰约束。OpenAI API provider 不使用这项 CLI 专属配置。

例如：

```bash
python -m bili_recipe_notes "https://www.bilibili.com/video/BVxxxx" \
  --llm-provider codex \
  --llm-extra-instructions-file my-recipe-prompt.txt
```

UI 中的菜谱预览会把 `note.md` 内的 `images/...` 相对路径解析到对应输出目录；`note.md` 本身仍保留相对路径，便于连同 `images/` 一起移动或打包。

### cookies.txt 说明

部分视频需登录态。可将浏览器导出的 Netscape 格式 cookies 保存为 `cookies.txt`，通过 `--cookies` 传入。

如果遇到 Bilibili `HTTP Error 412: Precondition Failed`：

- 先关闭 UI 重新双击启动文件，启动脚本会自动检查并重装项目锁定的 `yt-dlp` 版本。
- 需要升级时先更新 `requirements.txt` 中的版本号，再执行 `python -m pip install -r requirements.txt`。
- 如果视频需要登录态，重新从浏览器导出最新 `cookies.txt`，并在 CLI/UI 中传入。

## 输出示例

```text
outputs/视频标题/
├── recipe.json
├── recipe.review.json   # 开启逐项审核时生成
├── note.md
├── extra_analysis.md
├── transcript.json
└── images/
    ├── step_01.jpg
    └── ...
```

`note.md` 是面向检索和快速回顾的最终版：默认最多 10 个关键阶段、4 张关键步骤图，不显示置信度、字幕证据或审核意见。完整证据仍保留在 `recipe.json`；开启审核版后，也会写入 `recipe.review.json`，可在 UI 中像解决 merge 项一样逐项确认，全部解决后再应用到最终版。

## 二进制版本发布（含 Windows）

仓库已提供 GitHub Actions 工作流 `.github/workflows/release-binaries.yml`：

- 打 `v*` tag（如 `v0.2.0`）会自动构建 Linux / macOS / Windows 的 CLI 和 UI 可执行文件。
- 构建后会自动附加到 GitHub Release。
- Pull Request 会运行测试、语法检查和 CLI 打包启动冒烟测试。
- 本地也可手动构建 CLI：`python -m PyInstaller --clean --noconfirm bili-recipe-notes.spec`。

本地 UI 打包脚本：

- Windows: `package-ui-windows.bat`，输出 `dist/BiliRecipeNotesUI.exe`
- macOS: `package-ui-mac.command`，输出 `dist/BiliRecipeNotesUI`

macOS 首次运行打包脚本前可能需要：

```bash
chmod +x package-ui-mac.command
```

## 后续计划

见 [TODO.md](TODO.md)。远程访问、账号和多用户权限已明确延期；当前版本保持本机个人使用边界。

## 示例数据

`examples/sample_transcript.json` 提供了最小 transcript 样例。
