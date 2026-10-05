import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

test("a returning visitor receives the cached shell without waiting for a slow server", async () => {
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
  assert.equal(await (await response).text(), "cached shell");
  assert.deepEqual(writes, []);
  finishNetwork(new Response("updated shell"));
  await Promise.all(waiting);
  assert.deepEqual(writes, [["/", "updated shell"]]);
  let intercepted = false;
  handlers.fetch({ request: { url: "https://kitchen.test/api/v1/sync", method: "GET" }, respondWith: () => { intercepted = true; } });
  assert.equal(intercepted, false, "menu visibility and ordering continue to use live API synchronization");
});
