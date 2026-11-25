import { useEffect, useState } from "react";
import { api, errorMessage, safeUrl } from "./api";
import type { DishInfo, PageProps, RecipeDetail, RecipeDocument, RecipeIngredient } from "./types";

export function scaledAmount(amount: string | null | undefined, multiplier: number): string {
  if (!amount) return "未注明";
  if (multiplier === 1) return amount;
  const match = amount.match(/^\s*(\d+(?:\.\d+)?)(\s*(?:g|kg|ml|l|克|千克|毫升|升|斤|两|个|只|枚|根|片|颗|勺|小勺|大勺|汤匙|茶匙|杯))\s*$/i);
  if (!match) return amount;
  return `${Math.round(Number(match[1]) * multiplier * 100) / 100}${match[2]}`;
}

function IngredientList({ title, items, multiplier }: { title: string; items: RecipeIngredient[]; multiplier: number }) {
  if (!items.length) return null;
  return <div className="adm-ingredient-group"><h3>{title}</h3><ul>{items.map((item, index) => <li key={index}><label><input type="checkbox"/><span>{item.name}{item.note && <small>{item.note}</small>}</span></label><b>{scaledAmount(item.amount, multiplier)}</b></li>)}</ul></div>;
}

export function RecipeContents({ recipe, info, images = [], multiplier = 1 }: { recipe: RecipeDocument; info?: DishInfo | null; images?: { index: number; url: string }[]; multiplier?: number }) {
  const description = info?.summary || (recipe.ingredients?.length ? `主要用料：${recipe.ingredients.map(item => item.name).join("、")}。` : "暂未补充介绍，可以先查看下方用料与做法。");
  return <>
    <section className="adm-panel adm-introduction"><span className="adm-eyebrow">ABOUT THIS DISH</span><h2>这一道菜</h2><p>{description}</p>{info?.method && <div className="adm-method"><strong>做法概览</strong><p>{info.method}</p></div>}{info?.summary && <small className="adm-muted">{info.source_note || "AI 根据本菜谱整理；用量与详细步骤以原菜谱为准。"}</small>}</section>
    <section className="adm-panel"><div className="adm-section-head"><h2>准备好这些食材</h2>{recipe.servings && <span className="adm-badge">原份量 · {recipe.servings}</span>}</div><div className="adm-ingredient-columns"><IngredientList title="主要食材" items={recipe.ingredients || []} multiplier={multiplier}/><IngredientList title="调味配料" items={recipe.seasonings || []} multiplier={multiplier}/></div>{!recipe.ingredients?.length && !recipe.seasonings?.length && <p className="adm-muted">这道菜暂时没有用料清单。</p>}{multiplier !== 1 && <p className="adm-notice">显示 {multiplier} 倍用量，仅换算明确数字和单位；范围、分数与不明确用量保留原文。请结合实际食材调整。</p>}{Boolean(recipe.tools?.length) && <p className="adm-tools-note"><strong>用到的工具</strong> {recipe.tools!.join("、")}</p>}{Boolean(recipe.prep_items?.length) && <div className="adm-prep"><h3>动手之前</h3><ul>{recipe.prep_items!.map((item, index) => <li key={index}>{item}</li>)}</ul></div>}</section>
    <section className="adm-panel"><div className="adm-section-head"><h2>一步一步，做出好味道</h2><span className="adm-muted">{recipe.steps?.length || 0} 个步骤</span></div>{recipe.steps?.length ? <ol className="adm-recipe-steps">{recipe.steps.map((step, index) => <li key={index}><span className="adm-step-number">{String(index + 1).padStart(2, "0")}</span><div><h3>{step.title || `第 ${index + 1} 步`}</h3><p>{step.action}</p>{(step.heat || step.duration) && <div className="adm-actions"><span className="adm-badge">{[step.heat, step.duration].filter(Boolean).join(" · ")}</span></div>}{step.tips && <p className="adm-step-tip">小提示：{step.tips}</p>}{images.find(image => image.index === index) && <img className="adm-step-image" src={images.find(image => image.index === index)!.url} alt={`${recipe.title} · ${step.title || `第 ${index + 1} 步`}`} loading="lazy"/>}</div></li>)}</ol> : <p className="adm-muted">这道菜暂时没有详细步骤。</p>}</section>
    {Boolean(recipe.summary_tips?.length) && <section className="adm-panel adm-tips"><h2>厨房小贴士</h2><ul>{recipe.summary_tips!.map((tip, index) => <li key={index}>{tip}</li>)}</ul></section>}
    {Boolean(recipe.uncertain_points?.length) && <section className="adm-panel"><h2>做之前再确认一下</h2><ul>{recipe.uncertain_points!.map((point, index) => <li key={index}>{point}</li>)}</ul></section>}
    {(info?.background || info?.origin || info?.sources?.length || info?.warnings?.length || info?.search_status === "failed" || info?.search_status === "no_results") && <section className="adm-panel"><details className="adm-background"><summary>菜品背景与起源参考</summary><p className="adm-notice">外部资料可能对应同名菜或其他做法，尚未核实与这份菜谱的对应关系。</p>{info?.background && <p>{info.background}</p>}{info?.origin && <p>{info.origin}</p>}{info?.search_status === "failed" && <p className="adm-muted">本次背景检索未完成，已有菜谱内容仍可查看。</p>}{info?.search_status === "no_results" && <p className="adm-muted">暂未找到可供参考的背景资料。</p>}{info?.sources?.map((source, index) => <div className="adm-source-reference" key={index}>{safeUrl(source.url) ? <a href={safeUrl(source.url)} target="_blank" rel="noreferrer">{source.title || "参考来源"} ↗</a> : <strong>{source.title || "参考信息"}</strong>}<small>{source.match_status === "possible_match" ? "可能相关 · 尚待核实" : "匹配关系未确认，不作为本菜谱事实"}</small>{source.excerpt && <p>{source.excerpt}</p>}</div>)}{info?.warnings?.map((warning, index) => <p className="adm-muted" key={index}>{warning}</p>)}</details></section>}
  </>;
}

export default function RecipePage({ navigate }: PageProps) {
  const id = decodeURIComponent(window.location.pathname.split("/")[3] || "");
  const [detail, setDetail] = useState<RecipeDetail | null>(null);
  const [error, setError] = useState("");
  const [revision, refresh] = useState(0);
  const [multiplier, setMultiplier] = useState(1);
  const [cooking, setCooking] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setDetail(null); setError(""); setStepIndex(0);
    api<RecipeDetail>(`/recipes/${encodeURIComponent(id)}`, { signal: controller.signal }).then(setDetail).catch(error => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [id, revision]);
  if (error) return <div className="adm-empty" role="alert"><h2>这道菜暂时无法打开</h2><p>{error}</p><div className="adm-actions"><button className="adm-btn" onClick={() => navigate("/library")}>回到菜谱库</button><button className="adm-btn primary" onClick={() => refresh(value => value + 1)}>重试</button></div></div>;
  if (!detail) return <div className="adm-loading" role="status"><span className="adm-spinner"/>正在准备菜谱…</div>;
  const recipe = detail.recipe;
  const sourceUrl = safeUrl(recipe.source_url);
  const step = recipe.steps?.[stepIndex];
  const task = (kind: string) => navigate(`/tasks?create=${kind}&recipe_ids=${encodeURIComponent(id)}`);
  const returnToLibrary = () => {
    let target = "/library";
    try { const previous = sessionStorage.getItem("admin-library-location"); if (previous && /^\/library(?:\?|$)/.test(previous)) target = previous; } catch { /* Use the default library. */ }
    navigate(target);
  };
  return <div className="adm-page adm-recipe-detail">
    <div className="adm-detail-topline"><button className="adm-text-button" onClick={returnToLibrary}>← 返回菜谱库</button><div className="adm-actions"><button className="adm-btn" onClick={() => navigate(`/recipes/${encodeURIComponent(id)}/edit`)}>编辑菜谱</button><button className="adm-btn primary" disabled={!recipe.steps?.length} onClick={() => setCooking(!cooking)}>{cooking ? "退出烹饪模式" : "开始烹饪"}</button></div></div>
    <section className="adm-detail-hero"><div className="adm-detail-hero-copy"><span className="adm-eyebrow">{recipe.category || "MY RECIPE"}{recipe.cuisine && recipe.cuisine !== "未分类" ? ` / ${recipe.cuisine}` : ""}</span><h1>{recipe.title}</h1><p className="adm-muted">来自 {recipe.creator_name || recipe.uploader || "我的厨房收藏"}</p><div className="adm-actions">{recipe.total_time && <span className="adm-badge">◷ {recipe.total_time}</span>}{recipe.servings && <span className="adm-badge">{recipe.servings}</span>}{recipe.difficulty && <span className="adm-badge">{recipe.difficulty}</span>}{detail.visibility?.hidden && <span className="adm-badge warning">已隐藏</span>}</div><div className="adm-tags">{recipe.tags?.map(tag => <span key={tag}>#{tag}</span>)}</div>{sourceUrl && <a className="adm-text-button" href={sourceUrl} target="_blank" rel="noreferrer">查看原视频 ↗</a>}</div>{detail.cover_url && <img className="adm-detail-cover" src={detail.cover_url} alt={`${recipe.title}成品图`}/>}</section>
    {cooking && step && <section className="adm-cooking-focus" aria-label="烹饪模式"><div className="adm-section-head"><span className="adm-eyebrow">现在做到第 {stepIndex + 1} / {recipe.steps?.length} 步</span><button className="adm-text-button" onClick={() => setCooking(false)}>退出聚焦</button></div><h2>{step.title || `第 ${stepIndex + 1} 步`}</h2><p>{step.action}</p>{(step.heat || step.duration) && <p className="adm-muted">{[step.heat, step.duration].filter(Boolean).join(" · ")}</p>}{step.tips && <p className="adm-step-tip">{step.tips}</p>}<div className="adm-actions"><button className="adm-btn" disabled={stepIndex === 0} onClick={() => setStepIndex(value => value - 1)}>← 上一步</button><button className="adm-btn primary" disabled={stepIndex === recipe.steps!.length - 1} onClick={() => setStepIndex(value => value + 1)}>下一步 →</button></div></section>}
    <div className="adm-detail-layout"><div className="adm-detail-main"><RecipeContents recipe={recipe} info={detail.dish_info} images={detail.step_images} multiplier={multiplier}/></div><aside className="adm-detail-aside"><section className="adm-panel"><h2>这次做多少</h2><label className="adm-field">用量倍率<select value={multiplier} onChange={event => setMultiplier(Number(event.target.value))}>{[0.5, 1, 1.5, 2, 3, 4].map(value => <option key={value} value={value}>{value} 倍{value === 1 ? " · 原菜谱" : ""}</option>)}</select></label><p className="adm-muted">勾选已准备好的食材，再从第一步慢慢开始。</p></section><section className="adm-panel"><h2>完善这道菜</h2><div className="adm-detail-tools"><button onClick={() => navigate(`/review?id=${encodeURIComponent(id)}`)}>核对菜谱与来源 <span>→</span></button><button onClick={() => navigate(`/review?id=${encodeURIComponent(id)}&tab=cover`)}>确认或手工选择封面 <span>→</span></button><button onClick={() => task("frames")}>AI 挑选成品特写 <span>↗</span></button><button onClick={() => navigate(`/tasks?create=frames&mode=generate&recipe_ids=${encodeURIComponent(id)}`)}>AI 生成示意封面 <span>↗</span></button><button onClick={() => task("dish-info")}>AI 补充菜品介绍 <span>↗</span></button><button onClick={() => task("review")}>AI 审核这道菜 <span>↗</span></button><button onClick={() => navigate(`/recipes/${encodeURIComponent(id)}/tools`)}>AI 优化、二次分析与步骤图片 <span>→</span></button></div>{detail.frame_candidate?.status === "ok" && <p className="adm-notice">已有 AI 候选特写，可到封面审核查看与裁剪。</p>}</section><div className="adm-recipe-aside-note">每一道好菜，<br/>都是值得保留的日常。</div></aside></div>
  </div>;
}
