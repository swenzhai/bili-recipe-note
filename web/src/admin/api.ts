const API_ROOT = "/api/admin/v1";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.name = "ApiError"; this.status = status; }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("X-Admin-Request", "1");
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response: Response;
  try { response = await fetch(`${API_ROOT}${path}`, { ...options, headers, credentials: "same-origin", cache: "no-store" }); }
  catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError("暂时无法连接厨房服务，请检查连接后重试。", 0);
  }
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event("access:admin-denied"));
    let message = `操作未完成（${response.status}），请重试。`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item: { msg?: string }) => item.msg || "输入格式有误").join("；");
      else if (typeof body.error === "string") message = body.error;
    } catch { /* Proxies may return HTML on failure. */ }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function post<T>(path: string, body: unknown = {}): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作未完成，请重试。";
}

export function safeUrl(value: unknown): string | undefined {
  if (typeof value !== "string" || !value) return undefined;
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : undefined; }
  catch { return undefined; }
}

export function taskPath(task: { kind: string; id: string }): string {
  return `/tasks?kind=${encodeURIComponent(task.kind)}&id=${encodeURIComponent(task.id)}`;
}
