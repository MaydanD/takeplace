import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HallsPage } from "@/pages/HallsPage";
import { mockFetch, renderWithProviders } from "@/test/helpers";

const HALLS = {
  halls: [
    {
      id: 1,
      name: "Главный зал",
      is_bookable: true,
      canvas_width: 800,
      canvas_height: 600,
      layout_revision: 1,
      archived_at: null,
      table_count: 2,
    },
  ],
};

const TABLE = {
  id: 10,
  hall_id: 1,
  hall_name: "Главный зал",
  number: "1",
  capacity: 4,
  is_bookable: true,
  archived_at: null,
  x: 100,
  y: 100,
  width: 80,
  height: 80,
  rotation: 0,
  shape: "rect",
  z_index: 0,
};

const HALL_DETAIL = {
  id: 1,
  name: "Главный зал",
  is_bookable: true,
  canvas_width: 800,
  canvas_height: 600,
  layout_revision: 1,
  archived_at: null,
  static_elements: [{ type: "wall", x: 0, y: 0, width: 800, height: 10, rotation: 0, z_index: 0 }],
  tables: [TABLE],
};

const TABLES = { tables: [TABLE] };

function baseRoutes() {
  // More specific paths first: `/halls` is a substring of `/halls/1`.
  return {
    "GET /api/admin/v1/halls/1": { status: 200, body: HALL_DETAIL },
    "GET /api/admin/v1/tables": { status: 200, body: TABLES },
    "GET /api/admin/v1/halls": { status: 200, body: HALLS },
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("HallsPage", () => {
  it("renders a read-only canvas for the selected hall", async () => {
    vi.stubGlobal("fetch", mockFetch(baseRoutes()));

    renderWithProviders(<HallsPage />, { route: "/admin/halls" });

    const canvas = await screen.findByLabelText("Схема зала Главный зал");
    expect(canvas).toBeInTheDocument();
    // The wall static element and the table number are both drawn.
    expect(canvas.querySelectorAll("rect").length).toBeGreaterThan(0);
    expect(within(canvas).getByText("1")).toBeInTheDocument();
  });

  it("lists tables with hall, number, capacity and bookable state", async () => {
    vi.stubGlobal("fetch", mockFetch(baseRoutes()));

    renderWithProviders(<HallsPage />, { route: "/admin/halls" });

    const checkbox = await screen.findByRole("checkbox", { name: "1 bookable" });
    expect(checkbox).toBeChecked();
    const row = checkbox.closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("Главный зал")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("4")).toBeInTheDocument();
  });

  it("toggles is_bookable through the operational endpoint", async () => {
    const fetchMock = mockFetch({
      ...baseRoutes(),
      "PATCH /api/admin/v1/tables/10": { status: 200, body: { ...TABLE, is_bookable: false } },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<HallsPage />, { route: "/admin/halls" });
    await user.click(await screen.findByRole("checkbox", { name: "1 bookable" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/admin/v1/tables/10"),
        expect.objectContaining({ method: "PATCH" }),
      );
    });
  });

  it("shows a clear message when a hall cannot be archived", async () => {
    const fetchMock = mockFetch({
      ...baseRoutes(),
      "POST /api/admin/v1/halls/1/archive": {
        status: 409,
        body: { code: "HALL_ARCHIVE_BLOCKED", detail: "still has tables" },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<HallsPage />, { route: "/admin/halls" });
    await user.click(await screen.findByRole("button", { name: "Архивировать зал" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("неархивные столы");
  });
});
