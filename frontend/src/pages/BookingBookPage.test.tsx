import { screen, within, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BookingBookPage } from "@/pages/BookingBookPage";
import { mockFetch, renderWithProviders } from "@/test/helpers";
import { venueInput, venueInstant } from "@/api/bookings";

const root = "/api/admin/v1";
const booking = {
  id: 7,
  number: 42,
  venue_id: 1,
  business_date: "2026-10-05",
  source: "PHONE",
  status: "NEW",
  party_size: 2,
  guest_name: "Анна",
  guest_phone_raw: "+79990000000",
  guest_phone_normalized: "+79990000000",
  guest_comment: null,
  starts_at: "2026-10-05T15:00:00Z",
  ends_at: "2026-10-05T17:00:00Z",
  shift_starts_at: "2026-10-05T13:00:00Z",
  shift_ends_at: "2026-10-05T23:00:00Z",
  version: 1,
  table_ids: [10],
  live_table_ids: [],
  can_investigate_network: true,
  opened_at: null,
  canceled_at: null,
  closed_at: null,
  waiting_at: null,
  cancellation_reason: null,
  cancellation_note: null,
  created_at: "2026-10-04T12:00:00Z",
  updated_at: "2026-10-04T12:00:00Z",
};
const table = {
  id: 10,
  hall_id: 1,
  number: "1",
  capacity: 4,
  shape: "rect",
  is_bookable: true,
  archived_at: null,
  x: 10,
  y: 10,
  width: 80,
  height: 80,
  rotation: 0,
  z_index: 0,
};
function routes() {
  return {
    // The Stage 8 live widget queries OPEN bookings; return none so the mocked
    // single booking is not duplicated as a live row. Inserted first because
    // mockFetch matches keys in insertion order (path = substring after the space).
    [`GET status=OPEN`]: { status: 200, body: { items: [], next_cursor: null } },
    [`GET ${root}/me`]: {
      status: 200,
      body: { venue: { name: "Кафе", timezone: "Europe/Moscow" }, admin: { login: "admin" } },
    },
    [`GET ${root}/schedule/business-day`]: {
      status: 200,
      body: {
        business_date: "2026-10-05",
        current_business_date: "2026-10-05",
        is_open: true,
        shift_start: booking.shift_starts_at,
        shift_end: booking.shift_ends_at,
      },
    },
    [`GET ${root}/tables`]: { status: 200, body: { tables: [table] } },
    [`GET ${root}/halls/1`]: {
      status: 200,
      body: {
        id: 1,
        name: "Основной",
        canvas_width: 640,
        canvas_height: 480,
        static_elements: [],
        tables: [table],
      },
    },
    [`GET ${root}/halls`]: { status: 200, body: { halls: [{ id: 1, name: "Основной" }] } },
    [`GET ${root}/bookings/7/history`]: {
      status: 200,
      body: {
        events: [
          {
            id: 1,
            event_type: "BOOKING_CREATED",
            actor_type: "ADMIN",
            payload: {},
            created_at: booking.created_at,
          },
        ],
      },
    },
    [`GET ${root}/bookings/7`]: { status: 200, body: booking },
    [`GET ${root}/bookings`]: { status: 200, body: { items: [booking], next_cursor: null } },
  };
}
afterEach(() => {
  vi.restoreAllMocks();
});

describe("Booking Book", () => {
  it("uses venue timezone, not the browser timezone", () => {
    expect(venueInput("2026-10-05T15:00:00Z", "Asia/Yekaterinburg")).toBe("2026-10-05T20:00");
    expect(venueInstant("2026-10-05T20:00", "Asia/Yekaterinburg")).toBe("2026-10-05T15:00:00.000Z");
  });
  it("lists business day, opens detail/history and filters by map", async () => {
    const fetch = mockFetch(routes());
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<BookingBookPage />);
    await userEvent.click(await screen.findByRole("button", { name: /№42 · Анна/ }));
    const card = await screen.findByLabelText("Карточка брони");
    expect(await within(card).findByText("Бронь создана")).toBeInTheDocument();
    expect(within(card).getByText(/версия 1/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Стол 1, мест: 4" }));
    await waitFor(() =>
      expect(fetch.mock.calls.some(([url]) => String(url).includes("table_id=10"))).toBe(true),
    );
  });
  it("keeps filters on cursor pagination", async () => {
    const r = routes();
    r[`GET ${root}/bookings`].body = { items: [booking], next_cursor: 7 } as never;
    const fetch = mockFetch(r);
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<BookingBookPage />);
    await screen.findByRole("button", { name: /№42/ });
    await userEvent.selectOptions(screen.getByLabelText("Источник брони"), "PHONE");
    await userEvent.click(await screen.findByRole("button", { name: "Показать ещё" }));
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([url]) => String(url).includes("source=PHONE") && String(url).includes("cursor=7"),
        ),
      ).toBe(true),
    );
  });
  it("refreshes a stale booking and requires explicit resubmission with new version", async () => {
    const r = routes();
    let stale = false;
    const fallback = mockFetch(r);
    const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        stale = true;
        return Response.json({ code: "BOOKING_STALE", detail: "stale" }, { status: 409 });
      }
      if (stale && String(url) === `${root}/bookings/7`)
        return Response.json({ ...booking, version: 2, guest_name: "Серверное имя" });
      return fallback(url, init);
    });
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<BookingBookPage />);
    await userEvent.click(await screen.findByRole("button", { name: /№42/ }));
    const nameField = await screen.findByLabelText("Имя гостя");
    await userEvent.clear(nameField);
    await userEvent.type(nameField, "Локальное имя");
    await userEvent.click(await screen.findByRole("button", { name: "Сохранить гостя" }));
    expect(await screen.findByText(/Загружена новая версия/)).toBeInTheDocument();
    expect(await screen.findByDisplayValue("Серверное имя")).toBeInTheDocument();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "PATCH")).toHaveLength(1);
    const nameField2 = screen.getByLabelText("Имя гостя");
    await userEvent.clear(nameField2);
    await userEvent.type(nameField2, "Второе имя");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить гостя" }));
    const writes = fetch.mock.calls.filter(([, init]) => init?.method === "PATCH");
    expect(JSON.parse(String(writes[1]?.[1]?.body)).expected_version).toBe(2);
  });
  it.each([200, 201])("creates PHONE and accepts HTTP %i", async (status) => {
    const fetch = mockFetch({ [`POST ${root}/bookings`]: { status, body: booking }, ...routes() });
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<BookingBookPage />);
    await screen.findByRole("button", { name: /№42/ });
    await userEvent.click(screen.getByRole("button", { name: "Новая бронь" }));
    const form = within(screen.getByLabelText("Создание брони"));
    await userEvent.type(form.getByLabelText("Имя гостя"), "Анна");
    await userEvent.type(form.getByLabelText(/Телефон/), "+79990000000");
    await userEvent.click(form.getByRole("checkbox", { name: /Стол 1/ }));
    await userEvent.click(form.getByRole("button", { name: "Создать бронь" }));
    expect(await screen.findByText("Бронь №42 создана.")).toBeInTheDocument();
    const call = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    const body = JSON.parse(String(call?.[1]?.body));
    expect(body.source).toBe("PHONE");
    expect(body.table_ids).toEqual([10]);
    expect(call?.[1]?.headers).toHaveProperty("Idempotency-Key");
  });
  it("WALK_IN permits no phone and retries the exact body/key after network failure", async () => {
    const fallback = mockFetch(routes());
    let count = 0;
    const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        if (++count === 1) throw new TypeError("network");
        return Response.json({ ...booking, status: "OPEN" });
      }
      return fallback(url, init);
    });
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<BookingBookPage />);
    await screen.findByRole("button", { name: /№42/ });
    await userEvent.click(screen.getByRole("button", { name: "Новая бронь" }));
    const form = within(screen.getByLabelText("Создание брони"));
    await userEvent.selectOptions(form.getByLabelText("Источник"), "WALK_IN");
    await userEvent.type(form.getByLabelText("Имя гостя"), "Walk guest");
    await userEvent.click(form.getByRole("checkbox", { name: /Стол 1/ }));
    await userEvent.click(form.getByRole("button", { name: "Создать и разместить" }));
    await userEvent.click(
      await form.findByRole("button", { name: "Повторить создание с тем же ключом" }),
    );
    expect(await screen.findByText(/гости размещены/)).toBeInTheDocument();
    const writes = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(writes[0]?.[1]?.body).toBe(writes[1]?.[1]?.body);
    expect(writes[0]?.[1]?.headers).toEqual(writes[1]?.[1]?.headers);
    expect(JSON.parse(String(writes[0]?.[1]?.body))).toMatchObject({
      source: "WALK_IN",
      open_immediately: true,
      guest_phone_raw: null,
    });
  });
  it("searches exact phone/number and opens network investigation across dates", async () => {
    const fetch = mockFetch(routes());
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<BookingBookPage />);
    await screen.findByRole("button", { name: /№42/ });
    fireEvent.change(screen.getByLabelText("Телефон целиком"), {
      target: { value: "+79990000000" },
    });
    fireEvent.change(screen.getByLabelText("Номер брони"), { target: { value: "42" } });
    await userEvent.click(screen.getByRole("button", { name: "Найти" }));
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([url]) =>
            String(url).includes("number=42") && String(url).includes("phone=%2B79990000000"),
        ),
      ).toBe(true),
    );
    await userEvent.click(screen.getByRole("button", { name: /№42/ }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Брони с тем же сетевым отпечатком" }),
    );
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([url]) =>
            String(url).includes("same_network_as=7") && !String(url).includes("business_date"),
        ),
      ).toBe(true),
    );
  });
});
