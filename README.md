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

macOS 直接双击 `start-ui-mac.command` 即可。首次打开会自动在项目目录创建隔离的 `.venv` 并安装依赖，可能需要几分钟；以后双击会直接启动并打开浏览器，不会把依赖安装到 Conda base 环境。

如果从压缩包或网络下载后 macOS 丢失了执行权限，运行一次：

```bash
chmod +x start-ui-mac.command
```

UI 会在浏览器中打开，支持：

- 输入 Bilibili 视频链接并生成菜谱笔记
- 查看运行日志和 `note.md` 预览
- 在手机或窄屏浏览器中使用分步烹饪模式，逐步查看操作、火候、时长和关键截图
- 按目标人数自动缩放数字用量，并可将斤、两、杯、汤匙等换算为克或毫升
- 生成可勾选、可下载的临时购物清单；“少许、适量”和复杂复合用量会保留原文并提示人工确认
- 设置 cookies 文件路径、输出目录、语言、Whisper 模型
- 开关步骤截图、LLM 重写、临时媒体保留，并设置最终步骤/关键图片上限
- 可选生成逐项审核版，在“审核确认”中采用、修改后采用或跳过每一项
- 生成结果先进入“草稿与归档”，可以不修改直接存档，也可以完整编辑或审核后再存档
- 在编辑页修改分类、菜系、标签、配料、步骤、关键点，或直接手写最终 Markdown
- 将最终版本归档到现有或新建的 Obsidian vault；同一来源重复归档会更新原笔记
- 批量任务完成后可逐条编辑、审核、归档，也可一次归档全部已完成草稿
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

支持保存的默认项包括输出目录、cookies 路径、语言、Whisper 模型、截图/LLM/保留媒体开关、LLM provider、步骤/图片上限、审核版开关、Obsidian vault 路径和自动归档策略。

### 移动烹饪模式

在“烹饪模式”中选择一条已有菜谱即可开始。页面会根据 `servings` 识别原份量；能识别时填写目标份量，不能识别时直接填写用量倍率。单位可保留原样，也可统一换算为公制。

换算结果只存在于当前烹饪页面和下载的购物清单中，不会覆盖 `recipe.json`。数字、分数和简单范围可以自动缩放；“少许”“适量”以及包含多个数量语义的复杂写法会保留原文，避免产生危险或误导性的用量。

### 草稿、人工修改与 Obsidian 归档

每次抓取和 AI 抽取成功后，`recipe.json` 与 `note.md` 都先作为可修改的工作副本保留。完整流程为：

```text
抓取/转写 → AI 菜谱草稿 → 可选逐项审核 → 可选完整编辑 → Obsidian 归档
                                          ↘ 无需修改时直接归档
```

“草稿与归档”页会显示以下状态：待整理、已归档、归档后有修改、归档异常。归档后继续编辑不会丢失工作副本；状态会自动变为“归档后有修改”，重新归档即可同步。默认会保护 Obsidian 中的手写内容，如果 Vault 版本被修改过，需要明确勾选覆盖确认。

Vault 默认结构：

```text
obsidian-vault/
├── 菜谱/
│   ├── 中餐/
│   ├── 汤羹/
│   ├── 西餐/
│   └── 糕点/
├── 烹饪技巧/
│   ├── 火候/
│   ├── 调味/
│   └── 食材处理/
└── 附件/
```

菜谱笔记使用 YAML frontmatter 保存分类、菜系、标签、原视频和稳定来源 ID；只复制最终 note 实际引用的关键图片。同一个视频重新生成、修改标题或改变分类后仍会更新同一条归档，不会产生重复笔记。

归档前可以填写 1–5 星“个人喜爱度”。系统会根据总耗时、步骤数量和复杂技法自动给出“烹饪难度”和“时间投入”评级，三个评级都可以在“草稿与归档”、批量单条后处理或“编辑修复”页手动调整。最终评级同时写入菜谱正文、Obsidian YAML frontmatter、`recipe.json` 和 `archive.json`，便于以后按喜爱度或投入成本检索。

AI 提炼的通用烹饪技巧先标记为“AI 候选”。在知识库中人工编辑并点击“确认并收录为干货”后，才会同步到 Vault 的 `烹饪技巧/`；最终技巧笔记不会包含置信度或内部审核证据。

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
