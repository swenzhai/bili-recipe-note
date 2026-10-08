/// <reference types="vite/client" />
import React from "react";
import ReactDOM from "react-dom/client";
import AccessGate from "./AccessGate";
const isAdmin = /^\/admin(?:\/|$)/.test(window.location.pathname);
const root = ReactDOM.createRoot(document.getElementById("root")!);
if (!isAdmin && "serviceWorker" in navigator) void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
const App = React.lazy(() => isAdmin
  ? import("./admin/AdminApp")
  : Promise.all([import("./App"), import("./styles.css")]).then(([module]) => module));

class AppBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <main className="access-page"><section className="access-card"><h1>页面加载失败</h1><p>请检查连接并刷新重试。</p><button onClick={() => window.location.reload()}>刷新</button></section></main> : this.props.children;
  }
}

root.render(<React.StrictMode><AccessGate mode={isAdmin ? "admin" : "ordering"}><AppBoundary><React.Suspense fallback={<main className="access-page" role="status">正在打开厨房…</main>}><App /></React.Suspense></AppBoundary></AccessGate></React.StrictMode>);
