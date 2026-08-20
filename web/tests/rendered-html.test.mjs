import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

test("builds a standard Vite LAN client", async () => {
  const [html, source, packageJson, manifest] = await Promise.all([
    readFile(new URL("../dist/index.html", import.meta.url), "utf8"),
    readFile(new URL("../src/App.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../public/manifest.webmanifest", import.meta.url), "utf8"),
  ]);
  assert.match(html, /Bili 家庭点餐/);
  assert.match(source, /indexedDB\.open/);
  assert.match(source, /api\/v1\/events/);
  assert.match(source, /api\/v1\/join/);
  assert.match(source, /function DishThumbnail/);
  assert.match(source, /image_sha256/);
  assert.match(source, /IntersectionObserver/);
  assert.match(source, /serviceWorker\.register/);
  assert.match(source, /setInterval\([^]*3000/);
  assert.match(source, /while \(true\)[^]*payload\.has_more/);
  assert.match(source, /function mergeChanges/);
  assert.match(source, /function optimisticallyAddQuantity/);
  assert.match(source, /syncRequested\.current = true/);
  assert.match(source, /mealRef\.current/);
  assert.match(source, /updateViaCache: "none"/);
  assert.match(source, /function newUuid/);
  assert.doesNotMatch(source, /op_id: crypto\.randomUUID/);
  assert.match(source, /recipeOrderCategories/);
  assert.match(source, /categoryBar/);
  assert.match(source, /recipe\.published !== false/);
  assert.match(source, /Authorization: `Bearer/);
  assert.doesNotMatch(packageJson, /vinext|cloudflare|wrangler/);
  assert.equal(JSON.parse(manifest).display, "standalone");
  await access(new URL("../dist/assets", import.meta.url));
});
