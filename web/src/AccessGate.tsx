import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { accessRequest, accessView, readAccessStatus, type AccessStatus, type AccessView } from "./access";
import "./access.css";

export function AccessScreen({ view, error, retry, onLogin }: { view: Exclude<AccessView, "allowed">; error: string; retry: () => void; onLogin: () => Promise<void> }) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [setupToken, setSetupToken] = useState("");
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const setup = view === "setup";
  const login = setup || view === "admin-login" || view === "ordering-login";
  const title = { checking: "正在确认访问权限", unavailable: "暂时无法连接厨房", setup: "设置管理员密码", "admin-login": "登录厨房管理", closed: "点餐页面暂未开放", "not-ready": "点餐页面正在准备", "ordering-login": "欢迎来点餐" }[view];
  const hint = { checking: "请稍候…", unavailable: "连接恢复并确认访问权限后，即可继续使用。", setup: "先完成管理员设置，再开放家庭点餐。", "admin-login": "输入管理员密码，进入厨房工作台。", closed: "管理员暂时关闭了点餐，请稍后再来。", "not-ready": "请联系管理员完成密码和访问设置。", "ordering-login": "输入点餐密码，看看今天吃什么。" }[view];
  const submit = async (event: React.SubmitEvent<HTMLFormElement>) => {
    event.preventDefault(); setFormError("");
    if (setup && password !== confirmation) { setFormError("两次输入的密码不一致。"); return; }
    setBusy(true);
    try {
      await accessRequest(setup ? "/setup" : view === "admin-login" ? "/admin/login" : "/ordering/login", { method: "POST", body: JSON.stringify(setup ? { setup_token: setupToken.trim(), password } : { password }) });
      setPassword(""); setConfirmation(""); setSetupToken("");
      await onLogin();
    } catch (cause) { setFormError(cause instanceof Error ? cause.message : "登录失败，请重试。"); }
    finally { setBusy(false); }
  };
  return <main className="access-page"><section className="access-card" aria-labelledby="access-title">
    <a className="access-brand" href={view === "admin-login" || setup ? "/admin/" : "/"}>Chef Zhai <span>家庭厨房</span></a>
    <div className="access-mark" aria-hidden="true">⌂</div><h1 id="access-title">{title}</h1><p className="access-hint">{hint}</p>
    {view === "checking" && <p role="status">正在检查…</p>}
    {(formError || error) && <p className="access-error" role="alert">{formError || error}</p>}
    {login && <form onSubmit={event => void submit(event)} className="access-form">
      {setup && <label>初始化口令<input name="setup-token" type="password" autoComplete="off" required value={setupToken} onChange={event => setSetupToken(event.target.value)} /><small>在服务器数据目录下的 <code>state/admin-setup-token</code> 文件中查看一次性口令；默认位置为 <code>data/state/admin-setup-token</code>。</small></label>}
      <label>{view === "ordering-login" ? "点餐密码" : setup ? "管理员密码" : "密码"}<input name="password" type="password" autoComplete={setup ? "new-password" : "current-password"} required minLength={setup ? 8 : undefined} maxLength={128} value={password} onChange={event => setPassword(event.target.value)} />{setup && <small>8–128 个字符，请使用独立的管理员密码。</small>}</label>
      {setup && <label>再次输入密码<input name="password-confirmation" type="password" autoComplete="new-password" required minLength={8} maxLength={128} value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>}
      <button type="submit" disabled={busy}>{busy ? "正在验证…" : setup ? "设置密码并登录" : view === "ordering-login" ? "进入点餐" : "登录"}</button>
    </form>}
    {["unavailable", "closed", "not-ready"].includes(view) && <button className="access-retry" onClick={retry}>重新检查</button>}
    <p className="access-footer">{view === "admin-login" || setup ? <a href="/">前往点餐页面</a> : <a href="/admin/">管理员登录</a>}</p>
  </section></main>;
}

export default function AccessGate({ mode, children }: { mode: "admin" | "ordering"; children: ReactNode }) {
  const [status, setStatus] = useState<AccessStatus | null>(null);
  const [error, setError] = useState("");
  const request = useRef(0);
  const mounted = useRef(false);
  const check = useCallback(async () => {
    const current = ++request.current;
    try {
      const result = await readAccessStatus({ signal: AbortSignal.timeout(10000) });
      if (mounted.current && request.current === current) { setStatus(result); setError(""); }
    } catch (cause) {
      if (mounted.current && request.current === current) { setStatus(null); setError(cause instanceof Error && cause.name !== "TimeoutError" ? cause.message : "连接超时，请检查连接后重试。"); }
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void check();
    const refresh = () => { void check(); };
    const revalidate = () => { setStatus(null); refresh(); };
    const denied = revalidate;
    const visible = () => { if (document.visibilityState === "visible") refresh(); };
    const offline = () => { request.current++; setStatus(null); setError("无法连接厨房服务，请检查连接后重试。"); };
    const timer = window.setInterval(refresh, 15000);
    window.addEventListener(`access:${mode}-denied`, denied);
    window.addEventListener("access:changed", revalidate);
    window.addEventListener("focus", refresh);
    window.addEventListener("online", revalidate);
    window.addEventListener("offline", offline);
    document.addEventListener("visibilitychange", visible);
    return () => {
      mounted.current = false; request.current++;
      window.clearInterval(timer);
      window.removeEventListener(`access:${mode}-denied`, denied);
      window.removeEventListener("access:changed", revalidate);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("online", revalidate);
      window.removeEventListener("offline", offline);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [check, mode]);
  const view = accessView(mode, status, !!error);
  return view === "allowed" ? children : <AccessScreen key={view} view={view} error={error} retry={() => void check()} onLogin={check} />;
}
