import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RequireAdmin } from "@/components/RequireAdmin";
import { AdminDashboardPage } from "@/pages/AdminDashboardPage";
import { mockFetch, renderWithProviders } from "@/test/helpers";

const ME_RESPONSE = {
  admin: { id: 7, login: "dragon-admin", is_active: true },
  venue: {
    id: 3,
    slug: "dragon",
    name: "Dragon Hall",
    address: null,
    phone: null,
    timezone: "Europe/Moscow",
    is_active: true,
    online_booking_enabled: true,
  },
};

const VENUE_RESPONSE = ME_RESPONSE.venue;

function renderAdmin() {
  return renderWithProviders(
    <Routes>
      <Route
        path="/admin"
        element={
          <RequireAdmin>
            <AdminDashboardPage />
          </RequireAdmin>
        }
      />
      <Route path="/admin/login" element={<div>LOGIN SCREEN</div>} />
    </Routes>,
    { route: "/admin" },
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RequireAdmin", () => {
  it("redirects to the login screen when there is no session", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/admin/v1/me": {
          status: 401,
          body: { code: "UNAUTHENTICATED", detail: "authentication required" },
        },
      }),
    );

    renderAdmin();

    expect(await screen.findByText("LOGIN SCREEN")).toBeInTheDocument();
  });
});

describe("AdminDashboardPage", () => {
  it("shows the authenticated venue and admin", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "GET /api/admin/v1/me": { status: 200, body: ME_RESPONSE },
        "GET /api/admin/v1/settings": { status: 200, body: VENUE_RESPONSE },
      }),
    );

    renderAdmin();

    expect(await screen.findByText("Dragon Hall")).toBeInTheDocument();
    expect(screen.getByText("dragon")).toBeInTheDocument();
    expect(screen.getByText("dragon-admin")).toBeInTheDocument();
    expect(screen.getByText("Europe/Moscow")).toBeInTheDocument();
  });

  it("ends the session and returns to login on logout", async () => {
    const fetchMock = mockFetch({
      "GET /api/admin/v1/me": { status: 200, body: ME_RESPONSE },
      "GET /api/admin/v1/settings": { status: 200, body: VENUE_RESPONSE },
      "POST /api/admin/v1/auth/logout-all": { status: 200, body: { status: "ok" } },
      "POST /api/admin/v1/auth/logout": { status: 200, body: { status: "ok" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderAdmin();

    await user.click(await screen.findByRole("button", { name: "Выйти" }));

    expect(await screen.findByText("LOGIN SCREEN")).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/auth/logout"),
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});
