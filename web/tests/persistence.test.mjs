import assert from "node:assert/strict";
import test from "node:test";
import { createSnapshotWriter } from "../src/persistence.ts";

test("cached recipe data and its cursor commit together and unrelated updates skip recipes", async () => {
  const writes = [];
  const save = createSnapshotWriter(async entries => { writes.push(Object.fromEntries(entries)); });
  const recipes = [{ id: "egg" }];
  await save({ recipes, meal: null, cursor: 1 });
  assert.deepEqual(writes[0], { recipes, meal: null, cursor: 1 });
  await save({ recipes, meal: { id: "dinner" }, cursor: 2 });
  assert.deepEqual(writes[1], { meal: { id: "dinner" }, cursor: 2 });
});

test("failed transactions cannot advance the stored cursor past missing recipe data", async () => {
  const stored = {};
  let fail = true;
  const save = createSnapshotWriter(async entries => {
    if (fail) throw new Error("IndexedDB quota exceeded");
    Object.assign(stored, Object.fromEntries(entries));
  });
  const recipes = [{ id: "egg" }];
  await assert.rejects(save({ recipes, cursor: 1 }), /quota exceeded/);
  assert.deepEqual(stored, {});
  fail = false;
  await save({ recipes, cursor: 2 });
  assert.deepEqual(stored, { recipes, cursor: 2 }, "the unchanged reference is retried after its failed write");
});

test("a final cursor waits behind the first-page cache write and recovers after failure", async () => {
  let failFirst;
  const gate = new Promise((_resolve, reject) => { failFirst = reject; });
  let attempts = 0;
  const committed = [];
  const save = createSnapshotWriter(async entries => {
    if (++attempts === 1) await gate;
    committed.push(Object.fromEntries(entries));
  });
  const recipes = [{ id: "egg" }];
  const first = save({ recipes, cursor: 0 });
  const rejected = assert.rejects(first, /aborted/);
  const second = save({ recipes, cursor: 2 });
  await Promise.resolve();
  assert.equal(attempts, 1);
  failFirst(new Error("transaction aborted"));
  await rejected;
  await second;
  assert.deepEqual(committed, [{ recipes, cursor: 2 }]);
});
