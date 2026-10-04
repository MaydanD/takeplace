/**
 * React Query hooks for halls and tables.
 *
 * The server resolves the tenant from the session; there is no client-side
 * tenant state to tamper with (PROJECT-SPEC §7.1).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  archiveHall,
  archiveTable,
  createHall,
  fetchHall,
  fetchHalls,
  fetchTables,
  updateHall,
  updateTable,
  type HallCreate,
  type HallUpdate,
  type TableQuery,
  type TableUpdate,
} from "@/api/halls";

function hallsRoot() {
  return ["admin", "halls"] as const;
}

export const hallKeys = {
  list: (includeArchived: boolean) => ["admin", "halls", "list", { includeArchived }] as const,
  detail: (hallId: number) => ["admin", "halls", "detail", hallId] as const,
  tables: (query: TableQuery) => ["admin", "tables", query] as const,
};

function invalidateLayout(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: hallsRoot() });
  void queryClient.invalidateQueries({ queryKey: ["admin", "tables"] });
}

export function useHalls(includeArchived = false) {
  return useQuery({
    queryKey: hallKeys.list(includeArchived),
    queryFn: ({ signal }) => fetchHalls(includeArchived, signal),
    retry: false,
  });
}

export function useHall(hallId: number | undefined) {
  return useQuery({
    queryKey: hallKeys.detail(hallId ?? -1),
    queryFn: ({ signal }) => fetchHall(hallId as number, signal),
    enabled: hallId !== undefined,
    retry: false,
  });
}

export function useTables(query: TableQuery = {}) {
  return useQuery({
    queryKey: hallKeys.tables(query),
    queryFn: ({ signal }) => fetchTables(query, signal),
    retry: false,
  });
}

export function useCreateHall() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: HallCreate) => createHall(body),
    onSuccess: () => invalidateLayout(queryClient),
  });
}

export function useUpdateHall() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ hallId, body }: { hallId: number; body: HallUpdate }) =>
      updateHall(hallId, body),
    onSuccess: () => invalidateLayout(queryClient),
  });
}

export function useArchiveHall() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (hallId: number) => archiveHall(hallId),
    onSuccess: () => invalidateLayout(queryClient),
  });
}

export function useUpdateTable() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tableId, body }: { tableId: number; body: TableUpdate }) =>
      updateTable(tableId, body),
    onSuccess: () => invalidateLayout(queryClient),
  });
}

export function useArchiveTable() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (tableId: number) => archiveTable(tableId),
    onSuccess: () => invalidateLayout(queryClient),
  });
}
