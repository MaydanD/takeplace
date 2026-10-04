import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdminLoginPage } from "@/pages/AdminLoginPage";
import { mockFetch, renderWithProviders } from "@/test/helpers";

const ME_RESPONSE = {
  admin: { id: 1, login: "dragon-admin", is_active: true },
  venue: {
    id: 1,
    slug: "dragon",
    name: "Dragon",
    address: null,
    phone: null,
    timezone: "Europe/Moscow",
    is_active: true,
    online_booking_enabled: false,
  },
};

function renderLogin() {
  return renderWithProviders(
    <Routes>
      <Route path="/admin/login" element={<AdminLoginPage />} />
      <Route path="/admin" element={<div>ADMIN HOME</div>} />
    </Routes>,
    { route: "/admin/login" },
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AdminLoginPage", () => {
  it("logs in and redirects to the admin area", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ "POST /api/admin/v1/auth/login": { status: 200, body: ME_RESPONSE } }),
    );
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText("Логин"), "dragon-admin");
    await user.type(screen.getByLabelText("Пароль"), "secret-password");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByText("ADMIN HOME")).toBeInTheDocument();
  });

  it("shows a safe message on invalid credentials", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "POST /api/admin/v1/auth/login": {
          status: 401,
          body: { code: "INVALID_CREDENTIALS", detail: "invalid login or password" },
        },
      }),
    );
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText("Логин"), "nobody");
    await user.type(screen.getByLabelText("Пароль"), "wrong");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Неверный логин или пароль.");
  });

  it("explains a suspended venue", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "POST /api/admin/v1/auth/login": {
          status: 403,
          body: { code: "VENUE_SUSPENDED", detail: "venue is suspended" },
        },
      }),
    );
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText("Логин"), "admin");
    await user.type(screen.getByLabelText("Пароль"), "pw");
    await user.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Заведение приостановлено.");
  });
});
