import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Recipe = {
  id: string;
  title: string;
  category?: string;
  total_time?: string;
  tags?: string[];
  published?: boolean;
  recommended?: boolean;
  ingredients?: { name?: string; amount?: string }[];
  prep_items?: string[];
  steps?: { title?: string; action?: string; heat?: string; duration?: string; tips?: string; image_sha256?: string }[];
  assets?: { sha256: string; kind?: string; step_index?: number }[];
};
type Selection = { order_id: string; device_id: string; device_name: string; recipe_id: string; quantity: number; note: string };
type MealPhase = "ordering" | "prep" | "cooking" | "serving";
type DishState = { recipe_id: string; prep_completed: boolean; cook_completed: boolean; served: boolean; completed: boolean };
type Meal = { order: { id: string; status: string; epoch: number; phase: MealPhase; chef_device_id?: string | null; created_at?: string; completed_at?: string | null }; selections: Selection[]; dish_states: DishState[] };
type Practice = { id: string; recipe_id: string; cooked_on: string; rating?: number; notes: string; photo_sha256?: string; version: number };
type Plan = { id: string; name: string; items: { recipe_id: string; title: string; servings_multiplier: number; note: string }[]; version: number };
type Pair = { base_url: string; pairing_token: string };
type Device = { id: string; name: string; token: string };
type Operation = { op_id: string; entity_type: string; entity_id?: string; action: string; base_version?: number; order_id?: string; epoch?: number; phase?: MealPhase; stage?: Exclude<MealPhase, "ordering">; recipe_id?: string; device_id?: string; quantity?: number; note?: string; completed?: boolean; plan_id?: string; payload?: Record<string, unknown> };
type QueuedOperation = Operation & { sequence?: number; queued_at?: number };
type Change = { entity_type: string; action: string; payload: Record<string, unknown> };
type SyncPayload = {
  operation_results: { op_id: string; status: string; reason?: string; message?: string; loaded?: number; skipped?: number; conflict_id?: string; server?: Practice }[];
  changes: Change[];
  next_cursor: number;
  has_more: boolean;
  bootstrap?: boolean;
  meal?: Meal | null;
};

const DB_NAME = "bili-recipe-lan";
const OUTBOX_STORE = "outbox_v3";
const CLIENT_SYNC_BATCH = 100;
const CAPABILITIES = ["recipe", "practice_log", "meal_plan", "meal_order", "meal_selection", "meal_dish_state"];
const CAPABILITY_KEY = CAPABILITIES.slice().sort().join(",");
const ORDER_CATEGORIES = ["全部", "主厨推荐", "主菜", "肉类", "海鲜", "主食", "面条", "糕点", "汤羹", "小吃", "饮品", "素菜", "其他"] as const;
type OrderCategory = typeof ORDER_CATEGORIES[number];

function newUuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 3);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("state")) database.createObjectStore("state");
      if (!database.objectStoreNames.contains("assets")) database.createObjectStore("assets");
      if (!database.objectStoreNames.contains(OUTBOX_STORE)) {
        const outbox = database.createObjectStore(OUTBOX_STORE, { keyPath: "sequence", autoIncrement: true });
        outbox.createIndex("op_id", "op_id", { unique: true });
        if (database.objectStoreNames.contains("outbox")) {
          const legacy = request.transaction!.objectStore("outbox");
          const cursor = legacy.openCursor();
          cursor.onsuccess = () => {
            if (!cursor.result) return;
            outbox.add({ ...cursor.result.value, queued_at: Date.now() });
            cursor.result.continue();
          };
        }
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function readState<T>(key: string, fallback: T): Promise<T> {
  const database = await openDb();
  return new Promise((resolve, reject) => {
    const request = database.transaction("state").objectStore("state").get(key);
    request.onsuccess = () => resolve((request.result as T | undefined) ?? fallback);
    request.onerror = () => reject(request.error);
  });
}

async function writeState(key: string, value: unknown): Promise<void> {
  const database = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction("state", "readwrite");
    transaction.objectStore("state").put(value, key);
    transaction.oncomplete = () => resolve(); transaction.onerror = () => reject(transaction.error);
  });
}

async function deleteState(key: string): Promise<void> {
  const database = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction("state", "readwrite");
    transaction.objectStore("state").delete(key);
    transaction.oncomplete = () => resolve(); transaction.onerror = () => reject(transaction.error);
  });
}

async function queue(operation: Operation): Promise<void> {
  const database = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(OUTBOX_STORE, "readwrite");
    const store = transaction.objectStore(OUTBOX_STORE);
    const existing = store.index("op_id").getKey(operation.op_id);
    existing.onsuccess = () => {
      const record: QueuedOperation = { ...operation, queued_at: Date.now() };
      if (existing.result !== undefined) record.sequence = Number(existing.result);
      store.put(record);
    };
    transaction.oncomplete = () => resolve(); transaction.onerror = () => reject(transaction.error);
  });
}

async function queued(limit?: number): Promise<QueuedOperation[]> {
  const database = await openDb();
  return new Promise((resolve, reject) => {
    const request = database.transaction(OUTBOX_STORE).objectStore(OUTBOX_STORE).getAll(undefined, limit);
    request.onsuccess = () => resolve(request.result as QueuedOperation[]); request.onerror = () => reject(request.error);
  });
}

async function dequeue(id: string): Promise<void> {
  const database = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(OUTBOX_STORE, "readwrite");
    const store = transaction.objectStore(OUTBOX_STORE);
    const existing = store.index("op_id").getKey(id);
    existing.onsuccess = () => { if (existing.result !== undefined) store.delete(existing.result); };
    transaction.oncomplete = () => resolve(); transaction.onerror = () => reject(transaction.error);
  });
}

async function cacheAsset(digest: string, blob: Blob): Promise<void> {
  const database = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction("assets", "readwrite");
    transaction.objectStore("assets").put(blob, digest);
    transaction.oncomplete = () => resolve(); transaction.onerror = () => reject(transaction.error);
  });
}

async function cachedAsset(digest: string): Promise<Blob | undefined> {
  const database = await openDb();
  return new Promise((resolve, reject) => {
    const request = database.transaction("assets").objectStore("assets").get(digest);
    request.onsuccess = () => resolve(request.result as Blob | undefined); request.onerror = () => reject(request.error);
  });
}

function mealOp(action: string, meal: Meal | null, extra: Partial<Operation> = {}): Operation {
  return { op_id: newUuid(), entity_type: "meal_selection", action, order_id: meal?.order.id, epoch: meal?.order.epoch, phase: meal?.order.phase, ...extra };
}

function wireOperation(operation: QueuedOperation): Operation {
  const { sequence: _sequence, queued_at: _queuedAt, ...payload } = operation;
  return payload;
}

function pendingActionKey(operation: Partial<Operation>): string | undefined {
  if (operation.action === "set_dish_stage_completed") {
    return [operation.order_id, operation.epoch, operation.action, operation.stage, operation.recipe_id].join(":");
  }
  if (["advance_meal_phase", "complete_order", "clear_order", "load_plan"].includes(operation.action || "")) {
    return [operation.order_id, operation.epoch, operation.action].join(":");
  }
  return undefined;
}

function recipeThumbnail(recipe?: Recipe): string | undefined {
  return recipe?.steps?.find(step => step.image_sha256)?.image_sha256
    || recipe?.assets?.find(asset => asset.kind === "recipe_image")?.sha256
    || recipe?.assets?.[0]?.sha256;
}

function recipeOrderCategories(recipe: Recipe): OrderCategory[] {
  const category = String(recipe.category || "");
  const title = String(recipe.title || "");
  const text = [title, category, ...(recipe.tags || []), ...(recipe.ingredients || []).map(item => item.name || "")].join(" ");
  const result = new Set<OrderCategory>();
  if (recipe.recommended) result.add("主厨推荐");
  const seafood = /(鱼|虾|蟹|贝|蚝|螺|鱿|鳝|海鲜|海胆|鲍|瑶柱|带子|龙虾|多宝鱼|生蚝)/;
  const meat = /(猪|牛|羊|鸡|鸭|鹅|鸽|排骨|肉|叉烧|肥肠|生肠|牛杂|牛腩|牛展|乳鸽|猪手)/;
  const noodle = /(炒面|汤面|拌面|伊面|面条|河粉|米粉|粉面|云吞|粉皮|牛河)/;
  const staple = /(炒饭|煲仔饭|牛肉饭|米饭|砂锅粥|白粥|云吞|炒面|汤面|拌面|伊面|面条|河粉|米粉|牛河)/;
  const pastry = /(蛋挞|拿破仑酥|糖沙翁|玉米糕|沙琪玛|糕点|点心|酥皮)/;
  const vegetable = /(菜心|通菜|油麦菜|土豆丝|豆芽|韭菜|四季豆|菜花|凉瓜|节瓜|豆腐|茄子|青菜|蔬菜)/;
  if (category === "中餐") result.add("主菜");
  if (meat.test(text)) result.add("肉类");
  if (seafood.test(text)) result.add("海鲜");
  if (category === "主食" || staple.test(title)) result.add("主食");
  if (noodle.test(text) && !/面粉/.test(title)) result.add("面条");
  if (category === "糕点" || pastry.test(text)) result.add("糕点");
  if (category === "汤羹" || /(汤|羹|炖汤|糖水)/.test(title)) result.add("汤羹");
  if (category === "小吃") result.add("小吃");
  if (category === "饮品") result.add("饮品");
  if (category === "中餐" && vegetable.test(text) && !meat.test(text) && !seafood.test(text)) result.add("素菜");
  if (![...result].some(item => item !== "主厨推荐") || category === "其他") result.add("其他");
  return ORDER_CATEGORIES.filter(item => item !== "全部" && result.has(item));
}

function mergeChanges<T extends { id: string }>(current: T[], changes: Change[], entityType: string, reset: boolean): T[] {
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

function optimisticallyAddQuantity(current: Meal | null, operation: Operation, device: Device): Meal | null {
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

function optimisticallySetDishStage(current: Meal | null, operation: Operation): Meal | null {
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

export default function App() {
  const [device, setDevice] = useState<Device | null>(null);
  const [pair, setPair] = useState<Pair | null>(null);
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [meal, setMeal] = useState<Meal | null>(null);
  const [practices, setPractices] = useState<Practice[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [cursor, setCursor] = useState(0);
  const [online, setOnline] = useState(navigator.onLine);
  const [notice, setNotice] = useState("");
  const [pendingActions, setPendingActions] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState<OrderCategory>("全部");
  const [tab, setTab] = useState<"menu" | "meal">("menu");
  const [deviceName, setDeviceName] = useState("");
  const [joinEnabled, setJoinEnabled] = useState(true);
  const [selected, setSelected] = useState<Recipe | null>(null);
  const mealRef = useRef<Meal | null>(null);
  const cursorRef = useRef(0);
  const syncing = useRef(false);
  const syncRequested = useRef(false);
  const realtime = useRef(false);
  const pendingActionsRef = useRef<Set<string>>(new Set());
  const server = location.origin;
  const flash = (message: string) => { setNotice(message); window.setTimeout(() => setNotice(""), 2800); };

  useEffect(() => {
    if ("serviceWorker" in navigator) {
      void navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" }).then(registration => registration.update());
    }
    const encoded = new URLSearchParams(location.search).get("pairing");
    if (encoded) { try { setPair(JSON.parse(decodeURIComponent(encoded)) as Pair); } catch { flash("配对链接无效"); } }
    void Promise.all([
      readState<Pair | null>("pair", null), readState<Device | null>("device", null), readState<Recipe[]>("recipes", []),
      readState<Meal | null>("meal", null), readState<Practice[]>("practices", []), readState<Plan[]>("plans", []),
      readState<number>("cursor", 0), readState<string>("capability_key", ""),
    ]).then(([savedPair, savedDevice, savedRecipes, savedMeal, savedPractices, savedPlans, savedCursor, key]) => {
      if (savedPair) setPair(savedPair); if (savedDevice) setDevice(savedDevice);
      setRecipes(savedRecipes); mealRef.current = savedMeal; setMeal(savedMeal); setPractices(savedPractices); setPlans(savedPlans);
      const restoredCursor = key === CAPABILITY_KEY ? savedCursor : 0;
      cursorRef.current = restoredCursor; setCursor(restoredCursor); void writeState("capability_key", CAPABILITY_KEY);
    });
    void fetch("/api/v1/health").then(response => response.json()).then(health => setJoinEnabled(Boolean(health.self_join_enabled))).catch(() => undefined);
    const updateOnline = () => setOnline(navigator.onLine);
    window.addEventListener("online", updateOnline); window.addEventListener("offline", updateOnline);
    return () => { window.removeEventListener("online", updateOnline); window.removeEventListener("offline", updateOnline); };
  }, []);

  useEffect(() => { if (pair) void writeState("pair", pair); }, [pair]);
  useEffect(() => { if (device) void writeState("device", device); }, [device]);
  useEffect(() => { void writeState("recipes", recipes); }, [recipes]);
  useEffect(() => { void writeState("meal", meal); }, [meal]);
  useEffect(() => { void writeState("practices", practices); }, [practices]);
  useEffect(() => { void writeState("plans", plans); }, [plans]);
  useEffect(() => { cursorRef.current = cursor; void writeState("cursor", cursor); }, [cursor]);

  const markPending = useCallback((operation: Partial<Operation>, pending: boolean) => {
    const key = pendingActionKey(operation);
    if (!key) return;
    const updated = new Set(pendingActionsRef.current);
    if (pending) updated.add(key); else updated.delete(key);
    pendingActionsRef.current = updated;
    setPendingActions(updated);
  }, []);

  useEffect(() => {
    if (!device) return;
    void queued().then(operations => {
      const restored = new Set(operations.map(pendingActionKey).filter((key): key is string => Boolean(key)));
      pendingActionsRef.current = restored;
      setPendingActions(restored);
    });
  }, [device]);

  const sync = useCallback(async () => {
    if (!device || !server) return;
    if (syncing.current) { syncRequested.current = true; return; }
    syncing.current = true;
    syncRequested.current = false;
    let resync = false;
    try {
      const pendingPage = await queued(CLIENT_SYNC_BATCH + 1);
      const pending = pendingPage.slice(0, CLIENT_SYNC_BATCH).map(wireOperation);
      resync = pendingPage.length > CLIENT_SYNC_BATCH;
      for (const operation of pending) {
        const digest = String(operation.payload?.photo_sha256 || "");
        if (!digest) continue;
        const photo = await cachedAsset(digest);
        if (photo) {
          const uploaded = await fetch(`${server}/api/v1/assets/${digest}`, { method: "PUT", headers: { Authorization: `Bearer ${device.token}`, "Content-Type": photo.type }, body: photo });
          if (!uploaded.ok) throw new Error("实践照片上传失败");
        }
      }
      const changes: Change[] = [];
      let nextCursor = cursorRef.current;
      let bootstrap = false;
      let mealSnapshot: Meal | null | undefined;
      let firstPage = true;
      while (true) {
        const response = await fetch(`${server}/api/v1/sync`, { method: "POST", headers: { Authorization: `Bearer ${device.token}`, "Content-Type": "application/json" }, body: JSON.stringify({ cursor: nextCursor, operations: firstPage ? pending : [], capabilities: CAPABILITIES }) });
        if (response.status === 401) { await deleteState("device"); setDevice(null); throw new Error("设备已被撤销，请重新加入"); }
        if (!response.ok) throw new Error("同步失败");
        const payload = await response.json() as SyncPayload;
        if (firstPage) {
          for (const result of payload.operation_results) {
            const original = pending.find(item => item.op_id === result.op_id);
            if (result.status === "accepted") {
              await dequeue(result.op_id);
              if (original) markPending(original, false);
              if (original?.action === "load_plan" && result.skipped) flash(result.message || `已跳过 ${result.skipped} 道不可用菜品`);
              if (original?.action === "complete_order") resync = true;
            }
            else if (result.status === "conflict" && result.conflict_id && result.server) {
              const choice = prompt("心得冲突：输入 server 使用服务器版本，mine 使用我的版本，merge 手动合并", "server");
              if (original && choice === "server") await queue({ op_id: newUuid(), entity_type: "practice_log", entity_id: result.server.id, action: "resolve_conflict", payload: { id: result.server.id, conflict_id: result.conflict_id, resolution: "server" } });
              if (original && ["mine", "merge"].includes(choice || "")) {
                const notes = choice === "merge" ? prompt("输入合并后的心得", String(original.payload?.notes || "")) : original.payload?.notes;
                if (notes !== null) await queue({ ...original, op_id: newUuid(), base_version: result.server.version, payload: { ...original.payload, notes, _resolved_conflict_id: result.conflict_id, _conflict_resolution: choice === "merge" ? "merged" : "incoming" } });
              }
              await dequeue(result.op_id); flash("心得冲突已记录解决选择");
              if (original) markPending(original, false);
              resync = true;
            }
            else if (["stale_order", "order_completed"].includes(result.reason || "")) { await dequeue(result.op_id); if (original) markPending(original, false); flash("本餐已变化，旧离线操作没有提交"); }
            else if (["recipe_unpublished", "recipe_unavailable"].includes(result.reason || "")) { await dequeue(result.op_id); if (original) markPending(original, false); flash(result.message || "这道菜已不可用，没有提交该操作"); }
            else if (["meal_phase_changed", "meal_phase_locked", "phase_incomplete", "meal_empty"].includes(result.reason || "")) { await dequeue(result.op_id); if (original) markPending(original, false); flash(result.message || "本餐阶段已经变化"); resync = true; }
            else if (result.status === "conflict" && original?.entity_type.startsWith("meal_")) { await dequeue(result.op_id); markPending(original, false); flash(result.message || "这项本餐操作已失效，没有提交"); resync = true; }
          }
        }
        changes.push(...payload.changes);
        bootstrap ||= payload.bootstrap === true;
        if ("meal" in payload) mealSnapshot = payload.meal;
        nextCursor = payload.next_cursor;
        firstPage = false;
        if (!payload.has_more) break;
      }
      setRecipes(current => mergeChanges<Recipe>(current, changes, "recipe", bootstrap));
      setPractices(current => mergeChanges<Practice>(current, changes, "practice_log", bootstrap));
      setPlans(current => mergeChanges<Plan>(current, changes, "meal_plan", bootstrap));
      if (mealSnapshot !== undefined) { mealRef.current = mealSnapshot; setMeal(mealSnapshot); }
      cursorRef.current = nextCursor;
      setCursor(nextCursor);
    } catch (error) { if (navigator.onLine) flash(error instanceof Error ? error.message : "同步失败"); }
    finally {
      syncing.current = false;
      if (resync || syncRequested.current) {
        syncRequested.current = false;
        window.setTimeout(() => void sync(), 0);
      }
    }
  }, [device, server, markPending]);

  useEffect(() => {
    if (!device || meal) return;
    const retry = window.setTimeout(() => void sync(), 1000);
    return () => window.clearTimeout(retry);
  }, [device, meal, sync]);

  useEffect(() => {
    if (!device) return;
    void sync();
    const polling = window.setInterval(() => { if (!realtime.current && navigator.onLine) void sync(); }, 3000);
    let stopped = false; let reconnectDelay = 1000;
    const connect = async () => {
      try {
        const response = await fetch(`${server}/api/v1/events`, { headers: { Authorization: `Bearer ${device.token}` } });
        if (response.status === 401) { await deleteState("device"); setDevice(null); stopped = true; throw new Error(); }
        if (!response.ok || !response.body) throw new Error();
        realtime.current = true; reconnectDelay = 1000;
        const reader = response.body.getReader();
        while (!stopped) { const item = await reader.read(); if (item.done) break; if (item.value) void sync(); }
      } catch { realtime.current = false; if (!stopped) { const delay = reconnectDelay; reconnectDelay = Math.min(30000, reconnectDelay * 2); window.setTimeout(connect, delay); } }
    };
    void connect();
    return () => { stopped = true; realtime.current = false; window.clearInterval(polling); };
  }, [device, server, sync]);

  async function pairDevice() {
    if (!deviceName.trim()) return;
    try {
      const response = await fetch(`${server}/api/v1/join`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device_name: deviceName.trim() }) });
      if (!response.ok) throw new Error(response.status === 403 ? "管理员暂时关闭了新设备加入" : "加入失败，请确认服务器仍在运行");
      const result = await response.json(); setDevice({ id: result.device_id, name: result.device_name || deviceName.trim(), token: result.access_token });
      history.replaceState({}, "", "/"); flash("加入成功，正在同步菜谱");
    } catch (error) { flash(error instanceof Error ? error.message : "加入失败"); }
  }

  async function enqueue(operation: Operation, mealBound = true) {
    try {
      let ready = operation;
      if (mealBound && (!ready.order_id || ready.epoch === undefined)) {
        let currentMeal = mealRef.current;
        if (!currentMeal) {
          const response = await fetch(`${server}/api/v1/sync`, { method: "POST", headers: { Authorization: `Bearer ${device?.token || ""}`, "Content-Type": "application/json" }, body: JSON.stringify({ cursor: cursorRef.current, operations: [], capabilities: CAPABILITIES }) });
          if (response.status === 401) { await deleteState("device"); setDevice(null); throw new Error("设备已被撤销，请重新加入"); }
          if (!response.ok) throw new Error("暂时无法打开共享本餐");
          const payload = await response.json() as SyncPayload;
          currentMeal = payload.meal || null;
          if (currentMeal) { mealRef.current = currentMeal; setMeal(currentMeal); }
        }
        if (!currentMeal) throw new Error("共享本餐尚未准备好，请稍后再试");
        ready = { ...ready, order_id: currentMeal.order.id, epoch: currentMeal.order.epoch };
      }
      const actionKey = pendingActionKey(ready);
      if (actionKey && pendingActionsRef.current.has(actionKey)) return;
      markPending(ready, true);
      await queue(ready);
      if (device) {
        let optimistic = optimisticallyAddQuantity(mealRef.current, ready, device);
        optimistic = optimisticallySetDishStage(optimistic, ready);
        mealRef.current = optimistic;
        setMeal(optimistic);
      }
      void sync();
    } catch (error) {
      markPending(operation, false);
      flash(error instanceof Error ? error.message : "点菜失败，请重试");
    }
  }

  const availableRecipes = useMemo(() => recipes
    .filter(recipe => recipe.published !== false)
    .sort((first, second) => Number(Boolean(second.recommended)) - Number(Boolean(first.recommended)) || first.title.localeCompare(second.title, "zh-CN")), [recipes]);
  const categoryCounts = useMemo(() => {
    const counts = new Map<OrderCategory, number>([["全部", availableRecipes.length]]);
    for (const recipe of availableRecipes) {
      for (const category of recipeOrderCategories(recipe)) counts.set(category, (counts.get(category) || 0) + 1);
    }
    return counts;
  }, [availableRecipes]);
  const filtered = useMemo(() => availableRecipes.filter(recipe => {
    const categoryMatches = selectedCategory === "全部" || recipeOrderCategories(recipe).includes(selectedCategory);
    const queryMatches = !query || JSON.stringify(recipe).toLowerCase().includes(query.toLowerCase());
    return categoryMatches && queryMatches;
  }), [availableRecipes, query, selectedCategory]);
  const totalQuantity = (recipeId: string) => meal?.selections.filter(item => item.recipe_id === recipeId).reduce((sum, item) => sum + item.quantity, 0) || 0;
  const isOrdering = !meal?.order.phase || meal.order.phase === "ordering";
  const isChef = Boolean(meal?.order.chef_device_id && meal.order.chef_device_id === device?.id);

  useEffect(() => {
    if (selectedCategory !== "全部" && !categoryCounts.get(selectedCategory)) setSelectedCategory("全部");
  }, [categoryCounts, selectedCategory]);

  if (!device) return <main className="pairing"><div className="brand">CHEF ZHAI</div><h1>加入 Chef Zhai 家庭厨房</h1><p>首次使用只需取一个设备名称，以后打开这个网址会自动进入家庭餐桌。</p>{joinEnabled ? <><input value={deviceName} onChange={event => setDeviceName(event.target.value)} placeholder="例如：小王的手机" autoFocus /><button onClick={() => void pairDevice()}>进入家庭厨房</button></> : <p className="hint">管理员暂时关闭了新设备加入。已加入的设备仍可正常使用。</p>}{notice && <div className="notice">{notice}</div>}</main>;

  return <main className="shell">
    <header><div><span className="eyebrow">CHEF ZHAI · 家庭厨房</span><h1>{tab === "menu" ? "今日菜单" : isOrdering ? "家庭点餐" : isChef ? "主厨烹饪台" : "本餐进度"}</h1></div><span className={online ? "online" : "offline"}>{online ? "在线" : "断线重试"}</span></header>
    <div className="content">{tab === "menu" ? <>
      <input className="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索当前分类中的菜名、食材…" />
      <div className="categoryBar" role="tablist" aria-label="菜品分类">
        {ORDER_CATEGORIES.filter(category => category === "全部" || categoryCounts.get(category)).map(category => <button role="tab" aria-selected={selectedCategory === category} className={selectedCategory === category ? "active" : ""} key={category} onClick={() => setSelectedCategory(category)}><span>{category}</span><small>{categoryCounts.get(category) || 0}</small></button>)}
      </div>
      <div className="menuSummary"><b>{selectedCategory}</b><span>{filtered.length} 道可选{selectedCategory === "全部" && categoryCounts.get("主厨推荐") ? ` · ${categoryCounts.get("主厨推荐")} 道推荐` : ""}{query ? " · 已应用搜索" : ""}</span></div>
      {!isOrdering && <div className="phaseNotice">{isChef ? "本餐已进入主厨模式，菜单内容只读，请前往本餐更新烹饪进度。" : "点单已完成。现在可以查看菜单和主厨实时进度，点餐内容已锁定。"}</div>}
      {filtered.map(recipe => <article className={`recipe ${recipe.recommended ? "recommendedDish" : ""}`} key={recipe.id}><DishThumbnail recipe={recipe} server={server} token={device.token} /><button className="recipeInfo" onClick={() => setSelected(recipe)}><div className="recipeTitle"><b>{recipe.title}</b>{recipe.recommended && <span>Chef Zhai 推荐</span>}</div><small>{[recipe.category, recipeOrderCategories(recipe).filter(item => item !== recipe.category && item !== "主厨推荐").slice(0, 2).join(" · "), recipe.total_time].filter(Boolean).join(" · ")}</small><small>整桌已点 {totalQuantity(recipe.id).toFixed(1)} 份</small></button><button disabled={!isOrdering} aria-label={`添加${recipe.title}`} onClick={() => void enqueue(mealOp("add_quantity", meal, { recipe_id: recipe.id, quantity: 1 }))}>＋</button></article>)}
      {!filtered.length && <div className="empty">这个分类下没有匹配的菜，试试其他分类或清空搜索。</div>}
    </> : <MealView meal={meal} recipes={recipes} plans={plans} device={device} server={server} enqueue={enqueue} pendingActions={pendingActions} onRetry={() => void sync()} onClear={() => meal && window.confirm("确定清空共享本餐？") && void enqueue({ ...mealOp("clear_order", meal), entity_type: "meal_order" })} />}</div>
    <nav><button className={tab === "menu" ? "active" : ""} onClick={() => setTab("menu")}>菜单</button><button className={tab === "meal" ? "active" : ""} onClick={() => setTab("meal")}>本餐 {meal?.selections.length ? `· ${meal.selections.length}` : ""}</button></nav>
    {selected && <RecipeDetail recipe={selected} practices={practices.filter(item => item.recipe_id === selected.id)} server={server} token={device.token} close={() => setSelected(null)} submit={operation => enqueue(operation, false)} />}{notice && <div className="notice">{notice}</div>}
  </main>;
}

function MealView({ meal, recipes, plans, device, server, enqueue, pendingActions, onClear, onRetry }: { meal: Meal | null; recipes: Recipe[]; plans: Plan[]; device: Device; server: string; enqueue: (operation: Operation, mealBound?: boolean) => Promise<void>; pendingActions: Set<string>; onClear: () => void; onRetry: () => void }) {
  const [history, setHistory] = useState<Meal[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const openHistory = async () => {
    setHistoryOpen(true); setHistoryLoading(true); setHistoryError("");
    try {
      const response = await fetch(`${server}/api/v1/meal-history`, { headers: { Authorization: `Bearer ${device.token}` } });
      if (!response.ok) throw new Error("历史本餐加载失败");
      const payload = await response.json() as { meals?: Meal[] };
      setHistory(payload.meals || []);
    } catch (error) { setHistoryError(error instanceof Error ? error.message : "历史本餐加载失败"); }
    finally { setHistoryLoading(false); }
  };
  if (!meal) return <div className="empty mealLoading"><p>正在打开共享本餐…</p><button onClick={onRetry}>立即重试</button></div>;
  const phase = meal.order.phase || "ordering";
  const isChef = Boolean(meal.order.chef_device_id && meal.order.chef_device_id === device.id);
  const grouped = new Map<string, Selection[]>();
  meal.selections.forEach(item => grouped.set(item.recipe_id, [...(grouped.get(item.recipe_id) || []), item]));
  const dishes = [...grouped.entries()];
  const phases: { id: MealPhase; label: string }[] = [
    { id: "ordering", label: "点餐" }, { id: "prep", label: "备餐" },
    { id: "cooking", label: "烹饪" }, { id: "serving", label: "上桌" },
  ];
  const currentIndex = phases.findIndex(item => item.id === phase);
  const completionKey = phase === "prep" ? "prep_completed" : phase === "cooking" ? "cook_completed" : "served";
  const completedCount = phase === "ordering" ? 0 : dishes.filter(([recipeId]) => Boolean(meal.dish_states.find(item => item.recipe_id === recipeId)?.[completionKey])).length;
  const stageComplete = dishes.length > 0 && completedCount === dishes.length;
  const nextIncomplete = phase === "ordering" ? undefined : dishes.find(([recipeId]) => !meal.dish_states.find(item => item.recipe_id === recipeId)?.[completionKey]);
  const transitionAction = phase === "serving" ? "complete_order" : "advance_meal_phase";
  const transitionPending = pendingActions.has(pendingActionKey({ ...meal.order, order_id: meal.order.id, action: transitionAction }) || "");
  const clearPending = pendingActions.has(pendingActionKey({ ...meal.order, order_id: meal.order.id, action: "clear_order" }) || "");
  const planPending = pendingActions.has(pendingActionKey({ ...meal.order, order_id: meal.order.id, action: "load_plan" }) || "");
  const savePlan = () => {
    const name = prompt("套餐名称"); if (!name || !grouped.size) return;
    const items = dishes.map(([recipeId, selections]) => ({ recipe_id: recipeId, title: recipes.find(recipe => recipe.id === recipeId)?.title || recipeId, servings_multiplier: selections.reduce((sum, item) => sum + item.quantity, 0), note: selections.map(item => item.note).filter(Boolean).join("；") }));
    void enqueue({ op_id: newUuid(), entity_type: "meal_plan", action: "upsert", payload: { id: newUuid(), name, guest_count: 1, child_count: 0, occasion: "家庭用餐", notes: "", items } }, false);
  };
  const shopping = dishes.flatMap(([recipeId, selections]) => {
    const multiplier = selections.reduce((sum, item) => sum + item.quantity, 0);
    return (recipes.find(item => item.id === recipeId)?.ingredients || []).map(item => ({ name: item.name || "未命名食材", amount: item.amount || "适量", multiplier }));
  }).reduce((items, item) => {
    const existing = items.find(entry => entry.name === item.name && entry.amount === item.amount);
    if (existing) existing.multiplier += item.multiplier; else items.push({ ...item });
    return items;
  }, [] as { name: string; amount: string; multiplier: number }[]);
  const advance = () => {
    if (phase === "ordering" && !window.confirm(`确认以本设备认领主厨，并开始准备这 ${dishes.length} 道菜？开始后其他参与者只能查看菜单和进度。`)) return;
    void enqueue({ ...mealOp("advance_meal_phase", meal), entity_type: "meal_order" });
  };
  const finish = () => {
    if (window.confirm("所有菜都已上桌，确认完成并归档本餐？")) {
      void enqueue({ ...mealOp("complete_order", meal), entity_type: "meal_order" });
    }
  };
  const focusNextIncomplete = () => {
    if (!nextIncomplete) return;
    document.getElementById(`meal-dish-${nextIncomplete[0]}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return <>
    <section className="phasePanel">
      <div className="phaseHeader"><span>Chef Zhai · 第 {meal.order.epoch} 餐</span><div>{phase !== "ordering" && <b>{isChef ? "主厨模式" : "访客只读"} · {completedCount} / {dishes.length}</b>}<button onClick={() => void openHistory()}>历史</button></div></div>
      <ol className="phaseSteps">{phases.map((item, index) => <li className={index < currentIndex ? "complete" : index === currentIndex ? "active" : ""} key={item.id}><i>{index < currentIndex ? "✓" : index + 1}</i><span>{item.label}</span></li>)}</ol>
      {phase === "prep" && <p>{isChef ? "逐道核对食材和准备事项。所有菜完成备餐后，再统一进入烹饪。" : "您的点单已经完成，主厨正在备餐。您可以继续查看每道菜和实时进度。"}</p>}
      {phase === "cooking" && <p>{isChef ? "按菜谱步骤烹制。每道菜完成后标记，整桌完成再进入上桌。" : "主厨正在烹饪。菜单和烹饪步骤可查看，完成状态由主厨更新。"}</p>}
      {phase === "serving" && <p>{isChef ? "逐道确认摆盘上桌。全部上桌后，本餐将归档保存。" : "菜品正在陆续上桌，您可以在这里查看最新进度。"}</p>}
    </section>

    {phase === "ordering" && <>
      <div className="mealActions"><span>{dishes.length} 道菜</span><button onClick={savePlan}>存套餐</button><button disabled={clearPending} onClick={onClear}>{clearPending ? "清空中…" : "清空"}</button></div>
      {plans.length > 0 && <select className="search" disabled={planPending} defaultValue="" onChange={event => { if (event.target.value) void enqueue({ ...mealOp("load_plan", meal, { plan_id: event.target.value }), entity_type: "meal_order" }); event.currentTarget.value = ""; }}><option value="">{planPending ? "正在载入套餐…" : "载入已保存套餐…"}</option>{plans.map(plan => <option value={plan.id} key={plan.id}>{plan.name}</option>)}</select>}
    </>}

    {!dishes.length && <div className="empty">还没有选菜，请先从菜单添加餐品。</div>}
    {dishes.map(([recipeId, selections]) => {
      const recipe = recipes.find(item => item.id === recipeId);
      const state = meal.dish_states.find(item => item.recipe_id === recipeId);
      const completed = phase !== "ordering" && Boolean(state?.[completionKey]);
      const quantity = selections.reduce((sum, item) => sum + item.quantity, 0);
      const stagePending = phase !== "ordering" && pendingActions.has(pendingActionKey({ ...meal.order, order_id: meal.order.id, action: "set_dish_stage_completed", stage: phase, recipe_id: recipeId }) || "");
      return <article id={`meal-dish-${recipeId}`} className={`mealDish ${completed ? "stageDone" : ""}`} key={recipeId}>
        <DishThumbnail recipe={recipe} server={server} token={device.token} />
        <div className="mealDishBody">
          <div className="dishHeading"><div><h2>{recipe?.title || recipeId}</h2><strong>{quantity.toFixed(1)} 份</strong></div>{phase !== "ordering" && (isChef ? <div className="dishStageAction">{completed && <span>本阶段已完成</span>}<button disabled={stagePending} className={`stageToggle ${completed ? "done" : ""}`} onClick={() => void enqueue({ ...mealOp("set_dish_stage_completed", meal, { recipe_id: recipeId, stage: phase, completed: !completed }), entity_type: "meal_dish_state" })}>{stagePending ? "同步中…" : completed ? "撤销完成" : phase === "prep" ? "备餐完成" : phase === "cooking" ? "烹饪完成" : "已上桌"}</button></div> : <span className={`guestDishStatus ${completed ? "done" : ""}`}>{completed ? "已完成" : "主厨进行中"}</span>)}</div>
          {phase === "ordering" && selections.map(item => <div className="person" key={item.device_id}><small>{item.device_name} {item.quantity} 份{item.note ? ` · ${item.note}` : ""}</small><button onClick={() => void enqueue(mealOp("add_quantity", meal, { recipe_id: recipeId, device_id: item.device_id, quantity: -.5 }))}>−</button><button onClick={() => void enqueue(mealOp("add_quantity", meal, { recipe_id: recipeId, device_id: item.device_id, quantity: .5 }))}>＋</button><button onClick={() => void enqueue(mealOp("remove_selection", meal, { recipe_id: recipeId, device_id: item.device_id }))}>×</button><input defaultValue={item.note} placeholder="备注" onBlur={event => void enqueue(mealOp("set_note", meal, { recipe_id: recipeId, device_id: item.device_id, note: event.target.value }))} /></div>)}
          {phase === "prep" && <div className="stageContent"><h3>准备事项</h3>{recipe?.prep_items?.length ? <ul>{recipe.prep_items.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>暂无单独备餐事项，请按食材清单准备。</p>}<details><summary>查看食材</summary><ul>{recipe?.ingredients?.map((item, index) => <li key={index}>{item.name || "未命名食材"}{item.amount ? ` · ${item.amount}` : ""}</li>)}</ul></details></div>}
          {phase === "cooking" && <div className="stageContent"><h3>烹饪步骤</h3>{recipe?.steps?.length ? <details className="cookingSteps" open={!completed}><summary>{completed ? "已收起步骤" : `展开查看 ${recipe.steps.length} 个步骤`}</summary><ol>{recipe.steps.map((step, index) => <li key={index}><b>{step.title || `第 ${index + 1} 步`}</b><span>{step.action || "按菜谱完成此步骤"}</span>{(step.heat || step.duration) && <small>{[step.heat, step.duration].filter(Boolean).join(" · ")}</small>}</li>)}</ol></details> : <p>暂无详细步骤，请按已有菜谱说明完成。</p>}</div>}
          {phase === "serving" && <div className="stageContent servingInfo"><p>共 {quantity.toFixed(1)} 份 · {selections.map(item => item.device_name).join("、")}</p>{selections.some(item => item.note) && <small>{selections.map(item => item.note).filter(Boolean).join("；")}</small>}</div>}
        </div>
        {phase === "ordering" && <div className="controls"><button onClick={() => void enqueue(mealOp("add_quantity", meal, { recipe_id: recipeId, quantity: 1 }))}>我也要</button></div>}
      </article>;
    })}

    {phase === "ordering" && shopping.length > 0 && <section className="shopping"><h2>采购清单</h2>{shopping.map(item => <label key={`${item.name}:${item.amount}`}><input type="checkbox" /> <span>{item.name}</span><b>{item.amount} × {item.multiplier.toFixed(1)}</b></label>)}</section>}
    {dishes.length > 0 && (phase === "ordering" || isChef ? <div className="phaseFooter"><button disabled={transitionPending} className={phase !== "ordering" && !stageComplete ? "findNextAction" : "primaryAction"} onClick={phase !== "ordering" && !stageComplete ? focusNextIncomplete : phase === "serving" ? finish : advance}>{transitionPending ? "正在同步阶段…" : phase !== "ordering" && !stageComplete ? `去完成 · ${recipes.find(item => item.id === nextIncomplete?.[0])?.title || "下一道菜"}` : phase === "ordering" ? "我是主厨 · 完成点餐并开始备餐" : phase === "prep" ? "全部备好 · 进入烹饪" : phase === "cooking" ? "全部完成 · 进入上桌" : "全部上桌 · 完成本餐"}</button>{phase !== "ordering" && !stageComplete && <small>还需完成 {dishes.length - completedCount} 道菜，点击可直接定位</small>}</div> : <div className="guestFooter"><b>您的点单已完成</b><span>主厨正在更新本餐进度 · {completedCount} / {dishes.length} 道完成</span></div>)}
    {historyOpen && <div className="modal historyModal"><div><button className="close" onClick={() => setHistoryOpen(false)}>×</button><h1>历史本餐</h1>{historyLoading && <div className="empty">正在加载…</div>}{historyError && <p className="historyError">{historyError}</p>}{!historyLoading && !historyError && !history.length && <div className="empty">还没有已完成的本餐。</div>}{history.map(archived => {
      const archivedDishes = new Map<string, Selection[]>(); archived.selections.forEach(item => archivedDishes.set(item.recipe_id, [...(archivedDishes.get(item.recipe_id) || []), item]));
      return <article className="historyMeal" key={archived.order.id}><h2>{archived.order.completed_at ? new Date(archived.order.completed_at).toLocaleString() : "已完成本餐"}</h2><small>{archivedDishes.size} 道菜 · {new Set(archived.selections.map(item => item.device_id)).size} 位参与者</small>{[...archivedDishes.entries()].map(([recipeId, selections]) => <div key={recipeId}><b>{recipes.find(item => item.id === recipeId)?.title || recipeId}</b><span>{selections.reduce((sum, item) => sum + item.quantity, 0).toFixed(1)} 份 · {selections.map(item => item.device_name).join("、")}</span></div>)}</article>;
    })}</div></div>}
  </>;
}

function RecipeDetail({ recipe, practices, server, token, close, submit }: { recipe: Recipe; practices: Practice[]; server: string; token: string; close: () => void; submit: (operation: Operation) => Promise<void> }) {
  async function savePractice(form: HTMLFormElement) {
    const data = new FormData(form); let photo_sha256: string | undefined; const photo = data.get("photo");
    if (photo instanceof File && photo.size) {
      const compressed = await compressPhoto(photo); const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", await compressed.arrayBuffer()))].map(value => value.toString(16).padStart(2, "0")).join("");
      await cacheAsset(digest, compressed);
      if (navigator.onLine) {
        const uploaded = await fetch(`${server}/api/v1/assets/${digest}`, { method: "PUT", headers: { Authorization: `Bearer ${token}`, "Content-Type": compressed.type }, body: compressed });
        if (!uploaded.ok) throw new Error("照片上传失败");
      }
      photo_sha256 = digest;
    }
    const id = newUuid(); await submit({ op_id: newUuid(), entity_type: "practice_log", entity_id: id, action: "upsert", base_version: 0, payload: { id, recipe_id: recipe.id, cooked_on: String(data.get("date")), outcome: "success", rating: Number(data.get("rating")), notes: String(data.get("notes")), photo_sha256 } }); close();
  }
  return <div className="modal"><div><button className="close" onClick={close}>×</button><h1>{recipe.title}</h1><p>{recipe.ingredients?.map(item => `${item.name} ${item.amount || ""}`).join("、")}</p><h3>实践心得</h3>{practices.map(item => <article key={item.id}><b>{item.cooked_on} · {"★".repeat(item.rating || 0)}</b><p>{item.notes}</p>{item.photo_sha256 && <AuthenticatedImage digest={item.photo_sha256} server={server} token={token} />}<button onClick={() => { const notes = prompt("修改心得", item.notes); if (notes !== null) void submit({ op_id: newUuid(), entity_type: "practice_log", entity_id: item.id, action: "upsert", base_version: item.version, payload: { ...item, notes } }); }}>编辑</button> <button onClick={() => void submit({ op_id: newUuid(), entity_type: "practice_log", entity_id: item.id, action: "delete", base_version: item.version, payload: { id: item.id } })}>删除</button></article>)}<form onSubmit={event => { event.preventDefault(); void savePractice(event.currentTarget); }}><input name="date" type="date" defaultValue={new Date().toISOString().slice(0, 10)} required /><select name="rating" defaultValue="5"><option value="5">5 星</option><option value="4">4 星</option><option value="3">3 星</option><option value="2">2 星</option><option value="1">1 星</option></select><textarea name="notes" placeholder="这次做得怎么样？" required /><input name="photo" type="file" accept="image/jpeg,image/png,image/webp" /><button>保存心得</button></form></div></div>;
}

function DishThumbnail({ recipe, server, token }: { recipe?: Recipe; server: string; token: string }) {
  const digest = recipeThumbnail(recipe);
  const root = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!digest) return;
    if (!("IntersectionObserver" in window)) { setVisible(true); return; }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: "240px" });
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, [digest]);
  return <div className="dishThumbnail" ref={root}>{digest && visible && <AuthenticatedImage digest={digest} server={server} token={token} alt={recipe?.title || "菜品图片"} />}</div>;
}

function AuthenticatedImage({ digest, server, token, alt = "实践照片" }: { digest: string; server: string; token: string; alt?: string }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let active = true; let objectUrl = "";
    setUrl("");
    void (async () => {
      let blob = await cachedAsset(digest);
      if (!blob && navigator.onLine) {
        const response = await fetch(`${server}/api/v1/assets/${digest}`, { headers: { Authorization: `Bearer ${token}` } });
        if (response.ok) { blob = await response.blob(); await cacheAsset(digest, blob); }
      }
      if (blob && active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); }
    })().catch(() => undefined);
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [digest, server, token]);
  return url ? <img src={url} alt={alt} /> : null;
}

async function compressPhoto(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file); const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas"); canvas.width = Math.round(bitmap.width * scale); canvas.height = Math.round(bitmap.height * scale); canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  let quality = .88; let result: Blob | null = null;
  do { result = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", quality)); quality -= .1; } while (result && result.size > 5 * 1024 * 1024 && quality >= .38);
  if (!result || result.size > 5 * 1024 * 1024) throw new Error("照片压缩后仍超过 5 MiB"); return result;
}
