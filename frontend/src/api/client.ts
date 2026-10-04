/**
 * Typed API client foundation.
 *
 * Transport types come from the generated OpenAPI schema (`schema.ts`), never
 * hand-written (PROJECT-SPEC §3.1).
 */
import type { components } from "@/api/generated/schema";

export type OpsResponse = components["schemas"]["OpsResponse"];
export type ReadinessResponse = components["schemas"]["ReadinessResponse"];

/**
 * Base URL for API calls.
 *
 * Empty string means same-origin, which is the production shape (frontend and
 * API are served behind one Caddy origin). In development Vite proxies `/api`
 * and `/health` to the backend, so same-origin also works there.
 */
const API_BASE_URL = import.meta.env.TAKEPLACE_API_BASE_URL ?? "";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly retryAfterSeconds: number | undefined;

  constructor(status: number, message: string, code?: string, retryAfterSeconds?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export interface ApiRequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  // `| undefined` keeps callers that pass an optional signal valid under
  // `exactOptionalPropertyTypes`.
  signal?: AbortSignal | undefined;
  headers?: Record<string, string>;
  credentials?: RequestCredentials;
}

/**
 * Perform a JSON request and parse the response.
 *
 * `credentials: "include"` sends the admin session cookie on same-origin
 * requests; the browser adds the `Origin` header that the backend uses as its
 * CSRF check (PROJECT-SPEC §39.3). Backend errors carry a machine-readable
 * `code` that the UI branches on; the `detail` is only for display (§36).
 */
export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal, headers: extraHeaders } = options;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (extraHeaders) {
    Object.assign(headers, extraHeaders);
  }
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    credentials: options.credentials ?? "include",
    headers,
    ...(signal ? { signal } : {}),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });

  if (!response.ok) {
    let code: string | undefined;
    let message = response.statusText;
    try {
      const errorBody = (await response.json()) as { detail?: string; code?: string };
      code = errorBody.code;
      message = errorBody.detail ?? message;
    } catch {
      // Non-JSON error body; keep the status text.
    }
    const retryAfter = Number(response.headers.get("Retry-After"));
    throw new ApiError(
      response.status,
      message,
      code,
      Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  return apiRequest<T>(path, { signal: init?.signal ?? undefined });
}

export function fetchReadiness(signal?: AbortSignal): Promise<ReadinessResponse> {
  return apiRequest<ReadinessResponse>("/health/ready", { signal });
}

export function fetchOps(signal?: AbortSignal): Promise<OpsResponse> {
  return apiRequest<OpsResponse>("/health/ops", { signal });
}
