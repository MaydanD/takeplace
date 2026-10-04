import { PublicBookingPage } from "@/pages/PublicBookingPage";
import { Route, Routes } from "react-router-dom";

import { RequireAdmin } from "@/components/RequireAdmin";
import { AdminDashboardPage } from "@/pages/AdminDashboardPage";
import { AdminLoginPage } from "@/pages/AdminLoginPage";
import { HallsPage } from "@/pages/HallsPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { SchedulePage } from "@/pages/SchedulePage";
import { SystemStatusPage } from "@/pages/SystemStatusPage";
import { BookingBookPage } from "@/pages/BookingBookPage";

/**
 * Application routing.
 *
 * Stage 1 exposes the status page; Stage 2 adds the isolated admin area. The
 * public booking flow arrives in later stages (PROJECT-SPEC §50, §51).
 */
export function App() {
  return (
    <Routes>
      <Route path="/b/:slug" element={<PublicBookingPage />} />
      <Route path="/" element={<SystemStatusPage />} />
      <Route path="/admin/login" element={<AdminLoginPage />} />
      <Route
        path="/admin/bookings"
        element={
          <RequireAdmin>
            <BookingBookPage />
          </RequireAdmin>
        }
      />
      <Route
        path="/admin"
        element={
          <RequireAdmin>
            <AdminDashboardPage />
          </RequireAdmin>
        }
      />
      <Route
        path="/admin/schedule"
        element={
          <RequireAdmin>
            <SchedulePage />
          </RequireAdmin>
        }
      />
      <Route
        path="/admin/halls"
        element={
          <RequireAdmin>
            <HallsPage />
          </RequireAdmin>
        }
      />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
