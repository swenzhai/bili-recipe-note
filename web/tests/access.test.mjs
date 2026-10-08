import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { accessView, accessRequest, readAccessStatus, AccessError } from "../src/access.ts";
import { api } from "../src/admin/api.ts";

const baseline = {
  admin_configured: true, admin_authenticated: false, ordering_enabled: true,
  ordering_password_required: true, ordering_password_configured: true, ordering_authenticated: false,
};

const modules = new Map();
async function compiledModule(url) {
  if (modules.has(url.href)) return modules.get(url.href);
  const source = await readFile(url, "utf8");
  let compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  compiled = compiled.replace(/^import\s+["'][^"']+\.css["'];?\s*$/gm, "");
  for (const [, path] of [...compiled.matchAll(/from\s+["']([^"']+)["']/g)]) {
    const resolved = path.startsWith(".") ? await compiledModule(new URL(`${path}${path.endsWith("access") ? ".ts" : ".tsx"}`, url)) : import.meta.resolve(path);
    compiled = compiled.replaceAll(`"${path}"`, JSON.stringify(resolved)).replaceAll(`'${path}'`, JSON.stringify(resolved));
  }
  const result = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
  modules.set(url.href, result);
  return result;
}
const { default: AccessGate, AccessScreen } = await import(await compiledModule(new URL("../src/AccessGate.tsx", import.meta.url)));
const { orderingSettingsPayload } = await import(await compiledModule(new URL("../src/admin/AccessSettings.tsx", import.meta.url)));

test("the administrator gate requires setup or an authenticated session", () => {
  assert.equal(accessView("admin", null), "checking");
  assert.equal(accessView("admin", null, true), "unavailable");
  assert.equal(accessView("admin", { ...baseline, admin_configured: false }), "setup");
  assert.equal(accessView("admin", baseline), "admin-login");
  assert.equal(accessView("admin", { ...baseline, ordering_authenticated: true }), "admin-login", "a guest password never grants administration");
  assert.equal(accessView("admin", { ...baseline, admin_authenticated: true }), "allowed");
});

test("ordering is closed until its configured password is authenticated", () => {
  assert.equal(accessView("ordering", baseline), "ordering-login");
  assert.equal(accessView("ordering", { ...baseline, ordering_password_configured: false }), "not-ready");
  assert.equal(accessView("ordering", { ...baseline, admin_configured: false, ordering_password_required: false }), "not-ready");
  assert.equal(accessView("ordering", { ...baseline, ordering_authenticated: true }), "allowed");
  assert.equal(accessView("ordering", { ...baseline, ordering_password_required: false, ordering_password_configured: false }), "allowed");
  assert.equal(accessView("ordering", { ...baseline, admin_authenticated: true, ordering_password_configured: false }), "allowed");
  for (const admin_authenticated of [true, false]) {
    for (const ordering_password_required of [true, false]) {
      assert.equal(accessView("ordering", { ...baseline, ordering_enabled: false, admin_authenticated, ordering_password_required, ordering_authenticated: true }), "closed");
    }
  }
});

test("cached menu and administrator components never mount before access is checked", () => {
  let mounted = false;
  function PrivateContent() { mounted = true; return React.createElement("div", {}, "private cached recipes"); }
  for (const mode of ["admin", "ordering"]) {
    const html = renderToStaticMarkup(React.createElement(AccessGate, { mode }, React.createElement(PrivateContent)));
    assert.equal(mounted, false);
    assert.doesNotMatch(html, /private cached recipes/);
    assert.match(html, /正在确认访问权限/);
  }
});

test("setup requires the local one-time token and confirmation without printing credentials", () => {
  const setup = renderToStaticMarkup(React.createElement(AccessScreen, { view: "setup", error: "", retry() {}, async onLogin() {} }));
  assert.match(setup, /初始化口令/);
  assert.match(setup, /state\/admin-setup-token/);
  assert.match(setup, /再次输入密码/);
  assert.equal((setup.match(/type="password"/g) || []).length, 3);
  assert.match(setup, /minLength="8"/);
  const guest = renderToStaticMarkup(React.createElement(AccessScreen, { view: "ordering-login", error: "", retry() {}, async onLogin() {} }));
  assert.match(guest, /点餐密码/);
  assert.doesNotMatch(guest, /setup-token|再次输入密码/);
});

test("settings retain an existing password when blank and prevent opening an unconfigured protected menu", () => {
  assert.deepEqual(orderingSettingsPayload(true, true, "", "", true), { ordering_enabled: true, ordering_password_required: true });
  assert.deepEqual(orderingSettingsPayload(false, true, "", "", false), { ordering_enabled: false, ordering_password_required: true });
  assert.deepEqual(orderingSettingsPayload(true, false, "", "", false), { ordering_enabled: true, ordering_password_required: false });
  assert.throws(() => orderingSettingsPayload(true, true, "", "", false), /先设置点餐密码/);
  assert.throws(() => orderingSettingsPayload(true, true, "short", "short", false), /8–128/);
  assert.throws(() => orderingSettingsPayload(true, true, "example-password", "wrong-password", false), /不一致/);
  assert.deepEqual(orderingSettingsPayload(true, true, "example-password", "example-password", false), { ordering_enabled: true, ordering_password_required: true, ordering_password: "example-password" });
});

test("password requests use same-origin cookies, no cache, and a request protection header", async t => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => { calls.push({ url, options }); return Response.json(baseline); });
  assert.deepEqual(await readAccessStatus(), baseline);
  await accessRequest("/admin/login", { method: "POST", body: JSON.stringify({ password: "example-password" }), credentials: "omit", cache: "force-cache" });
  for (const { options } of calls) {
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
    assert.equal(options.headers.get("X-Access-Request"), "1");
  }
  assert.equal(calls[1].url, "/api/access/admin/login");
  assert.equal(calls[1].options.headers.get("Content-Type"), "application/json");
  assert.deepEqual(JSON.parse(calls[1].options.body), { password: "example-password" });
});

test("invalid or unavailable status fails closed and cancellation remains recognizable", async t => {
  const mocked = t.mock.method(globalThis, "fetch", async () => Response.json({ ...baseline, ordering_password_required: undefined }));
  await assert.rejects(readAccessStatus(), /访问状态无效/);
  mocked.mock.mockImplementation(async () => { throw new TypeError("offline"); });
  await assert.rejects(readAccessStatus(), error => error instanceof AccessError && error.status === 0);
  const abort = new DOMException("closed", "AbortError");
  mocked.mock.mockImplementation(async () => { throw abort; });
  await assert.rejects(readAccessStatus(), error => error === abort);
});

test("expired administrator requests trigger gate removal but bad login remains on the form", async t => {
  const events = [];
  globalThis.window = new EventTarget();
  window.addEventListener("access:admin-denied", () => events.push("denied"));
  t.after(() => { delete globalThis.window; });
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "请重新登录" }, { status: 401 }));
  await assert.rejects(api("/overview"), /请重新登录/);
  await assert.rejects(accessRequest("/settings"), /请重新登录/);
  assert.deepEqual(events, ["denied", "denied"]);
  await assert.rejects(accessRequest("/admin/login", { method: "POST", body: JSON.stringify({ password: "bad-password" }) }), /请重新登录/);
  assert.deepEqual(events, ["denied", "denied"]);
});
