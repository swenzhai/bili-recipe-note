import assert from "node:assert/strict";
import test from "node:test";
import {
  CLIENT_SYNC_BATCH,
  menuRecipes,
  mergeChanges,
  optimisticallyAddQuantity,
  optimisticallySetDishStage,
  synchronizeBatch,
} from "../src/sync.ts";

const device = { id: "phone", name: "手机", token: "test" };
const meal = () => ({ order: { id: "dinner", epoch: 1, phase: "ordering", status: "active" }, selections: [], dish_states: [] });
const operation = (id = "add-1", extra = {}) => ({
  op_id: id, entity_type: "meal_selection", action: "add_quantity",
  order_id: "dinner", epoch: 1, phase: "ordering", recipe_id: "egg", quantity: 1, ...extra,
});
const payload = (extra = {}) => ({ operation_results: [], changes: [], next_cursor: 1, has_more: false, ...extra });

function client(initial = []) {
  const outbox = initial.map((item, index) => ({ ...item, sequence: index + 1, queued_at: 1000 + index }));
  const notices = [];
  const pending = [];
  const effects = {
    queue: async item => { outbox.push(item); },
    dequeue: async id => { const index = outbox.findIndex(item => item.op_id === id); if (index >= 0) outbox.splice(index, 1); },
    markPending: (item, value) => { pending.push([item.op_id, value]); },
    flash: message => { notices.push(message); },
    prompt: () => "server",
    newUuid: () => "conflict-resolution",
  };
  return {
    outbox, notices, pending,
    run: (requestPage, cursor = 0) => synchronizeBatch({
      cursor, queued: async limit => outbox.slice(0, limit), effects, requestPage,
    }),
  };
}

test("server hiding and restoring a recipe updates the actual menu selector", async () => {
  const app = client();
  let recipes = [{ id: "egg", title: "炒蛋", published: true }, { id: "rice", title: "米饭", published: true }];
  const hidden = await app.run(async () => payload({
    changes: [{ entity_type: "recipe", action: "delete", payload: { id: "egg" } }],
  }));
  recipes = mergeChanges(recipes, hidden.changes, "recipe", hidden.bootstrap);
  assert.deepEqual(menuRecipes(recipes).map(item => item.id), ["rice"]);

  const restored = await app.run(async cursor => {
    assert.equal(cursor, hidden.nextCursor);
    return payload({ next_cursor: 2, changes: [
      { entity_type: "recipe", action: "upsert", payload: { id: "egg", title: "炒蛋", published: true, recommended: true } },
      { entity_type: "recipe", action: "upsert", payload: { id: "unpublished", title: "未上架", published: false } },
    ] });
  }, hidden.nextCursor);
  recipes = mergeChanges(recipes, restored.changes, "recipe", restored.bootstrap);
  assert.deepEqual(menuRecipes(recipes).map(item => item.id), ["egg", "rice"]);
  assert.equal(recipes.length, 3, "unpublished recipes may remain cached without entering the menu");
});

test("offline meal additions remain queued and reconcile with the server after reconnect", async () => {
  const add = operation();
  const app = client([add]);
  const optimistic = optimisticallyAddQuantity(meal(), add, device);
  assert.equal(optimistic.selections[0].quantity, 1);
  await assert.rejects(app.run(async () => { throw new TypeError("network offline"); }), /offline/);
  assert.equal(app.outbox.length, 1);
  assert.deepEqual(app.pending, []);

  const result = await app.run(async (cursor, operations) => {
    assert.equal(cursor, 0);
    assert.deepEqual(operations, [add], "local queue metadata never crosses the transport boundary");
    return payload({ operation_results: [{ op_id: add.op_id, status: "accepted" }], meal: optimistic });
  });
  assert.deepEqual(app.outbox, []);
  assert.deepEqual(app.pending, [[add.op_id, false]]);
  assert.equal(result.mealSnapshot.selections[0].quantity, 1, "server state replaces optimistic state without double counting");
});

test("a lost response retries the original operation ID and does not double add a dish", async () => {
  const app = client([operation()]);
  const applied = new Set();
  let serverQuantity = 0;
  let loseResponse = true;
  const request = async (_cursor, operations) => {
    for (const item of operations) {
      if (!applied.has(item.op_id)) { serverQuantity += item.quantity; applied.add(item.op_id); }
    }
    if (loseResponse) { loseResponse = false; throw new TypeError("response lost"); }
    return payload({ operation_results: operations.map(item => ({ op_id: item.op_id, status: "accepted" })) });
  };
  await assert.rejects(app.run(request), /response lost/);
  assert.equal(app.outbox[0].op_id, "add-1");
  await app.run(request);
  assert.equal(serverQuantity, 1);
  assert.deepEqual(app.outbox, []);
});

test("obsolete meal and unavailable recipe operations are removed with a visible explanation", async () => {
  const app = client([operation("old"), operation("hidden")]);
  const currentMeal = { ...meal(), order: { ...meal().order, id: "new-dinner", epoch: 2 } };
  const result = await app.run(async () => payload({
    operation_results: [
      { op_id: "old", status: "rejected", reason: "stale_order" },
      { op_id: "hidden", status: "rejected", reason: "recipe_unavailable" },
    ], meal: currentMeal,
  }));
  assert.deepEqual(app.outbox, []);
  assert.equal(app.notices.length, 2);
  assert.deepEqual(result.mealSnapshot, currentMeal);
});

test("paginated bootstrap submits operations once and removes obsolete cached recipes", async () => {
  const app = client([operation()]);
  let requests = 0;
  const result = await app.run(async (cursor, operations) => {
    requests += 1;
    if (requests === 1) {
      assert.equal(cursor, 0);
      assert.equal(operations.length, 1);
      return payload({ bootstrap: true, has_more: true, next_cursor: 7, operation_results: [{ op_id: "add-1", status: "accepted" }], changes: [
        { entity_type: "recipe", action: "upsert", payload: { id: "egg", title: "炒蛋" } },
      ] });
    }
    assert.equal(cursor, 7);
    assert.deepEqual(operations, []);
    return payload({ next_cursor: 9, changes: [
      { entity_type: "recipe", action: "upsert", payload: { id: "rice", title: "米饭" } },
    ] });
  });
  assert.equal(requests, 2);
  assert.equal(result.nextCursor, 9);
  const recipes = mergeChanges([{ id: "removed", title: "旧缓存" }], result.changes, "recipe", result.bootstrap);
  assert.deepEqual(recipes.map(item => item.id), ["egg", "rice"]);
});

test("a large offline queue drains in bounded batches without dropping later operations", async () => {
  const app = client(Array.from({ length: CLIENT_SYNC_BATCH + 1 }, (_, index) => operation(`add-${index}`)));
  const request = async (_cursor, operations) => payload({
    operation_results: operations.map(item => ({ op_id: item.op_id, status: "accepted" })),
  });
  const first = await app.run(request);
  assert.equal(first.resync, true);
  assert.equal(app.outbox.length, 1);
  const second = await app.run(request, first.nextCursor);
  assert.equal(second.resync, false);
  assert.deepEqual(app.outbox, []);
});

test("optimistic stage changes do not apply to a different meal epoch or phase", () => {
  const cooking = { ...meal(), order: { ...meal().order, phase: "cooking" } };
  const done = operation("done", { action: "set_dish_stage_completed", stage: "cooking", phase: "cooking", completed: true });
  assert.equal(optimisticallySetDishStage(cooking, { ...done, epoch: 0 }), cooking);
  assert.equal(optimisticallySetDishStage(cooking, { ...done, phase: "prep" }), cooking);
  assert.equal(optimisticallySetDishStage(cooking, done).dish_states[0].cook_completed, true);
});

test("resolving a practice conflict queues a new operation and continues syncing", async () => {
  const app = client([{ op_id: "practice", entity_type: "practice_log", action: "upsert", payload: { notes: "自己的心得" } }]);
  const result = await app.run(async () => payload({ operation_results: [{
    op_id: "practice", status: "conflict", conflict_id: "conflict-1",
    server: { id: "log-1", recipe_id: "egg", cooked_on: "2026-09-26", notes: "服务器心得", version: 2 },
  }] }));
  assert.equal(result.resync, true);
  assert.equal(app.outbox.length, 1);
  assert.equal(app.outbox[0].action, "resolve_conflict");
  assert.equal(app.outbox[0].payload.resolution, "server");
  assert.equal(app.outbox[0].op_id, "conflict-resolution");
});
