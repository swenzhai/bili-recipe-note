# bili-recipe-notes

本项目从 Bilibili 视频生成**个人学习用**菜谱笔记，提供 React 厨房管理工作台、家庭点餐客户端和 Python CLI。

家庭服务器使用同一套 React 前端与 FastAPI 服务：`http://服务器:8765/admin/` 是管理工作台，`http://服务器:8765/` 是多人共享点餐客户端。管理页按首页、菜谱库、审核工作台、采集与生成、任务中心、设置与数据组织；模型和目录等低频配置统一放在设置页。首次直接打开点餐网址并输入设备名称即可加入，之后菜谱、心得、套餐和当前本餐会自动同步；管理员可随时锁定新设备加入。点餐菜单支持主菜、肉类、海鲜、主食、面条、糕点等重叠分类与搜索组合筛选，管理页可逐项或批量上下架菜谱，首次升级默认全部上架。当前上架菜品还可一键生成适合转发的 Chef Zhai 菜单长图，或导出 300 DPI 的 A4 分页图片用于打印。原有 `mobile/` Flutter 客户端仍可同步菜谱与心得。开发说明见 [`mobile/README.md`](mobile/README.md) 和 [`web/README.md`](web/README.md)。

点餐页再次进入时先显示本机缓存，后台更新菜单；每批展示 40 道菜，可继续加载或直接搜索。点击菜名或图片可查看菜品简介、做法概览、用料、步骤配图、小贴士和原视频，并在详情中加菜。服务启动时先提供已有索引，整库扫描在后台进行；首次加入的新设备仍需下载菜单。

管理页的 **任务中心 → 新建 AI 任务 → 菜品介绍**可启动介绍生成任务，使用“设置与数据”中的模型配置；菜谱库批量操作和菜谱详情也提供入口。默认先处理 10 道可展示且已上架的菜，跳过已有有效介绍；数量填 0 可处理全部。任务支持进度、预计剩余时间、暂停、续跑和失败重试，关闭浏览器后继续运行。未补充介绍的菜仍展示已有用料与做法。这个任务与 AI 审核归类独立，不改变审核或上下架决定。

可选“联网查找背景与起源”会向 Bing 发送菜名和部分主料名称，读取最多 3 条搜索摘要，不抓取完整网页。背景与起源只采用摘要中的可核查原文片段，注明来源与“可能相关／尚待核实”等匹配提示；菜名或用料对不上、模型判定不匹配或没有引用依据时不补写历史。搜索失败仍可生成本菜简介，并显示检索失败提示。搜索结果不是已核实史实，也不代表对应视频中的这份菜。

介绍独立保存在各菜谱目录的 `recipe.dish-info.json`，任务与覆盖前历史保存在输出根目录 `.dish-info/`。原始菜谱、字幕、笔记和审核文件保持原样；菜谱核心内容或来源变化后旧介绍自动失效，需要重新补充。介绍文件随部署包和工作交接包迁移。

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
python -m pip install -r requirements.txt
```

开发、运行测试或构建二进制时改用 `pip install -r requirements-dev.txt`。

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Python 3.10+。

如果切换了 Conda 环境或升级了 Python，旧 `.venv` 可能仍指向已不存在的解释器，出现 `No such file or directory` 或 `bad interpreter`。在旧目录上重复执行 `python -m venv .venv` 不一定能修复。请先退出虚拟环境（若已激活，执行 `deactivate`），激活要使用的 Conda 环境（例如 `conda activate py314`），再备份并重建：

```bash
mv .venv ".venv.$(date +%Y%m%d-%H%M%S).bak"
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### ffmpeg 安装提示

需要系统可执行 `ffmpeg`，例如：

- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt install ffmpeg`
- Windows: `winget install ffmpeg`

Conda 环境也可使用 `conda install -n py314 -c conda-forge ffmpeg`（将 `py314` 替换为实际环境名）。

### 本地视觉模型选取菜品特写

选帧工具以 Git submodule 存放在 `external/video-frame-choose/`，固定版本由主仓库的 gitlink 记录。它使用独立 Python 环境和本地 Qwen2.5-VL 模型，避免与主应用的依赖混装。当前机器已迁入原工具的 `.venv/`、模型链接及旧验证记录，并修复虚拟环境的搬迁路径；其他机器需按 [工具 README](external/video-frame-choose/README.md) 准备环境和权重。

React 管理页“任务中心”支持批量后台选图，首页与菜谱库也提供入口。“审核工作台 → 成品图”可直接点击“AI 选这道菜的特写”，或从现有步骤图、视频截图、上传照片中选择。批量默认先处理 10 道已上架、可展示且未人工确认封面的菜品，跳过相同要求和参数下已有的有效结果。可修改选图要求、本地模型目录、运行设备、线程数及粗筛/精筛帧数；这里使用独立视觉模型，与“设置与数据”中的文本模型配置分开。

任务中心和管理首页的任务摘要每 5 秒刷新。任务详情显示菜品总进度、找到候选/无匹配/失败/跳过数量、当前视频的粗筛与精筛帧进度、已运行时间和预计剩余时间。关闭浏览器后任务继续；可在当前菜品完成后暂停、继续未完成项或重试失败项。多个菜谱库共用同一数据根目录时，模型进程会排队运行，避免同时加载多份模型。

结果、日志和任务状态独立保存在当前菜谱库的 `.frame-selection/` 中，批量任务不会改写原菜谱、笔记、步骤图或封面。在“审核工作台 → 成品图”选择 AI 候选，拖动裁剪框或使用位置和缩放滑块，点击“确认裁剪并设为封面”后才更新菜单封面，并记录原视频位置与裁剪范围。没有匹配画面会单独标记，不自动使用低质量兜底图；可调整参数重选或继续手工选图。菜谱内容或来源变化后，旧候选需重新生成。下载视频默认在本条选图结束后清理，也可选择保留；已有本地视频始终保留。

在本工程根目录运行：

```bash
.venv/bin/python -m bili_recipe_notes.frame_selection \
  --input /absolute/path/cooking.mp4 \
  --output data/experiments/frame-selection/new-run \
  --requirement "找到一张制作完成、盛在盘中的菜品特写，主体清晰，尽量没有手和锅铲遮挡"
```

省略 `--requirement` 时默认查找成品菜特写。默认调用子模块的 `.venv/bin/python`（Windows 为 `.venv/Scripts/python.exe`），可用 `--python` 或 `BILI_RECIPE_FRAME_PYTHON` 指定已安装选帧依赖的解释器；`--model` 可指定已有本地模型目录。`--device`、`--threads`、`--interval`、`--coarse-limit`、`--fine-limit` 等参数传给工具，完整说明见子模块 README。当前环境仅验证 CPU；选帧可能需要数分钟。

结果为新目录中的 `best_frame.png` 与 `result.json`，保留原视频分辨率、真实帧序号、PTS、相对时间戳、选择理由和候选判断。该入口只生成独立结果，不替换菜谱封面或步骤图，也不覆盖旧选帧结果。默认找不到匹配画面时只保存 `not_found` 元数据并返回退出码 2；只有显式传入 `--allow-fallback` 才允许导出标记为降级的候选。图片仍需人工查看，单次采样不保证覆盖所有短镜头。

已用王刚《番茄炒鸡蛋》完整视频实测：32 张候选选中 02:06 的装盘成品特写，输出 1920×1080 PNG；与原视频所选帧像素完全一致。本机 CPU 约耗时 12 分钟，截图保留原有字幕和水印。[验证记录与复现命令](docs/frame-selection-validation.md)。

选帧子模块的远程来源为 `git@github.com:swenzhai/video-frame-choose.git`。在具备该仓库 SSH 访问权限的机器上，从主工程根目录同步配置并初始化固定版本：

```bash
git submodule sync -- external/video-frame-choose
git submodule update --init -- external/video-frame-choose
```

本机的 `data/sources/git/video-frame-choose.git` bare 仓库保留作为备份。依赖环境和模型权重不纳入 Git；源码迁移与环境准备分别处理。子模块内原 AGENTS.md 的旧工程绝对路径应替换为当前 `external/video-frame-choose/`，推荐直接使用上述主工程入口。

### Whisper 模型下载失败

没有可用字幕的视频会使用 Whisper 转写。安装 `faster-whisper` 不会自动下载模型；首次使用默认 `small` 模型时需要访问 Hugging Face。如果批次集中报 `transcribe_audio`、`Connection reset by peer` 和找不到本地 snapshot，可在已激活的 `.venv` 中通过可访问的镜像预下载模型（Linux/macOS）：

```bash
HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 python -c "from faster_whisper.utils import download_model; print(download_model('small'))"
HF_HUB_OFFLINE=1 python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('模型离线加载成功')"
```

模型缓存在当前用户的 `~/.cache/huggingface/hub`（设置了 `HF_HOME` 或 `HF_HUB_CACHE` 时以对应路径为准）。下载并验证后，继续原批次或重试失败项。若希望完全避免模型联网检查，可在启动 UI 或批次前设置 `export HF_HUB_OFFLINE=1`；此时所选模型必须已经缓存。

## 数据目录与迁移

应用代码和个人数据分开保存，默认数据根目录为项目下的 `data/`：

```text
data/
├── recipes/                 # 菜谱、原始字幕、来源、审核和媒体
│   ├── <UP主>/
│   │   └── <菜名>--<BVID>/
│   ├── .ai-review/          # 审核任务与历史快照
│   ├── .dish-info/          # 菜品介绍任务、日志与历史快照
│   ├── .frame-selection/    # AI 选图候选、任务、日志与独立历史结果
│   ├── .admin/cover-candidates/  # 管理页的手工封面候选
│   ├── .admin-tools/        # 步骤图候选、修订历史和临时工作目录
│   └── curation-review/     # 整理报告与人工决定
├── creators/                # 按 UID 保存视频链接与 UP 主资料
│   └── <UID>-<UP名称>/
│       ├── video_links.txt
│       └── creator.json
├── state/                   # 配置、批次、知识库、数据库和私人状态
│   ├── config.json
│   ├── batches/
│   ├── creator-tasks/       # UP 主链接更新任务
│   ├── admin-operations/    # 知识、归档、修订、菜单与数据后台操作
│   ├── knowledge_base.json
│   ├── mobile-sync.sqlite3
│   ├── mobile-media/
│   └── cookies/
├── sources/                 # PDF、结构化原资料等输入材料
│   └── admin-imports/       # 管理页上传的文档和交接包
├── exports/                 # 部署包、工作交接包和默认归档
│   ├── admin-downloads/     # 管理页导出结果及受控下载记录
│   └── obsidian-vault/
└── logs/                    # 运行日志
```

可将数据放到独立磁盘或共享挂载目录。Linux/macOS 在启动 CLI、UI 或服务之前设置：

```bash
export BILI_RECIPE_DATA_DIR=/srv/bili-recipe-data
```

Windows PowerShell：

```powershell
$env:BILI_RECIPE_DATA_DIR = "D:\bili-recipe-data"
```

CLI 也可通过 `--data-dir /srv/bili-recipe-data` 为本次运行指定数据根目录。所有进程使用相同设置即可访问同一份数据。`--out` 仍可指定其他菜谱输出目录；配置、数据库与 UP 主清单统一归属于数据根目录。代码可独立通过 Git 更新，默认 `data/` 已由 Git 忽略；自定义目录建议放在代码仓库外。

已有 `outputs*`、`cunlv`、`creators`、`.bili-recipe-notes`、`deployments` 和 `obsidian-vault` 可使用一次性迁移命令整理：

```bash
python -m bili_recipe_notes --migrate-data preview
python -m bili_recipe_notes --migrate-data apply
```

先停止 UI、手机 API 和后台任务，再执行迁移。预览会列出来源和目标位置；执行时保留原始菜谱、字幕、图片和审核内容，按 UP 主建立子目录，更新运行状态中的路径并保留迁移记录。同名目录不会自动合并：来自不同旧目录的数据会保留来源子层，确保同名菜谱和不同版本都保留。升级后先完成迁移，再使用新默认目录启动服务。后续搬家只需复制数据根目录并设置环境变量，无需再分散查找数据库和链接清单。

`state/layout.json` 保存旧路径到当前数据目录的映射，搬家时请一并保留。旧配置、批次和知识库中的路径在读取时自动适配，无需改写原始文件；迁移清单和文件校验值保存在 `state/migrations/`。

## 使用方式

### 命令行

```bash
python -m bili_recipe_notes "https://www.bilibili.com/video/BVxxxx"
```

可选参数：

- `--cookies cookies.txt`
- `--out data/recipes`
- `--no-screenshot`
- `--max-steps 10`（最终版保留 4–12 个关键阶段，默认最多 10 个）
- `--max-images 3`（只保存 1–4 张关键步骤图片，默认最多 3 张）
- `--review`（额外生成逐项审核文件）
- `--whisper-model small`
- `--language zh`
- `--keep-media`
- `--no-llm-summary`
- `--require-llm`（LLM 失败时不使用规则降级，保留失败状态以便重试）
- `--require-screenshot`（无法生成步骤截图时保留失败状态以便重试）
- `--llm-provider opencode|codex|openai|local|none`
- `--codex-model MODEL` / `--codex-profile PROFILE`
- `--local-llm-command "COMMAND ..."`
- `--llm-extra-instructions "附加提示词"`
- `--llm-extra-instructions-file prompt.txt`（从 UTF-8 文件载入高级提示词）
- `--creator-home`（输入博主主页链接，提取全部视频链接）
- `--creator-links-file all_links.txt`（额外指定链接清单副本；默认按 UP 主保存到 `data/creators/`）
- `--batch`（创建持久化批次并在当前终端顺序执行）
- `--batch-file links.txt`（从文本文件读取批量 URL，可重复使用；传 `-` 时从 stdin 读取）
- `--batch-url URL`（额外加入一个 URL，可多次指定）
- `--batch-id ID`（为新批次指定便于记忆的稳定 ID）
- `--target-stage raw|recipe`（只形成原始版，或继续到完整菜谱版）
- `--create-only`（只创建 pending 批次，不执行下载、字幕、Whisper 或 LLM）
- `--resume-batch ID` / `--retry-batch ID`（继续未完成阶段 / 只重试失败阶段）
- `--list-batches` / `--show-batch ID`（查看批次列表或逐条状态）
- `--migrate-data preview|apply`（预览或执行旧数据统一迁移）
- `--normalize-output-folders preview|apply`（预览或执行旧输出目录规范化）
- `--export-curation-review [PATH]`（生成同名/近似名菜谱审核表）
- `--export-deployment-bundle [PATH]`（导出菜谱、UP 主清单、图片、知识库及完整数据库的数据迁移包；源码与运行环境由 Git 管理）
- `--export-handoff ID`（把批次和已完成工作导出为跨平台交接包）
- `--import-handoff PATH`（校验并导入 `.handoff.zip`）
- `--handoff-destination PATH`（指定交接包导出文件或目录）

提取博主全部视频链接示例：

```bash
python -m bili_recipe_notes "https://space.bilibili.com/123456/video" --creator-home --out data/recipes
```

执行后会在 `data/creators/<UID>-<UP名称>/video_links.txt` 保存该主页下全部视频 URL，并保存 `creator.json`，便于后续补抓和批量整理菜谱。

### CLI 批量处理

批量 CLI 与 UI 使用同一套批次文件和两阶段流水线。命令默认在当前终端前台顺序运行，每完成一个阶段都会保存状态；终端意外关闭后可按批次 ID 续跑，不需要保持 UI 开启。

从 UP 主已保存链接清单运行到原始版：

```bash
python -m bili_recipe_notes \
  --batch \
  --batch-file "data/creators/419872064-老饭骨/video_links.txt" \
  --batch-id laofangu-raw \
  --target-stage raw \
  --cookies data/state/cookies/bilibili-edge.txt \
  --whisper-model medium \
  --no-screenshot
```

只保存为待执行批次，暂时不做任何视频处理：

```bash
python -m bili_recipe_notes \
  --batch \
  --batch-file links.txt \
  --batch-id my-recipes \
  --target-stage raw \
  --create-only
```

稍后继续到原始版：

```bash
python -m bili_recipe_notes \
  --resume-batch my-recipes \
  --target-stage raw \
  --cookies data/state/cookies/bilibili-edge.txt \
  --whisper-model medium
```

已有原始版后继续生成完整菜谱，不会重新获取已有字幕或转写：

```bash
python -m bili_recipe_notes \
  --resume-batch my-recipes \
  --target-stage recipe \
  --llm-provider codex \
  --require-llm \
  --require-screenshot \
  --codex-model gpt-5.5 \
  --max-steps 10 \
  --max-images 4
```

如果不需要 LLM 和截图，可以只使用规则提取：

```bash
python -m bili_recipe_notes \
  --resume-batch my-recipes \
  --target-stage recipe \
  --llm-provider none \
  --no-llm-summary \
  --no-screenshot
```

直接传入少量 URL：

```bash
python -m bili_recipe_notes "https://www.bilibili.com/video/BV1xxxx" \
  --batch \
  --batch-url "https://www.bilibili.com/video/BV2xxxx" \
  --batch-id two-videos \
  --target-stage recipe
```

也可以从管道读取，每行一个 URL；空行和以 `#` 开头的注释会被忽略，重复链接自动去重：

```bash
printf '%s\n' \
  'https://www.bilibili.com/video/BV1xxxx' \
  'https://www.bilibili.com/video/BV2xxxx' | \
python -m bili_recipe_notes --batch --batch-file - --batch-id piped-videos --target-stage raw
```

查看状态和重试失败项：

```bash
python -m bili_recipe_notes --list-batches
python -m bili_recipe_notes --show-batch my-recipes
python -m bili_recipe_notes \
  --retry-batch my-recipes \
  --target-stage recipe \
  --cookies data/state/cookies/bilibili-edge.txt
```

Windows PowerShell 的参数完全相同，只需按 PowerShell 语法换行：

```powershell
python -m bili_recipe_notes `
  --batch `
  --batch-file "data\creators\419872064-老饭骨\video_links.txt" `
  --batch-id laofangu-win `
  --target-stage raw `
  --cookies "data\state\cookies\bilibili-edge.txt" `
  --whisper-model medium
```

批次状态保存在 `data/state/batches/<批次ID>.json`，输出仍写入 `--out` 指定的目录。每次续跑都会采用当前命令传入的 Whisper、LLM、截图和审核设置；Cookie 不会写入工作产物，换电脑后应传入该电脑自己的 Cookie 文件。批次中只要有条目失败，CLI 会返回退出码 `1`；全部成功或没有待处理条目时返回 `0`。

完整菜谱目录采用 `<UP主>/规范菜名--BVID`，分 P/CID 视频会继续附加稳定分段 ID。原始阶段暂时使用 `<UP主>/待整理--BVID`，菜谱抽取成功后自动改成最终名称；UP 主子目录便于人工浏览，视频宣传标题保存在元数据中。旧目录可先预览、确认无冲突后再迁移：

```bash
python -m bili_recipe_notes --normalize-output-folders preview --out data/recipes
python -m bili_recipe_notes --normalize-output-folders apply --out data/recipes
```

迁移会同步更新 `job.json`、批次状态和移动端索引，并在 `data/state/migrations/` 保存旧路径到新路径的清单。运行中的后台批次存在时会拒绝迁移。

准备合并最终版菜谱前，可先生成不会修改原始输出的审核表：

```bash
python -m bili_recipe_notes --export-curation-review --out data/recipes
```

默认生成 `data/recipes/curation-review/recipe-review.csv` 和 `recipe-review.json`。工具会聚合同名菜谱，谨慎提示仅相差一个字的近似名称，并根据步骤完整度、视频时长、质量分、字幕重合和推广信号推荐 `primary_candidate`、`variant_candidate`、`short_clip_candidate`、`exclude_candidate` 或 `name_review_candidate`。也可以在管理页的“菜谱版本整理”中对照各来源的用料、步骤、字幕证据和原视频，记录主版本、不同做法、相关短剪或排除意见。人工结果独立保存在 `data/recipes/curation-review/curation-decisions.json`，重新扫描不会覆盖；这些意见不会自动合并、隐藏或修改原菜谱。

### AI 审核归类

在“任务中心 → 新建 AI 任务”选择“AI 审核与归类”，或先在菜谱库勾选菜品再创建批量任务。“审核工作台”也可对当前一道菜重新审核。任务在后台顺序运行，进度每 5 秒更新；“完成当前条后暂停”保留已保存结果，之后可以继续未完成项或重试失败项。续跑默认沿用原任务设置，也可勾选使用当前设置。超时等短暂错误默认最多尝试 3 次并退避重试；配置错误或连续失败达到阈值时暂停，保留原因和尝试记录。

默认跳过有效审核。菜谱用料、步骤、字幕、来源上下文或审核规则变化后会提示重审；评分、封面、截图、检索分类标签和 Markdown 排版不使审核失效。旧版只有菜谱文件哈希的审核需要重新审核，原有人工展示决定继续有效。

AI 会把内容归为“菜谱”“烹饪技巧”“非菜谱”或“无法确认”，并对菜谱逐项核对食材、调料和步骤的字幕依据。其他分类可仅给出内容判断和证据。只有证据明确、置信度至少为 `0.9` 的烹饪技巧或非菜谱才会默认隐藏，不再主动进入菜谱展示和点餐。完整做菜视频不会仅因含推广口播被隐藏，同名菜谱的合理做法变体也会保留。证据不足的内容继续展示并标记待人工确认。

“审核工作台 → 内容与证据”并列展示生成版原文、可检索的字幕和 AI 意见，支持跳转到原视频时间点、修正内容分类、逐项记录核对结论，并从队列选择下一条待确认内容。菜谱需要所有用料和步骤明确核实后才能完成人工确认；技巧或非菜谱只需核实分类。通过字幕确认时保留原文引用，通过视频确认时填写依据说明。人工逐项确认随内容变化失效，但单纯 AI 规则升级不会清除人工确认。

在审核页查看隐藏项，可以阅读归类理由并人工恢复展示；人工分类和展示决定会在后续复审中保留。菜谱库、详情、烹饪模式和点餐共用展示规则，人工恢复可覆盖 AI 与旧来源排除分类；菜单的手动下架仍需在“菜谱库”重新上架，手机菜单在下一次同步时更新。“清除人工展示覆盖”后恢复按分类和有效审核决定展示。

审核记录独立写入菜谱目录的 `recipe.ai-review.json`，批次状态位于输出根目录的 `.ai-review/`，每次变更前的记录保存在 `.ai-review/history/`，可在“审核历史”查看。原始报告、字幕、来源、任务与质量报告，以及既有 `recipe.json`、`note.md`、人工审核文件和媒体均不会被本流程改写、删除或移动。AI 的修改意见也只保存在审核记录中。普通浏览缺失质量报告时仅在内存中计算，不自动补写文件。

移动端后台按变更增量更新索引和图片哈希，文件读取使用内存缓存并检查失效；单次异常后自动退避重试，保留上次可用菜谱。管理首页和设置页显示索引状态，`/api/v1/health` 和需设备认证的 `/api/v1/index-status` 提供运行状态。

项目内的 [AGENTS.md](AGENTS.md) 和 [菜谱审核 Agent 指南](docs/agents/recipe-audit.md) 规定了审核工作流、分类边界、证据要求及人工恢复规则，可用于指导 AI 后续整理。原有“审核确认”的生成版修改流程位于“菜谱详情 → 菜谱修订工具 → 生成版逐项审核”，需要明确确认后才会写入菜谱。

### 迁移到另一台电脑

需要把当前菜谱数据、知识库和整理进度搬到另一台已从 Git 获取源码的服务器时，生成数据迁移包：

```bash
python -m bili_recipe_notes --export-deployment-bundle --out data/recipes
```

默认写入 `data/exports/deployments/bili-recipe-notes-<时间>.deployment.zip`。包内包含菜谱 JSON/Markdown、字幕、图片、UP 主链接清单、知识库、批次、整理和审核记录，以及移动同步 SQLite 的一致快照、实践照片、`sources/` 原资料和默认 Obsidian 归档。数据库保留设备配对、套餐、本餐、实践记录、来源分类、人工上下架和推荐状态，JSON 固定备份一并保留。导出结束前会逐文件校验大小和 SHA-256。

数据包包含私人使用记录和设备认证信息，应作为私有备份保存。源码、依赖、启动脚本、Cookie、运行环境、Git 历史、运行日志、缓存、原始音视频和已有 ZIP 不进入数据包；外部自定义 Obsidian vault 需单独备份。需要原样保留 Cookie、媒体和历史导出包时，停止所有后台任务和服务后直接复制整个数据根目录。

目标服务器先从 Git 获取同版本应用源码和运行环境，再把数据包解压到临时目录；将包内的 `bili-recipe-notes/data/` 放到项目根目录，或放到独立磁盘并设置 `BILI_RECIPE_DATA_DIR`。恢复前先停止目标服务并备份已有数据，避免覆盖同名内容。

Windows PowerShell 示例（项目位于 `C:\apps\bili-recipe-notes`）：

```powershell
Expand-Archive .\bili-recipe-notes-*.deployment.zip -DestinationPath C:\temp\bili-recipe-data
Copy-Item -Recurse -Force C:\temp\bili-recipe-data\bili-recipe-notes\data C:\apps\bili-recipe-notes\
```

Linux/macOS 示例（项目位于 `/srv/apps/bili-recipe-notes`）：

```bash
unzip bili-recipe-notes-*.deployment.zip -d /tmp/bili-recipe-data
rsync -a /tmp/bili-recipe-data/bili-recipe-notes/data/ /srv/apps/bili-recipe-notes/data/
```

恢复后按项目现有方式启动应用，移动菜谱索引会自动更新。首次进入“菜谱版本整理”后点击“更新整理清单”，让报告路径适配新服务器。包内另附 `DATA-MIGRATION.md`，可离线查看恢复说明。

### React 管理工作台

先构建网页，再启动统一服务（Node.js 24+；Python 3.10+）：

```bash
cd web
corepack pnpm@12.6.0 install --frozen-lockfile
corepack pnpm@12.6.0 build
cd ..
.venv/bin/python -m bili_recipe_notes.ui_launcher
```

Windows PowerShell 将最后一行替换为 `.\.venv\Scripts\python.exe -m bili_recipe_notes.ui_launcher`。默认监听 `127.0.0.1:8765` 并打开 `/admin/`；点餐入口为同一服务的 `/`。服务器无浏览器时加 `--no-browser`；家庭局域网访问可加 `--host 0.0.0.0`，端口通过 `--port` 指定。

| 主导航 | 用途 |
| --- | --- |
| 首页 | 菜谱与待处理数量、运行中任务、最近菜谱和快捷入口 |
| 菜谱库 | 搜索、分类/展示/上下架筛选、分页多选；详情、烹饪、编辑和 AI 操作 |
| 审核工作台 | 原文/字幕/逐项意见、分类与恢复、AI 或手工封面候选、裁剪确认和历史 |
| 采集与生成 | 视频或清单导入、UP 主链接更新；创建待处理批次或立即后台执行 |
| 任务中心 | 统一进度、当前条目、失败原因、日志及各任务支持的暂停/续跑/重试 |
| 设置与数据 | 模型、转写、品牌、登录、设备、目录、工作交接和环境检查 |

知识库、草稿归档和菜谱版本整理位于侧栏次级入口。“菜单与同步管理”从菜谱库或设置进入，可生成菜单图片、推荐菜品、生成加入二维码、处理实践心得和同步冲突。菜谱详情的修订工具提供笔记优化/重写、字幕修订与重新提取、步骤图片和二次分析。“导出菜谱”可生成 Markdown、Obsidian Markdown、PDF、Word 或 ZIP，完成后从任务中心下载；PDF/Word 依赖缺失时会提示格式降级。

菜谱库和审核队列用 URL 保存筛选和页码，支持浏览器前进/后退。编辑草稿暂存在当前标签页；过期编辑拒绝覆盖。菜谱浏览读取后台索引，任务轮询不触发整库扫描。更改默认菜谱目录后需重启服务，当前索引和任务仍使用原目录。管理页面仍面向可信家庭局域网，没有新增账号体系。

源码快捷启动脚本同样启动 React/FastAPI 服务：

- Windows：`start-ui-windows.bat`
- macOS：`start-ui-mac.command`
- Linux：`start-ui-linux.sh`

脚本会检查网页构建产物，创建 `.venv` 并安装需要的 Python 依赖，然后监听 `0.0.0.0:8765`。首次使用前先完成上述网页构建。Linux/macOS 若缺少执行权限，可对对应脚本运行 `chmod +x`。Linux/macOS 的服务输出追加到 `data/logs/admin-web.log`；后台任务仍使用各自持久状态。

默认配置保存到 `data/state/config.json`。完整页面映射、模块职责、数据位置和验证命令见 [React 管理界面说明](docs/react-admin-migration.md)，前端开发方式见 [web/README.md](web/README.md)。

### 旧版 Streamlit 管理页

旧管理页作为兼容入口保留，需要单独启动：

```bash
python -m bili_recipe_notes.ui_launcher --legacy-ui
```

手动打开 `http://127.0.0.1:8501`。需设置其他 Streamlit 参数时，可以直接运行：

```bash
ARROW_DEFAULT_MEMORY_POOL=system python -m streamlit run bili_recipe_notes/ui.py \
  --server.address=127.0.0.1 --server.port=8501 --server.headless=true
```

Windows PowerShell 可先设置 `$env:ARROW_DEFAULT_MEMORY_POOL = "system"`，再执行同样的 `python -m streamlit run` 命令。远程兼容页可通过 `ssh -N -L 8501:127.0.0.1:8501 用户名@服务器地址` 转发访问。新旧页面共用数据；快捷启动脚本默认启动 React，不会自动启动这个兼容页。旧页面顶部也提供新版 `/admin/` 入口。

### 后台任务与恢复

“任务中心”和首页的运行摘要每 5 秒更新。任务详情显示处理数量、当前内容、失败原因、日志及可估算的剩余时间。状态筛选可区分待开始、进行中、需要处理和已完成；选中历史任务不会停止其他后台任务。

生成批次沿用原进程控制，Linux/macOS 支持即时暂停，Windows 暂不提供该按钮。AI 审核、介绍和选图在当前菜谱完成后暂停。暂停或中断后，可继续未完成项或重试失败项；原始资料批次完成后可继续生成完整菜谱。

关闭浏览器不停止后台任务。数据交接、知识提取、批量归档、菜单图片和修订工具在服务进程内执行；服务退出后会显示中断，需要在重启后的任务中心继续或重试。逐条归档和知识提取保留已成功结果。

### 浏览和整理菜谱库

“菜谱库”从已有索引搜索菜名、食材、UP 主及标签，支持分类、上下架和展示状态筛选。默认展示可见菜谱；已隐藏内容可以通过筛选找到，并进入审核工作台核对或恢复。每页展示 24 道，多页勾选可用于上下架或安排 AI 审核、介绍、选图任务。

“菜谱版本整理”独立保存同名/近似名来源的取舍意见，保留合理做法变体；“草稿与归档”处理当前笔记的 Obsidian 状态及批量归档。两者均可从菜谱库快速进入。

### 烹饪技巧专栏

“厨房知识库”独立于菜谱和点餐流程，集中浏览与搜索通用技巧、原理、适用场景、标签和来源依据，并提供确认、编辑、复习和实践记录。旧 Streamlit 的“烹饪技巧”只读专栏仍可通过兼容入口使用。

知识库也支持上传可复制文字的 PDF、菜谱截图/书页图片，或外部 OCR 导出的 TXT/Markdown。图片会先通过本机 Tesseract OCR 转成文字，再自动提取通用技巧，并保留图片来源和 OCR 原文摘录。候选与已确认知识可分别筛选，文档提取在后台运行；合并、版本恢复和 Markdown/CSV/Anki 导出位于知识库的“导出与整理”。

图片 OCR 需要本机安装 Tesseract 及 `chi_sim` 中文语言包；若不方便安装，也可以先用手机或其他工具 OCR 后上传 TXT。

### 完整菜谱查看

在菜谱库点击图片或菜名进入详情，查看简介、完整用料、准备事项、工具、步骤图片、火候、时长、小贴士、待确认事项与原视频。背景知识另行标注外部来源和匹配限制。详情提供“开始烹饪”、编辑、审核、AI 选图/介绍和修订工具入口；返回菜谱库时恢复之前的筛选。

### 本餐点菜与套餐

管理页顶部“开始点餐”打开独立的 `/` 点餐端。页面提供图片菜单、分类和搜索、菜品详情、加菜、份量与口味备注；本餐在多台已加入的设备间共享，也可保存和载入套餐、查看采购清单。

旧 Streamlit 的“本餐点菜”兼容入口另保留按总人数、儿童人数和饭局类型推荐组合的功能；独立 React 点餐端使用共享本餐与已保存套餐。

本餐中的每道菜可以单独调整份量倍率和备注，实践心得可在点餐端记录并同步。共享本餐、套餐与实践数据保存在移动同步数据库中；旧 Streamlit 的独立套餐库使用 `data/state/meal-plans.json`。

### 移动烹饪模式

在 React 菜谱详情点击“开始烹饪”，可以聚焦当前步骤并前后切换。用料支持勾选和倍率显示，明确的简单数字与单位可以缩放；范围、分数、“少许”“适量”以及复杂用量保留原文。换算和勾选只影响当前页面，不覆盖 `recipe.json`。

旧 Streamlit 的“烹饪模式”另保留按目标人数换算、公制换算和购物清单下载。

### 草稿、人工修改与 Obsidian 归档

每次抓取和 AI 抽取成功后，`recipe.json` 与 `note.md` 都先作为可修改的工作副本保留。完整流程为：

```text
抓取/转写 → AI 菜谱草稿 → 可选逐项审核 → 可选完整编辑 → Obsidian 归档
                                          ↘ 无需修改时直接归档
```

“草稿与归档”页显示待归档、已归档、归档后有修改、归档异常四种状态，支持搜索、分页和多选后台归档。归档后笔记发生变化会提示重新归档。批量操作保护 Obsidian 中的手写内容；遇到冲突会记录该条失败并继续处理其他菜谱。确认当前内容后，也可在编辑页的“笔记与归档”单独归档。

Vault 默认结构：

```text
data/exports/obsidian-vault/
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

归档前可以填写 1–5 星“个人喜爱度”。系统会根据总耗时、步骤数量和复杂技法自动给出“烹饪难度”和“时间投入”评级，三个评级都可以在 React 编辑页的“基本信息”中手动调整。最终评级同时写入菜谱正文、Obsidian YAML frontmatter、`recipe.json` 和 `archive.json`，便于以后按喜爱度或投入成本检索。

AI 提炼的通用烹饪技巧先标记为“AI 候选”。在知识库中人工编辑并点击“确认并收录为干货”后，才会同步到 Vault 的 `烹饪技巧/`；最终技巧笔记不会包含置信度或内部审核证据。

批量任务状态会保存到：

```text
data/state/batches/
```

UP 主全量链接会按 UID 独立保存：

```text
data/creators/<UID>-<UP名称>/
├── video_links.txt
└── creator.json
```

抓取后可以默认全选并排除少量非菜谱视频；链接文档始终保留抓到的全部视频，批次只包含最终勾选项。可以仅创建待执行批次，不立即下载字幕或调用 LLM。在“采集与生成”中可填写统一的 UP 主名称；生成后会保留视频原始 `uploader`，并额外写入规范化的 `creator_name`。

“采集与生成”中的来源归类可把广告、预告或无用内容标记为非菜谱或烹饪技巧。记录保存在移动同步 SQLite 的 `video_sources` 表中，源文件不会被删除，但不会再进入点餐菜单；以后导入同一链接会在执行前自动排除。烹饪技巧来源会在“知识库”页面单独列出，可直接载入历史视频提取通用技巧。误判可在同页恢复为菜谱来源。

新生成菜谱会在步骤截图之外专门从视频后段、出锅或装盘步骤选取 `images/cover.jpg`。手机点餐和导出图片菜单优先使用这张成品图；旧菜谱没有独立封面时改为优先使用最后一张步骤图，避免把切菜、备料画面当作菜单缩略图。

需要人工把关时，进入“审核工作台 → 成品图”。可以选择现有步骤图、AI 特写候选、视频自动候选、按秒截图或上传真实成品照片，再拖动 4:3 裁剪框确认。已确认封面可以重新裁剪；明确设置“暂无合适图片”后保留无图状态。确认后更新移动同步索引，菜谱或来源变化后的旧候选不可应用。

### 在 Mac、Windows 与 Linux 间交接工作

“设置与数据 → 数据与交接”可以把一个批次后台导出为 `.handoff.zip`。交接包会保存：

- 批次内的全部视频 URL，包括尚未执行的条目；
- 已完成原始阶段的 `source.json`、`transcript.json` 与阶段状态；
- 已完成菜谱阶段的 `recipe.json`、`note.md`、质量报告、审核文件和步骤图片；
- 与该批次匹配的 UP 主 `video_links.txt` 和 `creator.json`。

交接包不会保存 Bilibili Cookie、临时音频/视频、Obsidian 本机归档路径或原电脑绝对路径。导入另一台电脑后，文件路径会自动映射到当前“输出目录”；完成度较低的传入结果不会覆盖更完整的本地结果，同等完成度采用传入版本并在覆盖前保留 `.bak`。然后到“任务中心”选择该批次，点击“继续未完成”即可从已有阶段继续。

推荐的实际用法：在 Mac 导出后用 AirDrop、U 盘、局域网共享或网盘传送 ZIP；Windows 处理完成后再导出同一批次并传回 Mac。每台电脑都需要单独安装运行环境，并在需要访问登录态视频时从本机 Edge 重新导入 Cookie。大于 200 MB 的交接包建议直接传文件，不通过浏览器下载按钮。

Linux 服务器不需要启动 UI。导入交接包：

```bash
python -m bili_recipe_notes \
  --import-handoff /srv/transfer/my-recipes.handoff.zip \
  --out data/recipes
```

成功后最后一行会输出 `BATCH_ID=<批次ID>`。查看并继续运行：

```bash
python -m bili_recipe_notes --show-batch my-recipes
python -m bili_recipe_notes \
  --resume-batch my-recipes \
  --target-stage recipe \
  --cookies /srv/secrets/bilibili.txt \
  --whisper-model medium
```

处理完成后从 Linux 导出到共享目录：

```bash
python -m bili_recipe_notes \
  --export-handoff my-recipes \
  --out data/recipes \
  --handoff-destination /srv/transfer/out
```

成功后最后一行会输出 `HANDOFF_PATH=<交接包路径>`，可把该文件传回 Mac 或另一台服务器继续导入。同一批次在两台电脑上合并时仍采用前述完成度规则，Cookie 不会进入交接包。

个人厨艺知识库会保存到：

```text
data/state/knowledge_base.json
```

知识库独立于单个视频输出目录，用于长期积累从不同视频中提炼出的通用技巧、原理、适用场景和视频依据。
相似知识会尽量自动合并，并保留多个来源视频。你也可以在 UI 中手动编辑、合并、复习和记录实践。

配置、批次、知识库和质量报告采用原子替换写入；覆盖前会把上一版保留为同目录下的 `*.bak`。如果 JSON 已损坏，程序会明确报错并停止覆盖。此时应先检查并恢复对应的 `.bak`，不要用空文件覆盖原数据。

部署包会携带 `data/state/knowledge_base.json` 和固定备份；批次交接包也会携带知识库，保证迁移电脑时不会丢失个人烹饪技巧。

生成和修复后的质量报告会保存到每个菜谱输出目录：

```text
data/recipes/<UP主>/规范菜名--BVID/quality.json
```

### LLM provider

默认 provider 是 `opencode`。UI 也提供：

- `none`：不重写，直接使用规则版 Markdown
- `codex`：使用本机 Codex CLI 的 `codex exec` 非交互模式，需要本机已安装并登录 Codex CLI
- `openai`：使用 OpenAI Responses API，需要设置环境变量 `OPENAI_API_KEY`
- `local`：把提示词通过 stdin 传给本地命令

Codex 模式可在 UI 里填写模型名和 profile；两者都可留空，留空时使用 Codex CLI 默认配置。OpenAI 模式的模型名可在 UI 里修改，默认值保存在本地配置中。

#### LLM CLI 高级提示词

在“设置与数据 → 模型与登录”填写附加提示词，供 `opencode`、`codex` 或 `local` 使用；旧 Streamlit 侧栏另保留“严格证据、家庭实用、专业厨房”预设。附加指令会用于：

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

“设置与数据”的登录配置支持从已登录的 Edge 手动导入和刷新。工具只保留 Bilibili 域 Cookie，验证登录成功后保存到 `data/state/cookies/bilibili-edge.txt`，不会在日志或批次文件中写入 Cookie 内容。

如果遇到 Bilibili `HTTP Error 412: Precondition Failed`：

- CLI/UI 会自动刷新匿名请求指纹并重试，默认最多尝试 5 次，等待时间依次为 30、60、120、240 秒；前台 CLI 会在终端显示等待和重试进度。
- 5 次仍失败后才会把当前阶段标记为失败，批处理可以继续处理其他条目，不需要手动终止程序。
- 先关闭 UI 重新双击启动文件，启动脚本会自动检查并重装项目锁定的 `yt-dlp` 版本。
- 需要升级时先更新 `requirements.txt` 中的版本号，再执行 `python -m pip install -r requirements.txt`。
- 如果视频需要登录态，重新从浏览器导出最新 `cookies.txt`，并在 CLI/UI 中传入。

## 输出示例

```text
data/recipes/<UP主>/规范菜名--BVID/
├── source.json          # 原视频元数据；原始阶段即生成
├── recipe.json
├── recipe.review.json   # 开启逐项审核时生成
├── recipe.ai-review.json # AI 审核归类的独立记录；不覆盖生成版
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
- Pull Request 会运行 Python 测试、语法检查、CLI 打包启动冒烟测试，以及 Web 锁定依赖安装、构建和同步行为测试。
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
