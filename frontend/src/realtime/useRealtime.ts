/**
 * Realtime -> TanStack Query invalidation (PROJECT-SPEC §37).
 *
 * Realtime never patches complex booking state by hand: every signal maps to
 * `invalidateQueries`, and the authoritative state is refetched from the API.
 * A booking signal invalidates booking-scoped queries; a `resync` (or anything
 * unknown/forward-compatible) invalidates the whole admin surface, which is the
 * safe choice after a reconnect where events may have been missed.
 */
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { realtimeClient, type RealtimeSignal } from "@/realtime/client";

/** Prefix keys invalidated by any `booking.*` signal. */
export const bookingRealtimeKeys: readonly (readonly unknown[])[] = [
  ["booking-book"],
  ["booking"],
  ["booking-history"],
  ["booking-live"],
  ["booking-unresolved"],
  ["book-day"],
  ["book-current-day"],
  ["book-hall"],
  ["book-halls"],
  ["book-tables"],
];

/** Everything touched by schedule/hall/venue changes or a reconnect resync. */
export const venueRealtimeKeys: readonly (readonly unknown[])[] = [
  ["admin"],
  ...bookingRealtimeKeys,
];

export function realtimeInvalidationKeys(signal: RealtimeSignal): readonly (readonly unknown[])[] {
  const type = signal.type ?? "";
  if (type.startsWith("booking.")) return bookingRealtimeKeys;
  // `resync` and any unknown/forward-compatible type use the full resync set.
  return venueRealtimeKeys;
}

/**
 * Attach the shared realtime connection to the query cache.
 *
 * `enabled` gates the connection on an authenticated admin session. The hook is
 * reference-counted, so mounting it in multiple components still yields exactly
 * one `EventSource`.
 */
export function useRealtimeSync(enabled: boolean): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled) return;
    realtimeClient.acquire();
    const unsubscribeSignal = realtimeClient.subscribe((signal) => {
      for (const key of realtimeInvalidationKeys(signal)) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    });
    const unsubscribeResync = realtimeClient.onResync(() => {
      for (const key of venueRealtimeKeys) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    });
    return () => {
      unsubscribeSignal();
      unsubscribeResync();
      realtimeClient.release();
    };
  }, [enabled, queryClient]);
}
