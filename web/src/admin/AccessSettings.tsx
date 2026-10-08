import { useEffect, useState } from "react";
import { accessRequest, notifyAccessChanged, type AccessStatus } from "../access";

export function orderingSettingsPayload(enabled: boolean, required: boolean, password: string, confirmation: string, configured: boolean) {
  if (password && (password.length < 8 || password.length > 128)) throw new Error("点餐密码须为 8–128 个字符。");
  if (password !== confirmation) throw new Error("两次输入的点餐密码不一致。");
  if (enabled && required && !configured && !password) throw new Error("请先设置点餐密码，再开放需要密码的点餐页面。");
  return { ordering_enabled: enabled, ordering_password_required: required, ...(password ? { ordering_password: password } : {}) };
}

export default function AccessSettings() {
  const [settings, setSettings] = useState<AccessStatus>();
  const [enabled, setEnabled] = useState(true);
  const [required, setRequired] = useState(true);
  const [orderingPassword, setOrderingPassword] = useState("");
  const [orderingConfirmation, setOrderingConfirmation] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [adminConfirmation, setAdminConfirmation] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void accessRequest<AccessStatus>("/settings", { signal: controller.signal }).then(value => { setSettings(value); setEnabled(value.ordering_enabled); setRequired(value.ordering_password_required); }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法读取访问设置。"); });
    return () => controller.abort();
  }, []);
  const saveOrdering = async (event: React.SubmitEvent<HTMLFormElement>) => {
    event.preventDefault(); setError(""); setNotice(""); setBusy("ordering");
    try {
      const payload = orderingSettingsPayload(enabled, required, orderingPassword, orderingConfirmation, settings?.ordering_password_configured || false);
      const value = await accessRequest<AccessStatus>("/settings", { method: "PUT", body: JSON.stringify(payload) });
      setSettings(value); setOrderingPassword(""); setOrderingConfirmation("");
      setNotice(!value.ordering_enabled ? "点餐页面已关闭。已打开的点餐设备也将停止访问。" : value.ordering_password_required ? "点餐页面已开启，访客需要输入点餐密码。" : "点餐页面已开启，访客无需输入密码。");
      // Let other open tabs revalidate through their normal status checks. This
      // administrator stays on the form to see the saved result.
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败，请重试。"); }
    finally { setBusy(""); }
  };
  const saveAdmin = async (event: React.SubmitEvent<HTMLFormElement>) => {
    event.preventDefault(); setError(""); setNotice("");
    if (newPassword !== adminConfirmation) { setError("两次输入的管理员新密码不一致。"); return; }
    setBusy("admin");
    try {
      await accessRequest("/admin/password", { method: "PUT", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
      setCurrentPassword(""); setNewPassword(""); setAdminConfirmation("");
      setNotice("管理员密码已更新，其他管理员登录已失效。");
      notifyAccessChanged();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "修改失败，请重试。"); }
    finally { setBusy(""); }
  };
  return <div>
    {error && <p className="adm-error" role="alert">{error}</p>}{notice && <p className="adm-notice" role="status">{notice}</p>}
    {!settings ? <p className="adm-muted">正在读取访问设置…</p> : <div className="adm-grid adm-settings-grid">
      <section className="adm-panel"><h2>点餐页面访问</h2><p className="adm-muted">点餐密码与管理员密码分别设置。默认需要密码；管理员登录后可直接进入已开启的点餐页面。</p>
        <form onSubmit={event => void saveOrdering(event)} className="adm-access-form">
          <label className="adm-check"><input type="checkbox" checked={enabled} disabled={!!busy} onChange={event => setEnabled(event.target.checked)} />开启点餐页面</label>
          <p className="adm-muted">关闭后，访客和已加入的设备均无法查看菜单或点餐。</p>
          <label className="adm-check"><input type="checkbox" checked={required} disabled={!!busy} onChange={event => setRequired(event.target.checked)} />点餐需要密码</label>
          {!required && <p className="adm-notice">保存后，能访问此网址的人都可以进入点餐页面。</p>}
          {required && !settings.ordering_password_configured && <p className="adm-notice">尚未设置点餐密码，访客暂时不能进入。</p>}
          <label className="adm-field">{settings.ordering_password_configured ? "更换点餐密码" : "设置点餐密码"}<input type="password" autoComplete="new-password" minLength={8} maxLength={128} value={orderingPassword} onChange={event => setOrderingPassword(event.target.value)} /><small className="adm-muted">8–128 个字符。{settings.ordering_password_configured ? "留空保留现有密码；更换后访客需要重新登录。" : "设置后再将密码告诉家人。"}</small></label>
          <label className="adm-field">再次输入点餐密码<input type="password" autoComplete="new-password" minLength={8} maxLength={128} value={orderingConfirmation} onChange={event => setOrderingConfirmation(event.target.value)} /></label>
          <button type="submit" className="adm-btn primary" disabled={!!busy}>{busy === "ordering" ? "保存中…" : "保存点餐访问设置"}</button>
        </form>
      </section>
      <section className="adm-panel"><h2>修改管理员密码</h2><p className="adm-muted">管理员密码保护菜谱管理、任务和服务器设置。修改后，其他管理员登录将失效。</p>
        <form onSubmit={event => void saveAdmin(event)} className="adm-access-form">
          <label className="adm-field">当前管理员密码<input type="password" autoComplete="current-password" required maxLength={128} value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} /></label>
          <label className="adm-field">管理员新密码<input type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={newPassword} onChange={event => setNewPassword(event.target.value)} /></label>
          <label className="adm-field">再次输入管理员新密码<input type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={adminConfirmation} onChange={event => setAdminConfirmation(event.target.value)} /></label>
          <button type="submit" className="adm-btn primary" disabled={!!busy}>{busy === "admin" ? "修改中…" : "修改管理员密码"}</button>
        </form>
      </section>
    </div>}
  </div>;
}
