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
  assert.match(html, /Chef Zhai 家庭厨房/);
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
  assert.match(source, /function optimisticallySetDishStage/);
  assert.match(source, /syncRequested\.current = true/);
  assert.match(source, /mealRef\.current/);
  assert.match(source, /updateViaCache: "none"/);
  assert.match(source, /function newUuid/);
  assert.doesNotMatch(source, /op_id: crypto\.randomUUID/);
  assert.match(source, /recipeOrderCategories/);
  assert.match(source, /categoryBar/);
  assert.match(source, /recipe\.published !== false/);
  assert.match(source, /recipe\.recommended/);
  assert.match(source, /主厨推荐/);
  assert.match(source, /Chef Zhai 推荐/);
  assert.match(source, /advance_meal_phase/);
  assert.match(source, /set_dish_stage_completed/);
  assert.match(source, /"ordering" \| "prep" \| "cooking" \| "serving"/);
  assert.match(source, /prep_items/);
  assert.match(source, /phase_incomplete/);
  assert.match(source, /scrollIntoView/);
  assert.match(source, /去完成/);
  assert.match(source, /正在打开共享本餐/);
  assert.match(source, /立即重试/);
  assert.match(source, /outbox_v3/);
  assert.match(source, /autoIncrement: true/);
  assert.match(source, /CLIENT_SYNC_BATCH \+ 1/);
  assert.match(source, /cursorRef\.current/);
  assert.match(source, /pendingActionKey/);
  assert.match(source, /同步中/);
  assert.match(source, /api\/v1\/meal-history/);
  assert.match(source, /历史本餐/);
  assert.match(source, /CHEF ZHAI/);
  assert.match(source, /主厨烹饪台/);
  assert.match(source, /访客只读/);
  assert.match(source, /chef_device_id/);
  assert.match(source, /deleteState\("device"\)/);
  assert.match(source, /Authorization: `Bearer/);
  assert.doesNotMatch(packageJson, /vinext|cloudflare|wrangler/);
  assert.equal(JSON.parse(manifest).display, "standalone");
  assert.equal(JSON.parse(manifest).name, "Chef Zhai 家庭厨房");
  await access(new URL("../dist/assets", import.meta.url));
});
