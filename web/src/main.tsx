/// <reference types="vite/client" />
import React from "react";
import ReactDOM from "react-dom/client";
const isAdmin = /^\/admin(?:\/|$)/.test(window.location.pathname);
const root = ReactDOM.createRoot(document.getElementById("root")!);
const loadApp = isAdmin
  ? import("./admin/AdminApp")
  : Promise.all([import("./App"), import("./styles.css")]).then(([module]) => module);

loadApp.then(({ default: App }) => root.render(<React.StrictMode><App /></React.StrictMode>)).catch(() => {
  root.render(<main style={{ padding: 32, fontFamily: "sans-serif" }}><h1>页面加载失败</h1><p>请检查连接并刷新重试。</p><button onClick={() => window.location.reload()}>刷新</button></main>);
});
