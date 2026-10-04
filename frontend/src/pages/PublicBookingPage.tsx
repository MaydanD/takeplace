import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { ApiError } from "@/api/client";
import type { PublicBooking, PublicVenue } from "@/api/public";
import { useCreatePublicBooking, usePublicAvailability, usePublicVenue } from "@/api/publicQueries";
import { HallCanvas } from "@/components/HallCanvas";

function message(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429)
      return `Слишком много запросов. Повторите через ${error.retryAfterSeconds ?? 60} сек. Данные формы сохранены.`;
    if (error.code === "ONLINE_BOOKING_DISABLED")
      return "Онлайн-бронирование временно отключено. Свяжитесь с заведением.";
    if (error.code === "CAPTCHA_REQUIRED")
      return "Бронирование требует дополнительной проверки. Свяжитесь с заведением.";
    if (error.status === 422 || error.code === "BOOKING_RULE_VIOLATION")
      return "Проверьте данные и выбранное время. Возможно, условия бронирования изменились.";
    if (error.status === 404) return "Заведение или выбранный стол больше недоступны.";
  }
  return "Не удалось получить ответ. Проверьте соединение и повторите отправку — повторная бронь не создастся.";
}
function time(value: string, timezone: string) {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: timezone,
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
export function PublicBookingPage() {
  const { slug = "" } = useParams();
  const venue = usePublicVenue(slug);
  if (venue.isPending)
    return (
      <main className="page">
        <p role="status">Загружаем заведение…</p>
      </main>
    );
  if (venue.isError)
    return (
      <main className="page">
        <h1>Бронирование</h1>
        <p role="alert">
          {venue.error instanceof ApiError && venue.error.status === 404
            ? "Заведение не найдено или недоступно."
            : "Не удалось загрузить заведение. Попробуйте ещё раз."}
        </p>
        <button onClick={() => void venue.refetch()}>Повторить</button>
      </main>
    );
  if (!venue.data.is_active || !venue.data.online_booking_enabled)
    return (
      <main className="page">
        <h1>{venue.data.name}</h1>
        <p role="status">Онлайн-бронирование временно недоступно. Свяжитесь с заведением.</p>
      </main>
    );
  return <BookingFlow key={slug} venue={venue.data} />;
}
function BookingFlow({ venue }: { venue: PublicVenue }) {
  const [date, setDate] = useState("");
  const [hallId, setHallId] = useState(venue.halls[0]?.id);
  const [tableId, setTableId] = useState<number>();
  const [party, setParty] = useState("2");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [comment, setComment] = useState("");
  const [consent, setConsent] = useState(false);
  const [honeypot, setHoneypot] = useState("");
  const [view, setView] = useState<"map" | "list">("map");
  const [error, setError] = useState("");
  const [blocked, setBlocked] = useState(false);
  const [retryAt, setRetryAt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [success, setSuccess] = useState<PublicBooking>();
  const attempt = useRef<{ key: string; body: string }>();
  const inFlight = useRef(false);
  const uncertain = useRef(false);
  const mutation = useCreatePublicBooking(venue.slug);
  const partySize = Number(party);
  const validParty = Number.isInteger(partySize) && partySize >= 1 && partySize <= 200;
  const availability = usePublicAvailability(
    venue.slug,
    {
      ...(date ? { business_date: date } : {}),
      ...(hallId !== undefined ? { hall_id: hallId } : {}),
      party_size: partySize,
    },
    validParty && hallId !== undefined && !success,
  );
  const hall = venue.halls.find((h) => h.id === hallId);
  const tables = useMemo(() => availability.data?.tables ?? [], [availability.data]);
  const availableIds = new Set(
    availability.isError || availability.isFetching ? [] : tables.map((t) => t.id),
  );
  const selectedTable = tables.find((t) => t.id === tableId);
  const slot = selectedTable?.slots.find((s) => s.start === start);
  const slotValid = !!slot?.end_options.includes(end);
  const waiting = retryAt > now;
  useEffect(() => {
    if (!retryAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [retryAt]);
  // Do not discard an uncertain network submission: its exact payload and key
  // must survive even if a later snapshot no longer advertises that interval.
  useEffect(() => {
    if (!availability.data || availability.isFetching || uncertain.current) return;
    if (tableId !== undefined && !tables.some((t) => t.id === tableId)) {
      setTableId(undefined);
      setStart("");
      setEnd("");
      setError("Выбранный стол больше недоступен. Выберите другой вариант.");
    } else if (start && !slotValid) {
      setStart("");
      setEnd("");
      setError("Выбранное время больше недоступно. Выберите другое.");
    }
  }, [availability.data, availability.isFetching, tables, tableId, start, slotValid]);
  function changed() {
    attempt.current = undefined;
    uncertain.current = false;
    setError("");
  }
  function resetTime() {
    changed();
    setStart("");
    setEnd("");
  }
  function selectTable(id: number) {
    resetTime();
    setTableId(id);
    void availability.refetch();
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current || waiting || blocked) return;
    if (
      !validParty ||
      tableId === undefined ||
      !start ||
      !end ||
      (!slotValid && !uncertain.current) ||
      !name.trim() ||
      !phone.trim() ||
      !consent
    ) {
      setError("Выберите стол и время, укажите имя и телефон и подтвердите согласие.");
      return;
    }
    const body = {
      table_id: tableId,
      party_size: partySize,
      starts_at: start,
      ends_at: end,
      guest_name: name.trim(),
      guest_phone_raw: phone.trim(),
      guest_comment: comment.trim() || null,
      privacy_policy_version: venue.privacy_policy_version,
      honeypot,
      captcha_token: null,
    };
    const serialized = JSON.stringify(body);
    if (!attempt.current || attempt.current.body !== serialized)
      attempt.current = { key: crypto.randomUUID(), body: serialized };
    inFlight.current = true;
    setError("");
    try {
      const booking = await mutation.mutateAsync({ body, key: attempt.current.key });
      setSuccess(booking);
      attempt.current = undefined;
      uncertain.current = false;
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        ["BOOKING_CONFLICT", "TABLE_NOT_BOOKABLE", "HALL_NOT_BOOKABLE"].includes(caught.code ?? "")
      ) {
        uncertain.current = false;
        setStart("");
        setEnd("");
        attempt.current = undefined;
        setError(
          "Это время уже заняли или стол стал недоступен. Выберите другое время или стол. Данные гостя сохранены.",
        );
        await availability.refetch();
      } else {
        uncertain.current = !(caught instanceof ApiError) || caught.status >= 500;
        setError(message(caught));
        if (caught instanceof ApiError && caught.status === 429) {
          setRetryAt(Date.now() + (caught.retryAfterSeconds ?? 60) * 1000);
          setNow(Date.now());
        }
        if (caught instanceof ApiError && caught.code === "ONLINE_BOOKING_DISABLED")
          setBlocked(true);
      }
    } finally {
      inFlight.current = false;
    }
  }
  if (success)
    return (
      <main className="page public-booking">
        <section className="card" role="status">
          <p className="booking-eyebrow">{venue.name}</p>
          <h1>Бронь №{success.number} подтверждена</h1>
          <dl className="facts">
            <div>
              <dt>Начало</dt>
              <dd>{time(success.starts_at, venue.timezone)}</dd>
            </div>
            <div>
              <dt>Окончание</dt>
              <dd>{time(success.ends_at, venue.timezone)}</dd>
            </div>
            <div>
              <dt>Зал / стол</dt>
              <dd>
                {hall?.name} / {hall?.tables.find((t) => success.table_ids.includes(t.id))?.number}
              </dd>
            </div>
            <div>
              <dt>Гостей</dt>
              <dd>{success.party_size}</dd>
            </div>
          </dl>
          <p>Время указано для {venue.timezone}. До встречи!</p>
        </section>
      </main>
    );
  return (
    <main className="page page--wide public-booking">
      <header>
        <p className="booking-eyebrow">Забронировать стол</p>
        <h1>{venue.name}</h1>
        <p className="muted">Выберите место и время. Все часы — {venue.timezone}.</p>
      </header>
      <form onSubmit={submit} noValidate>
        <fieldset disabled={mutation.isPending} className="booking-fields">
          <section className="card">
            <h2>1. Дата и гости</h2>
            <div className="booking-grid">
              <label className="field">
                Дата
                <input
                  type="date"
                  required
                  value={date || availability.data?.business_date || ""}
                  onChange={(e) => {
                    resetTime();
                    setDate(e.target.value);
                    setTableId(undefined);
                  }}
                />
              </label>
              <label className="field">
                Количество гостей
                <input
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={200}
                  step={1}
                  value={party}
                  onChange={(e) => {
                    resetTime();
                    setParty(e.target.value);
                  }}
                  aria-invalid={!validParty}
                />
              </label>
            </div>
            {!validParty && <p role="alert">Укажите целое число гостей от 1 до 200.</p>}
          </section>
          <section className="card">
            <h2>2. Зал и стол</h2>
            <div className="hall-tabs">
              {venue.halls.map((h) => (
                <button
                  type="button"
                  key={h.id}
                  aria-pressed={hallId === h.id}
                  onClick={() => {
                    resetTime();
                    setHallId(h.id);
                    setTableId(undefined);
                  }}
                >
                  {h.name}
                </button>
              ))}
            </div>
            {!hall && <p>Залы пока недоступны.</p>}
            {availability.isFetching && <p role="status">Проверяем свободные столы…</p>}
            {availability.isError && (
              <div role="alert">
                <p>{message(availability.error)}</p>
                <button type="button" onClick={() => void availability.refetch()}>
                  Обновить доступность
                </button>
              </div>
            )}
            {availability.data &&
              !availability.isFetching &&
              !availability.isError &&
              (availability.data.is_open ? (
                tables.length === 0 && (
                  <p role="status">
                    Нет подходящих свободных столов. Измените дату или количество гостей.
                  </p>
                )
              ) : (
                <p role="status">В этот день заведение закрыто.</p>
              ))}
            {hall && (
              <>
                <div className="hall-tabs">
                  <button
                    type="button"
                    aria-pressed={view === "map"}
                    onClick={() => setView("map")}
                  >
                    Схема
                  </button>
                  <button
                    type="button"
                    aria-pressed={view === "list"}
                    onClick={() => setView("list")}
                  >
                    Список
                  </button>
                </div>
                {view === "map" ? (
                  <HallCanvas
                    hall={hall}
                    selectedTableId={tableId}
                    availableTableIds={availableIds}
                    onSelect={selectTable}
                  />
                ) : (
                  <div className="booking-options">
                    {hall.tables.map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        disabled={!availableIds.has(t.id)}
                        aria-pressed={tableId === t.id}
                        onClick={() => selectTable(t.id)}
                      >
                        Стол {t.number} · {t.capacity} мест
                        {!availableIds.has(t.id) ? " · недоступен" : ""}
                      </button>
                    ))}
                  </div>
                )}
                <p className="muted">
                  Зелёный — доступен; серый — недоступен; обводка — выбран. Доступность зависит от
                  количества гостей.
                </p>
              </>
            )}
          </section>
          <section className="card">
            <h2>3. Время</h2>
            <div className="booking-grid">
              <label className="field">
                Начало
                <select
                  value={start}
                  disabled={!selectedTable || availability.isFetching}
                  onChange={(e) => {
                    changed();
                    setStart(e.target.value);
                    setEnd(
                      selectedTable?.slots.find((s) => s.start === e.target.value)?.earliest_end ??
                        "",
                    );
                  }}
                >
                  <option value="">Выберите время</option>
                  {selectedTable?.slots.map((s) => (
                    <option key={s.start} value={s.start}>
                      {time(s.start, venue.timezone)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Окончание
                <select
                  value={end}
                  disabled={!slot || availability.isFetching}
                  onChange={(e) => {
                    changed();
                    setEnd(e.target.value);
                  }}
                >
                  <option value="">Выберите окончание</option>
                  {slot?.end_options.map((value) => (
                    <option key={value} value={value}>
                      {time(value, venue.timezone)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p className="muted">
              Показываем только доступное время. Для ночной смены рядом с часами указана дата.
            </p>
          </section>
          <section className="card">
            <h2>4. Ваши данные</h2>
            <label className="field">
              Имя
              <input
                autoComplete="given-name"
                value={name}
                maxLength={100}
                required
                onChange={(e) => {
                  changed();
                  setName(e.target.value);
                }}
              />
            </label>
            <label className="field">
              Телефон
              <input
                type="tel"
                autoComplete="tel"
                value={phone}
                maxLength={50}
                required
                onChange={(e) => {
                  changed();
                  setPhone(e.target.value);
                }}
              />
            </label>
            <label className="field">
              Комментарий
              <textarea
                value={comment}
                maxLength={1000}
                rows={3}
                onChange={(e) => {
                  changed();
                  setComment(e.target.value);
                }}
              />
            </label>
            <div className="booking-honeypot" aria-hidden="true">
              <label>
                Website
                <input
                  name="honeypot"
                  tabIndex={-1}
                  autoComplete="off"
                  value={honeypot}
                  onChange={(e) => {
                    changed();
                    setHoneypot(e.target.value);
                  }}
                />
              </label>
            </div>
            <label className="field field--checkbox">
              <input
                type="checkbox"
                checked={consent}
                required
                onChange={(e) => {
                  changed();
                  setConsent(e.target.checked);
                }}
              />
              <span>
                Согласен на обработку имени, телефона и комментария для оформления брони (версия
                политики {venue.privacy_policy_version}).
              </span>
            </label>
          </section>
        </fieldset>
        {error && (
          <p role="alert" className="callout callout--error booking-feedback">
            {error}
          </p>
        )}
        <button
          className="booking-submit"
          type="submit"
          disabled={
            mutation.isPending ||
            waiting ||
            blocked ||
            (!uncertain.current && availability.isFetching)
          }
        >
          {mutation.isPending
            ? "Бронируем…"
            : waiting
              ? `Повторить через ${Math.max(1, Math.ceil((retryAt - now) / 1000))} сек.`
              : "Забронировать"}
        </button>
      </form>
    </main>
  );
}
