import { VKIntegrationPage } from "@/pages/VKIntegrationPage";
import { PublicBookingPage } from "@/pages/PublicBookingPage";
import { lazy, Suspense } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { RequireAdmin } from "@/components/RequireAdmin";
import { RealtimeBridge } from "@/realtime/RealtimeBridge";
import { AdminDashboardPage } from "@/pages/AdminDashboardPage";
import { AdminLoginPage } from "@/pages/AdminLoginPage";
import { HallsPage } from "@/pages/HallsPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { SchedulePage } from "@/pages/SchedulePage";
import { SystemStatusPage } from "@/pages/SystemStatusPage";
import { BookingBookPage } from "@/pages/BookingBookPage";

// §30.5: the editor (Konva + its bundle) is code-split from the main admin app.
const HallEditorPage = lazy(() => import("@/pages/HallEditorPage"));

/**
 * Mount the realtime connection only inside the authenticated admin area, so
 * the public page pays nothing and the login page never opens a stream.
 */
function AdminRealtime() {
  const location = useLocation();
  const isAdminArea =
    location.pathname.startsWith("/admin") && location.pathname !== "/admin/login";
  return isAdminArea ? <RealtimeBridge /> : null;
}

/**
 * Application routing.
 *
 * Stage 1 exposes the status page; Stage 2 adds the isolated admin area. The
 * public booking flow arrives in later stages (PROJECT-SPEC §50, §51).
 */
export function App() {
  return (
    <>
      <AdminRealtime />
      <Routes>
        <Route
          path="/admin/vk"
          element={
            <RequireAdmin>
              <VKIntegrationPage />
            </RequireAdmin>
          }
        />
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
        <Route
          path="/admin/editor"
          element={
            <RequireAdmin>
              <Suspense fallback={<p>Загрузка редактора…</p>}>
                <HallEditorPage />
              </Suspense>
            </RequireAdmin>
          }
        />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </>
  );
}
