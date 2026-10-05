/**
 * Mounts the admin realtime connection for the authenticated admin area.
 *
 * Rendering this once at the app level (guarded by the route) ties the realtime
 * lifecycle to the admin session rather than to any single page: it connects
 * after login, stays connected across admin navigation, and disconnects when the
 * admin session is gone.
 */
import { useMe } from "@/api/adminQueries";
import { useRealtimeSync } from "@/realtime/useRealtime";

export function RealtimeBridge() {
  const me = useMe();
  useRealtimeSync(me.isSuccess);
  return null;
}
