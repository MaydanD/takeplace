/**
 * Typed API client foundation.
 *
 * Transport types come from the generated OpenAPI schema (`schema.ts`), never
 * hand-written (PROJECT-SPEC §3.1). The client itself stays small: Stage 1 only
 * needs health polling to prove frontend -> backend connectivity.
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

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/**
 * Perform a JSON request and parse the response.
 *
 * Backend errors carry a machine-readable `code` that the UI branches on; the
 * human-readable `detail` is only for display (PROJECT-SPEC §36).
 */
export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
  });

  if (!response.ok) {
    let code: string | undefined;
    let message = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string; code?: string };
      code = body.code;
      message = body.detail ?? message;
    } catch {
      // Non-JSON error body; keep the status text.
    }
    throw new ApiError(response.status, message, code);
  }

  return (await response.json()) as T;
}

export function fetchReadiness(signal?: AbortSignal): Promise<ReadinessResponse> {
  return apiGet<ReadinessResponse>("/health/ready", signal ? { signal } : undefined);
}

export function fetchOps(signal?: AbortSignal): Promise<OpsResponse> {
  return apiGet<OpsResponse>("/health/ops", signal ? { signal } : undefined);
}
