/**
 * React Query hooks for the admin area.
 *
 * Authentication is derived from the server session: `useMe` returns the
 * authenticated admin+venue or errors with 401. There is no client-side tenant
 * state to tamper with (PROJECT-SPEC §7.1).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchMe,
  fetchSettings,
  login,
  logout,
  logoutAll,
  updateSettings,
  type LoginRequest,
  type MeResponse,
  type SettingsUpdate,
  type VenueSummary,
} from "@/api/admin";

export const adminKeys = {
  me: ["admin", "me"] as const,
  settings: ["admin", "settings"] as const,
};

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
