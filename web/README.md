# Chef Zhai 家庭厨房与管理工作台

Vite + React 前端由 Python FastAPI 在 `8765` 端口同源托管：`/` 是点餐端，`/admin/` 是管理工作台。两个入口分别懒加载页面与样式。点餐端调用已有同步 API；管理端调用 `/api/admin/v1`，复用 Python 业务服务，不依赖 Streamlit 页面。

## 构建与启动

使用 Node.js 24+、Corepack 和 pnpm 12.6.0。CI 使用相同版本，并按 `pnpm-lock.yaml` 冻结安装。在 `web/` 目录执行：

```bash
corepack pnpm@12.6.0 install --frozen-lockfile
corepack pnpm@12.6.0 build
```

构建产物位于 `web/dist/`，不包含个人菜谱。在项目根目录安装好 Python 依赖后启动：

```bash
.venv/bin/python -m bili_recipe_notes.ui_launcher
```

管理页为 `http://127.0.0.1:8765/admin/`，点餐页为 `http://127.0.0.1:8765/`。局域网服务器可加 `--host 0.0.0.0 --no-browser`；端口可通过 `--port` 更改。Windows 使用 `.\.venv\Scripts\python.exe`。源码快捷启动脚本也启动这项统一服务，需先完成网页构建。

首次进入管理页，需要输入服务器数据目录下 `state/admin-setup-token` 文件的一次性初始化口令（默认 `data/state/admin-setup-token`），并自设 8–128 个字符的管理员密码。之后必须使用管理员密码登录。密码和初始化口令不会保存在浏览器本地存储中，登录使用同源 HttpOnly 会话 Cookie。

在“设置与数据 → 访问与密码”中可以修改管理员密码、开启或关闭点餐页面，以及选择点餐是否需要密码。默认开启点餐密码保护；管理员设置独立的点餐密码前，访客无法进入。关闭点餐页会同时停止已加入设备的菜单、图片和点餐访问。管理员登录后可直接进入已开启的点餐页；关闭点餐页对管理员同样生效。修改密码或访问规则后，受影响的既有会话需要重新验证。

开发时保持 Python 后端运行，在 `web/` 另开终端：

```bash
corepack pnpm@12.6.0 dev
```

打开 Vite 输出网址的 `/` 或 `/admin/`。`web/vite.config.ts` 将 `/api` 代理到 `http://127.0.0.1:8765`；后端端口有调整时需修改代理目标。旧 Streamlit 管理页由 `python -m bili_recipe_notes.ui_launcher --legacy-ui` 单独启动在 `8501`，不是 React 开发服务器的依赖。

## 管理工作台

主导航为首页、菜谱库、审核工作台、采集与生成、任务中心、设置与数据。侧栏次入口提供版本整理、草稿归档和知识库。菜单图片、推荐、二维码、实践日志及同步冲突放在“菜单与同步管理”，从菜谱库或设置进入。

`src/admin/` 按业务拆分：

- `AdminApp.tsx` 与 `admin.css`：布局、导航、URL/history、响应式侧栏和公共样式；业务页按需加载。
- `HomePage.tsx`、`LibraryPage.tsx`、`RecipePage.tsx`：概况、服务端搜索/分页/批量选择、完整详情与烹饪。
- `ReviewPage.tsx`、`EditorPage.tsx`、`RecipeToolsPage.tsx`：独立审核、编辑、封面裁剪、笔记/字幕/步骤图修订和文档导出。
- `AcquisitionPage.tsx`、`TasksPage.tsx`：采集、UP 主链接、统一任务与进度。
- `SettingsPage.tsx`、`KnowledgePage.tsx`、`MenuAdminPage.tsx`：配置、设备、数据交接、知识、菜单及同步管理。
- `CurationPage.tsx`、`DraftsPage.tsx`：版本比较、独立整理决定与 Obsidian 批量归档。
- `api.ts`、`types.ts`：请求、错误解释与共用类型。

页面默认导出，接收 `{navigate: (path: string) => void}`。路径相对于 `/admin`，例如 `/library?q=鸡蛋` 或 `/recipes/<id>`。常用样式使用 `adm-` 前缀；不要从管理端引入点餐端的全局 `styles.css`。

`api<T>()` 和 `post<T>()` 自动添加管理前缀和 `X-Admin-Request: 1`。普通 GET 也要求该标识；图片和受控下载链接由浏览器直接读取，服务端仍检查跨源访问。重操作交给 Python 执行，网页轮询只读任务状态。独立审核意见、AI 介绍和选图候选不自动改写菜谱；编辑、图片确认和应用修订有单独操作与版本校验。

菜谱库与审核队列将筛选、页码保存在 URL；库内选择和编辑草稿保存在标签页的 `sessionStorage`。管理端每次读取当前服务器状态，不将个人管理数据打包进静态资源。后台任务每 5 秒刷新；数据/知识/归档等服务内操作在服务重启后显示中断，可继续或重试。

“菜谱修订工具 → 导出菜谱”（`/admin/recipes/<id>/tools?tab=export`）提供 Markdown、Obsidian Markdown、PDF、Word 和 ZIP 资料包，完成后在任务中下载。Markdown 保留图片相对路径，需要一并携带图片时使用 ZIP。PDF/Word 依赖不可用时显示格式降级提示。

完整功能映射、Python 模块职责和新数据目录见 [React 管理工作台说明](../docs/react-admin-migration.md)。

## 点餐端与数据

菜谱、心得、套餐和共享本餐以家庭服务器为主数据源。首次打开点餐页先通过点餐密码验证，再输入设备名称加入；管理员明确关闭密码要求时可直接进入。设备令牌保存在浏览器中，不能替代点餐页面访问授权。管理员可在“设置与数据 → 设备管理”关闭新设备自行加入。

点餐端使用 IndexedDB 保存缓存和操作队列。再次打开时先在线确认访问权限，通过后显示缓存，首屏最多展示 40 道菜，其余通过“查看更多”展开；搜索与分类使用预计算索引。权限每 15 秒及窗口恢复前台时重新确认，拒绝访问、会话过期或连接失败时卸载菜单页面，不继续展示缓存。暂未同步的操作保留，重新登录并恢复连接后继续同步。首次加入仍需下载菜单，之后按修订增量同步。服务器启动时先提供已有索引，后台扫描菜库。

`src/AccessGate.tsx` 在业务页面挂载前验证权限，`src/access.ts` 负责访问接口与状态规则；管理页访问设置位于 `src/admin/AccessSettings.tsx`。Service Worker 不缓存 API，导航优先读取服务器最新页面；旧版点餐客户端首次升级时自动刷新为带门禁的入口。

点击菜名或图片打开详情，查看简介、做法概览、用料、准备事项、步骤图和原视频，并直接加菜。独立 `recipe.dish-info.json` 中的有效介绍随同步更新。外部背景折叠展示，保留出处和匹配提示；检索失败仍可查看菜谱内容。

服务器默认使用 `data/recipes/` 和 `data/state/mobile-sync.sqlite3`，管理任务等附加数据同样归属于服务器数据根目录。迁移时使用一致的 `BILI_RECIPE_DATA_DIR`；浏览器缓存仍归当前设备管理。完整目录与迁移命令见 [项目 README](../README.md#数据目录与迁移)。

## 验证

在 `web/` 执行：

```bash
corepack pnpm@12.6.0 test
```

该命令先执行 TypeScript 检查和生产构建，再使用 Node 内置测试运行器检查行为。管理端测试覆盖请求标识、错误/取消处理、文件上传、筛选参数、菜谱原文展示、背景来源标记和明确用量换算。点餐端测试覆盖增量索引、隐藏/恢复、缓存提交、分页同步、离线队列、失败恢复和操作 ID 去重。

测试使用合成数据和模拟响应，不访问家庭服务器或对真实菜谱运行批量任务。修改 Python 管理接口时，在项目根目录运行相关 `tests/test_admin_*.py` 和 `python -m compileall -q bili_recipe_notes tests`；验证命令及桌面/窄屏浏览器回归范围见迁移说明。
