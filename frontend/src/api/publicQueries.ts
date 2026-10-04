import { useMutation, useQuery } from "@tanstack/react-query";
import {
  createPublicBooking,
  fetchPublicAvailability,
  fetchPublicVenue,
  type AvailabilityQuery,
  type PublicBookingRequest,
} from "@/api/public";
export function usePublicVenue(slug: string) {
  return useQuery({
    queryKey: ["public", slug],
    queryFn: ({ signal }) => fetchPublicVenue(slug, signal),
    retry: false,
  });
}
export function usePublicAvailability(slug: string, query: AvailabilityQuery, enabled: boolean) {
  return useQuery({
    queryKey: ["public", slug, "availability", query],
    queryFn: ({ signal }) => fetchPublicAvailability(slug, query, signal),
    enabled,
    retry: false,
    refetchOnWindowFocus: false,
  });
}
export function useCreatePublicBooking(slug: string) {
  return useMutation({
    mutationFn: ({ body, key }: { body: PublicBookingRequest; key: string }) =>
      createPublicBooking(slug, body, key),
    retry: false,
  });
}
