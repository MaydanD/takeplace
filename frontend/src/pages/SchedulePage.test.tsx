import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SchedulePage } from "@/pages/SchedulePage";
import { mockFetch, renderWithProviders } from "@/test/helpers";

const WEEKDAYS = Array.from({ length: 7 }, (_, weekday) => ({
  weekday,
  is_open: weekday === 0,
  open_time: weekday === 0 ? "10:00" : null,
  close_time: weekday === 0 ? "22:00" : null,
}));

const SCHEDULE = { timezone: "Europe/Moscow", weekdays: WEEKDAYS };

const EXCEPTIONS = {
  timezone: "Europe/Moscow",
  exceptions: [{ date: "2026-10-07", is_closed: true, open_time: null, close_time: null }],
};

const BUSINESS_DAY = {
  business_date: "2026-10-05",
  timezone: "Europe/Moscow",
  is_open: true,
  is_open_now: true,
  current_business_date: "2026-10-05",
  shift_start: "2026-10-05T10:00:00+03:00",
  shift_end: "2026-10-05T22:00:00+03:00",
};

function baseRoutes() {
  // More specific paths first: `/schedule` is a substring of the others.
  return {
    "GET /api/admin/v1/schedule/business-day": { status: 200, body: BUSINESS_DAY },
    "GET /api/admin/v1/schedule/exceptions": { status: 200, body: EXCEPTIONS },
    "GET /api/admin/v1/schedule": { status: 200, body: SCHEDULE },
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SchedulePage", () => {
  it("renders the weekly schedule and the current business day", async () => {
    vi.stubGlobal("fetch", mockFetch(baseRoutes()));

    renderWithProviders(<SchedulePage />, { route: "/admin/schedule" });

    expect(await screen.findByText("Понедельник")).toBeInTheDocument();
    expect(screen.getByText("Воскресенье")).toBeInTheDocument();
    // Monday is open; Tuesday's checkbox is unchecked.
    expect(screen.getByRole("checkbox", { name: "Понедельник открыто" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Вторник открыто" })).not.toBeChecked();
    expect(screen.getByText("Текущая business date")).toBeInTheDocument();
  });

  it("saves the weekly schedule with 5-minute times", async () => {
    const fetchMock = mockFetch({
      ...baseRoutes(),
      "PUT /api/admin/v1/schedule": { status: 200, body: SCHEDULE },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<SchedulePage />, { route: "/admin/schedule" });
    await user.click(await screen.findByRole("button", { name: "Сохранить расписание" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/admin/v1/schedule"),
        expect.objectContaining({ method: "PUT" }),
      );
    });
    const putCall = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PUT",
    );
    const body = JSON.parse((putCall?.[1] as RequestInit).body as string);
    expect(body.weekdays).toHaveLength(7);
    expect(body.weekdays[0]).toEqual({
      weekday: 0,
      is_open: true,
      open_time: "10:00",
      close_time: "22:00",
    });
    expect(await screen.findByText("Сохранено.")).toBeInTheDocument();
  });

  it("shows a clear message when the schedule has an adjacent-shift overlap", async () => {
    const fetchMock = mockFetch({
      ...baseRoutes(),
      "PUT /api/admin/v1/schedule": {
        status: 409,
        body: { code: "SCHEDULE_OVERLAP", detail: "adjacent shifts overlap" },
      },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<SchedulePage />, { route: "/admin/schedule" });
    await user.click(await screen.findByRole("button", { name: "Сохранить расписание" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("пересекаются");
  });

  it("lists exceptions and deletes one", async () => {
    const fetchMock = mockFetch({
      ...baseRoutes(),
      "DELETE /api/admin/v1/schedule/exceptions/2026-10-07": { status: 204 },
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<SchedulePage />, { route: "/admin/schedule" });
    expect(await screen.findByText("2026-10-07")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Удалить" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/schedule/exceptions/2026-10-07"),
        expect.objectContaining({ method: "DELETE" }),
      );
    });
  });
});
