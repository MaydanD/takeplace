/**
 * Admin API calls (`/api/admin/v1`), PROJECT-SPEC §35.
 *
 * Types are taken from the generated OpenAPI schema. The tenant is always
 * resolved from the session cookie on the server; the client never sends a
 * `venue_id` (PROJECT-SPEC §7.1).
 */
import type { components } from "@/api/generated/schema";
import { apiRequest } from "@/api/client";

export type AdminSummary = components["schemas"]["AdminSummary"];
export type VenueSummary = components["schemas"]["VenueSummary"];
export type MeResponse = components["schemas"]["MeResponse"];
export type LoginRequest = components["schemas"]["LoginRequest"];
export type SettingsUpdate = components["schemas"]["SettingsUpdate"];
export type StatusResponse = components["schemas"]["StatusResponse"];

const ADMIN_PREFIX = "/api/admin/v1";

export function fetchMe(signal?: AbortSignal): Promise<MeResponse> {
  return apiRequest<MeResponse>(`${ADMIN_PREFIX}/me`, { signal });
}

export function login(body: LoginRequest): Promise<MeResponse> {
  return apiRequest<MeResponse>(`${ADMIN_PREFIX}/auth/login`, { method: "POST", body });
}

export function logout(): Promise<StatusResponse> {
  return apiRequest<StatusResponse>(`${ADMIN_PREFIX}/auth/logout`, { method: "POST" });
}

export function logoutAll(): Promise<StatusResponse> {
  return apiRequest<StatusResponse>(`${ADMIN_PREFIX}/auth/logout-all`, { method: "POST" });
}

export function fetchSettings(signal?: AbortSignal): Promise<VenueSummary> {
  return apiRequest<VenueSummary>(`${ADMIN_PREFIX}/settings`, { signal });
}

export function updateSettings(body: SettingsUpdate): Promise<VenueSummary> {
  return apiRequest<VenueSummary>(`${ADMIN_PREFIX}/settings`, { method: "PATCH", body });
}
