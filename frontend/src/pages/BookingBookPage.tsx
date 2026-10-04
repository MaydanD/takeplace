import { useRef, useState, type FormEvent } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useMe } from "@/api/adminQueries";
import { fetchBusinessDay } from "@/api/admin";
import { fetchHalls, fetchHall, fetchTables, type TableSummary } from "@/api/halls";
import { HallCanvas } from "@/components/HallCanvas";
import { ApiError } from "@/api/client";
import {
  bookingError,
  cancelBooking,
  changeTime,
  createBooking,
  editGuest,
  fetchBooking,
  fetchBookings,
  fetchHistory,
  venueInput,
  venueInstant,
  type Booking,
  type BookingCreate,
  type BookingCancel,
  type BookingFilters,
} from "@/api/bookings";

const statuses: Record<string, string> = {
  NEW: "Новая",
  WAITING: "Ожидает",
  OPEN: "Гости в зале",
  CLOSED: "Закрыта",
  CANCELED: "Отменена",
};
const sources: Record<string, string> = {
  PHONE: "Телефон",
  VK: "ВКонтакте",
  OTHER: "Другое",
  WALK_IN: "С улицы",
  ONLINE: "Онлайн",
};
const events: Record<string, string> = {
  BOOKING_CREATED: "Бронь создана",
  BOOKING_OPENED: "Гости размещены",
  BOOKING_EDITED: "Данные гостя изменены",
  BOOKING_CANCELED: "Бронь отменена",
  TIME_CHANGED: "Время изменено",
  BOOKING_RESCHEDULED: "Бронь перенесена",
};
const dateTime = (value: string, timezone: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    timeZone: timezone,
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
const tableNames = (ids: number[], tables: TableSummary[]) =>
  ids.map((id) => tables.find((t) => t.id === id)?.number ?? `#${id}`).join(", ") || "—";

function eventDetails(payload: Record<string, unknown>, timezone: string, tables: TableSummary[]) {
  const fields: Record<string, string> = {
    guest_name: "имя",
    guest_phone_raw: "телефон",
    guest_comment: "комментарий",
    party_size: "количество гостей",
  };
  const details: string[] = [];
  if (typeof payload.starts_at === "string" && typeof payload.ends_at === "string")
    details.push(
      `${dateTime(payload.starts_at, timezone)} — ${dateTime(payload.ends_at, timezone)}`,
    );
  if (Array.isArray(payload.table_ids))
    details.push(
      `Столы: ${tableNames(
        payload.table_ids.filter((id): id is number => typeof id === "number"),
        tables,
      )}`,
    );
  if (typeof payload.party_size === "number") details.push(`Гостей: ${payload.party_size}`);
  if (Array.isArray(payload.changed_fields))
    details.push(
      `Изменены: ${payload.changed_fields.map((f) => fields[String(f)] ?? String(f)).join(", ")}`,
    );
  return details.join(" · ");
}

function GuestFields({
  phoneRequired = false,
  booking,
}: {
  phoneRequired?: boolean;
  booking?: Booking;
}) {
  return (
    <>
      <label className="field">
        Имя гостя
        <input
          name="guest_name"
          required
          maxLength={100}
          defaultValue={booking?.guest_name ?? ""}
        />
      </label>
      <label className="field">
        Телефон{phoneRequired ? " *" : " (необязательно)"}
        <input
          name="guest_phone_raw"
          type="tel"
          required={phoneRequired}
          maxLength={50}
          defaultValue={booking?.guest_phone_raw ?? ""}
        />
      </label>
      <label className="field">
        Количество гостей
        <input
          name="party_size"
          type="number"
          min={1}
          max={1000}
          required
          defaultValue={booking?.party_size ?? 2}
        />
      </label>
      <label className="field">
        Комментарий
        <textarea
          name="guest_comment"
          maxLength={1000}
          defaultValue={booking?.guest_comment ?? ""}
        />
      </label>
    </>
  );
}
function guestValues(form: FormData) {
  return {
    guest_name: String(form.get("guest_name")).trim(),
    guest_phone_raw: String(form.get("guest_phone_raw")).trim() || null,
    party_size: Number(form.get("party_size")),
    guest_comment: String(form.get("guest_comment")).trim() || null,
  };
}

function CreateForm({
  timezone,
  tables,
  start,
  end,
  onCreated,
  onClose,
}: {
  timezone: string;
  tables: TableSummary[];
  start: string;
  end: string;
  onCreated: (booking: Booking) => void;
  onClose: () => void;
}) {
  const [source, setSource] = useState<BookingCreate["source"]>("PHONE");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const attempt = useRef<{ signature: string; key: string; body: BookingCreate }>();
  const [retry, setRetry] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const data = new FormData(event.currentTarget);
    setPending(true);
    setError("");
    try {
      const body: BookingCreate =
        retry && attempt.current
          ? attempt.current.body
          : {
              ...guestValues(data),
              source,
              open_immediately: source === "WALK_IN",
              starts_at:
                source === "WALK_IN"
                  ? new Date().toISOString()
                  : venueInstant(String(data.get("starts_at")), timezone),
              ends_at: venueInstant(String(data.get("ends_at")), timezone),
              table_ids: data.getAll("table_ids").map(Number),
            };
      const signature = JSON.stringify(body);
      if (attempt.current?.signature !== signature)
        attempt.current = { signature, key: crypto.randomUUID(), body };
      const result = await createBooking(body, attempt.current.key);
      onCreated(result);
    } catch (err) {
      setError(bookingError(err));
      setRetry(!(err instanceof ApiError) || err.status >= 500);
    } finally {
      setPending(false);
    }
  }
  return (
    <section className="card" aria-label="Создание брони">
      <header className="page-header">
        <h2>Новая бронь</h2>
        <button onClick={onClose} disabled={pending || retry}>
          Закрыть
        </button>
      </header>
      <form onSubmit={submit}>
        <fieldset disabled={pending || retry} className="booking-form-grid">
          <label className="field">
            Источник
            <select
              value={source}
              onChange={(e) => setSource(e.target.value as BookingCreate["source"])}
            >
              {Object.entries(sources)
                .filter(([key]) => key !== "ONLINE")
                .map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
            </select>
          </label>
          {source === "WALK_IN" ? (
            <p>
              Гости уже пришли. Бронь откроется сразу; начало определит сервер. Телефон
              необязателен.
            </p>
          ) : (
            <label className="field">
              Начало
              <input
                type="datetime-local"
                name="starts_at"
                step={300}
                required
                defaultValue={venueInput(start, timezone)}
              />
            </label>
          )}
          <label className="field">
            Конец
            <input
              type="datetime-local"
              name="ends_at"
              step={300}
              required
              defaultValue={venueInput(end, timezone)}
            />
          </label>
          <GuestFields phoneRequired={source === "PHONE" || source === "OTHER"} />
          <fieldset className="booking-tables">
            <legend>Столы · можно выбрать несколько</legend>
            {tables
              .filter((t) => t.is_bookable && !t.archived_at)
              .map((t) => (
                <label key={t.id}>
                  <input type="checkbox" name="table_ids" value={t.id} />
                  Стол {t.number} · {t.capacity} мест
                </label>
              ))}
          </fieldset>
        </fieldset>
        <p className="hint">
          Время заведения: {timezone}. Минимум 45 минут; для гостя с улицы у конца смены — весь
          оставшийся интервал.
        </p>
        {error && <p role="alert">{error}</p>}
        <button disabled={pending}>
          {pending
            ? "Создание…"
            : retry
              ? "Повторить создание с тем же ключом"
              : source === "WALK_IN"
                ? "Создать и разместить"
                : "Создать бронь"}
        </button>
      </form>
    </section>
  );
}

function BookingCard({
  id,
  timezone,
  tables,
  onClose,
  onNetwork,
}: {
  id: number;
  timezone: string;
  tables: TableSummary[];
  onClose: () => void;
  onNetwork: (id: number) => void;
}) {
  const client = useQueryClient();
  const detail = useQuery({
    queryKey: ["booking", id],
    queryFn: () => fetchBooking(id),
    refetchOnWindowFocus: false,
  });
  const history = useQuery({ queryKey: ["booking-history", id], queryFn: () => fetchHistory(id) });
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [blocked, setBlocked] = useState(false);
  async function refresh() {
    const result = await detail.refetch();
    await history.refetch();
    setBlocked(result.isError);
    return !result.isError;
  }
  async function mutate(action: () => Promise<Booking>) {
    setPending(true);
    setMessage("");
    try {
      const result = await action();
      client.setQueryData(["booking", id], result);
      await history.refetch();
      setMessage("Изменения сохранены.");
      await client.invalidateQueries({ queryKey: ["booking-book"] });
    } catch (error) {
      setMessage(bookingError(error));
      if (
        error instanceof ApiError &&
        ["BOOKING_STALE", "BOOKING_INVALID_STATE"].includes(error.code ?? "")
      ) {
        setBlocked(true);
        if (!(await refresh()))
          setMessage(
            "Бронь изменилась. Не удалось загрузить новую версию. Обновите карточку перед повторной попыткой.",
          );
        await client.invalidateQueries({ queryKey: ["booking-book"] });
      }
    } finally {
      setPending(false);
    }
  }
  const b = detail.data;
  return (
    <aside className="card booking-card" aria-label="Карточка брони">
      <header className="page-header">
        <h2>Бронь {b ? `№${b.number}` : "…"}</h2>
        <button onClick={onClose} disabled={pending}>
          Закрыть карточку
        </button>
      </header>
      {detail.isError && <p role="alert">Не удалось загрузить бронь.</p>}
      <button onClick={refresh} disabled={pending}>
        Обновить карточку
      </button>
      {message && (
        <p role="status" className="callout">
          {message}
        </p>
      )}
      {b && (
        <>
          <p>
            <strong>{statuses[b.status]}</strong> · {sources[b.source]} · версия {b.version}
          </p>
          <dl className="facts">
            <div>
              <dt>Бизнес-дата</dt>
              <dd>{b.business_date}</dd>
            </div>
            <div>
              <dt>Время брони</dt>
              <dd>
                {dateTime(b.starts_at, timezone)} — {dateTime(b.ends_at, timezone)}
              </dd>
            </div>
            <div>
              <dt>Снимок смены</dt>
              <dd>
                {dateTime(b.shift_starts_at, timezone)} — {dateTime(b.shift_ends_at, timezone)}
              </dd>
            </div>
            <div>
              <dt>Плановые столы</dt>
              <dd>{tableNames(b.table_ids, tables)}</dd>
            </div>
            <div>
              <dt>Гости размещены</dt>
              <dd>
                {tableNames(b.live_table_ids, tables)}
                {b.opened_at && ` · ${dateTime(b.opened_at, timezone)}`}
              </dd>
            </div>
          </dl>
          <p>
            {b.guest_name ?? "Данные удалены"} · {b.guest_phone_raw ?? "Без телефона"} ·{" "}
            {b.party_size} гостей
          </p>
          {b.guest_comment && <p className="booking-comment">{b.guest_comment}</p>}
          {b.cancellation_reason && (
            <p>
              Причина отмены: {b.cancellation_reason}. {b.cancellation_note}
            </p>
          )}
          {b.can_investigate_network && (
            <button onClick={() => onNetwork(b.id)}>Брони с тем же сетевым отпечатком</button>
          )}
          {["NEW", "WAITING", "OPEN"].includes(b.status) && (
            <form
              key={`guest-${b.version}`}
              onSubmit={(e) => {
                e.preventDefault();
                const values = guestValues(new FormData(e.currentTarget));
                void mutate(() => editGuest(b.id, { expected_version: b.version, ...values }));
              }}
            >
              <h3>Данные гостя</h3>
              <fieldset disabled={pending || blocked} className="booking-form-grid">
                <GuestFields
                  booking={b}
                  phoneRequired={["ONLINE", "PHONE", "OTHER"].includes(b.source)}
                />
                <button>Сохранить гостя</button>
              </fieldset>
            </form>
          )}
          {["NEW", "WAITING"].includes(b.status) && (
            <>
              <form
                key={`time-${b.version}`}
                onSubmit={(e) => {
                  e.preventDefault();
                  const values = new FormData(e.currentTarget);
                  void mutate(() =>
                    changeTime(b.id, {
                      expected_version: b.version,
                      starts_at: venueInstant(String(values.get("start")), timezone),
                      ends_at: venueInstant(String(values.get("end")), timezone),
                    }),
                  );
                }}
              >
                <h3>Изменить время</h3>
                <fieldset disabled={pending || blocked} className="booking-form-grid">
                  <label className="field">
                    Новое начало
                    <input
                      name="start"
                      type="datetime-local"
                      step={300}
                      required
                      defaultValue={venueInput(b.starts_at, timezone)}
                    />
                  </label>
                  <label className="field">
                    Новый конец
                    <input
                      name="end"
                      type="datetime-local"
                      step={300}
                      required
                      defaultValue={venueInput(b.ends_at, timezone)}
                    />
                  </label>
                  <button>Изменить время</button>
                </fieldset>
              </form>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const values = new FormData(e.currentTarget);
                  void mutate(() =>
                    cancelBooking(b.id, {
                      expected_version: b.version,
                      reason: String(values.get("reason")) as BookingCancel["reason"],
                      note: String(values.get("note")) || null,
                    }),
                  );
                }}
              >
                <h3>Отмена</h3>
                <fieldset disabled={pending || blocked} className="booking-form-grid">
                  <label className="field">
                    Причина
                    <select name="reason">
                      <option value="GUEST_CANCELED">Гость отменил</option>
                      <option value="NO_SHOW">Не пришёл</option>
                      <option value="DUPLICATE">Дубликат</option>
                      <option value="CREATION_ERROR">Ошибка создания</option>
                      <option value="OTHER">Другое</option>
                    </select>
                  </label>
                  <label className="field">
                    Примечание к отмене
                    <input name="note" maxLength={500} />
                  </label>
                  <button className="button--danger">Отменить бронь</button>
                </fieldset>
              </form>
            </>
          )}
          <h3>История</h3>
          {history.isError && <p role="alert">Не удалось загрузить историю.</p>}
          <ol className="booking-history">
            {history.data?.events.map((event) => (
              <li key={event.id}>
                <strong>{events[event.event_type] ?? event.event_type}</strong>
                <br />
                <small>
                  {dateTime(event.created_at, timezone)} ·{" "}
                  {event.actor_type === "ADMIN"
                    ? "Администратор"
                    : event.actor_type === "PUBLIC"
                      ? "Гость"
                      : "Система"}
                </small>
                <p>{eventDetails(event.payload, timezone, tables)}</p>
              </li>
            ))}
          </ol>
        </>
      )}
    </aside>
  );
}

export function BookingBookPage() {
  const me = useMe();
  const client = useQueryClient();
  const current = useQuery({ queryKey: ["book-current-day"], queryFn: () => fetchBusinessDay() });
  const [day, setDay] = useState("");
  const [filters, setFilters] = useState<BookingFilters>({});
  const [unresolved, setUnresolved] = useState(false);
  const [selected, setSelected] = useState<number>();
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState("");
  const [hallId, setHallId] = useState<number>();
  const date = day || current.data?.current_business_date;
  const timezone = me.data?.venue.timezone ?? "UTC";
  const business = useQuery({
    queryKey: ["book-day", date],
    queryFn: () => fetchBusinessDay(date),
    enabled: !!date,
  });
  const halls = useQuery({ queryKey: ["book-halls"], queryFn: () => fetchHalls() });
  const activeHall = hallId ?? halls.data?.halls[0]?.id;
  const hall = useQuery({
    queryKey: ["book-hall", activeHall],
    queryFn: () => fetchHall(activeHall!),
    enabled: activeHall !== undefined,
  });
  const tableQuery = useQuery({
    queryKey: ["book-tables"],
    queryFn: () => fetchTables({ includeArchived: true }),
  });
  const tables = tableQuery.data?.tables ?? [];
  const queryFilters: BookingFilters = {
    ...filters,
    ...(!unresolved && !filters.same_network_as ? { business_date: date ?? null } : {}),
    unresolved,
    limit: 30,
  };
  const list = useInfiniteQuery({
    queryKey: ["booking-book", queryFilters],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      fetchBookings({ ...queryFilters, cursor: pageParam ?? null }, signal),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: !!date,
  });
  const bookings = list.data?.pages.flatMap((page) => page.items) ?? [];
  const groups = [...new Set(bookings.map((b) => b.business_date))].sort().reverse();
  const start = business.data?.shift_start ?? new Date().toISOString();
  const end = business.data?.shift_end ?? new Date(Date.now() + 3600000).toISOString();
  const createStart = new Date(
    Math.max(Date.parse(start), Math.ceil(Date.now() / 300000) * 300000),
  ).toISOString();
  const createEnd = new Date(
    Math.min(Date.parse(end), Date.parse(createStart) + 3600000),
  ).toISOString();
  return (
    <main className="page booking-book">
      <header className="page-header">
        <div>
          <Link to="/admin">← Админка</Link>
          <h1>Книга броней</h1>
          <p>
            {me.data?.venue.name} · {timezone}
          </p>
        </div>
        <button onClick={() => setCreating(true)} disabled={!business.data || !tableQuery.data}>
          Новая бронь
        </button>
      </header>
      {notice && <p role="status">{notice}</p>}
      <section className="card" aria-label="Фильтры броней">
        <div className="booking-form-grid">
          <label className="field">
            Бизнес-дата
            <input
              type="date"
              value={date ?? ""}
              onChange={(e) => setDay(e.target.value)}
              disabled={unresolved || !!filters.same_network_as}
            />
          </label>
          <label className="field">
            Статус
            <select
              value={filters.status ?? ""}
              onChange={(e) => setFilters({ ...filters, status: e.target.value })}
            >
              <option value="">Все статусы</option>
              {Object.entries(statuses).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Источник брони
            <select
              value={filters.source ?? ""}
              onChange={(e) => setFilters({ ...filters, source: e.target.value })}
            >
              <option value="">Все источники</option>
              {Object.entries(sources).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Стол
            <select
              value={filters.table_id ?? ""}
              onChange={(e) =>
                setFilters({ ...filters, table_id: e.target.value ? Number(e.target.value) : null })
              }
            >
              <option value="">Все столы</option>
              {tables.map((t) => (
                <option key={t.id} value={t.id}>
                  Стол {t.number}
                </option>
              ))}
            </select>
          </label>
        </div>
        <form
          className="booking-search"
          onSubmit={(e) => {
            e.preventDefault();
            const form = new FormData(e.currentTarget);
            setFilters({
              ...filters,
              phone: String(form.get("phone")),
              number: form.get("number") ? Number(form.get("number")) : null,
            });
          }}
        >
          <label className="field">
            Телефон целиком
            <input name="phone" type="tel" maxLength={50} />
          </label>
          <label className="field">
            Номер брони
            <input name="number" type="number" min={1} />
          </label>
          <button>Найти</button>
          <button
            type="reset"
            onClick={() => {
              setFilters({});
              setUnresolved(false);
            }}
          >
            Сбросить фильтры
          </button>
        </form>
        <label>
          <input
            type="checkbox"
            checked={unresolved}
            onChange={(e) => setUnresolved(e.target.checked)}
          />{" "}
          Требуют обработки · все даты
        </label>
        {filters.same_network_as && (
          <p>
            Расследование сетевого отпечатка выбранной брони. Только записи с действующим сроком
            хранения. <button onClick={() => setFilters({})}>Завершить поиск</button>
          </p>
        )}
        <p>
          {business.data?.is_open
            ? `Смена ${dateTime(start, timezone)} — ${dateTime(end, timezone)}`
            : "В эту бизнес-дату смена закрыта"}
        </p>
      </section>
      {creating && (
        <CreateForm
          timezone={timezone}
          tables={tables}
          start={createStart}
          end={createEnd}
          onClose={() => setCreating(false)}
          onCreated={(booking) => {
            setCreating(false);
            setSelected(booking.id);
            setDay(booking.business_date);
            setFilters({});
            setUnresolved(false);
            setNotice(
              `Бронь №${booking.number} ${booking.status === "OPEN" ? "создана, гости размещены" : "создана"}.`,
            );
            void client.invalidateQueries({ queryKey: ["booking-book"] });
          }}
        />
      )}
      <div className="booking-workspace">
        <div>
          <section className="card">
            <header className="page-header">
              <h2>Брони смены</h2>
              <button onClick={() => list.refetch()}>Обновить список</button>
            </header>
            {list.isLoading && <p>Загрузка…</p>}
            {list.isError && <p role="alert">Не удалось загрузить книгу. Повторите обновление.</p>}
            {!list.isLoading && !list.isError && bookings.length === 0 && (
              <p>Броней по выбранным условиям нет.</p>
            )}
            {groups.map((group) => (
              <section key={group}>
                <h3>{group}</h3>
                <div className="booking-rows">
                  {bookings
                    .filter((b) => b.business_date === group)
                    .sort((a, b) => a.starts_at.localeCompare(b.starts_at) || a.number - b.number)
                    .map((b) => (
                      <button
                        className={`booking-row ${selected === b.id ? "booking-row--selected" : ""}`}
                        key={b.id}
                        onClick={() => setSelected(b.id)}
                      >
                        <span>
                          <strong>
                            №{b.number} · {b.guest_name ?? "Данные удалены"}
                          </strong>
                          <br />
                          {dateTime(b.starts_at, timezone)} — {dateTime(b.ends_at, timezone)}
                          <br />
                          {b.party_size} гостей · Столы {tableNames(b.table_ids, tables)}
                        </span>
                        <span>
                          {statuses[b.status]}
                          <br />
                          <small>{sources[b.source]}</small>
                          {["NEW", "WAITING", "OPEN"].includes(b.status) &&
                            new Date(b.ends_at).getTime() < Date.now() && (
                              <strong className="booking-overdue">Требует обработки</strong>
                            )}
                        </span>
                      </button>
                    ))}
                </div>
              </section>
            ))}
            {list.hasNextPage && (
              <button disabled={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>
                {list.isFetchingNextPage ? "Загрузка…" : "Показать ещё"}
              </button>
            )}
          </section>
          <section className="card">
            <h2>Схема зала</h2>
            <label className="field">
              Зал
              <select value={activeHall ?? ""} onChange={(e) => setHallId(Number(e.target.value))}>
                {halls.data?.halls.map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.name}
                  </option>
                ))}
              </select>
            </label>
            {hall.data && (
              <HallCanvas
                hall={hall.data}
                selectedTableId={filters.table_id ?? undefined}
                onSelect={(id) => setFilters({ ...filters, table_id: id })}
              />
            )}
            <p className="hint">
              Выберите стол на схеме, чтобы отфильтровать брони. Цвет показывает доступность для
              бронирования; занятость смотрите в списке.
            </p>
          </section>
        </div>
        {selected !== undefined && (
          <BookingCard
            key={selected}
            id={selected}
            timezone={timezone}
            tables={tables}
            onClose={() => setSelected(undefined)}
            onNetwork={(id) => {
              setFilters({ same_network_as: id });
              setUnresolved(false);
            }}
          />
        )}
      </div>
    </main>
  );
}
