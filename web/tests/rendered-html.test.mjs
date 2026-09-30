import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

test("builds a standard Vite LAN client", async () => {
  const [html, packageJson, manifest] = await Promise.all([
    readFile(new URL("../dist/index.html", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../public/manifest.webmanifest", import.meta.url), "utf8"),
  ]);
  assert.match(html, /Chef Zhai 家庭厨房/);
  assert.doesNotMatch(packageJson, /vinext|cloudflare|wrangler/);
  assert.equal(JSON.parse(manifest).display, "standalone");
  assert.equal(JSON.parse(manifest).name, "Chef Zhai 家庭厨房");
  await access(new URL("../dist/assets", import.meta.url));
});
