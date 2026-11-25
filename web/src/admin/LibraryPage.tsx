import { useEffect, useState } from "react";
import { api, errorMessage, post, taskPath } from "./api";
import { RecipeCard } from "./HomePage";
import type { PageProps, RecipeSummary, TaskSummary } from "./types";

type LibraryResponse = { items: RecipeSummary[]; total: number; page: number; page_size: number; categories: string[] };

export function libraryQuery(search: string): URLSearchParams {
  const source = new URLSearchParams(search);
  const result = new URLSearchParams();
  for (const key of ["q", "category", "visibility", "published"]) if (source.get(key)) result.set(key, source.get(key)!);
  result.set("page", String(Math.max(1, Number.parseInt(source.get("page") || "1", 10) || 1)));
  result.set("page_size", "24");
  return result;
}

export default function LibraryPage({ navigate }: PageProps) {
  const query = libraryQuery(window.location.search);
  const queryKey = query.toString();
  const [search, setSearch] = useState(query.get("q") || "");
  const [data, setData] = useState<LibraryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, refresh] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(() => {
    try { const stored = JSON.parse(sessionStorage.getItem("admin-library-selected") || "[]"); return new Set(Array.isArray(stored) ? stored.filter(value => typeof value === "string") : []); } catch { return new Set(); }
  });
  const [busy, setBusy] = useState(false);
  const [taskKind, setTaskKind] = useState("frames");
  const [confirmTask, setConfirmTask] = useState(false);
  useEffect(() => { setSearch(query.get("q") || ""); }, [query.get("q")]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    api<LibraryResponse>(`/recipes?${queryKey}`, { signal: controller.signal }).then(value => { setData(value); setLoading(false); }).catch(error => { if (!controller.signal.aborted) { setError(errorMessage(error)); setLoading(false); } });
    return () => controller.abort();
  }, [queryKey, revision]);
  useEffect(() => { try { sessionStorage.setItem("admin-library-selected", JSON.stringify([...selected])); } catch { /* Selection remains available in memory. */ } }, [selected]);
  useEffect(() => { try { sessionStorage.setItem("admin-library-location", `/library${window.location.search}`); } catch { /* URL filters remain available. */ } }, [queryKey]);
  const updateQuery = (changes: Record<string, string>) => {
    const next = new URLSearchParams(window.location.search);
    next.delete("page");
    for (const [key, value] of Object.entries(changes)) { if (value && !(key === "published" && value === "all")) next.set(key, value); else next.delete(key); }
    navigate(`/library${next.size ? `?${next}` : ""}`);
  };
  const toggle = (id: string) => { setSelected(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; }); setConfirmTask(false); };
  const publish = async (published: boolean) => {
    setBusy(true); setError(""); setNotice("");
    try { await post("/recipes/publication", { ids: [...selected], published }); setNotice(`已将选中的 ${selected.size} 道菜谱${published ? "上架菜单" : "从菜单下架"}。`); refresh(value => value + 1); }
    catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const startTask = async () => {
    setBusy(true); setError("");
    try { const task = await post<TaskSummary>("/tasks", { kind: taskKind, recipe_ids: [...selected], limit: selected.size, only_pending: true, options: {} }); if (task.already_running) setNotice("已有同类任务正在运行，将显示现有任务进度。"); navigate(taskPath(task) + (task.already_running ? "&existing=1" : "")); }
    catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const allPageSelected = Boolean(data?.items.length) && data!.items.every(recipe => selected.has(recipe.id));
  return <div className="adm-page">
    <div className="adm-page-head"><div><span className="adm-eyebrow">THE RECIPE COLLECTION</span><h1>我的菜谱库</h1><p className="adm-muted">把喜欢的味道好好收藏，也为下一餐留一点灵感。</p></div><button className="adm-btn primary" onClick={() => navigate("/import")}>＋ 添加菜谱</button></div>
    <div className="adm-actions"><button className="adm-text-button" onClick={() => navigate("/drafts")}>草稿与批量归档 →</button><button className="adm-text-button" onClick={() => navigate("/curation")}>比较同名菜谱与版本 →</button><button className="adm-text-button" onClick={() => navigate("/menu")}>菜单与同步管理 →</button></div>
    <section className="adm-library-tools" aria-label="菜谱筛选"><form className="adm-search" role="search" onSubmit={event => { event.preventDefault(); updateQuery({ q: search }); }}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg><input aria-label="搜索菜名、食材或 UP 主" placeholder="搜索菜名、食材或 UP 主…" value={search} onChange={event => setSearch(event.target.value)}/><button type="submit" className="adm-btn small">搜索</button></form><div className="adm-filters"><label><span>分类</span><select aria-label="菜谱分类" value={query.get("category") || ""} onChange={event => updateQuery({ category: event.target.value })}><option value="">全部分类</option>{data?.categories.map(category => <option key={category}>{category}</option>)}</select></label><label><span>菜单状态</span><select aria-label="菜单状态" value={query.get("published") || "all"} onChange={event => updateQuery({ published: event.target.value })}><option value="all">全部上架状态</option><option value="true">已上架</option><option value="false">未上架</option></select></label><label><span>展示</span><select aria-label="展示状态" value={query.get("visibility") || "visible"} onChange={event => updateQuery({ visibility: event.target.value })}><option value="visible">可展示菜谱</option><option value="hidden">已隐藏内容</option><option value="all">全部内容</option></select></label></div></section>
    {error && <div className="adm-error" role="alert">{error}<button className="adm-btn small" onClick={() => refresh(value => value + 1)}>重试</button></div>}{notice && <div className="adm-notice" role="status">{notice}</div>}
    <div className="adm-library-summary"><span>{loading ? "正在查找菜谱…" : `共 ${data?.total ?? 0} 道菜谱`}</span><label className="adm-check"><input type="checkbox" checked={allPageSelected} disabled={!data?.items.length || loading} onChange={() => { setSelected(current => { const next = new Set(current); for (const recipe of data?.items || []) if (allPageSelected) next.delete(recipe.id); else next.add(recipe.id); return next; }); setConfirmTask(false); }}/>选择本页</label></div>
    {selected.size > 0 && <section className="adm-selection-bar" aria-label="批量操作"><div className="adm-actions"><strong>已选 {selected.size} 道</strong><button className="adm-text-button" onClick={() => { setSelected(new Set()); setConfirmTask(false); }}>清空</button></div><div className="adm-actions"><button className="adm-btn small" disabled={busy} onClick={() => void publish(true)}>上架</button><button className="adm-btn small" disabled={busy} onClick={() => void publish(false)}>下架</button><select aria-label="批量 AI 操作" value={taskKind} onChange={event => { setTaskKind(event.target.value); setConfirmTask(false); }}><option value="frames">AI 选特写图</option><option value="review">AI 审核归类</option><option value="dish-info">AI 补充介绍</option></select><button className="adm-btn small primary" disabled={busy} onClick={() => setConfirmTask(!confirmTask)}>创建批量任务</button><button className="adm-btn small" disabled={busy} onClick={() => navigate(`/tasks?create=frames&mode=generate&recipe_ids=${encodeURIComponent([...selected].join(","))}`)}>批量生成示意封面</button></div>{confirmTask && <div className="adm-selection-confirm"><p>为选中的 {selected.size} 道菜创建{taskKind === "frames" ? "特写选图" : taskKind === "review" ? "审核归类" : "介绍补充"}任务，已有有效结果将跳过。{taskKind === "frames" ? "候选图片需人工确认后才会成为封面。" : "结果独立保存，可在任务中心查看进度。"}</p><div className="adm-actions"><button className="adm-btn primary small" disabled={busy} onClick={() => void startTask()}>{busy ? "正在创建…" : "开始后台任务"}</button><button className="adm-btn small" disabled={busy} onClick={() => navigate(`/tasks?create=${taskKind}&recipe_ids=${encodeURIComponent([...selected].join(","))}`)}>调整模型与参数</button><button className="adm-btn small" disabled={busy} onClick={() => setConfirmTask(false)}>取消</button></div></div>}</section>}
    {loading && !data ? <div className="adm-recipe-grid" role="status" aria-label="正在加载菜谱">{Array.from({ length: 8 }, (_, index) => <div className="adm-skeleton-card" key={index}/>)}</div> : data?.items.length ? <div className={`adm-recipe-grid ${loading ? "is-loading" : ""}`} aria-busy={loading}>{data.items.map(recipe => <RecipeCard key={recipe.id} recipe={recipe} navigate={navigate} selected={selected.has(recipe.id)} onSelect={() => toggle(recipe.id)}/>)}</div> : !error && <div className="adm-empty"><span className="adm-empty-symbol" aria-hidden="true">⌕</span><h2>{query.get("q") || query.get("category") ? "暂时没有找到这道味道" : "这里还没有菜谱"}</h2><p>试试更换关键词或筛选条件，或者收藏一道新的菜。</p><div className="adm-actions"><button className="adm-btn" onClick={() => { setSearch(""); navigate("/library"); }}>清除筛选</button><button className="adm-btn primary" onClick={() => navigate("/import")}>添加菜谱</button></div></div>}
    {data && data.total > data.page_size && <nav className="adm-pagination" aria-label="菜谱分页"><button className="adm-btn" disabled={data.page <= 1 || loading} onClick={() => updateQuery({ page: String(data.page - 1) })}>← 上一页</button><span>第 {data.page} / {Math.ceil(data.total / data.page_size)} 页</span><button className="adm-btn" disabled={data.page * data.page_size >= data.total || loading} onClick={() => updateQuery({ page: String(data.page + 1) })}>下一页 →</button></nav>}
  </div>;
}
