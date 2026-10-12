import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
  ],
};
const slot = (start: string, ends: string[]) => ({
  start,
  earliest_end: ends[0]!,
  latest_end: ends[ends.length - 1]!,
  end_options: ends,
});
const S18 = slot("2026-10-23T18:00:00+03:00", [
  "2026-10-23T18:45:00+03:00",
  "2026-10-23T19:00:00+03:00",
  "2026-10-23T19:30:00+03:00",
]);
const S21 = slot("2026-10-23T21:00:00+03:00", ["2026-10-23T21:45:00+03:00"]);
const S2330 = slot("2026-10-23T23:30:00+03:00", [
  "2026-10-24T01:30:00+03:00",
  "2026-10-24T01:45:00+03:00",
]);
const AVAILABILITY: PublicAvailability = {
  business_date: "2026-10-23",
  venue_timezone: "Europe/Moscow",
  shift_start: "2026-10-23T18:00:00+03:00",
  shift_end: "2026-10-24T03:00:00+03:00",
  is_open: true,
  tables: [
    {
      id: 10,
      number: "A1",
      capacity: 4,
      hall_id: 1,
      hall_name: "Главный зал",
      slots: [S18, S21, S2330],
    },
  ],
};
const BOOKING: PublicBooking = {
  id: 777,
  number: 42,
  business_date: "2026-10-23",
  shift_starts_at: AVAILABILITY.shift_start!,
  shift_ends_at: AVAILABILITY.shift_end!,
  starts_at: S18.start,
  ends_at: S18.end_options[2]!,
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

async function openTable(user: ReturnType<typeof userEvent.setup>, name = "Стол A1, мест: 4") {
  const table = await screen.findByRole("button", { name });
  await user.click(table);
  return screen.findByRole("dialog");
}

/** Walk the four time stages, assuming we are already on the start-hour step. */
async function chooseTime(
  user: ReturnType<typeof userEvent.setup>,
  startHour: string,
  startMinute: string,
  endHour: string,
  endMinute: string,
) {
  const dialog = () => screen.getByRole("dialog");
  await user.click(within(dialog()).getByRole("button", { name: startHour }));
  await user.click(within(dialog()).getByRole("button", { name: startMinute }));
  await user.click(within(dialog()).getByRole("button", { name: endHour }));
  await user.click(within(dialog()).getByRole("button", { name: endMinute }));
}

/** From the table-info modal: start booking, then walk the four stages. */
async function pickTime(
  user: ReturnType<typeof userEvent.setup>,
  startHour: string,
  startMinute: string,
  endHour: string,
  endMinute: string,
) {
  await user.click(screen.getByRole("button", { name: "Забронировать" }));
  await chooseTime(user, startHour, startMinute, endHour, endMinute);
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
  it("loads venue and renders the shared geometry without admin credentials", async () => {
    const { fetchMock } = setup();
    expect(await screen.findByRole("heading", { name: VENUE.name })).toBeInTheDocument();
    const canvas = screen.getByLabelText("Схема зала Главный зал");
    // Four seats around a 4-top: capacity squares + table + wall element.
    expect(canvas.querySelectorAll(".table-seat").length).toBeGreaterThanOrEqual(4);
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

  it("opens table info with capacity, date and a free/busy timeline", async () => {
    const { user } = setup();
    const dialog = await openTable(user);
    expect(within(dialog).getByRole("heading", { name: "Стол A1" })).toBeInTheDocument();
    expect(within(dialog).getByText(/Вместимость: 4 мест/)).toBeInTheDocument();
    expect(within(dialog).getByTestId("selected-date")).toHaveTextContent("23 октября 2026");
    // 18:00–19:30 free, then occupied until 21:00, then free.
    expect(within(dialog).getByText(/18:00–19:30 свободно/)).toBeInTheDocument();
    expect(within(dialog).getByText(/19:30–21:00 занято/)).toBeInTheDocument();
    expect(within(dialog).getByText(/21:00–21:45 свободно/)).toBeInTheDocument();
  });

  it("keeps a fully occupied table selectable and explains the state", async () => {
    // Table A2 is not in the availability snapshot → fully occupied.
    const { user } = setup();
    const dialog = await openTable(user, "Стол A2, мест: 4");
    expect(within(dialog).getByRole("status")).toHaveTextContent("полностью занят");
    expect(within(dialog).getByRole("button", { name: "Забронировать" })).toBeDisabled();
  });

  it("advances through the four stages automatically and disables invalid options", async () => {
    const { user } = setup();
    const dialog = await openTable(user);
    await user.click(within(dialog).getByRole("button", { name: "Забронировать" }));
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Начало — выберите час");
    // Hour 19 has no valid start.
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "19" })).toBeDisabled();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "18" }));
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Начало — выберите минуты");
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "05" })).toBeDisabled();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "00" }));
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Окончание — выберите час");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "19" }));
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Окончание — выберите минуты");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "30" }));
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Данные гостя");
  });

  it("keeps the chosen start visible while picking the end and supports Back", async () => {
    const { user } = setup();
    const dialog = await openTable(user);
    await user.click(within(dialog).getByRole("button", { name: "Забронировать" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "18" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "00" }));
    expect(screen.getByTestId("chosen-start")).toHaveTextContent("18:00");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Назад" }));
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Начало — выберите минуты");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Назад" }));
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Начало — выберите час");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Назад" }));
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Стол A1");
  });

  it("shows after-midnight end hours with a +1 day marker", async () => {
    const { user } = setup();
    const dialog = await openTable(user);
    await user.click(within(dialog).getByRole("button", { name: "Забронировать" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "23" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "30" }));
    const nextDay = within(screen.getByRole("dialog")).getByRole("button", { name: /01/ });
    expect(nextDay).toHaveTextContent("+1 день");
    await user.click(nextDay);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "45" }));
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Данные гостя");
    expect(screen.getByText("01:45")).toBeInTheDocument();
    expect(screen.getByText("+1 день")).toBeInTheDocument();
  });

  it("changing the date resets the time, returns to the first stage and keeps the table", async () => {
    const { user, fetchMock } = setup();
    const dialog = await openTable(user);
    await user.click(within(dialog).getByRole("button", { name: "Забронировать" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "18" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "00" }));
    // Now on the end-hour stage.
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Изменить дату" }),
    );
    fireEvent.change(screen.getByLabelText("Дата"), { target: { value: "2026-10-25" } });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("business_date=2026-10-25")),
      ).toBe(true),
    );
    expect(screen.getByRole("dialog", { name: "Начало — выберите час" })).toBeInTheDocument();
    // The selected table is preserved on the schema.
    expect(await screen.findByRole("button", { name: "Стол A1, мест: 4" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("sends the party size to availability", async () => {
    const { fetchMock } = setup();
    await screen.findByRole("heading", { name: VENUE.name });
    fireEvent.change(screen.getByLabelText("Количество гостей"), { target: { value: "4" } });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("party_size=4"))).toBe(true),
    );
  });

  it.each(["0", "201", "1.5", ""])('rejects invalid party "%s"', async (party) => {
    setup();
    await screen.findByRole("heading", { name: VENUE.name });
    fireEvent.change(screen.getByLabelText("Количество гостей"), { target: { value: party } });
    expect(screen.getByText("Укажите целое число гостей от 1 до 200.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("handles closed days", async () => {
    const { fetchMock } = setup({ availability: { ...AVAILABILITY, is_open: false, tables: [] } });
    // Wait until the business date is resolved and its availability is fetched.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).includes("/availability")).length,
      ).toBeGreaterThanOrEqual(2),
    );
    expect(
      await screen.findByText("В этот день заведение закрыто.", undefined, { timeout: 3000 }),
    ).toBeInTheDocument();
  });

  it("requires guest fields and consent before creating a booking", async () => {
    const { user, posts } = setup();
    await openTable(user);
    await pickTime(user, "18", "00", "19", "30");
    await user.click(screen.getByRole("button", { name: "Подтвердить бронирование" }));
    expect(posts()).toHaveLength(0);
    await guest(user, false);
    await user.click(screen.getByRole("button", { name: "Подтвердить бронирование" }));
    expect(posts()).toHaveLength(0);
    expect(await screen.findByRole("alert")).toHaveTextContent("подтвердите согласие");
  });

  it.each([200, 201])(
    "submits UUID, empty honeypot and policy version and confirms a safe response %s",
    async (status) => {
      const { user, posts } = setup({ post: async () => json(BOOKING, status) });
      await openTable(user);
      await pickTime(user, "18", "00", "19", "30");
      await guest(user);
      await user.click(screen.getByRole("button", { name: "Подтвердить бронирование" }));
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
        ends_at: S18.end_options[2],
      });
      expect((init.headers as Record<string, string>)["Idempotency-Key"]).toMatch(
        /^[0-9a-f-]{36}$/,
      );
      expect(screen.queryByText("777")).not.toBeInTheDocument();
      expect(screen.queryByText("Тестовый гость")).not.toBeInTheDocument();
    },
  );

  it("on conflict returns to the first stage preserving the table, date and guest input", async () => {
    const { user, fetchMock } = setup({
      post: async () => json({ code: "BOOKING_CONFLICT", detail: "internal constraint 777" }, 409),
    });
    await openTable(user);
    await pickTime(user, "18", "00", "19", "30");
    await guest(user);
    const before = fetchMock.mock.calls.filter(([url]) =>
      String(url).includes("availability"),
    ).length;
    await user.click(screen.getByRole("button", { name: "Подтвердить бронирование" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Это время уже заняли");
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Начало — выберите час");
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => String(url).includes("availability")).length,
      ).toBeGreaterThan(before),
    );
    // Re-enter the guest step: the guest input is still there.
    await chooseTime(user, "18", "00", "19", "30");
    expect(screen.getByLabelText("Имя")).toHaveValue("Тестовый гость");
    expect(screen.queryByText(/internal constraint/)).not.toBeInTheDocument();
  });

  it("honors Retry-After without losing input", async () => {
    const { user } = setup({
      post: async () => json({ code: "RATE_LIMITED" }, 429, { "Retry-After": "2" }),
    });
    await openTable(user);
    await pickTime(user, "18", "00", "19", "30");
    await guest(user);
    await user.click(screen.getByRole("button", { name: "Подтвердить бронирование" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("через 2 сек");
    expect(screen.getByRole("button", { name: /Повторить через/ })).toBeDisabled();
    expect(screen.getByLabelText("Имя")).toHaveValue("Тестовый гость");
  });

  it("blocks sends after the kill switch", async () => {
    const { user, posts } = setup({
      post: async () => json({ code: "ONLINE_BOOKING_DISABLED" }, 409),
    });
    await openTable(user);
    await pickTime(user, "18", "00", "19", "30");
    await guest(user);
    await user.click(screen.getByRole("button", { name: "Подтвердить бронирование" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("временно отключено");
    expect(posts()).toHaveLength(1);
  });

  it("prevents double submission", async () => {
    let resolve!: (response: Response) => void;
    const { user, posts } = setup({
      post: () => new Promise((done) => (resolve = done)),
    });
    await openTable(user);
    await pickTime(user, "18", "00", "19", "30");
    await guest(user);
    const form = screen.getByRole("button", { name: "Подтвердить бронирование" }).closest("form")!;
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
    await openTable(user);
    await pickTime(user, "18", "00", "19", "30");
    await guest(user);
    await user.click(screen.getByRole("button", { name: "Подтвердить бронирование" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Подтвердить бронирование" }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[0]![1]!.headers).toEqual(posts()[1]![1]!.headers);
    await user.type(screen.getByLabelText("Комментарий"), "!");
    await user.click(screen.getByRole("button", { name: "Подтвердить бронирование" }));
    await waitFor(() => expect(posts()).toHaveLength(3));
    expect(posts()[2]![1]!.headers).not.toEqual(posts()[0]![1]!.headers);
  });
});
