import { useEffect, useState } from "react";
import { api, errorMessage, post } from "./api";
import type { PageProps } from "./types";
import AccessSettings from "./AccessSettings";
import "./admin-settings.css";

type Config = Record<string, string | boolean | number | null>;
type Settings = {
  config: Config; active_out_dir: string; data_root: string; restart_required: boolean;
  self_join_enabled: boolean; indexing: { status: string; last_success?: string; last_error?: string };
  branding: { has_logo: boolean; logo_url?: string };
};
type Device = { id: string; name: string; created_at: string; last_seen_at: string; revoked_at?: string };
type Check = { name: string; ok: boolean; detail: string; suggestion?: string };
export type Operation = {
  id: string; kind: string; status: string; total: number; processed: number; error?: string;
  result?: { url?: string; name?: string; batch_id?: string; added_count?: number; updated_count?: number };
};
const statuses: Record<string, string> = { queued: "等待开始", running: "进行中", completed: "已完成", completed_with_errors: "部分失败", failed: "失败", interrupted: "服务重启，待继续" };
const kinds: Record<string, string> = { menu_image: "生成菜单图片", recipe_archive: "批量归档菜谱", knowledge_extract: "提取视频知识", knowledge_document: "提取文档知识", deployment_export: "导出完整数据", handoff_export: "导出工作交接包", handoff_import: "导入工作交接包" };

export function OperationList({ items, navigate, refresh }: { items: Operation[]; navigate: PageProps["navigate"]; refresh: () => void }) {
  const [error, setError] = useState("");
  return <div className="adm-operation-list">
    {error && <p role="alert" className="adm-error">{error}</p>}
    {items.length === 0 ? <p className="adm-muted">还没有后台操作。</p> : items.map(item => <div className="adm-operation" key={item.id}>
      <div className="adm-actions"><strong>{kinds[item.kind] || item.kind}</strong><span className={`adm-badge ${item.status === "completed" ? "success" : item.status === "failed" ? "danger" : ""}`}>{statuses[item.status] || item.status}</span><span className="adm-muted">{item.processed} / {item.total}</span></div>
      <progress aria-label={`${kinds[item.kind]}进度`} value={item.processed} max={Math.max(item.total, 1)} />
      {item.error && <p className="adm-error">{item.error}</p>}
      {item.result?.batch_id && <p>导入批次：{item.result.batch_id}</p>}
      <div className="adm-actions">
        {item.result?.url && <a className="adm-btn primary small" href={item.result.url} download>{item.result.name || "下载结果"}</a>}
        <button className="adm-btn small" onClick={() => navigate(`/tasks?kind=settings&id=${encodeURIComponent(item.id)}`)}>查看任务</button>
        {["failed", "interrupted", "completed_with_errors"].includes(item.status) && <button className="adm-btn small" onClick={() => { setError(""); void post(`/settings/operations/${item.id}/resume`).then(refresh).catch(e => setError(errorMessage(e))); }}>继续 / 重试失败项</button>}
      </div>
    </div>)}
  </div>;
}

export function fileBase64(file: File, maximum: number): Promise<string> {
  if (file.size > maximum) return Promise.reject(new Error(`文件不能超过 ${Math.round(maximum / 1024 / 1024)} MiB`));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(new Error("读取文件失败"));
    reader.readAsDataURL(file);
  });
}

export default function SettingsPage({ navigate }: PageProps) {
  const [settings, setSettings] = useState<Settings>();
  const [config, setConfig] = useState<Config>({});
  const [tab, setTab] = useState("access");
  const [devices, setDevices] = useState<Device[]>([]);
  const [checks, setChecks] = useState<Check[]>([]);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [batches, setBatches] = useState<{ id: string; count: number }[]>([]);
  const [batch, setBatch] = useState("");
  const [migration, setMigration] = useState<{ plan: { moves: { source: string; target: string }[]; conflicts: unknown[] }; command: string; message: string }>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [dirty, setDirty] = useState(false);
  const refreshOperations = () => { void api<{ items: Operation[] }>("/settings/operations").then(data => setOperations(data.items)).catch(e => setError(errorMessage(e))); };
  useEffect(() => {
    const abort = new AbortController();
    void Promise.all([api<Settings>("/settings", { signal: abort.signal }), api<{ devices: Device[] }>("/settings/devices", { signal: abort.signal }), api<{ items: { id: string; count: number }[] }>("/settings/handoff/batches", { signal: abort.signal })]).then(([data, deviceData, batchData]) => { setSettings(data); setConfig(data.config); setDevices(deviceData.devices); setBatches(batchData.items); setBatch(batchData.items[0]?.id || ""); }).catch(e => { if (!abort.signal.aborted) setError(errorMessage(e)); });
    refreshOperations();
    const timer = window.setInterval(refreshOperations, 5000);
    return () => { abort.abort(); window.clearInterval(timer); };
  }, []);
  const run = async (key: string, action: () => Promise<void>) => { setBusy(key); setError(""); setNotice(""); try { await action(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(""); } };
  const change = (key: string, value: Config[string]) => { setConfig(previous => ({ ...previous, [key]: value })); setDirty(true); };
  const save = () => run("save", async () => { const value = await api<Settings>("/settings", { method: "PUT", body: JSON.stringify({ config }) }); setSettings(value); setConfig(value.config); setDirty(false); setNotice(value.restart_required ? "配置已保存。菜谱目录将在服务重启后切换；当前仍使用原菜谱库。" : "设置已保存。"); });
  const input = (key: string, label: string, options?: { type?: string; hint?: string; min?: number; max?: number }) => <label className="adm-field" key={key}>{label}<input aria-label={label} type={options?.type || "text"} value={String(config[key] ?? "")} min={options?.min} max={options?.max} onChange={e => change(key, options?.type === "number" ? Number(e.target.value) : ["cookies", "codex_model", "codex_profile", "local_llm_command"].includes(key) ? e.target.value || null : e.target.value)} />{options?.hint && <small className="adm-muted">{options.hint}</small>}</label>;
  const toggle = (key: string, label: string) => <label className="adm-check" key={key}><input type="checkbox" checked={config[key] === true} onChange={e => change(key, e.target.checked)} />{label}</label>;
  const select = (key: string, label: string, values: string[]) => <label className="adm-field" key={key}>{label}<select aria-label={label} value={String(config[key] || "")} onChange={e => change(key, e.target.value)}>{values.map(value => <option key={value}>{value}</option>)}</select></label>;
  return <div className="adm-page">
    <div className="adm-page-head"><div><span className="adm-eyebrow">管理厨房</span><h1>设置与数据</h1><p className="adm-muted">模型、设备和数据集中管理，日常使用更专注。</p></div><div className="adm-actions"><button className="adm-btn secondary" onClick={() => navigate("/menu")}>菜单与同步管理</button><button className="adm-btn secondary" onClick={() => navigate("/knowledge")}>厨艺知识库</button><button className="adm-btn primary" disabled={!dirty || !!busy} onClick={() => void save()}>{busy === "save" ? "保存中…" : "保存设置"}</button></div></div>
    {error && <p role="alert" className="adm-error">{error}</p>}{notice && <p role="status" className="adm-notice">{notice}</p>}
    {!settings ? <div className="adm-empty">正在读取设置…</div> : <>
      {settings.restart_required && <p className="adm-notice">新菜谱目录已保存，等待服务重启。当前菜谱库：{settings.active_out_dir}</p>}
      <nav className="adm-tabs" aria-label="设置分类">{[["access", "访问与密码"], ["general", "厨房与生成"], ["models", "模型与登录"], ["devices", "设备管理"], ["data", "数据与交接"], ["environment", "环境检查"]].map(([value, label]) => <button key={value} className={`adm-btn ${tab === value ? "primary" : "secondary"}`} aria-pressed={tab === value} onClick={() => setTab(value)}>{label}</button>)}</nav>
      {tab === "access" && <AccessSettings />}
      {tab === "general" && <div className="adm-grid adm-settings-grid"><section className="adm-panel"><h2>厨房品牌</h2><div className="adm-form-grid">{input("restaurant_name", "餐厅名称")}{input("restaurant_subtitle", "副标题")}</div><div className="adm-logo-tools">{settings.branding.has_logo && <img src={`/api/v1/branding/logo?v=${notice}`} alt="当前餐厅 Logo" />}<label className="adm-field">上传 Logo<input type="file" accept="image/png,image/jpeg,image/webp" disabled={!!busy || dirty} onChange={e => { const file = e.target.files?.[0]; if (file) void run("logo", async () => { const data = await post<Settings>("/settings/logo", { content: await fileBase64(file, 5 * 1024 * 1024) }); setSettings(data); setConfig(data.config); setNotice("Logo 已更新。"); }); }} /><small className="adm-muted">PNG、JPG、WebP，最大 5 MiB。请先保存表单修改。</small></label><button className="adm-btn danger small" disabled={!!busy || dirty || !settings.branding.has_logo} onClick={() => void run("logo", async () => { const data = await api<Settings>("/settings/logo", { method: "DELETE" }); setSettings(data); setConfig(data.config); setNotice("Logo 已移除。"); })}>移除 Logo</button></div></section>
        <section className="adm-panel"><h2>生成偏好</h2><div className="adm-form-grid">{input("language", "字幕语言")}{select("whisper_model", "转写模型", ["tiny", "base", "small", "medium", "large-v3"])}{input("max_recipe_steps", "最多关键步骤", { type: "number", min: 4, max: 12 })}{input("max_step_images", "最多步骤图片", { type: "number", min: 1, max: 4 })}</div><div className="adm-check-list">{toggle("enable_screenshot", "生成步骤截图")}{toggle("enable_llm_summary", "使用文本模型整理菜谱")}{toggle("keep_media", "保留临时音视频")}{toggle("enable_recipe_review", "生成旧版逐项审核文件")}</div></section>
        <section className="adm-panel"><h2>归档</h2>{input("obsidian_vault_dir", "Obsidian 知识库目录")}<div className="adm-check-list">{toggle("auto_archive_after_generation", "生成后自动归档")}{toggle("archive_knowledge_with_recipe", "同时归档已确认的相关知识")}</div></section></div>}
      {tab === "models" && <div className="adm-grid adm-settings-grid"><section className="adm-panel"><h2>文本模型</h2><p className="adm-muted">用于菜谱生成、审核、介绍和知识提取。AI 选图在任务中使用独立的本地视觉模型。</p><div className="adm-form-grid">{select("llm_provider", "模型提供方", ["opencode", "codex", "openai", "local", "none"])}{input("openai_model", "OpenAI 模型")}{input("codex_model", "Codex 模型（可选）")}{input("codex_profile", "Codex 配置档（可选）")}</div>{input("local_llm_command", "本地模型命令（可选）")}<label className="adm-field">附加提示词<textarea rows={6} value={String(config.llm_cli_extra_instructions || "")} onChange={e => change("llm_cli_extra_instructions", e.target.value || null)} /></label></section>
        <section className="adm-panel"><h2>Bilibili 登录</h2>{input("cookies", "服务器 Cookie 文件路径（可选）")}<p className="adm-muted">登录操作使用已保存的配置。页面不会显示 Cookie 内容。</p><div className="adm-actions"><button className="adm-btn" disabled={!!busy || dirty} onClick={() => void run("cookies", async () => { const result = await post<{ valid: boolean }>("/settings/cookies/validate"); setNotice(result.valid ? "Bilibili 登录有效。" : "当前登录不可用，请导入或更新登录文件。"); })}>验证登录</button><button className="adm-btn" disabled={!!busy || dirty} onClick={() => void run("cookies", async () => { await post("/settings/cookies/import"); const data = await api<Settings>("/settings"); setSettings(data); setConfig(data.config); setNotice("已从服务器的 Edge 导入登录。"); })}>从服务器 Edge 导入</button><button className="adm-btn danger" disabled={!!busy || dirty} onClick={() => { if (window.confirm("清除服务器导入的 Bilibili 登录文件？")) void run("cookies", async () => { await post("/settings/cookies/clear"); const data = await api<Settings>("/settings"); setSettings(data); setConfig(data.config); setNotice("登录已清除。"); }); }}>清除登录</button></div></section></div>}
      {tab === "devices" && <section className="adm-panel"><div className="adm-page-head"><div><h2>家庭设备</h2><p className="adm-muted">已加入的设备可同步菜谱、心得和本餐点菜。</p></div><label className="adm-check"><input type="checkbox" checked={settings.self_join_enabled} disabled={!!busy} onChange={e => { const enabled = e.target.checked; void run("devices", async () => { const result = await api<{ devices: Device[]; self_join_enabled: boolean }>("/settings/devices/self-join", { method: "PUT", body: JSON.stringify({ enabled }) }); setDevices(result.devices); setSettings({ ...settings, self_join_enabled: result.self_join_enabled }); setNotice(enabled ? "已允许新设备自行加入。" : "已关闭新设备加入。"); }); }} />允许新设备自行加入</label></div><div className="adm-table-wrap"><table className="adm-table"><thead><tr><th>设备</th><th>最近使用</th><th>状态</th><th>操作</th></tr></thead><tbody>{devices.map(device => <tr key={device.id}><td>{device.name}</td><td>{device.last_seen_at ? new Date(device.last_seen_at).toLocaleString() : "—"}</td><td>{device.revoked_at ? "已撤销" : "已授权"}</td><td><button className="adm-btn danger small" disabled={!!busy || !!device.revoked_at} onClick={() => { if (window.confirm(`撤销“${device.name}”的访问权限？`)) void run("devices", async () => { const result = await api<{ devices: Device[] }>(`/settings/devices/${encodeURIComponent(device.id)}`, { method: "DELETE" }); setDevices(result.devices); setNotice("设备授权已撤销。"); }); }}>撤销</button></td></tr>)}</tbody></table></div>{!devices.length && <p className="adm-empty">还没有家庭设备加入。</p>}</section>}
      {tab === "data" && <div className="adm-grid adm-settings-grid"><section className="adm-panel"><h2>数据位置</h2><p className="adm-muted">数据根目录</p><code className="adm-wrap">{settings.data_root}</code><p className="adm-muted">当前服务的菜谱目录</p><code className="adm-wrap">{settings.active_out_dir}</code>{input("out_dir", "下次启动的菜谱目录", { hint: "修改并保存后需重启服务；当前任务与索引继续使用原目录。" })}<p>索引状态：<span className="adm-badge">{settings.indexing.status}</span></p>{settings.indexing.last_error && <p className="adm-error">{settings.indexing.last_error}</p>}<button className="adm-btn" disabled={!!busy} onClick={() => void run("migration", async () => { setMigration(await api("/settings/migration")); })}>预览旧数据迁移</button>{migration && <div className="adm-notice"><p>{migration.plan.moves.length} 个迁移项目，{migration.plan.conflicts.length} 个冲突。</p><p>{migration.message}</p><code className="adm-wrap">{migration.command}</code><details><summary>查看迁移清单</summary>{migration.plan.moves.map((move, index) => <p key={index}>{move.source}<br />→ {move.target}</p>)}</details></div>}</section>
        <section className="adm-panel"><h2>导出与工作交接</h2><p className="adm-muted">完整数据包含菜谱、UP 主清单、知识库和私人数据库。工作交接包用于在另一台机器继续某个批次。</p><button className="adm-btn primary" disabled={!!busy} onClick={() => void run("export", async () => { await post("/settings/export"); refreshOperations(); setNotice("完整数据导出已进入后台，完成后可在下方下载。"); })}>导出完整数据</button><hr /><label className="adm-field">选择交接批次<select value={batch} onChange={e => setBatch(e.target.value)}><option value="">请选择批次</option>{batches.map(item => <option key={item.id} value={item.id}>{item.id} · {item.count} 条</option>)}</select></label><button className="adm-btn" disabled={!!busy || !batch} onClick={() => void run("handoff", async () => { await post("/settings/handoff/export", { batch_id: batch }); refreshOperations(); setNotice("交接包导出已进入后台。"); })}>导出所选批次</button><hr /><label className="adm-field">导入工作交接包<input type="file" accept=".zip" disabled={!!busy} onChange={e => { const file = e.target.files?.[0]; if (!file) return; if (file.size > 256 * 1024 * 1024) { setError("交接包不能超过 256 MiB"); return; } if (window.confirm(`将“${file.name}”导入当前菜谱库？已有文件由交接服务检查冲突和备份。`)) void run("import", async () => { await api("/settings/handoff/import", { method: "POST", headers: { "Content-Type": "application/zip" }, body: file }); refreshOperations(); setNotice("已上传，正在后台校验并导入交接包。"); }); }} /><small className="adm-muted">仅支持本工具导出的 .handoff.zip，最大 256 MiB。</small></label></section>
        <section className="adm-panel adm-span-all"><h2>后台数据操作</h2><p className="adm-muted">关闭页面后继续执行。服务重启后，可继续中断操作。</p><OperationList items={operations.filter(item => ["deployment_export", "handoff_export", "handoff_import"].includes(item.kind))} navigate={navigate} refresh={refreshOperations} /></section></div>}
      {tab === "environment" && <section className="adm-panel"><div className="adm-page-head"><div><h2>运行环境</h2><p className="adm-muted">检查下载、转写、模型命令和媒体工具是否可用。</p></div><button className="adm-btn primary" disabled={!!busy} onClick={() => void run("environment", async () => { const result = await post<{ checks: Check[] }>("/settings/environment"); setChecks(result.checks); setNotice("环境检查完成。"); })}>{busy === "environment" ? "检查中…" : "开始检查"}</button></div>{checks.map(check => <div className="adm-environment-check" key={check.name}><div className="adm-actions"><strong>{check.name}</strong><span className={`adm-badge ${check.ok ? "success" : "warning"}`}>{check.ok ? "可用" : "需处理"}</span></div><p className="adm-wrap">{check.detail}</p>{!check.ok && check.suggestion && <code className="adm-wrap">{check.suggestion}</code>}</div>)}</section>}
    </>}
  </div>;
}
