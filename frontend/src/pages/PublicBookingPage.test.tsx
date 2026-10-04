import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";
import type { PublicAvailability, PublicBooking, PublicVenue } from "@/api/public";
import { renderWithProviders } from "@/test/helpers";
const TABLE = {
  id: 10,
  hall_id: 1,
  number: "A1",
  capacity: 4,
  x: 60,
  y: 50,
  width: 80,
  height: 80,
  rotation: 0,
  shape: "rect",
  z_index: 0,
};
const VENUE: PublicVenue = {
  slug: "cafe",
  name: "Кафе Тест",
  timezone: "Europe/Moscow",
  is_active: true,
  online_booking_enabled: true,
  privacy_policy_version: "2.3",
  halls: [
    {
      id: 1,
      name: "Главный зал",
      is_bookable: true,
      canvas_width: 600,
      canvas_height: 400,
      static_elements: [
        { type: "wall", x: 0, y: 0, width: 500, height: 10, rotation: 0, z_index: 0 },
      ],
      tables: [TABLE, { ...TABLE, id: 11, number: "A2", x: 200 }],
    },
    {
      id: 2,
      name: "Терраса",
      is_bookable: true,
      canvas_width: 600,
      canvas_height: 400,
      static_elements: [],
      tables: [{ ...TABLE, id: 20, hall_id: 2, number: "B1" }],
    },
  ],
};
const SLOT = {
  start: "2026-10-05T23:30:00+03:00",
  earliest_end: "2026-10-06T00:15:00+03:00",
  latest_end: "2026-10-06T00:20:00+03:00",
  end_options: ["2026-10-06T00:15:00+03:00", "2026-10-06T00:20:00+03:00"],
};
const AVAILABILITY: PublicAvailability = {
  business_date: "2026-10-05",
  venue_timezone: "Europe/Moscow",
  shift_start: "2026-10-05T16:00:00+03:00",
  shift_end: "2026-10-06T02:00:00+03:00",
  is_open: true,
  tables: [
    { id: 10, number: "A1", capacity: 4, hall_id: 1, hall_name: "Главный зал", slots: [SLOT] },
  ],
};
const BOOKING: PublicBooking = {
  id: 777,
  number: 42,
  business_date: "2026-10-05",
  shift_starts_at: AVAILABILITY.shift_start!,
  shift_ends_at: AVAILABILITY.shift_end!,
  starts_at: SLOT.start,
  ends_at: SLOT.earliest_end,
  party_size: 2,
  table_ids: [10],
  source: "ONLINE",
};
const json = (body: unknown, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
function setup(
  options: {
    venue?: PublicVenue;
    venueStatus?: number;
    availability?: PublicAvailability;
    post?: (init: RequestInit) => Promise<Response>;
  } = {},
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST") return options.post ? options.post(init) : json(BOOKING, 201);
    if (url.includes("/availability")) return json(options.availability ?? AVAILABILITY);
    return json(options.venue ?? VENUE, options.venueStatus ?? 200);
  });
  vi.stubGlobal("fetch", fetchMock);
  renderWithProviders(<App />, { route: "/b/cafe" });
  return {
    user: userEvent.setup(),
    fetchMock,
    posts: () => fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
  };
}
async function selectSlot(user: ReturnType<typeof userEvent.setup>) {
  const table = await screen.findByRole("button", { name: "Стол A1, мест: 4" });
  await waitFor(() => expect(table).toHaveAttribute("aria-disabled", "false"));
  await user.click(table);
  await waitFor(() => expect(screen.getByLabelText("Начало")).toBeEnabled());
  await user.selectOptions(screen.getByLabelText("Начало"), SLOT.start);
}
async function guest(user: ReturnType<typeof userEvent.setup>, consent = true) {
  await user.type(screen.getByLabelText("Имя"), "Тестовый гость");
  await user.type(screen.getByLabelText("Телефон"), "+70000000000");
  await user.type(screen.getByLabelText("Комментарий"), "У окна");
  if (consent) await user.click(screen.getByRole("checkbox"));
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
describe("Public booking", () => {
  it("loads venue and shared geometry without admin credentials", async () => {
    const { fetchMock } = setup();
    expect(await screen.findByRole("heading", { name: VENUE.name })).toBeInTheDocument();
    expect(
      screen.getByLabelText("Схема зала Главный зал").querySelectorAll("rect").length,
    ).toBeGreaterThan(2);
    expect(fetchMock.mock.calls.every(([, init]) => init?.credentials === "omit")).toBe(true);
    expect(fetchMock.mock.calls.every(([url]) => !String(url).includes("/admin"))).toBe(true);
  });
  it.each([404, 500])("handles venue error %s", async (status) => {
    setup({ venueStatus: status });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      status === 404 ? "не найдено" : "Не удалось загрузить",
    );
  });
  it.each([{ online_booking_enabled: false }, { is_active: false }])(
    "handles unavailable venue %j",
    async (state) => {
      setup({ venue: { ...VENUE, ...state } });
      await screen.findByRole("heading", { name: VENUE.name });
      expect(screen.getByRole("status")).toHaveTextContent("временно недоступно");
    },
  );
  it("sends date, hall and exact party capacity changes to availability", async () => {
    const { user, fetchMock } = setup();
    await selectSlot(user);
    fireEvent.change(screen.getByLabelText("Дата"), { target: { value: "2026-10-07" } });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("business_date=2026-10-07")),
      ).toBe(true),
    );
    fireEvent.change(screen.getByLabelText("Количество гостей"), { target: { value: "4" } });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("party_size=4"))).toBe(true),
    );
    await user.click(screen.getByRole("button", { name: "Терраса" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("hall_id=2"))).toBe(true),
    );
    expect(screen.getByLabelText("Начало")).toHaveValue("");
  });
  it.each(["0", "201", "1.5", ""])('rejects invalid party "%s"', async (party) => {
    const { user, posts } = setup();
    await screen.findByRole("heading", { name: VENUE.name });
    fireEvent.change(screen.getByLabelText("Количество гостей"), { target: { value: party } });
    await user.click(screen.getByRole("button", { name: "Забронировать" }));
    expect(screen.getByText("Укажите целое число гостей от 1 до 200.")).toBeInTheDocument();
    expect(posts()).toHaveLength(0);
  });
  it("uses backend response for insufficient capacity and disables unavailable tables", async () => {
    const { user } = setup({ availability: { ...AVAILABILITY, tables: [] } });
    expect(await screen.findByText(/Нет подходящих свободных столов/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Список" }));
    expect(screen.getByRole("button", { name: /Стол A1/ })).toBeDisabled();
  });
  it("handles closed days", async () => {
    setup({ availability: { ...AVAILABILITY, is_open: false, tables: [] } });
    expect(await screen.findByText("В этот день заведение закрыто.")).toBeInTheDocument();
  });
  it("supports keyboard table selection and only server end options, with overnight dates", async () => {
    const { user } = setup();
    const table = await screen.findByRole("button", { name: "Стол A1, мест: 4" });
    await waitFor(() => expect(table).toHaveAttribute("aria-disabled", "false"));
    table.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByLabelText("Начало")).toBeEnabled());
    await user.selectOptions(screen.getByLabelText("Начало"), SLOT.start);
    expect(screen.getByLabelText("Окончание").querySelectorAll("option")).toHaveLength(3);
    expect(screen.getByLabelText("Окончание")).toHaveTextContent("06.10");
  });
  it("requires guest fields and consent", async () => {
    const { user, posts } = setup();
    await selectSlot(user);
    await user.click(screen.getByRole("button", { name: "Забронировать" }));
    expect(posts()).toHaveLength(0);
    await guest(user, false);
    await user.click(screen.getByRole("button", { name: "Забронировать" }));
    expect(posts()).toHaveLength(0);
    expect(await screen.findByRole("alert")).toHaveTextContent("подтвердите согласие");
  });
  it.each([200, 201])(
    "submits UUID, empty honeypot and policy version, confirms safe response %s",
    async (status) => {
      const { user, posts } = setup({ post: async () => json(BOOKING, status) });
      await selectSlot(user);
      await guest(user);
      await user.click(screen.getByRole("button", { name: "Забронировать" }));
      expect(
        await screen.findByRole("heading", { name: "Бронь №42 подтверждена" }),
      ).toBeInTheDocument();
      expect(posts()).toHaveLength(1);
      const init = posts()[0]![1]!;
      expect(JSON.parse(String(init.body))).toMatchObject({
        table_id: 10,
        party_size: 2,
        honeypot: "",
        captcha_token: null,
        privacy_policy_version: "2.3",
        ends_at: SLOT.earliest_end,
      });
      expect((init.headers as Record<string, string>)["Idempotency-Key"]).toMatch(
        /^[0-9a-f-]{36}$/,
      );
      expect(screen.queryByText("777")).not.toBeInTheDocument();
      expect(screen.queryByText("Тестовый гость")).not.toBeInTheDocument();
    },
  );
  it("refreshes after conflict and preserves guest input", async () => {
    const { user, fetchMock } = setup({
      post: async () => json({ code: "BOOKING_CONFLICT", detail: "internal constraint 777" }, 409),
    });
    await selectSlot(user);
    await guest(user);
    const before = fetchMock.mock.calls.filter(([url]) =>
      String(url).includes("availability"),
    ).length;
    await user.click(screen.getByRole("button", { name: "Забронировать" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Это время уже заняли");
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).includes("availability")).length,
      ).toBeGreaterThan(before),
    );
    expect(screen.getByLabelText("Имя")).toHaveValue("Тестовый гость");
    expect(screen.getByLabelText("Телефон")).toHaveValue("+70000000000");
    expect(screen.getByLabelText("Комментарий")).toHaveValue("У окна");
    expect(screen.getByLabelText("Начало")).toHaveValue("");
    expect(screen.queryByText(/internal constraint/)).not.toBeInTheDocument();
  });
  it("honors Retry-After without losing input", async () => {
    const { user } = setup({
      post: async () => json({ code: "RATE_LIMITED" }, 429, { "Retry-After": "2" }),
    });
    await selectSlot(user);
    await guest(user);
    await user.click(screen.getByRole("button", { name: "Забронировать" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("через 2 сек");
    expect(screen.getByRole("button", { name: /Повторить через/ })).toBeDisabled();
    expect(screen.getByLabelText("Имя")).toHaveValue("Тестовый гость");
    await waitFor(
      () => expect(screen.getByRole("button", { name: "Забронировать" })).toBeEnabled(),
      { timeout: 4000 },
    );
  });
  it("blocks sends after the kill switch", async () => {
    const { user } = setup({ post: async () => json({ code: "ONLINE_BOOKING_DISABLED" }, 409) });
    await selectSlot(user);
    await guest(user);
    await user.click(screen.getByRole("button", { name: "Забронировать" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("временно отключено");
    expect(screen.getByRole("button", { name: "Забронировать" })).toBeDisabled();
  });
  it("prevents double submission", async () => {
    let resolve!: (response: Response) => void;
    const { user, posts } = setup({
      post: () =>
        new Promise((done) => {
          resolve = done;
        }),
    });
    await selectSlot(user);
    await guest(user);
    const form = screen.getByRole("button", { name: "Забронировать" }).closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(screen.getByRole("button", { name: "Бронируем…" })).toBeDisabled();
    resolve(json(BOOKING, 201));
    await screen.findByText("Бронь №42 подтверждена");
  });
  it("reuses the key on network retry and rotates after explicit changes", async () => {
    const { user, posts } = setup({
      post: async () => {
        throw new TypeError("network lost");
      },
    });
    await selectSlot(user);
    await guest(user);
    await user.click(screen.getByRole("button", { name: "Забронировать" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Забронировать" }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[0]![1]!.headers).toEqual(posts()[1]![1]!.headers);
    await user.type(screen.getByLabelText("Комментарий"), "!");
    await user.click(screen.getByRole("button", { name: "Забронировать" }));
    await waitFor(() => expect(posts()).toHaveLength(3));
    expect(posts()[2]![1]!.headers).not.toEqual(posts()[0]![1]!.headers);
  });
});
