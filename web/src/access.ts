export type AccessStatus = {
  admin_configured: boolean;
  admin_authenticated: boolean;
  ordering_enabled: boolean;
  ordering_password_required: boolean;
  ordering_password_configured: boolean;
  ordering_authenticated: boolean;
};

export type AccessView = "checking" | "unavailable" | "setup" | "admin-login" | "closed" | "not-ready" | "ordering-login" | "allowed";

export function accessView(mode: "admin" | "ordering", status: AccessStatus | null, failed = false): AccessView {
  if (!status) return failed ? "unavailable" : "checking";
  if (mode === "admin") return !status.admin_configured ? "setup" : status.admin_authenticated ? "allowed" : "admin-login";
  if (!status.ordering_enabled) return "closed";
  if (status.admin_configured && status.admin_authenticated) return "allowed";
  if (!status.admin_configured || (status.ordering_password_required && !status.ordering_password_configured)) return "not-ready";
  if (status.ordering_password_required && !status.ordering_authenticated) return "ordering-login";
  return "allowed";
}

export class AccessError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.name = "AccessError"; this.status = status; }
}

export function notifyAccessChanged() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event("access:changed"));
}

export async function accessRequest<T = AccessStatus>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("X-Access-Request", "1");
  if (options.body) headers.set("Content-Type", "application/json");
  let response: Response;
  try { response = await fetch(`/api/access${path}`, { ...options, headers, credentials: "same-origin", cache: "no-store" }); }
  catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new AccessError("无法连接厨房服务，请检查连接后重试。", 0);
  }
  if (!response.ok) {
    if (response.status === 401 && ["/settings", "/admin/password"].includes(path) && typeof window !== "undefined") window.dispatchEvent(new Event("access:admin-denied"));
    let message = `操作未完成（${response.status}），请重试。`;
    try { const data = await response.json(); if (typeof data.detail === "string") message = data.detail; } catch { /* A proxy can return HTML. */ }
    throw new AccessError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function readAccessStatus(options: RequestInit = {}): Promise<AccessStatus> {
  const status = await accessRequest<AccessStatus>("/status", options);
  const keys: (keyof AccessStatus)[] = ["admin_configured", "admin_authenticated", "ordering_enabled", "ordering_password_required", "ordering_password_configured", "ordering_authenticated"];
  if (!status || keys.some(key => typeof status[key] !== "boolean")) throw new AccessError("访问状态无效，请刷新后重试。", 0);
  return status;
}
