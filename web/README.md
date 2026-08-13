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
