# Bili 菜谱离线网页版

这是与原生 iOS/Android 客户端并存的 PWA。网页版只托管应用外壳；用户导入的菜谱、图片、点菜单和心得保存在浏览器 IndexedDB 中。

正式地址：<https://bili-recipe-offline.zhaiswen.chatgpt.site>

## 本地运行

```bash
npm install
npm run dev
```

## 从 Mac 导出菜谱包

在项目根目录运行：

```bash
python -m bili_recipe_notes --export-web-library
```

默认文件为 `outputs/bili-recipe-web-library.json`。在网页版“设置”中导入即可，之后可断网使用。
管理界面的“手机客户端”页可以选择导出全部步骤图、每道菜仅一张图或纯文字包。命令行可用
`--web-library-images all|first|none` 选择同样的策略，例如纯文字导出：

```bash
python -m bili_recipe_notes --export-web-library --web-library-images none
```

网页版会分段读取大型 JSON，并把图片逐张保存到 IndexedDB，避免 200 MB 以上的菜谱包在手机浏览器中因瞬时内存占用过高而无提示退出。导入大包时请保持页面在前台；如果设备存储配额不足，页面会保留明确的错误提示。
