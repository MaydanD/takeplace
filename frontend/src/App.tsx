import { Route, Routes } from "react-router-dom";

import { RequireAdmin } from "@/components/RequireAdmin";
import { AdminDashboardPage } from "@/pages/AdminDashboardPage";
import { AdminLoginPage } from "@/pages/AdminLoginPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { SystemStatusPage } from "@/pages/SystemStatusPage";

/**
 * Application routing.
 *
 * Stage 1 exposes the status page; Stage 2 adds the isolated admin area. The
 * public booking flow arrives in later stages (PROJECT-SPEC §50, §51).
 */
export function App() {
  return (
    <Routes>
      <Route path="/" element={<SystemStatusPage />} />
      <Route path="/admin/login" element={<AdminLoginPage />} />
      <Route
        path="/admin"
        element={
          <RequireAdmin>
            <AdminDashboardPage />
          </RequireAdmin>
        }
      />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
