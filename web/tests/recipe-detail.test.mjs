import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { recipeOrderCategories, recipeSearchText } from "../src/menu.ts";

const source = await readFile(new URL("../src/RecipeContent.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText.replace('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime")));
const { RecipeContent, safeSourceUrl } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const render = recipe => renderToStaticMarkup(React.createElement(RecipeContent, { recipe, renderImage: (digest, alt) => React.createElement("img", { src: `/test/${digest}`, alt }) }));

test("the dish detail renders cached ingredients, seasoning and actual cooking instructions", () => {
  const html = render({
    id: "egg", title: "炒鸡蛋", creator_name: "家常厨房", source_url: "https://www.bilibili.com/video/BVdemo",
    ingredients: [{ name: "鸡蛋", amount: "2个" }, { name: "葱花" }], seasonings: [{ name: "盐" }],
    prep_items: ["打散鸡蛋"], tools: ["炒锅"], summary_tips: ["出锅前再撒葱花"],
    steps: [{ title: "炒制", action: "将蛋液倒入锅中翻炒", heat: "中火", duration: "30秒", image_sha256: "image" }],
  });
  for (const text of ["菜品简介", "主要用料：鸡蛋、葱花", "主要食材", "调料", "盐", "打散鸡蛋", "炒锅", "详细做法", "将蛋液倒入锅中翻炒", "中火 · 30秒", "出锅前再撒葱花", "查看原视频"]) assert.ok(html.includes(text), text);
  assert.match(html, /src="\/test\/image"/);
  assert.doesNotMatch(html, /适量|AI 根据|外部参考/);
});

test("AI dish information augments original steps and distinguishes external references", () => {
  const html = render({
    id: "egg", title: "炒蛋", steps: [{ action: "按原菜谱翻炒" }],
    dish_info: { summary: "鸡蛋与葱花一起炒制。", method: "打蛋后炒制。", origin: "参考资料提到类似家常做法。", sources: [{ title: "外部参考标题", url: "https://example.com/dish", excerpt: "候选来源描述" }] },
  });
  for (const text of ["鸡蛋与葱花一起炒制", "打蛋后炒制", "按原菜谱翻炒", "AI 根据本菜谱整理", "尚未核实与这份菜谱的对应关系", "外部参考标题"]) assert.ok(html.includes(text), text);
  assert.match(html, /href="https:\/\/example.com\/dish"/);
});

test("missing facts stay absent and source text cannot introduce executable links or HTML", () => {
  const html = render({ id: "empty", title: "待补充", source_url: "javascript:alert(1)", dish_info: { summary: "<script>bad</script>", sources: [{ title: "链接", url: "data:text/html,bad" }] } });
  assert.match(html, /暂时没有用料清单/);
  assert.match(html, /暂时没有详细步骤/);
  assert.match(html, /&lt;script&gt;bad&lt;\/script&gt;/);
  assert.doesNotMatch(html, /href=|<script>|适量|中火/);
  assert.equal(safeSourceUrl("//example.com"), undefined);
});

test("external reference confidence and unavailable search results remain explicit", () => {
  const html = render({ id: "dish", title: "菜", dish_info: { search_status: "failed", warnings: ["同名做法可能不同"], sources: [
    { title: "同名菜", match_status: "unverified" }, { title: "相近做法", match_status: "possible_match" },
  ] } });
  for (const text of ["可能不是同一道菜，未采用其历史内容", "可能相关，尚待核实", "未能查询外部资料", "同名做法可能不同"]) assert.ok(html.includes(text), text);
  assert.match(render({ id: "dish", title: "菜", dish_info: { search_status: "no_results" } }), /暂时没有找到可供参考的外部资料/);
});

test("menu indexing searches dish facts without scanning the entire recipe document", () => {
  const recipe = { id: "noodles", title: "虾仁炒面", category: "主食", ingredients: [{ name: "虾仁" }], seasonings: [{ name: "黑胡椒" }], creator_name: "厨房老师", steps: [{ action: "与检索无关的很长文本" }] };
  assert.ok(recipeSearchText(recipe).includes("黑胡椒"));
  assert.ok(recipeSearchText(recipe).includes("厨房老师"));
  assert.ok(!recipeSearchText(recipe).includes("与检索无关"));
  assert.ok(recipeOrderCategories(recipe).includes("面条"));
  assert.ok(recipeOrderCategories(recipe).includes("海鲜"));
});
