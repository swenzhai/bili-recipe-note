import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { api, post, ApiError, safeUrl, taskPath } from "../src/admin/api.ts";

const modules = new Map();
async function compiledModule(name) {
  if (modules.has(name)) return modules.get(name);
  const source = await readFile(new URL(`../src/admin/${name}`, import.meta.url), "utf8");
  let compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  compiled = compiled.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, '');
  const dependencies = [...compiled.matchAll(/from\s+["']([^"']+)["']/g)];
  for (const [, path] of dependencies) {
    const resolved = path.startsWith(".") ? await compiledModule(`${path.slice(2)}${path === "./api" ? ".ts" : ".tsx"}`) : import.meta.resolve(path);
    compiled = compiled.replaceAll(`"${path}"`, JSON.stringify(resolved)).replaceAll(`'${path}'`, JSON.stringify(resolved));
  }
  const url = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
  modules.set(name, url);
  return url;
}

const { RecipeContents, scaledAmount } = await import(await compiledModule("RecipePage.tsx"));
const { libraryQuery } = await import(await compiledModule("LibraryPage.tsx"));
const { RecipeCard } = await import(await compiledModule("HomePage.tsx"));
const { default: TasksPage, GenerationDishes, queueOptions, controlOptions, matchesTaskFilter, taskExecutionLabels } = await import(await compiledModule("TasksPage.tsx"));
const { aiFrameCandidates } = await import(await compiledModule("ReviewPage.tsx"));

test("a video shows completed and failed dishes independently", () => {
  const html = renderToStaticMarkup(React.createElement(GenerationDishes, { item: {
    completed_dish_count: 1, dishes: [
      { dish_id: "first", title: "炒蛋", status: "done" },
      { dish_id: "second", title: "炒青菜", status: "failed", error: "模型超时" },
    ],
  } }));
  assert.match(html, /本视频 2 道菜，已完成 1 道/);
  assert.match(html, /炒蛋 · 已完成/);
  assert.match(html, /炒青菜 · 失败/);
  assert.match(html, /模型超时/);
  assert.equal(renderToStaticMarkup(React.createElement(GenerationDishes, { item: {} })), "");
});

test("uncertain original frames remain labeled for manual approval", () => {
  const source = { status: "needs_review", url: "/api/admin/v1/media/ai", timestamp: 187.5, candidate_revision: "review-frame" };
  assert.deepEqual(aiFrameCandidates(source), [{ id: "ai", url: source.url, label: "AI 候选 · 待确认", timestamp: 187.5, candidate_revision: "review-frame" }]);
  assert.equal(aiFrameCandidates({ ...source, status: "ok" })[0].label, "AI 成品特写");
  for (const status of ["not_found", "stale", "fallback"]) assert.deepEqual(aiFrameCandidates({ ...source, status }), []);
  assert.deepEqual(aiFrameCandidates({ ...source, url: null }), []);
  assert.equal(source.status, "needs_review");
});

test("no-match retry carries the quota only for frame selection", () => {
  assert.deepEqual(controlOptions("frames", "retry-not-found", 3, false), { use_current_config: false, run_limit: 3 });
  for (const kind of ["review", "dish-info", "generation"]) {
    assert.deepEqual(controlOptions(kind, "retry-not-found", 3, false), { use_current_config: false });
  }
  assert.equal(matchesTaskFilter({ status: "completed", failed: 0, not_found: 1 }, "attention"), true);
  assert.equal(matchesTaskFilter({ status: "completed", failed: 0, needs_review: 1 }, "attention"), true);
});

test("AI task creation separates the full queue from the first execution batch", () => {
  for (const kind of ["frames", "review", "dish-info"]) {
    assert.deepEqual(queueOptions(kind, 0, 10, false), { limit: 0, run_limit: 10, create_only: false });
    assert.deepEqual(queueOptions(kind, 45, 6, true), { limit: 45, run_limit: 6, create_only: true });
    assert.deepEqual(queueOptions(kind, 0, 0, false), { limit: 0, run_limit: 0, create_only: false });
  }
  assert.deepEqual(queueOptions("generation", 10, 5, false), { limit: 10 });
});

test("only AI resume and retry requests include the chosen next batch size", () => {
  for (const kind of ["frames", "review", "dish-info"]) {
    assert.deepEqual(controlOptions(kind, "resume", 3, false), { use_current_config: false, run_limit: 3 });
    assert.deepEqual(controlOptions(kind, "retry", 0, true), { use_current_config: true, run_limit: 0 });
    assert.deepEqual(controlOptions(kind, "pause", 10, false), { use_current_config: false });
  }
  for (const kind of ["generation", "creators", "settings"]) {
    assert.deepEqual(controlOptions(kind, "resume", 5, false), { use_current_config: false });
  }
});

test("pending task filter retains later batches after an automatic pause", () => {
  const task = { kind: "frames", status: "paused", pause_reason: "batch_limit", pending: 32, failed: 0 };
  assert.equal(matchesTaskFilter(task, "pending"), true);
  assert.equal(matchesTaskFilter(task, "active"), false);
  assert.equal(matchesTaskFilter({ ...task, status: "running" }, "pending"), true);
  assert.equal(matchesTaskFilter({ ...task, status: "pending", pending: 0 }, "pending"), true);
  assert.equal(matchesTaskFilter({ ...task, status: "completed", pending: 0 }, "pending"), false);
});

test("AI batch estimates and retry labels describe pending work accurately", () => {
  for (const kind of ["frames", "review", "dish-info"]) {
    const task = { kind, run_limit: 10, pending: 24, failed: 2 };
    const labels = taskExecutionLabels(task);
    assert.equal(labels.eta, "预计本批剩余");
    assert.equal(labels.retry, "加入失败项并执行下一批（2）");
    assert.match(labels.retryHint, /与原待办共同计入本批执行数量/);
    assert.equal(taskExecutionLabels({ ...task, pending: 0 }).retry, "重试失败项（2）");
    assert.equal(taskExecutionLabels({ ...task, pending: 0 }).retryHint, "");
    assert.equal(taskExecutionLabels({ ...task, run_limit: null }).eta, "预计剩余");
  }
  assert.deepEqual(taskExecutionLabels({ kind: "generation", pending: 24, failed: 2, run_limit: 10 }), {
    eta: "预计剩余", retry: "重试失败项（2）", retryHint: "",
  });
});

test("task form defaults to queueing all recipes and offers separate queue and run actions", t => {
  globalThis.window = { location: { search: "?create=frames" } };
  t.after(() => { delete globalThis.window; });
  const html = renderToStaticMarkup(React.createElement(TasksPage, { navigate() {} }));
  assert.match(html, /加入列表数量（0 表示全部）<input[^>]*value="0"/);
  assert.match(html, /本批执行数量（0 表示全部）<input[^>]*value="10"/);
  assert.match(html, /仅加入待执行列表/);
  assert.match(html, /加入并执行首批/);
  assert.match(html, /达到本批数量后自动暂停/);
});

test("selected recipe scope keeps all selected recipes queued and bounds the first batch", t => {
  globalThis.window = { location: { search: "?create=review&recipe_ids=a,b,c" } };
  t.after(() => { delete globalThis.window; });
  const html = renderToStaticMarkup(React.createElement(TasksPage, { navigate() {} }));
  assert.match(html, /已选择 3 道菜谱/);
  assert.match(html, /加入列表数量（0 表示全部）<input[^>]*value="0"/);
  assert.match(html, /本批执行数量（0 表示全部）<input[^>]*value="3"/);
});

test("admin API sends request protection and uses uncached same-origin requests", async t => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => { calls.push({ url, options }); return Response.json({ ok: true }); });
  assert.deepEqual(await api("/overview"), { ok: true });
  await post("/recipes/publication", { ids: ["dish"], published: true });
  for (const { url, options } of calls) {
    assert.ok(url.startsWith("/api/admin/v1/"));
    assert.equal(options.headers.get("X-Admin-Request"), "1");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
  }
  assert.equal(calls[1].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[1].options.body), { ids: ["dish"], published: true });
});

test("admin API preserves file upload boundaries and server explanations", async t => {
  const form = new FormData(); form.set("file", new Blob(["sample"]), "photo.png");
  t.mock.method(globalThis, "fetch", async (_url, options) => {
    assert.equal(options.headers.has("Content-Type"), false);
    assert.equal(options.body, form);
    return Response.json({ detail: "菜谱内容已变化，请重新加载。" }, { status: 409 });
  });
  await assert.rejects(api("/recipes/dish/cover", { method: "POST", body: form }), error => error instanceof ApiError && error.status === 409 && error.message.includes("已变化"));
});

test("admin API aborts remain aborts and network failures have a useful explanation", async t => {
  const abort = new DOMException("navigation", "AbortError");
  const mocked = t.mock.method(globalThis, "fetch", async () => { throw abort; });
  await assert.rejects(api("/recipes"), error => error === abort);
  mocked.mock.mockImplementation(async () => { throw new TypeError("Failed to fetch"); });
  await assert.rejects(api("/recipes"), /无法连接厨房服务/);
});

test("library requests preserve filtering while bounding pagination to the intended page size", () => {
  const query = libraryQuery("?q=鸡蛋&category=家常菜&visibility=all&published=false&page=2&page_size=99999&unrelated=1");
  assert.equal(query.get("q"), "鸡蛋");
  assert.equal(query.get("category"), "家常菜");
  assert.equal(query.get("visibility"), "all");
  assert.equal(query.get("published"), "false");
  assert.equal(query.get("page"), "2");
  assert.equal(query.get("page_size"), "24");
  assert.equal(query.has("unrelated"), false);
  assert.equal(libraryQuery("?page=-4").get("page"), "1");
  assert.equal(libraryQuery("?page=bad").get("page"), "1");
});

test("detail renders source facts, every ingredient and step without inventing amounts", () => {
  const html = renderToStaticMarkup(React.createElement(RecipeContents, { recipe: { title: "炒蛋", ingredients: [{ name: "鸡蛋", amount: "2个" }, { name: "葱花" }], seasonings: [{ name: "盐" }], tools: ["炒锅"], prep_items: ["把鸡蛋打散"], steps: [{ title: "翻炒", action: "倒入蛋液翻炒", heat: "中火", duration: "30秒", tips: "保持松软" }], summary_tips: ["按喜好调味"] }, images: [{ index: 0, url: "/api/admin/v1/media/step" }] }));
  for (const value of ["主要用料：鸡蛋、葱花", "2个", "未注明", "盐", "炒锅", "把鸡蛋打散", "倒入蛋液翻炒", "中火 · 30秒", "保持松软", "按喜好调味"]) assert.ok(html.includes(value), value);
  assert.match(html, /src="\/api\/admin\/v1\/media\/step"/);
  assert.doesNotMatch(html, /适量|JSON/);
});

test("AI background remains marked and source text cannot execute HTML or URLs", () => {
  const html = renderToStaticMarkup(React.createElement(RecipeContents, { recipe: { title: "<script>wrong</script>" }, info: { summary: "<script>bad</script>", origin: "同名菜历史", warnings: ["同名不代表相同做法"], sources: [{ title: "不安全来源", url: "javascript:alert(1)" }, { title: "外部参考", url: "https://example.com/dish", match_status: "possible_match" }] } }));
  for (const text of ["&lt;script&gt;bad&lt;/script&gt;", "尚未核实与这份菜谱的对应关系", "可能相关 · 尚待核实", "同名不代表相同做法", "暂时没有用料清单", "暂时没有详细步骤"]) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /<script>|javascript:/);
  assert.equal(safeUrl("data:text/html,bad"), undefined);
});

test("cooking portions scale only simple explicit quantities and preserve ambiguous amounts", () => {
  assert.equal(scaledAmount("250克", 2), "500克");
  assert.equal(scaledAmount("1.5 小勺", 0.5), "0.75 小勺");
  assert.equal(scaledAmount("2-3个", 2), "2-3个");
  assert.equal(scaledAmount("1/2杯", 2), "1/2杯");
  assert.equal(scaledAmount("少许", 3), "少许");
  assert.equal(scaledAmount(undefined, 2), "未注明");
});

test("recipe cards expose independent menu and visibility states with direct detail links", () => {
  const html = renderToStaticMarkup(React.createElement(RecipeCard, { recipe: { id: "sample", title: "清蒸鱼", category: "海鲜", creator_name: "家常厨房", hidden: true, published: true }, navigate() {}, selected: true, onSelect() {} }));
  assert.match(html, /已隐藏/);
  assert.match(html, /href="\/admin\/recipes\/sample"/);
  assert.match(html, /aria-label="选择清蒸鱼"/);
  assert.match(html, /checked/);
  assert.doesNotMatch(html, /菜单在售/);
  assert.equal(taskPath({ kind: "frames", id: "a/b" }), "/tasks?kind=frames&id=a%2Fb");
});
