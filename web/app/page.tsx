"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { streamLibraryPackage } from "./library-import";
import styles from "./page.module.css";

type Ingredient = { name?: string; amount?: string; note?: string };
type Step = {
  title?: string;
  action?: string;
  heat?: string;
  duration?: string;
  tips?: string;
  image_path?: string;
};
type Recipe = {
  id: string;
  title: string;
  category?: string;
  cuisine?: string;
  servings?: string;
  total_time?: string;
  tags?: string[];
  ingredients?: Ingredient[];
  seasonings?: Ingredient[];
  steps?: Step[];
  summary_tips?: string[];
};
type MealItem = {
  recipeId: string;
  multiplier: number;
  note: string;
  completed: boolean;
};
type PracticeLog = {
  id: string;
  recipeId: string;
  cookedOn: string;
  notes: string;
  rating?: number;
};
type Backup = {
  schema_version: 1;
  exported_at: string;
  recipes: Recipe[];
  assets?: Record<string, string>;
  meal?: MealItem[];
  practice_logs?: PracticeLog[];
};
type View = "recipes" | "meal" | "settings";

const DB_NAME = "bili-recipe-pwa";
const DB_VERSION = 2;
const ASSET_STORE = "assets";
let databasePromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains("state")) db.createObjectStore("state");
      if (!db.objectStoreNames.contains(ASSET_STORE)) db.createObjectStore(ASSET_STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => {
      databasePromise = null;
      reject(request.error);
    };
  });
  return databasePromise;
}

async function readState<T>(key: string, fallback: T): Promise<T> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const request = db.transaction("state").objectStore("state").get(key);
    request.onsuccess = () => resolve((request.result as T | undefined) ?? fallback);
    request.onerror = () => reject(request.error);
  });
}

async function writeState(key: string, value: unknown): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction("state", "readwrite");
    transaction.objectStore("state").put(value, key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

async function deleteState(key: string): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction("state", "readwrite");
    transaction.objectStore("state").delete(key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

async function writeAssetBatch(entries: [string, Blob][]): Promise<void> {
  if (!entries.length) return;
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(ASSET_STORE, "readwrite");
    const store = transaction.objectStore(ASSET_STORE);
    entries.forEach(([key, value]) => store.put(value, key));
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

async function readAsset(key: string): Promise<Blob | null> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const request = db.transaction(ASSET_STORE).objectStore(ASSET_STORE).get(key);
    request.onsuccess = () => resolve(request.result instanceof Blob ? request.result : null);
    request.onerror = () => reject(request.error);
  });
}

async function clearAssets(): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(ASSET_STORE, "readwrite");
    transaction.objectStore(ASSET_STORE).clear();
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

function dataUrlToBlob(value: string): Blob {
  const match = value.match(/^data:([^;,]+)?(;base64)?,([\s\S]*)$/);
  if (!match) throw new Error("菜谱包中包含无法识别的图片");
  const mimeType = match[1] || "application/octet-stream";
  if (!match[2]) return new Blob([decodeURIComponent(match[3])], { type: mimeType });
  const decoded = atob(match[3]);
  const bytes = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index += 1) bytes[index] = decoded.charCodeAt(index);
  return new Blob([bytes], { type: mimeType });
}

async function migrateLegacyAssets(): Promise<void> {
  const legacy = await readState<Record<string, string>>("assets", {});
  const entries = Object.entries(legacy);
  for (let offset = 0; offset < entries.length; offset += 40) {
    await writeAssetBatch(
      entries.slice(offset, offset + 40).map(([key, value]) => [key, dataUrlToBlob(value)]),
    );
  }
  if (entries.length) await deleteState("assets");
}

async function writeAssetEntries(entries: [string, Blob][]): Promise<void> {
  for (let offset = 0; offset < entries.length; offset += 40) {
    await writeAssetBatch(entries.slice(offset, offset + 40));
  }
}

async function assetKeys(): Promise<string[]> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const request = db.transaction(ASSET_STORE).objectStore(ASSET_STORE).getAllKeys();
    request.onsuccess = () => resolve(request.result.map(String));
    request.onerror = () => reject(request.error);
  });
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

async function exportAssets(): Promise<Record<string, string>> {
  const exported: Record<string, string> = {};
  for (const key of await assetKeys()) {
    const blob = await readAsset(key);
    if (blob) exported[key] = await blobToDataUrl(blob);
  }
  return exported;
}

const sampleRecipe: Recipe = {
  id: "sample-tomato-eggs",
  title: "番茄炒蛋",
  category: "家常菜",
  cuisine: "中式",
  servings: "2 人份",
  total_time: "15 分钟",
  tags: ["快手", "下饭"],
  ingredients: [
    { name: "番茄", amount: "400克" },
    { name: "鸡蛋", amount: "3个" },
  ],
  seasonings: [
    { name: "盐", amount: "适量" },
    { name: "食用油", amount: "20毫升" },
  ],
  steps: [
    { title: "准备食材", action: "番茄切块，鸡蛋打散并加少许盐。", duration: "3 分钟" },
    { title: "炒鸡蛋", action: "热锅加油，倒入蛋液快速推炒，凝固后盛出。", heat: "中大火", duration: "2 分钟" },
    { title: "炒番茄", action: "原锅下番茄炒软出汁，加盐调味。", heat: "中火", duration: "4 分钟" },
    { title: "合炒", action: "鸡蛋回锅翻匀，关火装盘。", heat: "中火", duration: "1 分钟" },
  ],
  summary_tips: ["番茄炒出汁后再放鸡蛋，口感更嫩。"],
};

export default function Home() {
  const [ready, setReady] = useState(false);
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [meal, setMeal] = useState<MealItem[]>([]);
  const [logs, setLogs] = useState<PracticeLog[]>([]);
  const [view, setView] = useState<View>("recipes");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Recipe | null>(null);
  const [cooking, setCooking] = useState<{ mealIndex: number; stepIndex: number } | null>(null);
  const [notice, setNotice] = useState("");
  const [online, setOnline] = useState(() => typeof navigator === "undefined" || navigator.onLine);
  const [installPrompt, setInstallPrompt] = useState<Event | null>(null);
  const [assetRevision, setAssetRevision] = useState(0);
  const [importState, setImportState] = useState<{
    kind: "working" | "error";
    message: string;
    progress?: number;
  } | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const restoreRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    Promise.all([
      readState<Recipe[]>("recipes", []),
      readState<MealItem[]>("meal", []),
      readState<PracticeLog[]>("practice_logs", []),
    ]).then(async ([savedRecipes, savedMeal, savedLogs]) => {
      setRecipes(savedRecipes);
      setMeal(savedMeal);
      setLogs(savedLogs);
      try {
        await migrateLegacyAssets();
      } catch (error) {
        setImportState({
          kind: "error",
          message: `旧版图片迁移失败：${error instanceof Error ? error.message : "请重新导入菜谱库"}`,
        });
      }
      setReady(true);
    }).catch((error) => {
      setImportState({
        kind: "error",
        message: `本地菜谱库打开失败：${error instanceof Error ? error.message : "请刷新页面重试"}`,
      });
      setReady(true);
    });
    const status = () => setOnline(navigator.onLine);
    const prompt = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event);
    };
    window.addEventListener("online", status);
    window.addEventListener("offline", status);
    window.addEventListener("beforeinstallprompt", prompt);
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
    return () => {
      window.removeEventListener("online", status);
      window.removeEventListener("offline", status);
      window.removeEventListener("beforeinstallprompt", prompt);
    };
  }, []);

  useEffect(() => {
    if (ready) void writeState("recipes", recipes);
  }, [ready, recipes]);
  useEffect(() => {
    if (ready) void writeState("meal", meal);
  }, [ready, meal]);
  useEffect(() => {
    if (ready) void writeState("practice_logs", logs);
  }, [ready, logs]);

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return recipes;
    return recipes.filter((recipe) =>
      JSON.stringify(recipe).toLowerCase().includes(term),
    );
  }, [query, recipes]);

  const mealRecipes = meal
    .map((item) => ({ item, recipe: recipes.find((recipe) => recipe.id === item.recipeId) }))
    .filter((value): value is { item: MealItem; recipe: Recipe } => Boolean(value.recipe));
  const shopping = buildShoppingList(mealRecipes);

  function flash(message: string) {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 2600);
  }

  function addToMeal(recipe: Recipe) {
    if (meal.some((item) => item.recipeId === recipe.id)) {
      flash("这道菜已经在本餐中");
      return;
    }
    setMeal((current) => [
      ...current,
      { recipeId: recipe.id, multiplier: 1, note: "", completed: false },
    ]);
    flash(`已将“${recipe.title}”加入本餐`);
  }

  function updateMeal(recipeId: string, update: Partial<MealItem>) {
    setMeal((current) =>
      current.map((item) => (item.recipeId === recipeId ? { ...item, ...update } : item)),
    );
  }

  async function importPackage(file?: File) {
    if (!file) return;
    setImportState({
      kind: "working",
      message: file.size > 100 * 1024 * 1024
        ? `正在分段导入 ${formatBytes(file.size)}，请保持页面在前台…`
        : `正在导入 ${file.name}…`,
      progress: 0,
    });
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    const assetBatch: [string, Blob][] = [];
    try {
      const backup = await streamLibraryPackage(file, {
        onAsset: async (key, value) => {
          assetBatch.push([key, dataUrlToBlob(value)]);
          if (assetBatch.length >= 40) {
            await writeAssetBatch(assetBatch.splice(0));
          }
        },
        onProgress: (progress) => setImportState((current) => current?.kind === "working"
          ? { ...current, progress: Math.round(progress * 100) }
          : current),
      });
      await writeAssetBatch(assetBatch.splice(0));
      const merged = new Map(recipes.map((recipe) => [recipe.id, recipe]));
      backup.recipes.forEach((recipe) => {
        const typed = recipe as Recipe;
        merged.set(String(typed.id), normalizeRecipe(typed));
      });
      setRecipes([...merged.values()]);
      setAssetRevision((current) => current + 1);
      if (backup.practiceLogs.length) {
        setLogs((current) => {
          const combined = new Map(current.map((entry) => [entry.id, entry]));
          backup.practiceLogs.forEach((entry) => {
            const typed = entry as PracticeLog;
            combined.set(String(typed.id), typed);
          });
          return [...combined.values()];
        });
      }
      flash(`已导入 ${backup.recipes.length} 道菜谱，可离线使用`);
      setImportState(null);
      setView("recipes");
    } catch (error) {
      const message = error instanceof DOMException && error.name === "QuotaExceededError"
        ? "手机可用存储空间不足，无法保存全部图片。请释放 Safari 网站数据或改用较小的菜谱包。"
        : error instanceof Error ? error.message : "菜谱包导入失败";
      setImportState({ kind: "error", message: `导入失败：${message}` });
    }
  }

  async function restoreBackup(file?: File) {
    if (!file) return;
    try {
      const backup = JSON.parse(await file.text()) as Backup;
      if (backup.schema_version !== 1 || !Array.isArray(backup.recipes)) throw new Error();
      setRecipes(backup.recipes.map(normalizeRecipe));
      await clearAssets();
      await writeAssetEntries(
        Object.entries(backup.assets ?? {}).map(([key, value]) => [key, dataUrlToBlob(value)]),
      );
      setAssetRevision((current) => current + 1);
      setMeal(backup.meal ?? []);
      setLogs(backup.practice_logs ?? []);
      flash("备份已完整恢复");
    } catch {
      flash("备份文件无法识别");
    }
  }

  async function exportBackup() {
    setImportState({ kind: "working", message: "正在准备完整备份…" });
    try {
      downloadJson(`bili-recipe-backup-${dateKey()}.json`, {
        schema_version: 1,
        exported_at: new Date().toISOString(),
        recipes,
        assets: await exportAssets(),
        meal,
        practice_logs: logs,
      });
      setImportState(null);
      flash("完整备份已生成");
    } catch (error) {
      setImportState({
        kind: "error",
        message: `备份生成失败：${error instanceof Error ? error.message : "浏览器可用内存不足"}`,
      });
    }
  }

  function addSample() {
    if (recipes.some((recipe) => recipe.id === sampleRecipe.id)) {
      flash("示例菜谱已经存在");
      return;
    }
    setRecipes((current) => [...current, sampleRecipe]);
    flash("已加入示例菜谱");
  }

  function clearLibrary() {
    if (!window.confirm("确定清空手机中的菜谱、点菜单和心得吗？请先导出备份。")) return;
    setRecipes([]);
    void clearAssets().then(() => setAssetRevision((current) => current + 1));
    setMeal([]);
    setLogs([]);
    setSelected(null);
    flash("本地数据已清空");
  }

  async function install() {
    const prompt = installPrompt as (Event & { prompt?: () => Promise<void> }) | null;
    if (prompt?.prompt) {
      await prompt.prompt();
      setInstallPrompt(null);
    } else {
      flash("iPhone：点 Safari 分享按钮，再选“添加到主屏幕”");
    }
  }

  if (!ready) return <main className={styles.loading}>正在打开离线菜谱库…</main>;

  if (cooking) {
    const current = mealRecipes[cooking.mealIndex];
    if (current) {
      const steps = current.recipe.steps ?? [];
      const step = steps[cooking.stepIndex];
      return (
        <CookingView
          recipe={current.recipe}
          item={current.item}
          step={step}
          stepIndex={cooking.stepIndex}
          imageKey={step?.image_path}
          assetRevision={assetRevision}
          onBack={() => setCooking(null)}
          onPrevious={() => setCooking({ ...cooking, stepIndex: cooking.stepIndex - 1 })}
          onNext={() => {
            if (cooking.stepIndex < steps.length - 1) {
              setCooking({ ...cooking, stepIndex: cooking.stepIndex + 1 });
              return;
            }
            updateMeal(current.recipe.id, { completed: true });
            const next = mealRecipes.findIndex(
              (entry, index) => index > cooking.mealIndex && !entry.item.completed,
            );
            if (next >= 0) setCooking({ mealIndex: next, stepIndex: 0 });
            else {
              setCooking(null);
              flash("本餐全部完成，开饭吧！");
            }
          }}
        />
      );
    }
  }

  return (
    <main className={styles.appShell}>
      <header className={styles.header}>
        <div>
          <span className={styles.eyebrow}>BILI RECIPE · OFFLINE</span>
          <h1>{view === "recipes" ? "我的菜谱" : view === "meal" ? "本餐点菜" : "离线与备份"}</h1>
        </div>
        <span className={`${styles.status} ${online ? styles.online : styles.offline}`}>
          {online ? "在线" : "离线可用"}
        </span>
      </header>

      {view === "recipes" && (
        <section className={styles.content}>
          {recipes.length === 0 ? (
            <EmptyLibrary onImport={() => importRef.current?.click()} onSample={addSample} />
          ) : (
            <>
              <label className={styles.search}>
                <span>⌕</span>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索菜名、食材、步骤…"
                  aria-label="搜索菜谱"
                />
              </label>
              <div className={styles.librarySummary}>
                <span>本机保存 {recipes.length} 道菜</span>
                <button className={styles.textButton} onClick={() => importRef.current?.click()}>
                  导入更新
                </button>
              </div>
              <div className={styles.recipeGrid}>
                {filtered.map((recipe, index) => (
                  <article className={styles.recipeCard} key={recipe.id}>
                    <button className={styles.cardMain} onClick={() => setSelected(recipe)}>
                      <span className={styles.cardNumber}>{String(index + 1).padStart(2, "0")}</span>
                      <div>
                        <h2>{recipe.title}</h2>
                        <p>{[recipe.category, recipe.cuisine, recipe.total_time].filter(Boolean).join(" · ")}</p>
                        <div className={styles.tags}>
                          {(recipe.tags ?? []).slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}
                        </div>
                      </div>
                    </button>
                    <button
                      className={styles.addButton}
                      onClick={() => addToMeal(recipe)}
                      disabled={meal.some((item) => item.recipeId === recipe.id)}
                      aria-label={`将${recipe.title}加入本餐`}
                    >
                      {meal.some((item) => item.recipeId === recipe.id) ? "✓" : "+"}
                    </button>
                  </article>
                ))}
              </div>
            </>
          )}
        </section>
      )}

      {view === "meal" && (
        <section className={styles.content}>
          {mealRecipes.length === 0 ? (
            <div className={styles.emptyState}>
              <span className={styles.emptyIcon}>⌑</span>
              <h2>本餐还没有菜</h2>
              <p>从菜谱中挑几道想吃的，采购清单会自动汇总。</p>
              <button className={styles.primaryButton} onClick={() => setView("recipes")}>去点菜</button>
            </div>
          ) : (
            <>
              <div className={styles.mealHero}>
                <div><span>今日菜单</span><strong>{mealRecipes.length} 道菜</strong></div>
                <button onClick={() => window.confirm("清空本餐？") && setMeal([])}>清空</button>
              </div>
              <div className={styles.mealList}>
                {mealRecipes.map(({ item, recipe }) => (
                  <article className={styles.mealCard} key={recipe.id}>
                    <button
                      className={`${styles.check} ${item.completed ? styles.checked : ""}`}
                      onClick={() => updateMeal(recipe.id, { completed: !item.completed })}
                    >{item.completed ? "✓" : ""}</button>
                    <div className={styles.mealInfo}>
                      <h2>{recipe.title}</h2>
                      <input
                        value={item.note}
                        onChange={(event) => updateMeal(recipe.id, { note: event.target.value })}
                        placeholder="口味备注：少辣、不要香菜…"
                        aria-label={`${recipe.title}备注`}
                      />
                      <div className={styles.portionRow}>
                        <span>份量</span>
                        <button onClick={() => updateMeal(recipe.id, { multiplier: Math.max(.5, item.multiplier - .5) })}>−</button>
                        <strong>{item.multiplier.toFixed(1)}×</strong>
                        <button onClick={() => updateMeal(recipe.id, { multiplier: Math.min(4, item.multiplier + .5) })}>＋</button>
                      </div>
                    </div>
                    <button className={styles.remove} onClick={() => setMeal((current) => current.filter((entry) => entry.recipeId !== recipe.id))}>×</button>
                  </article>
                ))}
              </div>
              <section className={styles.shoppingSection}>
                <div className={styles.sectionHeading}><div><span>SHOPPING LIST</span><h2>采购清单</h2></div><small>{shopping.length} 项</small></div>
                <div className={styles.shoppingList}>
                  {shopping.map((entry) => (
                    <label key={entry.name}>
                      <input type="checkbox" />
                      <span><strong>{entry.name}</strong><small>{entry.sources.join("、")}</small></span>
                      <b>{entry.amount}</b>
                    </label>
                  ))}
                </div>
              </section>
              <button
                className={styles.primaryButton}
                onClick={() => {
                  const index = mealRecipes.findIndex(({ item }) => !item.completed);
                  setCooking({ mealIndex: index >= 0 ? index : 0, stepIndex: 0 });
                }}
              >开始做饭</button>
            </>
          )}
        </section>
      )}

      {view === "settings" && (
        <section className={styles.content}>
          <div className={styles.settingsIntro}>
            <span>PRIVATE BY DEFAULT</span>
            <h2>菜谱只保存在这台设备</h2>
            <p>网页外壳可自动更新，你导入的菜谱、点菜单和实践记录不会上传服务器。</p>
          </div>
          <div className={styles.actionList}>
            <button onClick={install}><span>▣</span><div><strong>添加到主屏幕</strong><small>像普通 App 一样全屏打开</small></div><b>›</b></button>
            <button onClick={() => importRef.current?.click()}><span>⇩</span><div><strong>导入菜谱库</strong><small>从 Mac 生成的 JSON 包更新</small></div><b>›</b></button>
            <button onClick={exportBackup} disabled={!recipes.length}><span>⇧</span><div><strong>导出完整备份</strong><small>包含点菜单和实践记录</small></div><b>›</b></button>
            <button onClick={() => restoreRef.current?.click()}><span>↻</span><div><strong>恢复完整备份</strong><small>覆盖这台设备中的本地数据</small></div><b>›</b></button>
          </div>
          <div className={styles.storageCard}>
            <span>本机内容</span>
            <strong>{recipes.length} 道菜 · {meal.length} 道本餐菜品 · {logs.length} 条心得</strong>
          </div>
          <button className={styles.dangerButton} onClick={clearLibrary}>清空本机数据</button>
        </section>
      )}

      <nav className={styles.nav}>
        <button className={view === "recipes" ? styles.active : ""} onClick={() => setView("recipes")}><span>⌂</span>菜谱</button>
        <button className={view === "meal" ? styles.active : ""} onClick={() => setView("meal")}><span className={styles.badgeWrap}>⌑{meal.length > 0 && <b>{meal.length}</b>}</span>本餐</button>
        <button className={view === "settings" ? styles.active : ""} onClick={() => setView("settings")}><span>⚙</span>设置</button>
      </nav>

      <input ref={importRef} hidden type="file" accept="application/json,.json" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; void importPackage(file); }} />
      <input ref={restoreRef} hidden type="file" accept="application/json,.json" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; void restoreBackup(file); }} />
      {selected && <RecipeDetail recipe={selected} assetRevision={assetRevision} inMeal={meal.some((item) => item.recipeId === selected.id)} onClose={() => setSelected(null)} onAdd={() => addToMeal(selected)} />}
      {importState && <div className={styles.importPanel} role={importState.kind === "error" ? "alert" : "status"}>
        <strong>{importState.kind === "working" ? "正在导入菜谱库" : "菜谱库未导入"}</strong>
        <p>{importState.message}</p>
        {importState.kind === "working" && <div className={styles.importProgress}><i style={{ width: `${importState.progress ?? 0}%` }} /></div>}
        {importState.kind === "error" && <button onClick={() => setImportState(null)}>知道了</button>}
      </div>}
      {notice && <div className={styles.toast}>{notice}</div>}
    </main>
  );
}

function EmptyLibrary({ onImport, onSample }: { onImport: () => void; onSample: () => void }) {
  return <div className={styles.emptyState}>
    <div className={styles.emptyArtwork}><span>食</span></div>
    <span className={styles.eyebrow}>YOUR PRIVATE COOKBOOK</span>
    <h2>把菜谱装进口袋</h2>
    <p>从 Mac 导入一次，之后断网、关电脑也能查看、点菜和做饭。</p>
    <button className={styles.primaryButton} onClick={onImport}>导入菜谱库</button>
    <button className={styles.secondaryButton} onClick={onSample}>先看看示例</button>
  </div>;
}

function RecipeDetail({ recipe, assetRevision, inMeal, onClose, onAdd }: { recipe: Recipe; assetRevision: number; inMeal: boolean; onClose: () => void; onAdd: () => void }) {
  return <div className={styles.modalBackdrop}>
    <button className={styles.modalDismiss} onClick={onClose} aria-label="关闭菜谱详情" />
    <article className={styles.detailSheet}>
      <div className={styles.sheetHandle} />
      <button className={styles.closeButton} onClick={onClose}>×</button>
      <span className={styles.eyebrow}>{recipe.category ?? "我的菜谱"}</span>
      <h2>{recipe.title}</h2>
      <p className={styles.meta}>{[recipe.servings, recipe.total_time, recipe.cuisine].filter(Boolean).join(" · ")}</p>
      <button className={styles.primaryButton} onClick={onAdd} disabled={inMeal}>{inMeal ? "已加入本餐" : "加入本餐"}</button>
      <h3>食材与调料</h3>
      <div className={styles.ingredientList}>{[...(recipe.ingredients ?? []), ...(recipe.seasonings ?? [])].map((item, index) => <div key={`${item.name}-${index}`}><span>{item.name}</span><b>{item.amount}</b><small>{item.note}</small></div>)}</div>
      <h3>步骤</h3>
      <div className={styles.stepList}>{(recipe.steps ?? []).map((step, index) => <section key={`${step.title}-${index}`}>{step.image_path && <AssetImage assetKey={step.image_path} revision={assetRevision} alt={step.title ?? `步骤${index + 1}`} />}<span>{String(index + 1).padStart(2, "0")}</span><div><h4>{step.title}</h4><p>{step.action}</p><small>{[step.heat, step.duration].filter(Boolean).join(" · ")}</small></div></section>)}</div>
    </article>
  </div>;
}

function CookingView({ recipe, item, step, stepIndex, imageKey, assetRevision, onBack, onPrevious, onNext }: { recipe: Recipe; item: MealItem; step?: Step; stepIndex: number; imageKey?: string; assetRevision: number; onBack: () => void; onPrevious: () => void; onNext: () => void }) {
  const steps = recipe.steps ?? [];
  if (!step) return <main className={styles.cooking}><button className={styles.backButton} onClick={onBack}>‹ 返回本餐</button><h1>{recipe.title}</h1><p>这道菜没有分步内容。</p><button className={styles.primaryButton} onClick={onNext}>标记完成</button></main>;
  return <main className={styles.cooking}>
    <header><button className={styles.backButton} onClick={onBack}>‹ 本餐</button><span>{stepIndex + 1} / {steps.length}</span></header>
    <div className={styles.progress}><i style={{ width: `${((stepIndex + 1) / steps.length) * 100}%` }} /></div>
    <span className={styles.eyebrow}>{recipe.title} · {item.multiplier.toFixed(1)}×</span>
    {imageKey && <AssetImage className={styles.cookingImage} assetKey={imageKey} revision={assetRevision} alt={step.title ?? "烹饪步骤"} />}
    <article className={styles.cookingCard}><span>STEP {String(stepIndex + 1).padStart(2, "0")}</span><h1>{step.title}</h1><p>{step.action}</p>{(step.heat || step.duration) && <div className={styles.cookingMeta}>{step.heat && <b>火候<br /><strong>{step.heat}</strong></b>}{step.duration && <b>时间<br /><strong>{step.duration}</strong></b>}</div>}{step.tips && <aside>提示：{step.tips}</aside>}</article>
    <footer><button onClick={onPrevious} disabled={stepIndex === 0}>上一步</button><button onClick={onNext}>{stepIndex === steps.length - 1 ? "完成这道菜" : "下一步"}</button></footer>
  </main>;
}

function AssetImage({ assetKey, revision, alt, className }: { assetKey: string; revision: number; alt: string; className?: string }) {
  const [source, setSource] = useState("");
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    void readAsset(assetKey).then((blob) => {
      if (!active || !blob) return;
      objectUrl = URL.createObjectURL(blob);
      setSource(objectUrl);
    });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [assetKey, revision]);
  return source ? <img className={className} src={source} alt={alt} /> : null;
}

function normalizeRecipe(recipe: Recipe): Recipe {
  return { ...recipe, id: String(recipe.id), title: recipe.title || "未命名菜谱" };
}

function scaleAmount(amount: string, multiplier: number): string {
  if (multiplier === 1 || !amount || amount.includes("少许") || amount.includes("适量")) return amount;
  const matches = [...amount.matchAll(/\d+(?:\.\d+)?/g)];
  if (matches.length !== 1) return amount;
  const value = Number(matches[0][0]) * multiplier;
  return amount.replace(matches[0][0], Number.isInteger(value) ? String(value) : value.toFixed(1));
}

function buildShoppingList(entries: { item: MealItem; recipe: Recipe }[]) {
  type ShoppingGroup = { name: string; numeric: Map<string, number>; literal: string[]; sources: string[] };
  const grouped = new Map<string, ShoppingGroup>();
  entries.forEach(({ item, recipe }) => [...(recipe.ingredients ?? []), ...(recipe.seasonings ?? [])].forEach((ingredient) => {
    const name = ingredient.name?.trim();
    if (!name) return;
    const group: ShoppingGroup = grouped.get(name) ?? {
      name,
      numeric: new Map<string, number>(),
      literal: [],
      sources: [],
    };
    const amount = scaleAmount(ingredient.amount?.trim() ?? "", item.multiplier);
    const match = amount.match(/^\s*(\d+(?:\.\d+)?)\s*(.*?)\s*$/);
    if (match) group.numeric.set(match[2], (group.numeric.get(match[2]) ?? 0) + Number(match[1]));
    else if (amount && !group.literal.includes(amount)) group.literal.push(amount);
    if (!group.sources.includes(recipe.title)) group.sources.push(recipe.title);
    grouped.set(name, group);
  }));
  return [...grouped.values()].map((group) => ({
    name: group.name,
    amount: [...group.numeric.entries()].map(([unit, value]) => `${Number.isInteger(value) ? value : value.toFixed(1)}${unit}`).concat(group.literal).join(" + ") || "按需准备",
    sources: group.sources,
  })).sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
}

function downloadJson(filename: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function dateKey() { return new Date().toISOString().slice(0, 10); }
function formatBytes(value: number) { return `${(value / 1024 / 1024).toFixed(1)} MB`; }
