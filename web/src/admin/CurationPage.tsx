import { useEffect, useState } from "react";
import { api, errorMessage, post, safeUrl } from "./api";
import type { PageProps, RecipeDocument } from "./types";
import "./curation.css";

const decisions: Record<string, string> = { pending: "暂未决定", keep_primary: "保留为主版本", keep_variant: "保留不同做法", merge_clip: "标记为相关短剪", exclude: "不纳入最终整理", review: "还需核对" };
const roles: Record<string, string> = { primary_candidate: "主版本候选", variant_candidate: "不同做法候选", short_clip_candidate: "相关短剪候选", exclude_candidate: "需要检查完整性", name_review_candidate: "菜名待核对" };
type Item = { item_id: string; recipe_id: string | null; group_title: string; video_title: string; bvid: string; source_url: string; suggested_role: string; review_reasons: string; duration_seconds: number; step_count: number; ingredient_count: number; transcript_overlap: number; decision: string; saved: { final_title?: string; variant_name?: string; review_notes?: string }; recipe?: RecipeDocument; transcript?: { start?: number; text: string }[]; content_revision?: string };
type Group = { title: string; similar_titles: string[]; total: number; completed: number; items: Item[] };
type Report = { groups: Group[]; total: number; completed: number; generated_at?: string; revision: string; conflicts: string[] };
type Comparison = { title: string; similar_titles: string[]; items: Item[]; revision: string };

function ComparisonCard({ item, reportRevision, navigate, onSaved }: { item: Item; reportRevision: string; navigate: PageProps["navigate"]; onSaved: () => void }) {
  const [decision, setDecision] = useState(item.decision);
  const [title, setTitle] = useState(item.saved.final_title || item.group_title);
  const [variant, setVariant] = useState(item.saved.variant_name || "");
  const [notes, setNotes] = useState(item.saved.review_notes || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setDecision(item.decision); setTitle(item.saved.final_title || item.group_title); setVariant(item.saved.variant_name || ""); setNotes(item.saved.review_notes || ""); }, [item]);
  async function save() {
    setBusy(true); setError("");
    try { await post("/curation/decisions", { item_id: item.item_id, decision, final_title: title, variant_name: variant, review_notes: notes, revision: reportRevision, content_revision: item.content_revision }); onSaved(); }
    catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  const recipe = item.recipe;
  const source = safeUrl(item.source_url);
  return <article className="adm-panel adm-comparison-card"><div className="adm-section-head"><span className="adm-badge">{roles[item.suggested_role] || "待核对来源"}</span><span className={`adm-badge ${item.decision.startsWith("keep") ? "success" : ""}`}>{decisions[item.decision]}</span></div><h2>{item.group_title}</h2><p className="adm-muted">{item.video_title || item.bvid}</p><div className="adm-comparison-facts"><span>{Math.round(item.duration_seconds)} 秒</span><span>{item.step_count} 个步骤</span><span>{item.ingredient_count} 项用料</span><span>字幕重合 {Math.round(item.transcript_overlap * 100)}%</span></div><p className="adm-notice">{item.review_reasons}。这些提示仅用于比较，完整菜谱即使有推广也应保留。</p><div className="adm-actions">{item.recipe_id && <button className="adm-text-button" onClick={() => navigate(`/recipes/${encodeURIComponent(item.recipe_id!)}`)}>查看完整菜谱 →</button>}{source && <a className="adm-text-button" href={source} target="_blank" rel="noreferrer">核对原视频 ↗</a>}</div>
    <details open><summary>用料对比</summary>{[...(recipe?.ingredients || []), ...(recipe?.seasonings || [])].length ? <ul className="adm-comparison-ingredients">{[...(recipe?.ingredients || []), ...(recipe?.seasonings || [])].map((ingredient, index) => <li key={index}><span>{ingredient.name}</span><b>{ingredient.amount || "未注明"}</b>{ingredient.evidence && <small>原提取依据：{ingredient.evidence}</small>}</li>)}</ul> : <p className="adm-muted">未提取到用料。</p>}</details>
    <details><summary>做法与提取依据</summary>{recipe?.steps?.map((step, index) => <section className="adm-comparison-step" key={index}><h3>{index + 1}. {step.title || "步骤"}</h3><p>{step.action}</p>{(step.heat || step.duration) && <small>{[step.heat, step.duration].filter(Boolean).join(" · ")}</small>}{step.evidence && <blockquote>{step.evidence}</blockquote>}</section>)}</details>
    <details><summary>来源字幕 · 核实引用</summary><div className="adm-comparison-transcript">{item.transcript?.length ? item.transcript.map((line, index) => <p key={index}><time>{line.start === undefined ? "" : `${Math.floor(line.start / 60)}:${String(Math.floor(line.start % 60)).padStart(2, "0")}`}</time>{line.text}</p>) : <p className="adm-muted">暂时没有来源字幕。</p>}</div></details>
    <form onSubmit={event => { event.preventDefault(); void save(); }}><h3>我的整理决定</h3><label className="adm-field">这个来源如何保留<select value={decision} onChange={event => setDecision(event.target.value)}>{Object.entries(decisions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="adm-field">最终规范菜名<input value={title} onChange={event => setTitle(event.target.value)} maxLength={500}/></label><label className="adm-field">做法版本名<input value={variant} onChange={event => setVariant(event.target.value)} placeholder="例如：家常版、传统版" maxLength={500}/></label><label className="adm-field">取舍理由<textarea value={notes} onChange={event => setNotes(event.target.value)} placeholder="留下值得保留的差异和仍需核实的地方" rows={2} maxLength={10000}/></label>{error && <p className="adm-error" role="alert">{error}</p>}<button className="adm-btn primary" disabled={busy} type="submit">{busy ? "正在保存…" : "保存这条决定"}</button></form>
  </article>;
}

export default function CurationPage({ navigate }: PageProps) {
  const query = new URLSearchParams(window.location.search);
  const [search, setSearch] = useState(query.get("q") || "");
  const [report, setReport] = useState<Report | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [revision, refresh] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const filterQuery = new URLSearchParams({ q: query.get("q") || "", status: query.get("status") || "all" }).toString();
  const selected = query.get("group") || "";
  useEffect(() => {
    const controller = new AbortController(); setError("");
    api<Report>(`/curation?${filterQuery}`, { signal: controller.signal }).then(setReport).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [filterQuery, revision]);
  useEffect(() => {
    setComparison(null); if (!selected) return;
    const controller = new AbortController();
    api<Comparison>(`/curation/group?title=${encodeURIComponent(selected)}`, { signal: controller.signal }).then(setComparison).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [selected, revision]);
  function update(changes: Record<string, string>) {
    const next = new URLSearchParams(window.location.search);
    for (const [key, value] of Object.entries(changes)) if (value) next.set(key, value); else next.delete(key);
    navigate(`/curation?${next}`);
  }
  async function scan() {
    setBusy(true); setError("");
    try { const report = await post<Report>("/curation/scan"); setReport(report); refresh(value => value + 1); setNotice("整理清单已更新，已有人工决定继续保留。"); }
    catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <div className="adm-page"><header className="adm-page-head"><div><span className="adm-eyebrow">EVERY VERSION HAS A STORY</span><h1>菜谱版本整理</h1><p className="adm-muted">对照同名与近似名菜谱，留下主版本，也保留不同做法。</p></div><button className="adm-btn primary" disabled={busy} onClick={() => void scan()}>{busy ? "正在整理清单…" : report?.generated_at ? "更新整理清单" : "生成整理清单"}</button></header><p className="adm-notice">这里保存独立的整理意见，不自动合并、删除、隐藏或改写菜谱。选定主版本与变体后，仍可随时调整决定。</p>{error && <div className="adm-error" role="alert">{error}<button className="adm-btn small" onClick={() => refresh(value => value + 1)}>重新加载</button></div>}{notice && <p className="adm-notice" role="status">{notice}</p>}{report?.conflicts.map((conflict, index) => <p className="adm-error" key={index}>{conflict}</p>)}
    {report?.generated_at && <><section className="adm-panel"><div className="adm-section-head"><h2>{report.completed} / {report.total} 个来源已确认</h2><div className="adm-actions"><a className="adm-text-button" href="/api/admin/v1/curation/export?kind=csv" download>下载清单 CSV</a><a className="adm-text-button" href="/api/admin/v1/curation/export?kind=decisions" download>导出人工决定</a></div></div><div className="adm-progress"><i style={{ width: `${report.total ? report.completed / report.total * 100 : 0}%` }}/></div></section><form className="adm-actions" onSubmit={event => { event.preventDefault(); update({ q: search, group: "" }); }}><input aria-label="搜索菜名或来源" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索菜名、视频标题或 BVID"/><select aria-label="整理状态" value={query.get("status") || "all"} onChange={event => update({ status: event.target.value, group: "" })}><option value="all">全部菜名组</option><option value="pending">尚待确认</option><option value="completed">已确认</option></select><button className="adm-btn" type="submit">搜索</button></form></>}
    {!report ? <div className="adm-loading" role="status">正在读取整理清单…</div> : !report.generated_at ? <div className="adm-empty"><h2>每一种做法，都值得认真比较</h2><p>生成清单后，会找出同名及近似名菜谱供你对照。浏览和比较不修改原始内容。</p><button className="adm-btn primary" disabled={busy} onClick={() => void scan()}>开始生成清单</button></div> : <div className="adm-curation-layout"><aside className="adm-curation-groups" aria-label="菜名组">{report.groups.length ? report.groups.map(group => <button key={group.title} className={`adm-panel ${selected === group.title ? "selected" : ""}`} onClick={() => update({ group: group.title })}><strong>{group.title}</strong><span>{group.completed} / {group.total} 个来源已确认</span>{group.similar_titles.length > 0 && <small>近似名：{group.similar_titles.join("、")}</small>}</button>) : <div className="adm-empty"><p>当前没有需要对照的菜名组。</p></div>}</aside><section>{selected ? comparison ? <><div className="adm-section-head"><div><h2>{comparison.title}</h2>{comparison.similar_titles.length > 0 && <p className="adm-muted">一并对照：{comparison.similar_titles.join("、")}</p>}</div></div><div className="adm-comparison-grid">{comparison.items.map(item => <ComparisonCard key={item.item_id} item={item} reportRevision={comparison.revision} navigate={navigate} onSaved={() => { setNotice("整理决定已保存，原始菜谱保持完整。"); refresh(value => value + 1); }}/>)}</div></> : <div className="adm-loading">正在打开各个做法…</div> : <div className="adm-empty"><h2>选择一组菜名，开始比较</h2><p>用料、步骤和来源字幕会并排显示，方便看清做法之间的差异。</p></div>}</section></div>}
  </div>;
}
