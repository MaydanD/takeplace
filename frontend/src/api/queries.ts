import { useQuery } from "@tanstack/react-query";

import { fetchOps, fetchReadiness } from "@/api/client";

export const healthKeys = {
  readiness: ["health", "readiness"] as const,
  ops: ["health", "ops"] as const,
};

export function useReadiness() {
  return useQuery({
    queryKey: healthKeys.readiness,
    queryFn: ({ signal }) => fetchReadiness(signal),
    refetchInterval: 10_000,
  });
}

export function useOps() {
  return useQuery({
    queryKey: healthKeys.ops,
    queryFn: ({ signal }) => fetchOps(signal),
    refetchInterval: 30_000,
  });
}
