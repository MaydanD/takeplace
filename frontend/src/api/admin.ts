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
export type ScheduleDay = components["schemas"]["ScheduleDay"];
export type WeeklyScheduleResponse = components["schemas"]["WeeklyScheduleResponse"];
export type WeeklyScheduleUpdate = components["schemas"]["WeeklyScheduleUpdate"];
export type ScheduleExceptionEntry = components["schemas"]["ScheduleExceptionEntry"];
export type ScheduleExceptionsResponse = components["schemas"]["ScheduleExceptionsResponse"];
export type ScheduleExceptionUpdate = components["schemas"]["ScheduleExceptionUpdate"];
export type BusinessDayResponse = components["schemas"]["BusinessDayResponse"];

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

export function fetchSchedule(signal?: AbortSignal): Promise<WeeklyScheduleResponse> {
  return apiRequest<WeeklyScheduleResponse>(`${ADMIN_PREFIX}/schedule`, { signal });
}

export function updateSchedule(body: WeeklyScheduleUpdate): Promise<WeeklyScheduleResponse> {
  return apiRequest<WeeklyScheduleResponse>(`${ADMIN_PREFIX}/schedule`, { method: "PUT", body });
}

export function fetchScheduleExceptions(signal?: AbortSignal): Promise<ScheduleExceptionsResponse> {
  return apiRequest<ScheduleExceptionsResponse>(`${ADMIN_PREFIX}/schedule/exceptions`, { signal });
}

export function upsertScheduleException(
  date: string,
  body: ScheduleExceptionUpdate,
): Promise<ScheduleExceptionEntry> {
  return apiRequest<ScheduleExceptionEntry>(`${ADMIN_PREFIX}/schedule/exceptions/${date}`, {
    method: "PUT",
    body,
  });
}

export function deleteScheduleException(date: string): Promise<void> {
  return apiRequest<void>(`${ADMIN_PREFIX}/schedule/exceptions/${date}`, { method: "DELETE" });
}

export function fetchBusinessDay(
  date?: string,
  signal?: AbortSignal,
): Promise<BusinessDayResponse> {
  const query = date ? `?business_date=${encodeURIComponent(date)}` : "";
  return apiRequest<BusinessDayResponse>(`${ADMIN_PREFIX}/schedule/business-day${query}`, {
    signal,
  });
}
