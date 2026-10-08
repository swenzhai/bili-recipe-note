import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

test("navigation prefers the current access gate and never caches access or recipe APIs", async () => {
  const handlers = {};
  const writes = [];
  const waiting = [];
  const cached = new Response("cached shell");
  let finishNetwork;
  const network = new Promise(resolve => { finishNetwork = resolve; });
  const script = await readFile(new URL("../public/sw.js", import.meta.url), "utf8");
  vm.runInNewContext(script, {
    URL, Response,
    self: { location: { origin: "https://kitchen.test" }, addEventListener: (type, handler) => { handlers[type] = handler; } },
    caches: { match: async () => cached, open: async () => ({ put: async (key, value) => { writes.push([key, await value.text()]); } }) },
    fetch: () => network,
  });
  let response;
  handlers.fetch({ request: { url: "https://kitchen.test/", mode: "navigate", method: "GET" }, respondWith: value => { response = value; }, waitUntil: value => { waiting.push(value); } });
  assert.deepEqual(writes, []);
  finishNetwork(new Response("updated shell"));
  assert.equal(await (await response).text(), "updated shell");
  await Promise.all(waiting);
  assert.deepEqual(writes, [["/", "updated shell"]]);
  let intercepted = false;
  handlers.fetch({ request: { url: "https://kitchen.test/api/v1/sync", method: "GET" }, respondWith: () => { intercepted = true; } });
  assert.equal(intercepted, false, "menu visibility and ordering continue to use live API synchronization");
  handlers.fetch({ request: { url: "https://kitchen.test/api/access/status", method: "GET" }, respondWith: () => { intercepted = true; } });
  assert.equal(intercepted, false, "the access check can never use a service worker cache");
});

test("offline navigation may load only the shell, whose gate still requires a live access check", async () => {
  const handlers = {};
  const script = await readFile(new URL("../public/sw.js", import.meta.url), "utf8");
  vm.runInNewContext(script, {
    URL, Response,
    self: { location: { origin: "https://kitchen.test" }, addEventListener: (type, handler) => { handlers[type] = handler; } },
    caches: { match: async () => new Response("cached access-gated shell") },
    fetch: async () => { throw new TypeError("offline"); },
  });
  let response;
  handlers.fetch({ request: { url: "https://kitchen.test/", mode: "navigate", method: "GET" }, respondWith: value => { response = value; } });
  assert.equal(await (await response).text(), "cached access-gated shell");
});

test("the access upgrade replaces old ordering windows once without interrupting administrator forms", async () => {
  const handlers = {};
  const navigated = [];
  const deleted = [];
  let claimed = false;
  let keys = ["chef-zhai-family-kitchen-shell-v13", "chef-zhai-family-kitchen-shell-v14"];
  const script = await readFile(new URL("../public/sw.js", import.meta.url), "utf8");
  vm.runInNewContext(script, {
    URL, Response,
    self: {
      location: { origin: "https://kitchen.test" }, addEventListener: (type, handler) => { handlers[type] = handler; },
      clients: {
        claim: async () => { claimed = true; },
        matchAll: async () => ["https://kitchen.test/", "https://kitchen.test/admin/recipes/example/edit", "https://other.test/"].map(url => ({ url, navigate: async value => { navigated.push(value); } })),
      },
    },
    caches: { keys: async () => keys, delete: async key => { deleted.push(key); } },
  });
  let activation;
  handlers.activate({ waitUntil: result => { activation = result; } });
  await activation;
  assert.equal(claimed, true);
  assert.deepEqual(navigated, ["https://kitchen.test/"]);
  assert.deepEqual(deleted, ["chef-zhai-family-kitchen-shell-v13"]);
  keys = ["chef-zhai-family-kitchen-shell-v14"];
  handlers.activate({ waitUntil: result => { activation = result; } });
  await activation;
  assert.deepEqual(navigated, ["https://kitchen.test/"], "normal activation does not create a reload loop");
});
