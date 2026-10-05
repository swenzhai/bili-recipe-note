import { useEffect, useState } from "react";
import { api, errorMessage, taskPath } from "./api";
import type { PageProps, RecipeSummary, TaskSummary } from "./types";

type Overview = { counts: { total: number; published: number; hidden: number; cover_pending: number; review_pending: number }; recent: RecipeSummary[]; indexing?: { status?: string; last_error?: string; last_success?: string } };

export function RecipeCard({ recipe, navigate, selected, onSelect }: { recipe: RecipeSummary; navigate: PageProps["navigate"]; selected?: boolean; onSelect?: () => void }) {
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => setImageFailed(false), [recipe.cover_url]);
  return <article className={`adm-recipe-card ${selected ? "is-selected" : ""}`}>
    <div className="adm-recipe-photo"><a href={`/admin/recipes/${encodeURIComponent(recipe.id)}`} aria-label={`查看${recipe.title}详情`} onClick={event => { if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); navigate(`/recipes/${encodeURIComponent(recipe.id)}`); } }}>
      {recipe.cover_url && !imageFailed ? <img src={recipe.cover_url} alt={recipe.title} loading="lazy" onError={() => setImageFailed(true)}/> : <div className="adm-photo-placeholder"><svg viewBox="0 0 80 80" width="64" height="64" fill="none" aria-hidden="true"><circle cx="40" cy="40" r="24" stroke="currentColor" strokeWidth="1.2"/><circle cx="40" cy="40" r="17" stroke="currentColor" strokeWidth="1.2"/><path d="M8 17v16m5-16v16M3 17v16c0 8 10 8 10 0M8 39v24m61-45v45m0-45c-8 6-9 20 0 20" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></svg><span>等待一道好味道</span></div>}
    </a><span className={`adm-photo-status ${recipe.hidden ? "hidden" : recipe.published ? "published" : ""}`}>{recipe.hidden ? "已隐藏" : recipe.published ? "菜单在售" : "未上架"}</span>{onSelect && <label className="adm-card-select"><input type="checkbox" checked={Boolean(selected)} onChange={onSelect} aria-label={`选择${recipe.title}`}/></label>}</div>
    <div className="adm-recipe-card-body"><span className="adm-recipe-category">{recipe.category || "未分类"}</span><h3><a href={`/admin/recipes/${encodeURIComponent(recipe.id)}`} onClick={event => { if (!event.ctrlKey && !event.metaKey) { event.preventDefault(); navigate(`/recipes/${encodeURIComponent(recipe.id)}`); } }}>{recipe.title}</a></h3><div className="adm-recipe-card-meta"><span>{recipe.creator_name || "我的收藏"}</span><span aria-hidden="true">↗</span></div></div>
  </article>;
}

export default function HomePage({ navigate }: PageProps) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [error, setError] = useState("");
  const [taskError, setTaskError] = useState("");
  const [revision, refresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    api<Overview>("/overview", { signal: controller.signal }).then(setOverview).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    const loadTasks = () => api<{ items: TaskSummary[] }>("/tasks", { signal: controller.signal }).then(data => { setTasks(data.items); setTaskError(""); }).catch(error => { if (!controller.signal.aborted) setTaskError(errorMessage(error)); });
    void loadTasks();
    const timer = window.setInterval(() => { if (!document.hidden) void loadTasks(); }, 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [revision]);
  const running = tasks.filter(task => ["running", "queued", "pausing", "stopping", "starting"].includes(task.status));
  const counts = overview?.counts;
  return <div className="adm-page">
    <div className="adm-page-head"><div><span className="adm-eyebrow">YOUR EVERYDAY KITCHEN</span><h1>欢迎回到你的厨房</h1><p className="adm-muted">收藏好味道，安排好一餐。今天也从容一点。</p></div><button className="adm-btn primary" onClick={() => navigate("/import")}><span aria-hidden="true">＋</span> 添加新菜谱</button></div>
    {error && <div className="adm-error" role="alert">{error}<button className="adm-btn small" onClick={() => refresh(value => value + 1)}>重试</button></div>}
    <section className="adm-welcome"><div className="adm-welcome-copy"><span className="adm-eyebrow">一点灵感 · 一桌家常</span><h2>把每一份灵感，<br/>变成餐桌上的好味道。</h2><p>从喜欢的视频收藏菜谱，让 AI 帮你整理，<br className="adm-wide-only"/>把更多时间留给烹饪和家人。</p><button className="adm-btn" onClick={() => navigate("/library")}>翻翻我的菜谱 <span aria-hidden="true">→</span></button></div><div className="adm-welcome-art" aria-hidden="true"><div className="adm-plate"><div className="adm-plate-inner"><span className="adm-food-leaf leaf-a"/><span className="adm-food-leaf leaf-b"/><span className="adm-food-leaf leaf-c"/><span className="adm-food-tomato tomato-a"/><span className="adm-food-tomato tomato-b"/><span className="adm-food-tomato tomato-c"/><span className="adm-food-grain grain-a"/><span className="adm-food-grain grain-b"/></div></div><div className="adm-art-caption">GOOD FOOD, GOOD MOOD.</div></div></section>
    <div className="adm-stat-grid" aria-label="厨房概况">{[
      { label: "珍藏菜谱", value: counts?.total, caption: "一道一道，慢慢积累", path: "/library", icon: "▤" },
      { label: "已上架菜单", value: counts?.published, caption: "今天可以点的好味道", path: "/library?published=true", icon: "✳" },
      { label: "待审核菜谱", value: counts?.review_pending, caption: "核对来源，让做法更安心", path: "/review", icon: "✓" },
      { label: "待确认封面", value: counts?.cover_pending, caption: "为菜品挑一张好看的照片", path: "/review?tab=cover", icon: "▧" },
    ].map(stat => <button className="adm-stat-card" key={stat.label} onClick={() => navigate(stat.path)}><div className="adm-stat-top"><span>{stat.label}</span><i aria-hidden="true">{stat.icon}</i></div><strong>{stat.value === undefined ? "—" : stat.value.toLocaleString()}</strong><small>{stat.caption}</small></button>)}</div>
    {overview?.indexing?.status === "starting" && <p className="adm-notice" role="status">正在后台更新菜谱索引，你可以继续浏览。</p>}
    {overview?.indexing?.status === "degraded" && <p className="adm-error" role="status">索引更新暂未完成，当前显示上次成功读取的菜谱。{overview.indexing.last_error || ""}</p>}
    <div className="adm-home-columns"><section className="adm-panel"><div className="adm-section-head"><div><span className="adm-eyebrow">IN THE WORKS</span><h2>厨房正在忙</h2></div><button className="adm-text-button" onClick={() => navigate("/tasks")}>全部任务 →</button></div>{taskError ? <p className="adm-error">{taskError}</p> : running.length ? <div className="adm-home-tasks">{running.slice(0, 3).map(task => <button className="adm-home-task" key={`${task.kind}-${task.id}`} onClick={() => navigate(taskPath(task))}><div><strong>{task.title}</strong><span>{task.processed} / {task.total}</span></div><div className="adm-progress" role="progressbar" aria-label={task.title} aria-valuenow={Math.round(task.progress * 100)} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${Math.max(0, Math.min(100, task.progress * 100))}%` }}/></div><small>{task.current || "任务正在后台处理，可放心离开页面"}</small></button>)}</div> : <div className="adm-quiet"><span aria-hidden="true">☕</span><div><strong>此刻，厨房很从容</strong><p>没有进行中的任务，去发现下一道想做的菜吧。</p></div></div>}</section><section className="adm-panel adm-shortcuts"><div className="adm-section-head"><div><span className="adm-eyebrow">A LITTLE HELP</span><h2>交给 AI 帮帮忙</h2></div></div><button onClick={() => navigate("/tasks?create=frames")}><span className="adm-shortcut-icon">▧</span><span><strong>挑选菜品特写</strong><small>从视频中寻找成品画面</small></span><b>↗</b></button><button onClick={() => navigate("/tasks?create=dish-info")}><span className="adm-shortcut-icon peach">✧</span><span><strong>补充菜品介绍</strong><small>整理做法概览与背景参考</small></span><b>↗</b></button></section></div>
    <section><div className="adm-section-head"><div><span className="adm-eyebrow">FRESH FROM YOUR COLLECTION</span><h2>最近收藏</h2></div><button className="adm-text-button" onClick={() => navigate("/library")}>查看全部菜谱 →</button></div>{!overview && !error ? <div className="adm-recipe-grid" aria-label="正在加载菜谱">{Array.from({ length: 4 }, (_, index) => <div className="adm-skeleton-card" key={index}/>)}</div> : overview?.recent.length ? <div className="adm-recipe-grid">{overview.recent.slice(0, 4).map(recipe => <RecipeCard key={recipe.id} recipe={recipe} navigate={navigate}/>)}</div> : <div className="adm-empty"><h3>厨房的第一道菜，从这里开始</h3><p>粘贴一个喜欢的烹饪视频，建立你的菜谱收藏。</p><button className="adm-btn primary" onClick={() => navigate("/import")}>添加第一道菜谱</button></div>}</section>
  </div>;
}
