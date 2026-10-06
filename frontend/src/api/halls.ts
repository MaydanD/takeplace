/**
 * Halls and tables API (`/api/admin/v1/halls`, `/tables`), PROJECT-SPEC §35.
 *
 * Types come from the generated OpenAPI schema. The tenant is always resolved
 * from the session cookie on the server; the client never sends a `venue_id`
 * (PROJECT-SPEC §7.1).
 */
import type { components } from "@/api/generated/schema";
import { apiRequest } from "@/api/client";

export type HallSummary = components["schemas"]["HallSummary"];
export type HallDetail = components["schemas"]["HallDetail"];
export type HallCreate = components["schemas"]["HallCreate"];
export type HallUpdate = components["schemas"]["HallUpdate"];
export type TableSummary = components["schemas"]["TableSummary"];
export type TableUpdate = components["schemas"]["TableUpdate"];
export type HallsResponse = components["schemas"]["HallsResponse"];
export type TablesResponse = components["schemas"]["TablesResponse"];
export type LayoutSaveRequest = components["schemas"]["LayoutSaveRequest"];
export type LayoutSaveTable = components["schemas"]["LayoutSaveTable"];
/** A static layout element (wall/stage/bar/zone/text) as a discriminated union. */
export type StaticElement = HallDetail["static_elements"][number];

const ADMIN_PREFIX = "/api/admin/v1";

export interface TableQuery {
  hallId?: number;
  includeArchived?: boolean;
}

export function fetchHalls(includeArchived = false, signal?: AbortSignal): Promise<HallsResponse> {
  const query = includeArchived ? "?include_archived=true" : "";
  return apiRequest<HallsResponse>(`${ADMIN_PREFIX}/halls${query}`, { signal });
}

export function fetchHall(hallId: number, signal?: AbortSignal): Promise<HallDetail> {
  return apiRequest<HallDetail>(`${ADMIN_PREFIX}/halls/${hallId}`, { signal });
}

export function createHall(body: HallCreate): Promise<HallSummary> {
  return apiRequest<HallSummary>(`${ADMIN_PREFIX}/halls`, { method: "POST", body });
}

export function updateHall(hallId: number, body: HallUpdate): Promise<HallSummary> {
  return apiRequest<HallSummary>(`${ADMIN_PREFIX}/halls/${hallId}`, { method: "PATCH", body });
}

export function archiveHall(hallId: number): Promise<HallSummary> {
  return apiRequest<HallSummary>(`${ADMIN_PREFIX}/halls/${hallId}/archive`, { method: "POST" });
}

export function fetchTables(query: TableQuery = {}, signal?: AbortSignal): Promise<TablesResponse> {
  const params = new URLSearchParams();
  if (query.hallId !== undefined) {
    params.set("hall_id", String(query.hallId));
  }
  if (query.includeArchived) {
    params.set("include_archived", "true");
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return apiRequest<TablesResponse>(`${ADMIN_PREFIX}/tables${suffix}`, { signal });
}

export function updateTable(tableId: number, body: TableUpdate): Promise<TableSummary> {
  return apiRequest<TableSummary>(`${ADMIN_PREFIX}/tables/${tableId}`, { method: "PATCH", body });
}

export function archiveTable(tableId: number): Promise<TableSummary> {
  return apiRequest<TableSummary>(`${ADMIN_PREFIX}/tables/${tableId}/archive`, { method: "POST" });
}

/**
 * Full-state editor layout-save (§31): `expected_revision` plus the complete
 * editor-owned state. Returns the fresh `HallDetail` (new revision).
 */
export function saveHallLayout(hallId: number, body: LayoutSaveRequest): Promise<HallDetail> {
  return apiRequest<HallDetail>(`${ADMIN_PREFIX}/halls/${hallId}/layout`, {
    method: "PUT",
    body,
  });
}
