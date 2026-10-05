import type { ReactNode } from "react";
import type { Recipe } from "./sync";

export function safeSourceUrl(value?: string): string | undefined {
  if (!value) return undefined;
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : undefined; }
  catch { return undefined; }
}

export function RecipeContent({ recipe, renderImage }: { recipe: Recipe; renderImage?: (digest: string, alt: string) => ReactNode }) {
  const info = recipe.dish_info;
  const names = (recipe.ingredients || []).map(item => item.name).filter(Boolean);
  const summary = info?.summary || recipe.summary || (names.length ? `主要用料：${names.join("、")}。` : "这道菜暂时没有单独的介绍，可查看下方做法。");
  const method = info?.method || (recipe.steps || []).map(step => step.title || step.action).filter(Boolean).slice(0, 4).join(" → ");
  const source = safeSourceUrl(recipe.source_url);
  return <>
    <section className="dishIntroduction" aria-labelledby="dish-introduction-heading">
      <h2 id="dish-introduction-heading">菜品简介</h2><p>{summary}</p>
      {method && <><h3>做法概览</h3><p>{method}</p></>}
      {(info?.summary || info?.method) && <small className="informationSource">{info.source_note || "AI 根据本菜谱整理，详细用料与步骤见下方。"}</small>}
    </section>
    <section className="dishIngredients" aria-labelledby="dish-ingredients-heading">
      <h2 id="dish-ingredients-heading">用料</h2>
      <div className="ingredientGroups">{([['主要食材', recipe.ingredients], ['调料', recipe.seasonings]] as const).map(([label, items]) => items?.length ? <div key={label}><h3>{label}</h3><ul>{items.map((item, index) => <li key={index}><span>{item.name || "未命名用料"}</span>{item.amount && <b>{item.amount}</b>}</li>)}</ul></div> : null)}</div>
      {!recipe.ingredients?.length && !recipe.seasonings?.length && <p className="hint">这道菜暂时没有用料清单。</p>}
      {Boolean(recipe.tools?.length) && <p className="dishTools">工具：{recipe.tools!.join("、")}</p>}
    </section>
    {Boolean(recipe.prep_items?.length) && <section><h2>动手之前</h2><ul>{recipe.prep_items!.map((item, index) => <li key={index}>{item}</li>)}</ul></section>}
    <section aria-labelledby="dish-steps-heading"><h2 id="dish-steps-heading">详细做法</h2>
      {recipe.steps?.length ? <ol className="recipeSteps">{recipe.steps.map((step, index) => <li key={index}>
        <span className="stepNumber" aria-hidden="true">{index + 1}</span><div><h3>{step.title || `第 ${index + 1} 步`}</h3>
          {step.action && <p>{step.action}</p>}{(step.heat || step.duration) && <p className="stepTiming">{[step.heat, step.duration].filter(Boolean).join(" · ")}</p>}
          {step.tips && <p className="stepTip">提示：{step.tips}</p>}
          {step.image_sha256 && renderImage?.(step.image_sha256, `${recipe.title}：${step.title || `第 ${index + 1} 步`}`)}
        </div></li>)}</ol> : <p className="hint">这道菜暂时没有详细步骤。</p>}
    </section>
    {Boolean(recipe.summary_tips?.length) && <section className="dishTips"><h2>烹饪小贴士</h2><ul>{recipe.summary_tips!.map((tip, index) => <li key={index}>{tip}</li>)}</ul></section>}
    {(info?.background || info?.origin || info?.sources?.length || info?.warnings?.length || ["failed", "no_results"].includes(info?.search_status || "")) && <details className="dishBackground"><summary>延伸了解</summary><p className="informationSource">以下是外部参考，尚未核实与这份菜谱的对应关系。</p>{info?.search_status === "failed" && <p className="informationSource">这次未能查询外部资料，菜谱用料与做法仍可查看。</p>}{info?.search_status === "no_results" && <p className="informationSource">暂时没有找到可供参考的外部资料。</p>}{info?.background && <p>{info.background}</p>}{info?.origin && <p>{info.origin}</p>}{info?.sources?.map((item, index) => {
      const url = safeSourceUrl(item.url);
      return <div className="referenceSource" key={index}>{url ? <a href={url} target="_blank" rel="noreferrer">{item.title || "查看参考来源"} ↗</a> : <b>{item.title || "参考信息"}</b>}<small className="informationSource">{item.match_status === "possible_match" ? "可能相关，尚待核实" : "可能不是同一道菜，未采用其历史内容"}</small>{item.excerpt && <p>{item.excerpt}</p>}</div>;
    })}{info?.warnings?.map((warning, index) => <p className="informationSource" key={index}>{warning}</p>)}</details>}
    {(source || recipe.creator_name || recipe.uploader) && <p className="recipeSource">{(recipe.creator_name || recipe.uploader) && <span>菜谱来源：{recipe.creator_name || recipe.uploader}</span>}{source && <a href={source} target="_blank" rel="noreferrer">查看原视频 ↗</a>}</p>}
  </>;
}
