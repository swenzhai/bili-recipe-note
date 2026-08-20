# Bili 家庭局域网点餐客户端

标准 Vite + React 静态客户端，由 FastAPI 在 `8765` 端口同源托管。菜谱、心得、套餐和共享本餐以家庭服务器为主数据源，浏览器使用 IndexedDB 保存缓存与离线操作队列。

```bash
corepack pnpm install
corepack pnpm build
```

开发时运行 `corepack pnpm dev`，`/api` 会代理到 `http://127.0.0.1:8765`。正式使用直接打开服务器的 `8765` 地址，输入设备名称即可加入；设备令牌和名称会长期保存在浏览器中。管理员可在 Streamlit 中临时关闭新设备加入。
