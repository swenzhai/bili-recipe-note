import { Component, Suspense, lazy, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import HomePage from "./HomePage";
import LibraryPage from "./LibraryPage";
import RecipePage from "./RecipePage";
import { accessRequest, notifyAccessChanged } from "../access";
import "./admin.css";

const TasksPage = lazy(() => import("./TasksPage"));
const ImportPage = lazy(() => import("./AcquisitionPage"));
const ReviewPage = lazy(() => import("./ReviewPage"));
const EditorPage = lazy(() => import("./EditorPage"));
const SettingsPage = lazy(() => import("./SettingsPage"));
const KnowledgePage = lazy(() => import("./KnowledgePage"));
const CurationPage = lazy(() => import("./CurationPage"));
const DraftsPage = lazy(() => import("./DraftsPage"));
const RecipeToolsPage = lazy(() => import("./RecipeToolsPage"));
const MenuAdminPage = lazy(() => import("./MenuAdminPage"));

const navigation = [
  { path: "/", title: "首页", icon: "home", subtitle: "厨房工作台" },
  { path: "/library", title: "菜谱库", icon: "book", subtitle: "收藏每一道好味道" },
  { path: "/review", title: "审核工作台", icon: "check", subtitle: "认真对待每一份菜谱" },
  { path: "/import", title: "采集与生成", icon: "plus", subtitle: "从灵感到一道新菜" },
  { path: "/tasks", title: "任务中心", icon: "activity", subtitle: "所有进展，一目了然" },
  { path: "/settings", title: "设置与数据", icon: "settings", subtitle: "让厨房按你的方式运行" },
];

function Icon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M9 21V12h6v9"/></>,
    book: <><path d="M12 5c-3-2-6-2-10-1v16c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1Z"/><path d="M12 5v16"/></>,
    check: <><rect x="4" y="3" width="16" height="18" rx="3"/><path d="m8 12 3 3 5-6"/></>,
    plus: <><rect x="3" y="3" width="18" height="18" rx="5"/><path d="M12 8v8M8 12h8"/></>,
    activity: <><path d="M3 12h4l3-8 4 16 3-8h4"/></>,
    settings: <><path d="M5 4v16M12 4v16M19 4v16"/><path d="M2 8h6M9 16h6M16 9h6"/></>,
    leaf: <><path d="M20 3C9 2 3 7 4 14s14 10 16-11Z"/><path d="M4 21 16 9"/></>,
  };
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.book}</svg>;
}

class PageBoundary extends Component<{ children: ReactNode }, { error: string }> {
  state = { error: "" };
  static getDerivedStateFromError(error: Error) { return { error: error.message }; }
  render() {
    if (this.state.error) return <div className="adm-empty" role="alert"><h2>这一页暂时无法显示</h2><p>刷新后重试，后台任务会继续运行。</p><button className="adm-btn primary" onClick={() => window.location.reload()}>刷新页面</button><details><summary>查看原因</summary>{this.state.error}</details></div>;
    return this.props.children;
  }
}

function currentLocation() { return window.location.pathname.replace(/^\/admin(?=\/|$)/, "") + window.location.search || "/"; }

export default function AdminApp() {
  const [location, setLocation] = useState(currentLocation);
  const [menuOpen, setMenuOpen] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 760px)").matches);
  const menuWasOpen = useRef(false);
  const contentRef = useRef<HTMLElement>(null);
  const navigate = useCallback((path: string) => {
    if (!window.dispatchEvent(new Event("admin:before-navigate", { cancelable: true }))) return;
    const next = path.startsWith("/") ? path : `/${path}`;
    if (`/admin${next}` !== window.location.pathname + window.location.search) window.history.pushState({}, "", `/admin${next}`);
    setLocation(currentLocation()); setMenuOpen(false);
    window.scrollTo({ top: 0, behavior: "instant" });
    contentRef.current?.focus({ preventScroll: true });
  }, []);
  useEffect(() => {
    const onPop = () => { setLocation(currentLocation()); setMenuOpen(false); };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const resize = () => { setNarrow(media.matches); if (!media.matches) setMenuOpen(false); };
    media.addEventListener("change", resize);
    return () => media.removeEventListener("change", resize);
  }, []);
  useEffect(() => {
    if (menuOpen && narrow) document.querySelector<HTMLAnchorElement>(".adm-sidebar a")?.focus();
    else if (menuWasOpen.current && narrow) document.querySelector<HTMLButtonElement>(".adm-mobile-menu")?.focus();
    menuWasOpen.current = menuOpen;
  }, [menuOpen, narrow]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setMenuOpen(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  const pathname = location.split("?")[0].replace(/\/$/, "") || "/";
  const secondaryTitle = ({ "/knowledge": "厨房知识库", "/curation": "菜谱版本整理", "/drafts": "草稿与归档", "/menu": "菜单与同步管理" } as Record<string, string>)[pathname];
  const active = pathname.startsWith("/recipes") || pathname === "/drafts" ? navigation[1] : pathname === "/acquisition" ? navigation[3] : navigation.find(item => item.path !== "/" && pathname.startsWith(item.path)) || navigation[0];
  useEffect(() => { document.title = `${secondaryTitle || active.title} · Chef Zhai`; }, [active.title, secondaryTitle]);
  const props = { navigate };
  const logout = async () => {
    if (!window.dispatchEvent(new Event("admin:before-navigate", { cancelable: true }))) return;
    setLoggingOut(true); setLogoutError("");
    try { await accessRequest("/admin/logout", { method: "POST" }); notifyAccessChanged(); }
    catch (cause) { setLogoutError(cause instanceof Error ? cause.message : "退出失败，请重试。"); }
    finally { setLoggingOut(false); }
  };
  let page: ReactNode;
  if (pathname === "/") page = <HomePage {...props}/>;
  else if (pathname === "/library") page = <LibraryPage {...props}/>;
  else if (/^\/recipes\/[^/]+\/edit$/.test(pathname)) page = <EditorPage {...props}/>;
  else if (/^\/recipes\/[^/]+\/tools$/.test(pathname)) page = <RecipeToolsPage {...props}/>;
  else if (/^\/recipes\/[^/]+$/.test(pathname)) page = <RecipePage {...props}/>;
  else if (pathname === "/review") page = <ReviewPage {...props}/>;
  else if (pathname.startsWith("/tasks")) page = <TasksPage {...props}/>;
  else if (["/import", "/acquisition"].includes(pathname)) page = <ImportPage {...props}/>;
  else if (pathname === "/settings") page = <SettingsPage {...props}/>;
  else if (pathname === "/knowledge") page = <KnowledgePage {...props}/>;
  else if (pathname === "/curation") page = <CurationPage {...props}/>;
  else if (pathname === "/drafts") page = <DraftsPage {...props}/>;
  else if (pathname === "/menu") page = <MenuAdminPage {...props}/>;
  else page = <div className="adm-empty"><h1>没有找到这个页面</h1><button className="adm-btn primary" onClick={() => navigate("/")}>回到工作台</button></div>;
  return <div className="adm-app">
    <a className="adm-skip" href="#admin-content">跳到主要内容</a>
    {menuOpen && <button className="adm-menu-overlay" aria-label="关闭导航" onClick={() => setMenuOpen(false)}/>}
    <aside className={`adm-sidebar ${menuOpen ? "is-open" : ""}`} inert={narrow && !menuOpen}>
      <a className="adm-brand" href="/admin/" onClick={event => { event.preventDefault(); navigate("/"); }}><span className="adm-brand-symbol"><Icon name="leaf"/></span><span><strong>Chef Zhai</strong><small>我的家庭厨房</small></span></a>
      <div className="adm-nav-label">厨房管理</div>
      <nav aria-label="管理导航">{navigation.map(item => <a key={item.path} className={`adm-nav-item ${active.path === item.path && !secondaryTitle ? "active" : ""}`} aria-current={active.path === item.path && !secondaryTitle ? "page" : undefined} href={`/admin${item.path}`} onClick={event => { if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); navigate(item.path); } }}><Icon name={item.icon}/><span>{item.title}</span>{active.path === item.path && !secondaryTitle && <i/>}</a>)}</nav>
      <div className="adm-sidebar-bottom">{[{ path: "/curation", title: "菜谱版本整理", icon: "check" }, { path: "/drafts", title: "草稿与归档", icon: "book" }, { path: "/knowledge", title: "厨房知识库", icon: "leaf" }].map(item => <a key={item.path} className={`adm-nav-item ${pathname === item.path ? "active" : ""}`} href={`/admin${item.path}`} onClick={event => { if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); navigate(item.path); } }}><Icon name={item.icon}/>{item.title}</a>)}<div className="adm-sidebar-note"><span className="adm-status-dot"/><span>好好吃饭，慢慢积累<small>你的菜谱与厨房灵感</small></span></div></div>
    </aside>
    <div className="adm-workspace" inert={narrow && menuOpen}><header className="adm-topbar"><div className="adm-topbar-start"><button className="adm-mobile-menu" aria-label="打开管理导航" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}>☰</button><span className="adm-breadcrumb">我的厨房<span>/</span><b>{secondaryTitle || active.title}</b></span></div><div className="adm-actions"><a className="adm-order-link" href="/">开始点餐 <span aria-hidden="true">↗</span></a><button className="adm-btn small" disabled={loggingOut} onClick={() => void logout()}>{loggingOut ? "正在退出…" : "退出登录"}</button></div></header>
      {logoutError && <p className="adm-error" role="alert">{logoutError}</p>}
      <main className="adm-content" id="admin-content" ref={contentRef} tabIndex={-1}><PageBoundary key={pathname}><Suspense fallback={<div className="adm-loading" role="status"><span className="adm-spinner"/>正在打开厨房…</div>}>{page}</Suspense></PageBoundary></main>
      <footer className="adm-footer">CHEF ZHAI <span>把喜欢的味道，留在自己的厨房。</span></footer>
    </div>
  </div>;
}
