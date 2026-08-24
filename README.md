# bili-recipe-notes

本项目是一个本地 Python CLI 小工具，用于从 Bilibili 视频生成**个人学习用**菜谱笔记。

家庭服务器同时提供管理页与移动端：`http://服务器:8501/` 用于生成、审核和设备管理，`http://服务器:8765/` 是多人共享点餐客户端与 API。首次直接打开点餐网址并输入设备名称即可加入，之后菜谱、心得、套餐和当前本餐会自动同步；管理员可随时锁定新设备加入。点餐菜单支持主菜、肉类、海鲜、主食、面条、糕点等重叠分类与搜索组合筛选，管理页可逐项或批量上下架菜谱，首次升级默认全部上架。当前上架菜品还可一键生成适合转发的 Chef Zhai 菜单长图，或导出 300 DPI 的 A4 分页图片用于打印。原有 `mobile/` Flutter 客户端仍可同步菜谱与心得。开发说明见 [`mobile/README.md`](mobile/README.md) 和 [`web/README.md`](web/README.md)。

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
- `--creator-links-file all_links.txt`（提取结果文件名，默认 `creator_video_links.txt`）
- `--batch`（创建持久化批次并在当前终端顺序执行）
- `--batch-file links.txt`（从文本文件读取批量 URL，可重复使用；传 `-` 时从 stdin 读取）
- `--batch-url URL`（额外加入一个 URL，可多次指定）
- `--batch-id ID`（为新批次指定便于记忆的稳定 ID）
- `--target-stage raw|recipe`（只形成原始版，或继续到完整菜谱版）
- `--create-only`（只创建 pending 批次，不执行下载、字幕、Whisper 或 LLM）
- `--resume-batch ID` / `--retry-batch ID`（继续未完成阶段 / 只重试失败阶段）
- `--list-batches` / `--show-batch ID`（查看批次列表或逐条状态）
- `--normalize-output-folders preview|apply`（预览或执行旧输出目录规范化）
- `--export-curation-review [PATH]`（生成同名/近似名菜谱审核表）
- `--export-deployment-bundle [PATH]`（导出源码、菜谱、图片和整理进度的跨平台部署包）
- `--export-handoff ID`（把批次和已完成工作导出为跨平台交接包）
- `--import-handoff PATH`（校验并导入 `.handoff.zip`）
- `--handoff-destination PATH`（指定交接包导出文件或目录）

提取博主全部视频链接示例：

```bash
python -m bili_recipe_notes "https://space.bilibili.com/123456/video" --creator-home --out outputs
```

执行后会在 `outputs/creator_video_links.txt` 生成该主页下全部视频 URL，便于后续批量整理菜谱。

### CLI 批量处理

批量 CLI 与 UI 使用同一套批次文件和两阶段流水线。命令默认在当前终端前台顺序运行，每完成一个阶段都会保存状态；终端意外关闭后可按批次 ID 续跑，不需要保持 UI 开启。

从 UP 主已保存链接清单运行到原始版：

```bash
python -m bili_recipe_notes \
  --batch \
  --batch-file "outputs/creators/419872064-老饭骨/video_links.txt" \
  --batch-id laofangu-raw \
  --target-stage raw \
  --cookies .bili-recipe-notes/cookies/bilibili-edge.txt \
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
  --cookies .bili-recipe-notes/cookies/bilibili-edge.txt \
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
  --cookies .bili-recipe-notes/cookies/bilibili-edge.txt
```

Windows PowerShell 的参数完全相同，只需按 PowerShell 语法换行：

```powershell
python -m bili_recipe_notes `
  --batch `
  --batch-file "outputs\creators\419872064-老饭骨\video_links.txt" `
  --batch-id laofangu-win `
  --target-stage raw `
  --cookies ".bili-recipe-notes\cookies\bilibili-edge.txt" `
  --whisper-model medium
```

批次状态保存在 `.bili-recipe-notes/batches/<批次ID>.json`，输出仍写入 `--out` 指定的目录。每次续跑都会采用当前命令传入的 Whisper、LLM、截图和审核设置；Cookie 不会写入工作产物，换电脑后应传入该电脑自己的 Cookie 文件。批次中只要有条目失败，CLI 会返回退出码 `1`；全部成功或没有待处理条目时返回 `0`。

完整菜谱目录采用 `规范菜名--BVID`，分 P/CID 视频会继续附加稳定分段 ID。原始阶段暂时使用 `待整理--BVID`，菜谱抽取成功后自动改成最终名称；视频宣传标题和 UP 主名称只保存在元数据中，不进入目录名。旧目录可先预览、确认无冲突后再迁移：

```bash
python -m bili_recipe_notes --normalize-output-folders preview --out outputs
python -m bili_recipe_notes --normalize-output-folders apply --out outputs
```

迁移会同步更新 `job.json`、批次状态和移动端索引，并在 `.bili-recipe-notes/migrations/` 保存旧路径到新路径的清单。运行中的后台批次存在时会拒绝迁移。

准备合并最终版菜谱前，可先生成不会修改原始输出的审核表：

```bash
python -m bili_recipe_notes --export-curation-review --out outputs
```

默认生成 `outputs/curation-review/recipe-review.csv` 和 `recipe-review.json`。工具会聚合同名菜谱，谨慎提示仅相差一个字的近似名称，并根据步骤完整度、视频时长、质量分、字幕重合和推广信号推荐 `primary_candidate`、`variant_candidate`、`short_clip_candidate`、`exclude_candidate` 或 `name_review_candidate`。也可以在网页的“最终菜谱整理”页面对照全部来源的用料、步骤、字幕证据和原视频，保存主版本、不同做法、短剪合并或排除决定。人工结果独立保存在 `outputs/curation-review/curation-decisions.json`，重新扫描不会覆盖。

### 迁移到另一台电脑

需要把当前源码、全部输出和整理进度一起搬走时，生成跨平台部署包：

```bash
python -m bili_recipe_notes --export-deployment-bundle --out outputs
```

默认写入 `deployments/bili-recipe-notes-<时间>.deployment.zip`。包内包含当前应用源码、依赖清单、启动脚本、菜谱 JSON/Markdown、字幕、步骤图片、批次状态、整理报告和人工决定；不会包含 Cookie、`.venv`、Git 历史、缓存、原始音视频、`.bak`、已有 ZIP 或移动端数据库。导出结束前会逐文件校验大小和 SHA-256。

Windows PowerShell 解压后，在包内的 `bili-recipe-notes` 目录运行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start-ui-windows.bat
```

Linux 解压后如需让可信局域网内的其他机器访问，运行：

```bash
chmod +x start-ui-linux.sh
./start-ui-linux.sh
```

macOS 解压后运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
ARROW_DEFAULT_MEMORY_POOL=system .venv/bin/python -m streamlit run bili_recipe_notes/ui.py \
  --server.address=127.0.0.1 --server.port=8501 --server.headless=true
```

首次进入“最终菜谱整理”后点击“重新扫描输出”，让报告路径适配新电脑。已经保存的人工决定使用稳定目录 ID，不会被重新扫描覆盖。包内另附 `DEPLOYMENT.md`，可离线查看相同步骤。

### 本地 UI

安装依赖后可启动本地 Web UI。macOS/Linux 直接运行时建议在 PyArrow 导入前选择系统内存池：

```bash
ARROW_DEFAULT_MEMORY_POOL=system python -m streamlit run bili_recipe_notes/ui.py \
  --server.address=127.0.0.1 \
  --browser.serverAddress=127.0.0.1
```

Windows PowerShell：

```powershell
$env:ARROW_DEFAULT_MEMORY_POOL = "system"
python -m streamlit run bili_recipe_notes/ui.py --server.address=127.0.0.1 --browser.serverAddress=127.0.0.1
```

代码运行在远程服务器时，仍应只监听服务器回环地址，再从自己的电脑通过 SSH 隧道访问。先在服务器运行：

```bash
cd /home/swenzhai/work/bili-recipe-note
ARROW_DEFAULT_MEMORY_POOL=system .venv/bin/python -m streamlit run bili_recipe_notes/ui.py \
  --server.address=127.0.0.1 --server.port=8501 --server.headless=true
```

再在自己的电脑保持以下命令运行，并打开 `http://127.0.0.1:8501`：

```bash
ssh -N -L 8501:127.0.0.1:8501 用户名@服务器地址
```

如果本机 `8501` 已占用，可改用 `-L 18501:127.0.0.1:8501` 并打开 `http://127.0.0.1:18501`。当前 Streamlit 页面没有账号认证，不应直接监听公网 `0.0.0.0`。

也可以直接双击项目根目录下的快捷启动文件：

- Windows: `start-ui-windows.bat`
- macOS: `start-ui-mac.command`
- Linux: `start-ui-linux.sh`

Linux 下运行：

```bash
chmod +x start-ui-linux.sh
./start-ui-linux.sh
```

脚本会自动创建 `.venv`、安装或更新依赖，同时在 `0.0.0.0:8501` 启动管理页面、在 `0.0.0.0:8765` 同源启动移动点餐页面与 API。启动前需要在 `web/` 运行一次 `corepack pnpm install && corepack pnpm build`；启动器会检查 `web/dist/index.html`。局域网其他设备打开管理页生成的配对链接即可使用。

macOS 直接双击 `start-ui-mac.command` 即可。首次打开会自动在项目目录创建隔离的 `.venv` 并安装依赖，可能需要几分钟；以后双击会直接启动并打开浏览器，不会把依赖安装到 Conda base 环境。

macOS 启动器会自动设置稳定的 Arrow 内存池，把 Whisper 批处理放在独立进程，并在 UI 意外退出时最多自动重启 3 次。UI 诊断日志保存在 `.venv/ui.log`，手机 API 日志保存在 `.venv/mobile-api.log`。后台批次拥有独立 PID 和持久状态，即使网页刷新或 UI 重启也会继续运行。

如果从压缩包或网络下载后 macOS 丢失了执行权限，运行一次：

```bash
chmod +x start-ui-mac.command
```

UI 会在浏览器中打开，支持：

- 输入 Bilibili 视频链接并生成菜谱笔记
- 查看运行日志和 `note.md` 预览
- 在“任务仪表盘”查看所有后台批次的实时进度、当前处理项、平均速度、预计剩余时间和预计完成时刻
- 在“菜谱库全览”中查看菜谱总量、分类、菜系、热门标签及对应数量图表，并筛选完整明细
- 在“菜谱详情”中集中查看基本信息、全部用料、备菜、工具、完整步骤、图片和关键提示，并可切换到同一道菜的烹饪模式
- 从“本餐点菜”进入独立餐厅式点餐台，按图片卡片、分类和搜索直接加菜；也可按人数、儿童情况和饭局类型推荐组合，调整份量、汇总采购清单并保存套餐
- 在手机或窄屏浏览器中使用分步烹饪模式，逐步查看操作、火候、时长和关键截图
- 多台已配对设备自动加入唯一共享本餐，分别记录份量和备注，并实时查看整桌汇总
- 管理局域网手机客户端配对、设备撤销、实践日志和同步冲突
- 自动发布菜谱到移动端；旧版手动菜谱包导出保留在“高级功能”中
- 按目标人数自动缩放数字用量，并可将斤、两、杯、汤匙等换算为克或毫升
- 生成可勾选、可下载的临时购物清单；“少许、适量”和复杂复合用量会保留原文并提示人工确认
- 设置 cookies 文件路径、输出目录、语言、Whisper 模型
- 开关步骤截图、LLM 重写、临时媒体保留，并设置最终步骤/关键图片上限
- 可选生成逐项审核版，在“审核确认”中采用、修改后采用或跳过每一项
- 在“最终菜谱整理”中横向比较同名和近似名来源，确认主版本、不同做法、短剪合并或排除
- 生成结果先进入“草稿与归档”，可以不修改直接存档，也可以完整编辑或审核后再存档
- 在编辑页修改分类、菜系、标签、配料、步骤、关键点，或直接手写最终 Markdown
- 将最终版本归档到现有或新建的 Obsidian vault；同一来源重复归档会更新原笔记
- 批量任务完成后可逐条编辑、审核、归档，也可一次归档全部已完成草稿
- 批处理可选填统一的 UP 主名称，并将名称同时写入菜谱和视频来源数据库
- 广告或无用视频可归类为“非菜谱”；整集烹饪技巧可归类为“烹饪技巧”学习资料。两者都会保留去重记录、后续自动跳过，也可在批处理页恢复误判
- 提取 UP 主主页下的视频链接
- 递归展开 UP 主投稿中的隐藏合集，并按 UP 主长期保存完整链接清单
- 扫描历史记录、搜索并预览已生成的菜谱
- 在历史记录中可选择“仅重写 note.md”，只基于本地 `recipe.json` 重试 LLM，不重新下载视频
- 批量读取多行 URL 或链接文件，失败不中断，已生成内容可跳过，并支持断点续跑
- 批次可只运行到“原始版”（元数据与字幕/转写），之后再批量继续生成菜谱，避免重复抓取
- 大批次在后台顺序运行，页面会立即返回；可刷新查看每条阶段状态和最新日志
- 可把整个批次导出为跨平台工作交接包，在 macOS 与 Windows 间恢复产物和续跑状态
- 检查本地环境：依赖包、ffmpeg、yt-dlp Bilibili 支持、opencode、Codex CLI、Whisper
- 编辑 `recipe.json` / `transcript.json` 后重新生成笔记
- 步骤图多候选优选、精确重截、上传替代图或明确不配图；另行从末段/装盘阶段选择成品菜封面，低质量候选不会被强行保存
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

三个平台的启动器都会启动 `8501` 管理页和 `8765` 移动点餐/API。移动端使用长期设备令牌，管理页面本身没有登录认证，因此两项服务都只适合可信家庭局域网，不能暴露到互联网或公共 Wi-Fi。输出仍写入 `outputs/` 或界面中指定的目录。

UI 默认配置会保存到：

```text
.bili-recipe-notes/config.json
```

支持保存的默认项包括输出目录、cookies 路径、语言、Whisper 模型、截图/LLM/保留媒体开关、LLM provider、步骤/图片上限、审核版开关、Obsidian vault 路径和自动归档策略。

### 后台任务仪表盘

“任务仪表盘”默认每 10 秒刷新一次，显示正在运行的批次、当前处理链接、成功/失败/剩余数量和平均处理速度。预计剩余时间按本次运行的实际平均速度计算，会随着视频长度、字幕获取和 LLM 处理速度动态变化；可从仪表盘直接跳转到对应批次详情。

### 菜谱库全览

“菜谱库全览”会根据当前输出目录中的 `recipe.json` 自动统计菜谱总数、归档分类、菜系和标签，使用柱状图与完整计数表同时展示。页面下方可按分类、菜系、菜名、UP 主或标签筛选明细，并可直接选择一条菜谱进入完整详情。

### 完整菜谱查看

在“菜谱详情”顶部可按菜名、分类、UP 主或标签搜索，通过快捷按钮或上一道/下一道快速切换。选中后可以只读查看完整用料、备菜事项、工具、全部步骤图片、火候、时长、关键提示和待确认事项。右侧快捷操作栏可直接进入同一道菜的“烹饪模式”“编辑修复”或“审核确认”；“草稿与归档”和批量结果页也提供详情入口。

### 本餐点菜与套餐

主工作区的“本餐点菜”只保留点餐台入口，进入后使用独立 URL 界面，不会被后台管理菜单和表单干扰。点餐台使用双向定制前端组件复刻手机客户端的使用方式：暖色餐厅主题、图片菜单卡、横向分类和搜索、直接加菜、份量步进及口味备注；宽屏右侧固定显示本餐餐篮，手机窄屏使用“菜单 / 本餐”底部导航，不再使用菜谱下拉框。

点餐台可设置总人数、儿童人数和日常家宴、朋友聚餐、带小孩、清淡家宴等类型。系统根据现有菜谱的分类、标题、标签和完整度推荐荤菜、素菜、汤羹、主食等组合；带儿童时会降低明显辛辣和含酒菜品的优先级，但过敏原和实际忌口仍需人工确认。

本餐中的每道菜可以单独调整份量倍率和备注，页面会生成跨菜品采购汇总并支持下载。组合可保存到 `.bili-recipe-notes/meal-plans.json`，同一套餐可以记录多次实践日期、评分和调整经验；完整部署包会携带套餐库，方便迁移到另一台服务器。

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

UP 主全量链接会按 UID 独立保存：

```text
outputs/creators/<UID>-<UP名称>/
├── video_links.txt
└── creator.json
```

抓取后可以默认全选并排除少量非菜谱视频；链接文档始终保留抓到的全部视频，批次只包含最终勾选项。可以仅创建待执行批次，不立即下载字幕或调用 LLM。在“批量处理”中可选填或修正统一的 UP 主名称；生成后会保留视频原始 `uploader`，并额外写入规范化的 `creator_name`。

批次结果里的广告、预告或无用内容可以展开“归类来源：非菜谱或烹饪技巧”后批量标记。记录保存在移动同步 SQLite 的 `video_sources` 表中，源文件不会被删除，但不会再进入点餐菜单；以后导入同一链接会在执行前自动排除。烹饪技巧来源会在“知识库”页面单独列出，可直接载入历史视频提取通用技巧。误判可在同页恢复为菜谱来源。

新生成菜谱会在步骤截图之外专门从视频后段、出锅或装盘步骤选取 `images/cover.jpg`。手机点餐和导出图片菜单优先使用这张成品图；旧菜谱没有独立封面时改为优先使用最后一张步骤图，避免把切菜、备料画面当作菜单缩略图。

需要人工把关时，进入“手机客户端 → 点餐菜单管理 → 成品图审核”。审核台默认连续显示所有待审核的已上架菜品：可以从现有步骤图一键确认，也可以下载最高 1080p 的封面视频，从开头展示段、出锅和装盘段生成更多候选；高清候选最长边保留到 1920 像素，并显示实际分辨率。还支持按秒精确截图、上传真实成品照片，或明确设置“暂无合适图片”。点击任意图片后会在当前位置弹出裁剪窗口，直接在图片上拖动和缩放固定 4:3 的矩形框；单击即可出现，不需要跳回页面上方。审核台使用持久开关，生成候选图或页面刷新后不会自动收起。已确认封面同样可以重新裁剪。确认后会自动切到下一道菜并立即更新移动端同步索引。

### 在 Mac、Windows 与 Linux 间交接工作

UI 的“工作交接”页面可以把一个批次导出为 `.handoff.zip`。交接包会保存：

- 批次内的全部视频 URL，包括尚未执行的条目；
- 已完成原始阶段的 `source.json`、`transcript.json` 与阶段状态；
- 已完成菜谱阶段的 `recipe.json`、`note.md`、质量报告、审核文件和步骤图片；
- 与该批次匹配的 UP 主 `video_links.txt` 和 `creator.json`。

交接包不会保存 Bilibili Cookie、临时音频/视频、Obsidian 本机归档路径或原电脑绝对路径。导入另一台电脑后，文件路径会自动映射到当前“输出目录”；完成度较低的传入结果不会覆盖更完整的本地结果，同等完成度采用传入版本并在覆盖前保留 `.bak`。然后到“批量处理”选择该批次，点击“继续未完成”即可从已有阶段继续。

推荐的实际用法：在 Mac 导出后用 AirDrop、U 盘、局域网共享或网盘传送 ZIP；Windows 处理完成后再导出同一批次并传回 Mac。每台电脑都需要单独安装运行环境，并在需要访问登录态视频时从本机 Edge 重新导入 Cookie。大于 200 MB 的交接包建议直接传文件，不通过浏览器下载按钮。

Linux 服务器不需要启动 UI。导入交接包：

```bash
python -m bili_recipe_notes \
  --import-handoff /srv/transfer/my-recipes.handoff.zip \
  --out outputs
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
  --out outputs \
  --handoff-destination /srv/transfer/out
```

成功后最后一行会输出 `HANDOFF_PATH=<交接包路径>`，可把该文件传回 Mac 或另一台服务器继续导入。同一批次在两台电脑上合并时仍采用前述完成度规则，Cookie 不会进入交接包。

个人厨艺知识库会保存到：

```text
.bili-recipe-notes/knowledge_base.json
```

知识库独立于单个视频输出目录，用于长期积累从不同视频中提炼出的通用技巧、原理、适用场景和视频依据。
相似知识会尽量自动合并，并保留多个来源视频。你也可以在 UI 中手动编辑、合并、复习和记录实践。

配置、批次、知识库和质量报告采用原子替换写入；覆盖前会把上一版保留为同目录下的 `*.bak`。如果 JSON 已损坏，程序会明确报错并停止覆盖。此时应先检查并恢复对应的 `.bak`，不要用空文件覆盖原数据。

生成和修复后的质量报告会保存到每个菜谱输出目录：

```text
outputs/规范菜名--BVID/quality.json
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

本地 UI 的“Bilibili 登录”设置也支持直接从已登录的 Edge 手动导入和刷新。工具只保留 Bilibili 域 Cookie，验证登录成功后保存到 `.bili-recipe-notes/cookies/bilibili-edge.txt`，不会在日志或批次文件中写入 Cookie 内容。

如果遇到 Bilibili `HTTP Error 412: Precondition Failed`：

- CLI/UI 会自动刷新匿名请求指纹并重试，默认最多尝试 5 次，等待时间依次为 30、60、120、240 秒；前台 CLI 会在终端显示等待和重试进度。
- 5 次仍失败后才会把当前阶段标记为失败，批处理可以继续处理其他条目，不需要手动终止程序。
- 先关闭 UI 重新双击启动文件，启动脚本会自动检查并重装项目锁定的 `yt-dlp` 版本。
- 需要升级时先更新 `requirements.txt` 中的版本号，再执行 `python -m pip install -r requirements.txt`。
- 如果视频需要登录态，重新从浏览器导出最新 `cookies.txt`，并在 CLI/UI 中传入。

## 输出示例

```text
outputs/规范菜名--BVID/
├── source.json          # 原视频元数据；原始阶段即生成
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
