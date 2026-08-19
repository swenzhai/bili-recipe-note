from __future__ import annotations

from typing import Any


COMPONENT_HTML = """
<div class="meal-app" aria-label="家庭点餐台"></div>
"""


COMPONENT_CSS = r"""
:host {
  --paper: #f7f1e7;
  --paper-deep: #ece2d2;
  --ink: #26372f;
  --muted: #756f65;
  --line: rgba(49, 58, 50, .14);
  --red: #c85c43;
  --red-deep: #9f3f30;
  --green: #31483d;
  --cream: #fffaf2;
  --shadow: 0 18px 48px rgba(58, 43, 29, .10);
  display: block;
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
    "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
* { box-sizing: border-box; }
button, input, textarea { font: inherit; }
button { color: inherit; }
.meal-app {
  min-height: 760px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 26px;
  background:
    radial-gradient(circle at 4% 0%, rgba(221, 152, 92, .20), transparent 28%),
    linear-gradient(145deg, #fbf6ed 0%, var(--paper) 52%, #f4ecdf 100%);
  box-shadow: var(--shadow);
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 24px;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 250, 242, .78);
  backdrop-filter: blur(16px);
}
.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark {
  width: 42px; height: 42px; display: grid; place-items: center;
  border-radius: 14px; color: white; font-size: 21px;
  background: linear-gradient(145deg, var(--red), var(--red-deep));
  box-shadow: 0 8px 20px rgba(159, 63, 48, .25);
}
.brand small, .eyebrow {
  display: block; color: var(--red); font-size: 10px; font-weight: 850;
  letter-spacing: .16em; text-transform: uppercase;
}
.brand strong {
  display: block; margin-top: 2px; font-family: Georgia, "Songti SC", serif;
  font-size: 21px; line-height: 1;
}
.top-summary {
  display: flex; align-items: center; gap: 9px; color: var(--muted);
  font-size: 12px; font-weight: 700;
}
.count-badge {
  min-width: 28px; height: 28px; padding: 0 8px; display: grid; place-items: center;
  border-radius: 999px; background: var(--green); color: white;
}
.hero {
  position: relative; overflow: hidden; margin: 18px 22px 16px; padding: 26px 28px;
  border-radius: 22px; color: white;
  background:
    radial-gradient(circle at 88% 16%, rgba(239, 178, 112, .35), transparent 30%),
    linear-gradient(120deg, #263d34, #3d5448 58%, #253b32);
}
.hero::after {
  content: "食"; position: absolute; right: 5%; bottom: -42px;
  color: rgba(255,255,255,.055); font: 170px/1 Georgia, "Songti SC", serif;
}
.hero-content { position: relative; z-index: 1; display: grid; grid-template-columns: 1.3fr 1fr; gap: 28px; align-items: end; }
.hero h1 { margin: 7px 0 8px; font: 700 35px/1.12 Georgia, "Songti SC", serif; letter-spacing: -.02em; }
.hero p { margin: 0; max-width: 580px; color: rgba(255,255,255,.70); font-size: 13px; line-height: 1.65; }
.occasion-row { display: flex; gap: 7px; flex-wrap: wrap; margin-top: 18px; }
.occasion {
  border: 1px solid rgba(255,255,255,.19); border-radius: 999px;
  padding: 8px 12px; background: rgba(255,255,255,.06); color: rgba(255,255,255,.78);
  font-size: 12px; cursor: pointer; transition: .18s ease;
}
.occasion:hover, .occasion.active { background: #fff7ea; border-color: #fff7ea; color: var(--green); transform: translateY(-1px); }
.party-panel {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding: 14px;
  border: 1px solid rgba(255,255,255,.14); border-radius: 18px;
  background: rgba(255,255,255,.08); backdrop-filter: blur(12px);
}
.party-control { min-width: 0; }
.party-control label { display: block; margin-bottom: 7px; color: rgba(255,255,255,.65); font-size: 10px; font-weight: 800; letter-spacing: .08em; }
.stepper { display: grid; grid-template-columns: 34px 1fr 34px; align-items: center; border-radius: 12px; background: rgba(0,0,0,.16); overflow: hidden; }
.stepper button { height: 36px; border: 0; background: transparent; color: white; cursor: pointer; font-size: 20px; }
.stepper button:hover { background: rgba(255,255,255,.10); }
.stepper strong { text-align: center; font-size: 14px; }
.recommend {
  grid-column: 1 / -1; display: flex; justify-content: center; align-items: center; gap: 8px;
  height: 42px; border: 0; border-radius: 12px; background: #e7a66f; color: #26372f;
  font-weight: 850; cursor: pointer; box-shadow: 0 8px 18px rgba(0,0,0,.13);
}
.recommend:hover { background: #f1b581; transform: translateY(-1px); }
.workspace { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 18px; padding: 0 22px 24px; }
.catalog { min-width: 0; }
.tools { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
.search {
  flex: 1; display: flex; align-items: center; gap: 9px; height: 46px; padding: 0 15px;
  border: 1px solid var(--line); border-radius: 14px; background: rgba(255,255,255,.63);
  box-shadow: 0 5px 16px rgba(61, 47, 34, .04);
}
.search span { color: var(--red); font-size: 18px; }
.search input { width: 100%; border: 0; outline: 0; background: transparent; color: var(--ink); font-size: 14px; }
.search input::placeholder { color: #a49d92; }
.result-count { white-space: nowrap; color: var(--muted); font-size: 12px; }
.category-strip { display: flex; gap: 8px; overflow-x: auto; padding: 2px 0 13px; scrollbar-width: none; }
.category-strip::-webkit-scrollbar { display: none; }
.category {
  flex: 0 0 auto; border: 1px solid var(--line); border-radius: 999px; padding: 8px 13px;
  background: rgba(255,255,255,.44); color: var(--muted); font-size: 12px; font-weight: 750; cursor: pointer;
}
.category:hover, .category.active { border-color: var(--green); background: var(--green); color: white; }
.recipe-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 13px; }
.recipe-card {
  position: relative; overflow: hidden; min-width: 0; border: 1px solid var(--line);
  border-radius: 18px; background: rgba(255,255,255,.68); box-shadow: 0 8px 24px rgba(59, 45, 32, .06);
  transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
}
.recipe-card:hover { transform: translateY(-3px); border-color: rgba(200,92,67,.35); box-shadow: 0 15px 32px rgba(59,45,32,.11); }
.cover { position: relative; height: 142px; overflow: hidden; background: linear-gradient(145deg, #d6a178, #7d4635); }
.cover img { width: 100%; height: 100%; display: block; object-fit: cover; transition: transform .35s ease; }
.recipe-card:hover .cover img { transform: scale(1.04); }
.cover-fallback { width: 100%; height: 100%; display: grid; place-items: center; color: rgba(255,255,255,.9); font: 700 45px Georgia, serif; background: radial-gradient(circle at 30% 20%, #e5b17a, #b95742 68%, #7e3028); }
.kind-label { position: absolute; left: 10px; top: 10px; padding: 5px 8px; border-radius: 999px; background: rgba(37,55,47,.82); color: white; font-size: 9px; font-weight: 850; letter-spacing: .08em; backdrop-filter: blur(7px); }
.card-body { padding: 13px 13px 14px; }
.card-body h3 { overflow: hidden; margin: 0 42px 6px 0; font: 750 18px/1.25 Georgia, "Songti SC", serif; text-overflow: ellipsis; white-space: nowrap; }
.meta { overflow: hidden; color: var(--muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.tag-row { display: flex; gap: 5px; min-height: 23px; margin-top: 10px; overflow: hidden; }
.tag { flex: 0 0 auto; padding: 4px 6px; border-radius: 5px; background: var(--paper-deep); color: #6a6259; font-size: 9px; }
.add {
  position: absolute; right: 12px; bottom: 44px; width: 36px; height: 36px;
  border: 0; border-radius: 50%; background: var(--red); color: white; font-size: 22px;
  line-height: 1; cursor: pointer; box-shadow: 0 7px 16px rgba(164,60,44,.25); transition: .18s ease;
}
.add:hover { transform: scale(1.08); }
.add.added { background: var(--green); font-size: 16px; }
.empty-menu { grid-column: 1 / -1; padding: 70px 20px; text-align: center; color: var(--muted); }
.empty-menu b { display: block; margin-bottom: 7px; color: var(--ink); font: 700 24px Georgia, serif; }
.pager { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 10px; margin: 15px 0 2px; }
.pager span { color: var(--muted); font-size: 11px; font-weight: 750; }
.pager button { height: 38px; border: 1px solid var(--line); border-radius: 11px; background: rgba(255,255,255,.62); cursor: pointer; font-size: 12px; font-weight: 750; }
.pager button:last-child { justify-self: stretch; }
.pager button:disabled { opacity: .35; cursor: default; }
.pager button:not(:disabled):hover { border-color: var(--red); color: var(--red); }
.cart {
  position: sticky; top: 12px; align-self: start; max-height: 720px; display: flex; flex-direction: column;
  overflow: hidden; border-radius: 20px; background: var(--cream); border: 1px solid var(--line);
  box-shadow: 0 13px 34px rgba(59,45,32,.09);
}
.cart-head { padding: 18px 18px 14px; color: white; background: linear-gradient(125deg, var(--green), #40594c); }
.cart-title { display: flex; align-items: end; justify-content: space-between; gap: 12px; }
.cart-title h2 { margin: 4px 0 0; font: 700 23px Georgia, "Songti SC", serif; }
.cart-title strong { font: 700 25px Georgia, serif; }
.cart-head p { margin: 8px 0 0; color: rgba(255,255,255,.65); font-size: 11px; }
.cart-list { flex: 1; overflow: auto; padding: 6px 14px; scrollbar-width: thin; }
.cart-empty { padding: 42px 18px; text-align: center; color: var(--muted); }
.cart-empty span { display: block; margin-bottom: 12px; font-size: 45px; filter: grayscale(.2); }
.cart-empty b { display: block; margin-bottom: 7px; color: var(--ink); font-family: Georgia, serif; font-size: 19px; }
.cart-item { padding: 13px 2px; border-bottom: 1px solid var(--line); }
.cart-item-top { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: start; }
.cart-item h4 { margin: 0; font: 750 15px/1.35 Georgia, "Songti SC", serif; }
.cart-item small { display: block; margin-top: 3px; color: var(--muted); font-size: 10px; }
.remove { width: 27px; height: 27px; border: 0; border-radius: 50%; background: transparent; color: #9c9185; cursor: pointer; font-size: 18px; }
.remove:hover { background: #f0e4d6; color: var(--red); }
.portion { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.portion span { margin-right: auto; color: var(--muted); font-size: 10px; font-weight: 750; }
.portion button { width: 27px; height: 27px; border: 1px solid var(--line); border-radius: 8px; background: white; cursor: pointer; }
.portion button:hover { border-color: var(--red); color: var(--red); }
.portion strong { min-width: 38px; text-align: center; font-size: 12px; }
.note-input { width: 100%; margin-top: 9px; padding: 8px 9px; border: 1px dashed #c9bbaa; border-radius: 8px; outline: 0; background: #fbf5ec; color: var(--ink); font-size: 11px; }
.note-input:focus { border-style: solid; border-color: var(--red); background: white; }
.cart-foot { padding: 13px 15px 15px; border-top: 1px solid var(--line); background: #f5ecdf; }
.cart-summary { display: flex; justify-content: space-between; margin-bottom: 10px; color: var(--muted); font-size: 11px; }
.cart-summary strong { color: var(--ink); }
.settle { width: 100%; height: 43px; border: 0; border-radius: 12px; background: var(--red); color: white; font-weight: 850; cursor: pointer; box-shadow: 0 8px 18px rgba(165,70,51,.20); }
.settle:hover { background: var(--red-deep); }
.clear { width: 100%; margin-top: 8px; border: 0; background: transparent; color: var(--muted); font-size: 11px; cursor: pointer; }
.mobile-nav { display: none; }
.toast {
  position: fixed; z-index: 20; left: 50%; bottom: 82px; transform: translate(-50%, 12px);
  max-width: calc(100% - 36px); padding: 10px 15px; border-radius: 10px;
  background: #23372e; color: white; box-shadow: 0 9px 28px rgba(0,0,0,.2);
  font-size: 12px; opacity: 0; pointer-events: none; transition: .2s ease;
}
.toast.show { opacity: 1; transform: translate(-50%, 0); }
@media (max-width: 1050px) {
  .recipe-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .workspace { grid-template-columns: minmax(0, 1fr) 300px; }
}
@media (max-width: 760px) {
  .meal-app { min-height: 720px; border: 0; border-radius: 0; box-shadow: none; }
  .topbar { padding: 14px 16px; }
  .brand-mark { width: 38px; height: 38px; border-radius: 12px; }
  .brand strong { font-size: 18px; }
  .top-summary > span { display: none; }
  .hero { margin: 10px 12px 13px; padding: 21px 18px; border-radius: 18px; }
  .hero-content { grid-template-columns: 1fr; gap: 18px; }
  .hero h1 { font-size: 29px; }
  .hero p { font-size: 12px; }
  .occasion-row { flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; margin-right: -18px; padding-right: 18px; }
  .occasion { white-space: nowrap; }
  .workspace { display: block; padding: 0 12px 86px; }
  .tools { position: sticky; z-index: 5; top: 0; padding: 8px 0; background: rgba(247,241,231,.95); backdrop-filter: blur(12px); }
  .result-count { display: none; }
  .recipe-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; }
  .cover { height: 118px; }
  .card-body { padding: 11px; }
  .card-body h3 { margin-right: 34px; font-size: 16px; }
  .add { right: 9px; bottom: 40px; width: 33px; height: 33px; }
  .catalog.mobile-hidden, .cart.mobile-hidden { display: none; }
  .cart { position: static; max-height: none; border-radius: 18px; }
  .cart-list { max-height: none; overflow: visible; }
  .mobile-nav {
    position: fixed; z-index: 15; left: 0; right: 0; bottom: 0; display: grid; grid-template-columns: 1fr 1fr;
    padding: 7px 12px max(9px, env(safe-area-inset-bottom)); border-top: 1px solid var(--line);
    background: rgba(255,250,242,.94); backdrop-filter: blur(18px); box-shadow: 0 -8px 25px rgba(54,42,31,.08);
  }
  .mobile-nav button { position: relative; height: 49px; border: 0; border-radius: 12px; background: transparent; color: #8c8277; font-size: 11px; font-weight: 800; cursor: pointer; }
  .mobile-nav button span { display: block; margin-bottom: 2px; font-size: 20px; }
  .mobile-nav button.active { background: #eee3d4; color: var(--red); }
  .mobile-nav b { position: absolute; top: 2px; left: calc(50% + 8px); min-width: 18px; height: 18px; display: grid; place-items: center; padding: 0 5px; border-radius: 999px; background: var(--red); color: white; font-size: 9px; }
}
@media (max-width: 390px) {
  .recipe-grid { grid-template-columns: 1fr; }
  .cover { height: 168px; }
}
"""


COMPONENT_JS = r"""
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");

const normalizeOrder = (raw, recipes) => {
  const known = new Set(recipes.map((recipe) => recipe.id));
  const selected = Array.isArray(raw?.selected_ids)
    ? [...new Set(raw.selected_ids.filter((id) => known.has(id)))] : [];
  const multipliers = {};
  const notes = {};
  selected.forEach((id) => {
    const factor = Number(raw?.multipliers?.[id] ?? 1);
    multipliers[id] = Number.isFinite(factor) ? Math.min(10, Math.max(.25, factor)) : 1;
    notes[id] = String(raw?.notes?.[id] ?? "").slice(0, 200);
  });
  const guestCount = Math.min(50, Math.max(1, Number(raw?.guest_count ?? 4)));
  return {
    selectedIds: selected,
    multipliers,
    notes,
    guestCount,
    childCount: Math.min(guestCount, Math.max(0, Number(raw?.child_count ?? 0))),
    occasion: String(raw?.occasion ?? "日常家宴"),
    query: "",
    category: "全部",
    page: 0,
    mobileView: "menu",
  };
};

const publicOrder = (state) => ({
  selected_ids: state.selectedIds,
  multipliers: state.multipliers,
  notes: state.notes,
  guest_count: state.guestCount,
  child_count: state.childCount,
  occasion: state.occasion,
});

const scoreRecipe = (recipe, state) => {
  const text = [recipe.title, recipe.category, recipe.cuisine, ...(recipe.tags || [])].join(" ");
  let score = Number(recipe.quality_score || 0) / 10;
  if (state.childCount || state.occasion === "带小孩") {
    ["蒸", "炖", "蛋", "豆腐", "汤", "粥", "鸡", "虾", "南瓜"].forEach((word) => { if (text.includes(word)) score += 8; });
    ["辣", "麻", "剁椒", "水煮", "香辣", "酸辣", "酒", "醉", "花雕", "啤酒"].forEach((word) => { if (text.includes(word)) score -= 35; });
    ["炸", "冰镇", "生食"].forEach((word) => { if (text.includes(word)) score -= 10; });
  }
  if (state.occasion === "清淡家宴") {
    ["蒸", "炖", "汤", "白灼", "清炒"].forEach((word) => { if (text.includes(word)) score += 5; });
    ["炸", "辣", "麻", "油"].forEach((word) => { if (text.includes(word)) score -= 20; });
  }
  return score;
};

const recommendRecipes = (recipes, state) => {
  const seen = new Set();
  const ordered = [...recipes]
    .sort((left, right) => scoreRecipe(right, state) - scoreRecipe(left, state)
      || left.title.localeCompare(right.title, "zh-CN") || left.id.localeCompare(right.id))
    .filter((recipe) => {
      const title = recipe.title.replaceAll(/\s/g, "").toLocaleLowerCase();
      if (seen.has(title)) return false;
      seen.add(title); return true;
    });
  const target = Math.min(10, Math.max(3, state.guestCount + 1));
  const pools = Object.fromEntries(["main", "vegetable", "soup", "staple", "dessert"].map((kind) => [kind, ordered.filter((recipe) => recipe.kind === kind)]));
  const selected = [];
  const take = (kind, count) => {
    for (const recipe of pools[kind]) {
      if (selected.length >= target || count <= 0) break;
      if (!selected.includes(recipe)) { selected.push(recipe); count -= 1; }
    }
  };
  const vegetableTarget = target <= 5 ? 1 : 2;
  let reserved = (pools.vegetable.length ? vegetableTarget : 0)
    + (pools.soup.length ? 1 : 0) + (pools.staple.length ? 1 : 0);
  if (state.childCount && pools.dessert.length && target >= 5) reserved += 1;
  take("main", Math.max(1, target - reserved));
  take("vegetable", vegetableTarget); take("soup", 1); take("staple", 1);
  if (state.childCount && target >= 5) take("dessert", 1);
  ordered.forEach((recipe) => { if (selected.length < target && !selected.includes(recipe)) selected.push(recipe); });
  return selected.map((recipe) => recipe.id);
};

export default function(component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component;
  const root = parentElement.querySelector(".meal-app");
  const recipes = Array.isArray(data?.recipes) ? data.recipes : [];
  const recipeById = new Map(recipes.map((recipe) => [recipe.id, recipe]));
  if (!root.__mealState || root.__mealRevision !== data?.revision) {
    root.__mealState = normalizeOrder(data?.order || {}, recipes);
    root.__mealRevision = data?.revision;
  }
  const state = root.__mealState;
  const occasions = Array.isArray(data?.occasions) ? data.occasions : [];

  const commit = (message) => {
    setStateValue("order", publicOrder(state));
    render();
    if (message) showToast(message);
  };
  const scheduleCommit = () => {
    clearTimeout(root.__commitTimer);
    root.__commitTimer = setTimeout(() => setStateValue("order", publicOrder(state)), 420);
  };
  const showToast = (message) => {
    const toast = root.querySelector(".toast");
    if (!toast) return;
    toast.textContent = message; toast.classList.add("show");
    clearTimeout(root.__toastTimer);
    root.__toastTimer = setTimeout(() => toast.classList.remove("show"), 1700);
  };
  const updateFactor = (id, delta) => {
    state.multipliers[id] = Math.min(10, Math.max(.5, Math.round(((state.multipliers[id] || 1) + delta) * 2) / 2));
    commit();
  };
  const removeRecipe = (id) => {
    state.selectedIds = state.selectedIds.filter((value) => value !== id);
    delete state.multipliers[id]; delete state.notes[id];
    commit("已从本餐移除");
  };
  const addRecipe = (id) => {
    if (!state.selectedIds.includes(id)) state.selectedIds.push(id);
    if (!(id in state.multipliers)) state.multipliers[id] = 1;
    if (!(id in state.notes)) state.notes[id] = "";
    commit(`已加入“${recipeById.get(id)?.title || "菜品"}”`);
  };

  const renderCard = (recipe) => {
    const added = state.selectedIds.includes(recipe.id);
    const image = recipe.image
      ? `<img src="${escapeHtml(recipe.image)}" alt="${escapeHtml(recipe.title)}" loading="lazy">`
      : `<div class="cover-fallback">${escapeHtml(recipe.title.slice(0, 1))}</div>`;
    const tags = (recipe.tags || []).slice(0, 3).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
    return `<article class="recipe-card">
      <div class="cover">${image}<span class="kind-label">${escapeHtml(recipe.category || "今日推荐")}</span></div>
      <div class="card-body"><h3 title="${escapeHtml(recipe.title)}">${escapeHtml(recipe.title)}</h3>
        <div class="meta">${escapeHtml([recipe.cuisine, recipe.servings].filter(Boolean).join(" · ") || "家常风味")}</div>
        <div class="tag-row">${tags}</div>
      </div>
      <button class="add ${added ? "added" : ""}" data-action="${added ? "remove" : "add"}" data-id="${escapeHtml(recipe.id)}" aria-label="${added ? "移出本餐" : "加入本餐"}">${added ? "✓" : "+"}</button>
    </article>`;
  };

  const renderCartItem = (id) => {
    const recipe = recipeById.get(id);
    if (!recipe) return "";
    const factor = Number(state.multipliers[id] || 1);
    return `<section class="cart-item">
      <div class="cart-item-top"><div><h4>${escapeHtml(recipe.title)}</h4><small>${escapeHtml([recipe.category, recipe.cuisine].filter(Boolean).join(" · "))}</small></div>
        <button class="remove" data-action="remove" data-id="${escapeHtml(id)}" aria-label="移出本餐">×</button></div>
      <div class="portion"><span>份量</span><button data-action="factor" data-id="${escapeHtml(id)}" data-delta="-.5">−</button><strong>${factor.toFixed(1)}×</strong><button data-action="factor" data-id="${escapeHtml(id)}" data-delta=".5">＋</button></div>
      <input class="note-input" data-note-id="${escapeHtml(id)}" maxlength="200" value="${escapeHtml(state.notes[id] || "")}" placeholder="少辣、不要香菜、儿童份……">
    </section>`;
  };

  function render() {
    const categories = ["全部", ...new Set(recipes.map((recipe) => recipe.category || "未分类"))];
    const query = state.query.trim().toLocaleLowerCase();
    const filtered = recipes.filter((recipe) => (state.category === "全部" || (recipe.category || "未分类") === state.category)
      && (!query || [recipe.title, recipe.category, recipe.cuisine, ...(recipe.tags || [])].join(" ").toLocaleLowerCase().includes(query)));
    const pageSize = window.matchMedia("(max-width: 760px)").matches ? 8 : 12;
    const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
    state.page = Math.min(Math.max(0, state.page), pageCount - 1);
    const visibleRecipes = filtered.slice(state.page * pageSize, (state.page + 1) * pageSize);
    const cards = visibleRecipes.length ? visibleRecipes.map(renderCard).join("") : `<div class="empty-menu"><b>没有找到这道菜</b>换个菜名、分类或标签试试。</div>`;
    const pager = filtered.length > pageSize ? `<div class="pager"><button data-action="page" data-delta="-1" ${state.page === 0 ? "disabled" : ""}>← 上一页</button><span>${state.page + 1} / ${pageCount}</span><button data-action="page" data-delta="1" ${state.page >= pageCount - 1 ? "disabled" : ""}>下一页 →</button></div>` : "";
    const cartItems = state.selectedIds.length ? state.selectedIds.map(renderCartItem).join("") : `<div class="cart-empty"><span>🍲</span><b>本餐还没有菜</b>从菜单中挑几道想吃的，份量和采购清单会自动整理。</div>`;
    root.innerHTML = `<header class="topbar"><div class="brand"><div class="brand-mark">食</div><div><small>FAMILY TABLE</small><strong>家庭点餐台</strong></div></div>
      <div class="top-summary"><span>${escapeHtml(state.occasion)} · ${state.guestCount} 人</span><b class="count-badge">${state.selectedIds.length}</b></div></header>
      <section class="hero"><div class="hero-content"><div><span class="eyebrow">TODAY'S TABLE</span><h1>今天，想吃点什么？</h1><p>从自己的菜谱库慢慢挑选。系统会照顾人数、荤素搭配和儿童口味，最后统一整理份量与采购清单。</p>
        <div class="occasion-row">${occasions.map((value) => `<button class="occasion ${value === state.occasion ? "active" : ""}" data-action="occasion" data-value="${escapeHtml(value)}">${escapeHtml(value)}</button>`).join("")}</div></div>
        <div class="party-panel"><div class="party-control"><label>用餐人数</label><div class="stepper"><button data-action="guest" data-delta="-1">−</button><strong>${state.guestCount} 人</strong><button data-action="guest" data-delta="1">＋</button></div></div>
          <div class="party-control"><label>其中儿童</label><div class="stepper"><button data-action="child" data-delta="-1">−</button><strong>${state.childCount} 人</strong><button data-action="child" data-delta="1">＋</button></div></div>
          <button class="recommend" data-action="recommend"><span>✦</span> 按这桌客人智能配菜</button></div></div></section>
      <main class="workspace"><section class="catalog ${state.mobileView === "cart" ? "mobile-hidden" : ""}"><div class="tools"><label class="search"><span>⌕</span><input value="${escapeHtml(state.query)}" placeholder="搜索菜名、菜系或标签"></label><span class="result-count">${filtered.length} 道可选</span></div>
        <nav class="category-strip">${categories.map((value) => `<button class="category ${value === state.category ? "active" : ""}" data-action="category" data-value="${escapeHtml(value)}">${escapeHtml(value)}</button>`).join("")}</nav><div class="recipe-grid">${cards}</div>${pager}</section>
        <aside class="cart ${state.mobileView === "menu" ? "mobile-hidden" : ""}"><div class="cart-head"><span class="eyebrow">MY ORDER</span><div class="cart-title"><h2>本餐菜单</h2><strong>${state.selectedIds.length} 道</strong></div><p>调整份量和口味备注，数据会自动保存。</p></div><div class="cart-list">${cartItems}</div>
          <div class="cart-foot"><div class="cart-summary"><span>${state.guestCount} 位用餐</span><strong>${state.selectedIds.length} 道菜</strong></div><button class="settle" data-action="checkout" ${state.selectedIds.length ? "" : "disabled"}>查看采购清单与保存套餐</button><button class="clear" data-action="clear">清空本餐</button></div></aside></main>
      <nav class="mobile-nav"><button class="${state.mobileView === "menu" ? "active" : ""}" data-action="view" data-value="menu"><span>▤</span>菜单</button><button class="${state.mobileView === "cart" ? "active" : ""}" data-action="view" data-value="cart"><span>⌑</span>本餐${state.selectedIds.length ? `<b>${state.selectedIds.length}</b>` : ""}</button></nav><div class="toast"></div>`;

    root.querySelector(".search input")?.addEventListener("input", (event) => {
      state.query = event.target.value; state.page = 0; render();
      requestAnimationFrame(() => { const input = root.querySelector(".search input"); input?.focus(); input?.setSelectionRange(state.query.length, state.query.length); });
    });
    root.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", () => {
      const action = button.dataset.action; const id = button.dataset.id;
      if (action === "add") addRecipe(id);
      if (action === "remove") removeRecipe(id);
      if (action === "factor") updateFactor(id, Number(button.dataset.delta));
      if (action === "occasion") { state.occasion = button.dataset.value; commit(); }
      if (action === "category") { state.category = button.dataset.value; state.page = 0; render(); }
      if (action === "page") { state.page += Number(button.dataset.delta); render(); root.scrollIntoView({ behavior: "smooth", block: "start" }); }
      if (action === "view") { state.mobileView = button.dataset.value; render(); }
      if (action === "guest") { state.guestCount = Math.min(50, Math.max(1, state.guestCount + Number(button.dataset.delta))); state.childCount = Math.min(state.childCount, state.guestCount); commit(); }
      if (action === "child") { state.childCount = Math.min(state.guestCount, Math.max(0, state.childCount + Number(button.dataset.delta))); commit(); }
      if (action === "recommend") {
        state.selectedIds = recommendRecipes(recipes, state);
        state.selectedIds.forEach((recipeId) => { state.multipliers[recipeId] ??= 1; state.notes[recipeId] ??= ""; });
        commit(`已为 ${state.guestCount} 人推荐 ${state.selectedIds.length} 道菜`);
      }
      if (action === "clear" && (!state.selectedIds.length || confirm("清空本餐中的所有菜品、份量和备注？"))) {
        state.selectedIds = []; state.multipliers = {}; state.notes = {}; commit("本餐已清空");
      }
      if (action === "checkout") { setTriggerValue("checkout", Date.now()); showToast("采购清单和套餐保存区在点餐台下方"); }
    }));
    root.querySelectorAll("[data-note-id]").forEach((input) => {
      input.addEventListener("input", () => {
        state.notes[input.dataset.noteId] = input.value.slice(0, 200); scheduleCommit();
      });
      input.addEventListener("blur", () => showToast("备注已保存"));
    });
  }
  render();
  return () => { clearTimeout(root.__toastTimer); clearTimeout(root.__commitTimer); };
}
"""


_COMPONENT = None


def render_meal_order_component(
    st,
    *,
    data: dict[str, Any],
    default_order: dict[str, Any],
):
    global _COMPONENT
    if _COMPONENT is None:
        _COMPONENT = st.components.v2.component(
            "bili_recipe_meal_order",
            html=COMPONENT_HTML,
            css=COMPONENT_CSS,
            js=COMPONENT_JS,
        )
    return _COMPONENT(
        key="meal_order_component",
        data=data,
        default={"order": default_order},
        height="content",
        on_order_change=lambda: None,
        on_checkout_change=lambda: None,
    )
