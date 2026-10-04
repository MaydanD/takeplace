import type { components, operations } from "@/api/generated/schema";
import { apiRequest } from "@/api/client";

export type PublicVenue = components["schemas"]["PublicVenueResponse"];
export type PublicAvailability = components["schemas"]["PublicAvailabilityResponse"];
export type PublicBookingRequest = components["schemas"]["PublicCreateRequest"];
export type PublicBooking = components["schemas"]["PublicCreateResponse"];
export type AvailabilityQuery = NonNullable<
  operations["get_public_availability_api_public_v1_venues__slug__availability_get"]["parameters"]["query"]
>;
const path = (slug: string) => `/api/public/v1/venues/${encodeURIComponent(slug)}`;
export function fetchPublicVenue(slug: string, signal?: AbortSignal) {
  return apiRequest<PublicVenue>(path(slug), { signal, credentials: "omit" });
}
export function fetchPublicAvailability(
  slug: string,
  query: AvailabilityQuery,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value != null) params.set(key, String(value));
  });
  return apiRequest<PublicAvailability>(`${path(slug)}/availability?${params}`, {
    signal,
    credentials: "omit",
  });
}
export function createPublicBooking(slug: string, body: PublicBookingRequest, key: string) {
  return apiRequest<PublicBooking>(`${path(slug)}/bookings`, {
    method: "POST",
    credentials: "omit",
    body,
    headers: { "Idempotency-Key": key },
  });
}
