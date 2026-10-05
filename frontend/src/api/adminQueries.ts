/**
 * React Query hooks for the admin area.
 *
 * Authentication is derived from the server session: `useMe` returns the
 * authenticated admin+venue or errors with 401. There is no client-side tenant
 * state to tamper with (PROJECT-SPEC §7.1).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { realtimeClient } from "@/realtime/client";
import {
  deleteScheduleException,
  fetchBusinessDay,
  fetchMe,
  fetchSchedule,
  fetchScheduleExceptions,
  fetchSettings,
  login,
  logout,
  logoutAll,
  updateSchedule,
  updateSettings,
  upsertScheduleException,
  type LoginRequest,
  type MeResponse,
  type ScheduleExceptionUpdate,
  type SettingsUpdate,
  type VenueSummary,
  type WeeklyScheduleUpdate,
} from "@/api/admin";

export const adminKeys = {
  me: ["admin", "me"] as const,
  settings: ["admin", "settings"] as const,
  schedule: ["admin", "schedule"] as const,
  scheduleExceptions: ["admin", "schedule", "exceptions"] as const,
};

export function businessDayKey(date?: string) {
  return ["admin", "schedule", "business-day", date ?? "current"] as const;
}

export function useMe() {
  return useQuery({
    queryKey: adminKeys.me,
    queryFn: ({ signal }) => fetchMe(signal),
    retry: false,
    staleTime: 30_000,
  });
}

export function useSettings() {
  return useQuery({
    queryKey: adminKeys.settings,
    queryFn: ({ signal }) => fetchSettings(signal),
    retry: false,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (credentials: LoginRequest) => login(credentials),
    onSuccess: (data: MeResponse) => {
      queryClient.setQueryData(adminKeys.me, data);
    },
  });
}

function useSessionEndMutation(sessionEnd: () => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: sessionEnd,
    onSuccess: () => {
      queryClient.setQueryData(adminKeys.me, null);
      queryClient.removeQueries({ queryKey: adminKeys.settings });
      // Tear the realtime stream down with the session (§37.4).
      realtimeClient.disconnect();
    },
  });
}

export function useLogout() {
  return useSessionEndMutation(logout);
}

export function useLogoutAll() {
  return useSessionEndMutation(logoutAll);
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SettingsUpdate) => updateSettings(body),
    onSuccess: (venue: VenueSummary) => {
      queryClient.setQueryData(adminKeys.settings, venue);
      queryClient.setQueryData<MeResponse | null>(adminKeys.me, (previous) =>
        previous ? { ...previous, venue } : previous,
      );
    },
  });
}

export function useSchedule() {
  return useQuery({
    queryKey: adminKeys.schedule,
    queryFn: ({ signal }) => fetchSchedule(signal),
    retry: false,
  });
}

export function useScheduleExceptions() {
  return useQuery({
    queryKey: adminKeys.scheduleExceptions,
    queryFn: ({ signal }) => fetchScheduleExceptions(signal),
    retry: false,
  });
}

export function useBusinessDay(date?: string) {
  return useQuery({
    queryKey: businessDayKey(date),
    queryFn: ({ signal }) => fetchBusinessDay(date, signal),
    retry: false,
  });
}

export function useUpdateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: WeeklyScheduleUpdate) => updateSchedule(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: adminKeys.schedule });
      void queryClient.invalidateQueries({ queryKey: ["admin", "schedule", "business-day"] });
    },
  });
}

export function useUpsertScheduleException() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ date, body }: { date: string; body: ScheduleExceptionUpdate }) =>
      upsertScheduleException(date, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: adminKeys.scheduleExceptions });
      void queryClient.invalidateQueries({ queryKey: ["admin", "schedule", "business-day"] });
    },
  });
}

export function useDeleteScheduleException() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (date: string) => deleteScheduleException(date),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: adminKeys.scheduleExceptions });
      void queryClient.invalidateQueries({ queryKey: ["admin", "schedule", "business-day"] });
    },
  });
}
