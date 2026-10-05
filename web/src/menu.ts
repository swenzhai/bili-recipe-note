import type { Recipe } from "./sync";

export const ORDER_CATEGORIES = ["全部", "主厨推荐", "主菜", "肉类", "海鲜", "主食", "面条", "糕点", "汤羹", "小吃", "饮品", "素菜", "其他"] as const;
export type OrderCategory = typeof ORDER_CATEGORIES[number];

export function recipeOrderCategories(recipe: Recipe): OrderCategory[] {
  const category = String(recipe.category || "");
  const title = String(recipe.title || "");
  const text = [title, category, ...(recipe.tags || []), ...(recipe.ingredients || []).map(item => item.name || "")].join(" ");
  const result = new Set<OrderCategory>();
  if (recipe.recommended) result.add("主厨推荐");
  const seafood = /(鱼|虾|蟹|贝|蚝|螺|鱿|鳝|海鲜|海胆|鲍|瑶柱|带子|龙虾|多宝鱼|生蚝)/;
  const meat = /(猪|牛|羊|鸡|鸭|鹅|鸽|排骨|肉|叉烧|肥肠|生肠|牛杂|牛腩|牛展|乳鸽|猪手)/;
  const noodle = /(炒面|汤面|拌面|伊面|面条|河粉|米粉|粉面|云吞|粉皮|牛河)/;
  const staple = /(炒饭|煲仔饭|牛肉饭|米饭|砂锅粥|白粥|云吞|炒面|汤面|拌面|伊面|面条|河粉|米粉|牛河)/;
  const pastry = /(蛋挞|拿破仑酥|糖沙翁|玉米糕|沙琪玛|糕点|点心|酥皮)/;
  const vegetable = /(菜心|通菜|油麦菜|土豆丝|豆芽|韭菜|四季豆|菜花|凉瓜|节瓜|豆腐|茄子|青菜|蔬菜)/;
  if (category === "中餐") result.add("主菜");
  if (meat.test(text)) result.add("肉类");
  if (seafood.test(text)) result.add("海鲜");
  if (category === "主食" || staple.test(title)) result.add("主食");
  if (noodle.test(text) && !/面粉/.test(title)) result.add("面条");
  if (category === "糕点" || pastry.test(text)) result.add("糕点");
  if (category === "汤羹" || /(汤|羹|炖汤|糖水)/.test(title)) result.add("汤羹");
  if (category === "小吃") result.add("小吃");
  if (category === "饮品") result.add("饮品");
  if (category === "中餐" && vegetable.test(text) && !meat.test(text) && !seafood.test(text)) result.add("素菜");
  if (![...result].some(item => item !== "主厨推荐") || category === "其他") result.add("其他");
  return ORDER_CATEGORIES.filter(item => item !== "全部" && result.has(item));
}

export function recipeSearchText(recipe: Recipe): string {
  return [recipe.title, recipe.category, recipe.cuisine, recipe.creator_name, recipe.uploader,
    ...(recipe.tags || []), ...(recipe.ingredients || []).map(item => item.name),
    ...(recipe.seasonings || []).map(item => item.name)].filter(Boolean).join(" ").toLocaleLowerCase();
}

export const MENU_PAGE_SIZE = 40;
