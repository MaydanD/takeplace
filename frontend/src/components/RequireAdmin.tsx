import { OutboxBanner } from "@/components/OutboxBanner";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useMe } from "@/api/adminQueries";

/**
 * Route guard for the admin area.
 *
 * Authentication is decided by the server (`GET /me`); without a valid session
 * the server returns 401 and we redirect to the login page. The tenant is never
 * chosen on the client (PROJECT-SPEC §7.1).
 */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();

  if (me.isPending) {
    return (
      <main className="page">
        <p>Загрузка…</p>
      </main>
    );
  }

  if (me.isError) {
    return <Navigate to="/admin/login" state={{ from: location }} replace />;
  }

  return (
    <>
      <OutboxBanner venueId={me.data.venue.id} />
      {children}
    </>
  );
}
