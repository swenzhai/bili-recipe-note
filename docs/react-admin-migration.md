# React 管理工作台

默认管理入口为 `http://服务器:8765/admin/`，同一服务的 `/` 保留家庭点餐客户端。前端使用现有 Vite + React 技术栈；Python 的采集、审核、选图、归档和同步服务继续使用已有实现。Streamlit 管理页仍可单独启动，用于兼容旧操作。

## 页面与功能映射

六个主导航集中日常操作，专业工具从菜谱详情或次级入口进入。

| 入口 | URL | 操作范围 |
| --- | --- | --- |
| 首页 | `/admin/` | 菜谱与待处理计数、运行中任务、最近菜谱、新增菜谱及 AI 快捷入口 |
| 菜谱库 | `/admin/library` | 按菜名、食材、UP 主搜索，分类、展示和上下架筛选，分页多选，批量上下架及 AI 任务 |
| 菜谱详情 | `/admin/recipes/<id>` | 简介、用料、步骤图片、来源与背景参考；食材勾选、简单用量倍数和分步烹饪 |
| 审核工作台 | `/admin/review` | 内容分类、展示覆盖、逐项人工意见、可检索字幕、历史记录；AI/手工封面候选及 4:3 裁剪确认 |
| 采集与生成 | `/admin/import` | 单视频、多行链接或链接文件导入，UP 主链接更新，原始资料/完整菜谱两阶段批次 |
| 任务中心 | `/admin/tasks` | 生成、审核、介绍、选图、UP 主、知识、归档、修订和数据任务的统一列表、详情及可用控制 |
| 设置与数据 | `/admin/settings` | 文本模型、转写、目录、品牌、Cookie、设备、数据交接和环境检查 |
| 编辑菜谱 | `/admin/recipes/<id>/edit` | 食材、调料、步骤、标签、评分及 Markdown 编辑，保存与单条 Obsidian 归档 |
| 菜谱修订工具 | `/admin/recipes/<id>/tools` | 优化/重写笔记、二次分析、字幕修订与重新提取、步骤图、生成版逐项审核及应用、Markdown/Obsidian/PDF/Word/ZIP 导出 |
| 草稿与归档 | `/admin/drafts` | 待归档/已归档/归档后修改/异常筛选，分页选择，后台批量归档 |
| 菜谱版本整理 | `/admin/curation` | 同名和近似名来源并排比较、主版本/变体等人工决定、冲突提示与导出 |
| 厨房知识库 | `/admin/knowledge` | 浏览、编辑、复习、实践、从菜谱或文档提取、合并、历史恢复与导出 |
| 菜单与同步管理 | `/admin/menu` | 上下架和主厨推荐、分享长图/A4 图片、加入链接/二维码、实践日志及同步冲突处理 |

草稿、版本整理、知识库放在侧栏次级入口；菜单与同步管理从菜谱库或设置页进入。顶部“开始点餐”打开 `/`，点餐页使用自己的布局与缓存。

旧入口对应关系：

- “单视频生成”“批量处理”“UP 主主页提取”归入“采集与生成”。
- “任务仪表盘”和各 AI 页的后台进度归入“任务中心”。
- “AI 审核归类”“快速审核”的独立核对、分类、恢复与封面操作归入“审核工作台”。
- 原“审核确认”中会改写生成版的操作放在“菜谱修订工具 → 生成版逐项审核”；它与独立审核意见分开。
- “编辑修复”、笔记优化、二次分析和步骤图片由详情进入编辑页或修订工具。
- “最终菜谱整理”改为“菜谱版本整理”；“手机客户端”的管理操作归入“菜单与同步管理”和“设置与数据”。

## 启动与开发

源码运行需要 Python 3.10+、Node.js 24+ 和 Corepack。先安装项目 Python 依赖，再构建前端：

```bash
cd web
corepack pnpm@12.6.0 install --frozen-lockfile
corepack pnpm@12.6.0 build
cd ..
.venv/bin/python -m bili_recipe_notes.ui_launcher
```

启动器默认监听 `127.0.0.1:8765` 并打开 `/admin/`。可用 `--port` 更改端口；家庭服务器示例：

```bash
.venv/bin/python -m bili_recipe_notes.ui_launcher --host 0.0.0.0 --port 8765 --no-browser
```

Windows 在 PowerShell 中将解释器替换为 `.\.venv\Scripts\python.exe`。`start-ui-linux.sh`、`start-ui-mac.command` 和 `start-ui-windows.bat` 均启动统一 React/FastAPI 服务，并检查 `web/dist/index.html` 是否存在。Linux/macOS 脚本将服务输出追加到 `data/logs/admin-web.log`。

开发时保持上述 Python 服务运行，在另一终端执行：

```bash
cd web
corepack pnpm@12.6.0 dev
```

打开 Vite 输出网址的 `/admin/`。Vite 将 `/api` 代理到 `http://127.0.0.1:8765`。后端端口有调整时，同步修改 `web/vite.config.ts` 的代理目标。

旧 Streamlit 页面需要显式启动：

```bash
.venv/bin/python -m bili_recipe_notes.ui_launcher --legacy-ui
```

旧页面使用 Streamlit 默认端口 `8501`；其端口和其他 Streamlit 参数可通过直接运行 `python -m streamlit run bili_recipe_notes/ui.py` 设置。旧页面与新页面操作同一份数据，旧页顶部提供返回新版 `/admin/` 的链接。管理页面继续面向可信家庭局域网，没有新增账号体系。

## 代码结构

| 层次 | 主要文件 | 职责 |
| --- | --- | --- |
| Web 入口 | `web/src/main.tsx` | 按 `/admin` 和 `/` 分别懒加载管理端、点餐端及样式 |
| 页面外壳 | `web/src/admin/AdminApp.tsx`、`admin.css` | 六个主导航、次级入口、响应式侧栏、URL/history、错误边界和公共样式 |
| 页面交互 | `web/src/admin/*Page.tsx` | 页面自己的查询、表单和局部刷新；不直接读取服务器文件 |
| 请求与类型 | `web/src/admin/api.ts`、`types.ts` | 管理 API 前缀、请求标识、错误解释、共用数据类型 |
| HTTP 服务 | `mobile_api.py`、`admin_api.py`、`admin_common.py` | 同源托管、路由组合、请求保护、索引 ID 到菜谱目录的约束和版本校验 |
| 业务路由 | `admin_library.py`、`admin_review.py`、`admin_tasks.py`、`admin_settings.py`、`admin_recipe_tools.py`、`admin_curation.py`、`admin_menu.py` | 按业务包装已有 Python 服务，管理接口不导入 Streamlit |
| 既有服务 | 审核/介绍/选图 runner、采集流水线、知识库、Obsidian 与移动同步模块 | 内容规则、后台执行、原子存储、索引及跨设备同步 |

管理接口前缀为 `/api/admin/v1`。普通请求（包含 GET）携带 `X-Admin-Request: 1`；图片、受控下载和导出链接允许浏览器直接 GET，仍检查跨源请求。服务端通过菜谱索引 ID 定位目录，拒绝越界路径。

`AdminContext` 提供 `store`、`indexer`、`config()`、`row()`、`rows()`、`folder()` 和版本校验。路由模块导出 `create_router(context)`；页面默认导出，接收 `{navigate: (path: string) => void}`，路径相对于 `/admin`。

## 交互与执行语义

菜谱库查询和审核队列使用 URL 保存筛选、页码及选中项。菜谱库跨页勾选与返回位置保存在当前标签页；编辑页暂存未保存表单，恢复时检查服务器版本。后台任务进度每 5 秒刷新；隐藏标签页暂停轮询，后台处理继续。浏览和轮询读取索引或持久状态，不以刷新页面触发视频处理或整库重扫。

任务控制按执行器能力显示：生成任务沿用原进程管理，Linux/macOS 支持即时暂停，Windows 不提供该暂停；AI 审核、介绍和选图在当前菜谱结束后暂停。数据导入导出、知识提取、批量归档及修订工具使用服务内的后台操作，不提供即时暂停。关闭浏览器不停止这些操作；如果服务进程退出，它们会显示为中断，重启后可继续/重试。逐条归档和知识提取会跳过已成功项。

独立审核只保存 `recipe.ai-review.json` 和审核历史，展示与恢复继续调用统一的 `review_policy`。编辑、封面确认、字幕重提取或生成版审核应用是明确的修订操作，校验当前版本并保留相应备份。修订工具先在隔离工作目录生成结果，再检查源内容并写入；变更期间源内容发生变化时拒绝覆盖。

封面候选显示与确认分开。生成、上传或 AI 选图不自动替换封面；用户确认裁剪后才更新。应用前重新检查候选与当前菜谱/来源的对应关系；AI 候选还检查所展示的结果和图片版本，失配时拒绝应用。“暂无合适图片”会保留无图状态，不回退到步骤图。

“菜谱修订工具 → 导出菜谱”可通过 `/admin/recipes/<id>/tools?tab=export` 直接进入。导出在后台只读生成 Markdown、Obsidian Markdown、PDF、Word 或含图片的 ZIP，完成后从任务中心下载；PDF/Word 依赖缺失时给出格式降级说明。单独 Markdown 保留相对图片路径，带走完整图片可选择 ZIP。

版本整理只生成整理报告和人工决定，不自动合并、隐藏、删除或改写原菜谱；重新扫描保留人工决定。Obsidian 批量归档保护 Vault 中的手写修改，单条冲突记录为失败并继续其他条目。

## 数据位置

所有路径相对于 `BILI_RECIPE_DATA_DIR`，默认是项目的 `data/`。当前菜谱库可由 `--out` 或配置指定，以下 `recipes/` 指代该库。

| 位置 | 内容 |
| --- | --- |
| `recipes/<UP主>/<菜名>--<BVID>/` | 既有菜谱、笔记、字幕、来源、图片及独立审核/介绍记录 |
| `recipes/.ai-review/`、`.dish-info/`、`.frame-selection/` | 既有 AI 任务、结果和历史 |
| `recipes/.admin/cover-candidates/` | React 手工封面候选及其来源指纹 |
| `recipes/.admin-tools/candidates/` | 步骤图片候选 |
| `recipes/.admin-tools/history/` | 明确应用修订前的文件与操作记录；同级 `work-*` 为操作中的临时目录 |
| `recipes/curation-review/` | 同名/近似名整理报告、CSV 与独立人工决定 |
| `creators/<UID>-<UP名称>/` | UP 主资料和长期视频链接清单 |
| `state/batches/` | 生成批次及运行状态 |
| `state/creator-tasks/` | UP 主链接更新任务 |
| `state/admin-operations/` | 数据、知识、归档、菜单图片和菜谱修订等后台操作状态 |
| `state/config.json`、`knowledge_base.json`、`mobile-sync.sqlite3` | 配置、知识库及共享本餐/设备/实践等原有主数据 |
| `sources/admin-imports/` | 管理页面上传的文档和交接包 |
| `exports/admin-downloads/` | 受控下载文件及记录，包括菜单图片和数据导出结果 |
| `exports/obsidian-vault/` | 默认归档位置；可以配置为外部 Vault |

更改默认菜谱目录后需要重启服务，运行中的索引与任务保持绑定原目录。迁移完整工作环境时，先停止服务和后台任务，再复制数据根目录；前端 `web/dist/` 是构建产物，不含个人菜谱。迁移命令和数据导出包边界见项目 [README](../README.md#数据目录与迁移)。

## 验证方式

开发验证只使用临时合成菜谱库和模型/下载替身，不对真实菜谱库运行测试批次。常用命令：

```bash
.venv/bin/python -m pytest -q tests/test_admin_library.py tests/test_admin_tasks.py tests/test_admin_settings.py tests/test_admin_recipe_tools.py tests/test_admin_curation.py tests/test_admin_menu.py tests/test_pipeline_and_entrypoints.py
.venv/bin/python -m compileall -q bili_recipe_notes tests
cd web
corepack pnpm@12.6.0 test
```

前端测试先检查 TypeScript 并构建，再验证管理 API 请求、筛选、内容呈现、外部来源标记、简单用量换算，以及原点餐端的缓存和增量同步行为。Python 测试覆盖只读浏览、请求和路径边界、人工意见与备份、失效候选、并发版本拒绝、后台状态/恢复、数据交接、知识库、归档及菜单管理。

浏览器回归使用合成库，在桌面和窄屏分别检查导航与历史、搜索/筛选/分页、跨页选择、菜谱详情、编辑草稿、审核依据、图片裁剪、任务启动/重试、知识编辑、设置保存与导出下载。真实模型效果和下载环境按相应功能的独立验证记录确认，不能由模拟任务测试推断。
