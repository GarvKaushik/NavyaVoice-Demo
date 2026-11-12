/* ─────────────────────────────────────────────────────────────
 * Portal API client. The login session is an httpOnly cookie, so
 * requests only need to be same-origin.
 * ─────────────────────────────────────────────────────────── */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Row = Record<string, any>;

export interface SessionUser {
  id: string;
  email: string;
  name: string | null;
  role: "admin" | "client";
  client_id: string | null;
  client_name: string | null;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    let message = "Something went wrong. Please try again.";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) {
        // Name the field that failed, e.g. "products → 1 → eta_text: String should have at most 128 characters"
        const first = body.detail[0];
        const where = Array.isArray(first?.loc) ? first.loc.filter((part: unknown) => part !== "body").join(" → ") : "";
        message = first?.msg ? `Please check ${where ? `${where}: ` : ""}${first.msg}` : "Please check the details you entered.";
      }
    } catch {
      /* keep the general message */
    }
    if (response.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event("session-expired"));
    throw new ApiError(response.status, message);
  }
  return response.json();
}

export const get = <T = Row>(path: string) => request<T>(path);

export const post = <T = Row>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });

export const put = <T = Row>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const patch = <T = Row>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const del = <T = Row>(path: string) => request<T>(path, { method: "DELETE" });

export const upload = <T = Row>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form });

/** Adds the client an admin is viewing; client users are always scoped to themselves by the server. */
export function scoped(path: string, clientId?: string): string {
  if (!clientId) return path;
  return `${path}${path.includes("?") ? "&" : "?"}client_id=${encodeURIComponent(clientId)}`;
}

export const formatNumber = (n: number | null | undefined, digits = 0) =>
  (n ?? 0).toLocaleString("en-IN", { maximumFractionDigits: digits });

export const formatInr = (n: number | null | undefined) => `₹${formatNumber(n, 2)}`;

/** The API sends naive UTC timestamps. */
export function parseTime(value: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  return parseTime(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function formatDuration(seconds: number | null | undefined): string {
  const s = seconds ?? 0;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
