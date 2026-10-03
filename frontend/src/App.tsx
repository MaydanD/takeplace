import { Route, Routes } from "react-router-dom";

import { NotFoundPage } from "@/pages/NotFoundPage";
import { SystemStatusPage } from "@/pages/SystemStatusPage";

/**
 * Stage 1 routing.
 *
 * The public booking flow and admin UI arrive in later stages (PROJECT-SPEC §50,
 * §51). This shell exists so those routes have a mounted application and a
 * working backend connection to build on.
 */
export function App() {
  return (
    <Routes>
      <Route path="/" element={<SystemStatusPage />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
