/** Shared sync and menu behavior. Browser storage and transport are injected. */

export type Recipe = {
  id: string;
  title: string;
  category?: string;
  total_time?: string;
  tags?: string[];
  published?: boolean;
  recommended?: boolean;
  creator_name?: string;
  uploader?: string;
  source_url?: string;
  cuisine?: string;
  servings?: string;
  difficulty?: string;
  summary?: string;
  seasonings?: { name?: string; amount?: string }[];
  tools?: string[];
  summary_tips?: string[];
  dish_info?: {
    summary?: string; method?: string; background?: string | null; origin?: string | null;
    sources?: { title?: string; url?: string; excerpt?: string; match_status?: string }[];
    search_status?: string; match_status?: string; generated_at?: string; source_note?: string;
    warnings?: string[];
  };
  cover_image_sha256?: string;
  cover_image_status?: string;
  ingredients?: { name?: string; amount?: string }[];
  prep_items?: string[];
  steps?: { title?: string; action?: string; heat?: string; duration?: string; tips?: string; image_sha256?: string }[];
  assets?: { sha256: string; kind?: string; step_index?: number }[];
};
export type Selection = { order_id: string; device_id: string; device_name: string; recipe_id: string; quantity: number; note: string };
export type MealPhase = "ordering" | "prep" | "cooking" | "serving";
export type DishState = { recipe_id: string; prep_completed: boolean; cook_completed: boolean; served: boolean; completed: boolean };
export type Meal = { order: { id: string; status: string; epoch: number; phase: MealPhase; chef_device_id?: string | null; created_at?: string; completed_at?: string | null }; selections: Selection[]; dish_states: DishState[] };
export type Practice = { id: string; recipe_id: string; cooked_on: string; rating?: number; notes: string; photo_sha256?: string; version: number };
export type Plan = { id: string; name: string; items: { recipe_id: string; title: string; servings_multiplier: number; note: string }[]; version: number };
export type Pair = { base_url: string; pairing_token: string };
export type Device = { id: string; name: string; token: string };
export type Branding = { name: string; subtitle: string; has_logo: boolean; logo_url?: string | null };
export type Operation = { op_id: string; entity_type: string; entity_id?: string; action: string; base_version?: number; order_id?: string; epoch?: number; phase?: MealPhase; stage?: Exclude<MealPhase, "ordering">; recipe_id?: string; device_id?: string; quantity?: number; note?: string; completed?: boolean; plan_id?: string; payload?: Record<string, unknown> };
export type QueuedOperation = Operation & { sequence?: number; queued_at?: number };
export type Change = { entity_type: string; action: string; payload: Record<string, unknown> };
export type SyncPayload = {
  operation_results: { op_id: string; status: string; reason?: string; message?: string; loaded?: number; skipped?: number; conflict_id?: string; server?: Practice }[];
  changes: Change[];
  next_cursor: number;
  has_more: boolean;
  bootstrap?: boolean;
  meal?: Meal | null;
};

export const CLIENT_SYNC_BATCH = 100;
const recipeTitleCollator = new Intl.Collator("zh-CN");

export function wireOperation(operation: QueuedOperation): Operation {
  const { sequence: _sequence, queued_at: _queuedAt, ...payload } = operation;
  return payload;
}

export function pendingActionKey(operation: Partial<Operation>): string | undefined {
  if (operation.action === "set_dish_stage_completed") {
    return [operation.order_id, operation.epoch, operation.action, operation.stage, operation.recipe_id].join(":");
  }
  if (["advance_meal_phase", "complete_order", "clear_order", "load_plan"].includes(operation.action || "")) {
    return [operation.order_id, operation.epoch, operation.action].join(":");
  }
  return undefined;
}

export function mergeChanges<T extends { id: string }>(current: T[], changes: Change[], entityType: string, reset: boolean): T[] {
  if (!reset && !changes.some(change => change.entity_type === entityType)) return current;
  const merged = new Map((reset ? [] : current).map(item => [item.id, item]));
  for (const change of changes) {
    if (change.entity_type !== entityType) continue;
    const id = String(change.payload.id || "");
    if (!id) continue;
    if (change.action === "delete") merged.delete(id);
    else merged.set(id, change.payload as unknown as T);
  }
  return [...merged.values()];
}

export function optimisticallyAddQuantity(current: Meal | null, operation: Operation, device: Device): Meal | null {
  if (
    !current
    || operation.action !== "add_quantity"
    || operation.order_id !== current.order.id
    || operation.epoch !== current.order.epoch
    || !operation.recipe_id
  ) return current;
  const targetDevice = operation.device_id || device.id;
  const existing = current.selections.find(item => item.recipe_id === operation.recipe_id && item.device_id === targetDevice);
  const quantity = (existing?.quantity || 0) + (operation.quantity || 0);
  const selections = current.selections.filter(item => item.recipe_id !== operation.recipe_id || item.device_id !== targetDevice);
  if (quantity > 0) selections.push({
    order_id: current.order.id,
    device_id: targetDevice,
    device_name: existing?.device_name || (targetDevice === device.id ? device.name : targetDevice),
    recipe_id: operation.recipe_id,
    quantity,
    note: existing?.note || "",
  });
  const dishStates = current.dish_states.some(item => item.recipe_id === operation.recipe_id)
    ? current.dish_states
    : [...current.dish_states, { recipe_id: operation.recipe_id, prep_completed: false, cook_completed: false, served: false, completed: false }];
  return { ...current, selections, dish_states: dishStates };
}

export function optimisticallySetDishStage(current: Meal | null, operation: Operation): Meal | null {
  if (
    !current
    || operation.action !== "set_dish_stage_completed"
    || operation.order_id !== current.order.id
    || operation.epoch !== current.order.epoch
    || operation.phase !== current.order.phase
    || operation.stage !== current.order.phase
    || !operation.recipe_id
  ) return current;
  const state = current.dish_states.find(item => item.recipe_id === operation.recipe_id) || {
    recipe_id: operation.recipe_id, prep_completed: false, cook_completed: false, served: false, completed: false,
  };
  const completed = Boolean(operation.completed);
  const updated = operation.stage === "prep"
    ? { ...state, prep_completed: completed }
    : operation.stage === "cooking"
      ? { ...state, cook_completed: completed, completed }
      : { ...state, served: completed };
  return {
    ...current,
    dish_states: [...current.dish_states.filter(item => item.recipe_id !== operation.recipe_id), updated],
  };
}

export function menuRecipes(recipes: Recipe[]): Recipe[] {
  return recipes
    .filter(recipe => recipe.published !== false)
    .sort((first, second) => Number(Boolean(second.recommended)) - Number(Boolean(first.recommended)) || recipeTitleCollator.compare(first.title, second.title));
}

export type SyncEffects = {
  queue: (operation: Operation) => Promise<void>;
  dequeue: (id: string) => Promise<void>;
  markPending: (operation: Partial<Operation>, pending: boolean) => void;
  flash: (message: string) => void;
  prompt: (message: string, initial: string) => string | null;
  newUuid: () => string;
};

export type SyncBatchOptions = {
  cursor: number;
  queued: (limit: number) => Promise<QueuedOperation[]>;
  uploadPhoto?: (operation: Operation) => Promise<void>;
  requestPage: (cursor: number, operations: Operation[]) => Promise<SyncPayload>;
  effects: SyncEffects;
  onPage?: (page: { changes: Change[]; bootstrap: boolean; mealSnapshot: Meal | null | undefined; nextCursor: number }) => void;
};

/** Drain one outbox batch and all change pages; failed transport leaves unacknowledged operations queued. */
export async function synchronizeBatch({ cursor, queued, uploadPhoto, requestPage, effects, onPage }: SyncBatchOptions) {
  const pendingPage = await queued(CLIENT_SYNC_BATCH + 1);
  const pending = pendingPage.slice(0, CLIENT_SYNC_BATCH).map(wireOperation);
  let resync = pendingPage.length > CLIENT_SYNC_BATCH;
  if (uploadPhoto) for (const operation of pending) await uploadPhoto(operation);
  const changes: Change[] = [];
  let nextCursor = cursor;
  let bootstrap = false;
  let mealSnapshot: Meal | null | undefined;
  let firstPage = true;
  let resetPublished = false;
  while (true) {
    const payload = await requestPage(nextCursor, firstPage ? pending : []);
    if (firstPage) {
      for (const result of payload.operation_results) {
        const original = pending.find(item => item.op_id === result.op_id);
        if (result.status === "accepted") {
          await effects.dequeue(result.op_id);
          if (original) effects.markPending(original, false);
          if (original?.action === "load_plan" && result.skipped) effects.flash(result.message || `已跳过 ${result.skipped} 道不可用菜品`);
          if (original?.action === "complete_order") resync = true;
        }
        else if (result.status === "conflict" && result.conflict_id && result.server) {
          const choice = effects.prompt("心得冲突：输入 server 使用服务器版本，mine 使用我的版本，merge 手动合并", "server");
          if (original && choice === "server") await effects.queue({ op_id: effects.newUuid(), entity_type: "practice_log", entity_id: result.server.id, action: "resolve_conflict", payload: { id: result.server.id, conflict_id: result.conflict_id, resolution: "server" } });
          if (original && ["mine", "merge"].includes(choice || "")) {
            const notes = choice === "merge" ? effects.prompt("输入合并后的心得", String(original.payload?.notes || "")) : original.payload?.notes;
            if (notes !== null) await effects.queue({ ...original, op_id: effects.newUuid(), base_version: result.server.version, payload: { ...original.payload, notes, _resolved_conflict_id: result.conflict_id, _conflict_resolution: choice === "merge" ? "merged" : "incoming" } });
          }
          await effects.dequeue(result.op_id); effects.flash("心得冲突已记录解决选择");
          if (original) effects.markPending(original, false);
          resync = true;
        }
        else if (["stale_order", "order_completed"].includes(result.reason || "")) { await effects.dequeue(result.op_id); if (original) effects.markPending(original, false); effects.flash("本餐已变化，旧离线操作没有提交"); }
        else if (["recipe_unpublished", "recipe_unavailable"].includes(result.reason || "")) { await effects.dequeue(result.op_id); if (original) effects.markPending(original, false); effects.flash(result.message || "这道菜已不可用，没有提交该操作"); }
        else if (["meal_phase_changed", "meal_phase_locked", "phase_incomplete", "meal_empty"].includes(result.reason || "")) { await effects.dequeue(result.op_id); if (original) effects.markPending(original, false); effects.flash(result.message || "本餐阶段已经变化"); resync = true; }
        else if (result.status === "conflict" && original?.entity_type.startsWith("meal_")) { await effects.dequeue(result.op_id); effects.markPending(original, false); effects.flash(result.message || "这项本餐操作已失效，没有提交"); resync = true; }
      }
    }
    changes.push(...payload.changes);
    bootstrap ||= payload.bootstrap === true;
    if ("meal" in payload) mealSnapshot = payload.meal;
    nextCursor = payload.next_cursor;
    const resetPage: boolean = payload.bootstrap === true && !resetPublished;
    onPage?.({ changes: payload.changes, bootstrap: resetPage, mealSnapshot: payload.meal, nextCursor });
    resetPublished ||= resetPage;
    firstPage = false;
    if (!payload.has_more) break;
  }
  return { changes, nextCursor, bootstrap, mealSnapshot, resync };
}
