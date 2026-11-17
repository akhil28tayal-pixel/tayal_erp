import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "@/api/schema";

/** Session state lives in memory only. The refresh token is an HttpOnly cookie the page cannot read. */
let accessToken: string | null = null;
let companyId: string | null = null;
let refreshing: Promise<boolean> | null = null;
let onSessionLost: (() => void) | null = null;

export const session = {
  setToken(t: string | null) { accessToken = t; },
  setCompany(id: string | null) { companyId = id; },
  get company() { return companyId; },
  onLost(cb: () => void) { onSessionLost = cb; },
};

export type Problem = {
  status: number;
  code: string;
  detail: string;
  errors?: { field: string; code: string; message: string }[];
  [k: string]: unknown;
};

export class ApiError extends Error {
  constructor(public problem: Problem) { super(problem.detail); }
}

/** Uses the HttpOnly cookie to get a new access token. Concurrent callers share one request. */
export async function refreshAccess(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin" })
      .then(async (r) => {
        if (!r.ok) return false;
        accessToken = (await r.json()).access_token;
        return true;
      })
      .catch(() => false)
      .finally(() => { setTimeout(() => { refreshing = null; }, 0); });
  }
  return refreshing;
}

const auth: Middleware = {
  onRequest({ request }) {
    if (accessToken) request.headers.set("Authorization", `Bearer ${accessToken}`);
    if (companyId && !request.headers.has("X-Company-Id")) request.headers.set("X-Company-Id", companyId);
    return request;
  },
};

/** Retries once after a silent refresh when the access token has expired. */
const retryOn401 = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const first = await fetch(input instanceof Request ? input.clone() : input, init);
  if (first.status !== 401 || String(input).includes("/auth/")) return first;
  const ok = await refreshAccess();
  if (!ok) { onSessionLost?.(); return first; }
  const req = input instanceof Request ? new Request(input) : new Request(input, init);
  req.headers.set("Authorization", `Bearer ${accessToken}`);
  return fetch(req);
};

export const api = createClient<paths>({ baseUrl: "", fetch: retryOn401, credentials: "same-origin" });
api.use(auth);

/** Unwraps an openapi-fetch result: returns data or throws ApiError with the server's problem+json. */
export async function call<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await p;
  if (error !== undefined || !response.ok) {
    const prob = (error ?? {}) as Partial<Problem>;
    throw new ApiError({
      status: response.status,
      code: prob.code ?? "HTTP_ERROR",
      detail: prob.detail ?? `Request failed (${response.status})`,
      errors: prob.errors,
      ...prob,
    } as Problem);
  }
  return data as T;
}
