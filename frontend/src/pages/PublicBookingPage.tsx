/**
 * Public booking UI (PROJECT-SPEC §34, §50).
 *
 * Everything happens on a single, non-scrolling screen sized to `100dvh`: the
 * hall schema fills the stage, and table info / time selection / guest details
 * all live in modals over it. There is no zoom, no pan and no separate page.
 *
 * Availability comes exclusively from the server snapshot; this component only
 * groups the returned 5-minute slots into the four selection stages (start
 * hour → start minutes → end hour → end minutes) and renders a free/busy
 * timeline. The final create call is still validated by the booking core.
 */
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";

import { ApiError } from "@/api/client";
import type { PublicBooking, PublicVenue } from "@/api/public";
import { useCreatePublicBooking, usePublicAvailability, usePublicVenue } from "@/api/publicQueries";
import {
  buildTimeline,
  clockLabel,
  dayOffset,
  endHours,
  endMinutes,
  localTime,
  startHours,
  startMinutes,
  type AvailabilitySlot,
} from "@/components/availabilityOptions";
import { HallCanvas } from "@/components/HallCanvas";

type Stage = "table" | "startHour" | "startMinute" | "endHour" | "endMinute" | "guest";

const MONTH_NAMES = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];
const WEEKDAYS = [
  "воскресенье",
  "понедельник",
  "вторник",
  "среда",
  "четверг",
  "пятница",
  "суббота",
];

function formatDateLabel(value: string): string {
  if (!value) return "";
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  const weekday = WEEKDAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()] ?? "";
  const capitalised = weekday.charAt(0).toUpperCase() + weekday.slice(1);
  return `${capitalised}, ${day} ${MONTH_NAMES[month - 1]} ${year}`;
}

function addMonths(value: string, months: number): string {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  const date = new Date(Date.UTC(year, month - 1 + months, day));
  return date.toISOString().slice(0, 10);
}

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

/** Hours spanning a shift, wrapping past midnight (16,17,…,23,0,1,2). */
function shiftHours(
  shiftStart: string | null,
  shiftEnd: string | null,
  timezone: string,
): { hour: number; dayOffset: number }[] {
  if (!shiftStart || !shiftEnd) return [];
  const start = localTime(shiftStart, timezone);
  const end = localTime(shiftEnd, timezone);
  const wrap = dayOffset(start.date, end.date);
  const result: { hour: number; dayOffset: number }[] = [];
  for (let hour = start.hour; hour < 24; hour += 1) result.push({ hour, dayOffset: 0 });
  if (wrap > 0) {
    for (let hour = 0; hour <= end.hour; hour += 1) result.push({ hour, dayOffset: 1 });
  }
  return result;
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
  const [minDate, setMinDate] = useState("");
  const [hallId, setHallId] = useState(venue.halls[0]?.id);
  const [tableId, setTableId] = useState<number | undefined>();
  const [party, setParty] = useState("2");
  const [stage, setStage] = useState<Stage | null>(null);
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [comment, setComment] = useState("");
  const [consent, setConsent] = useState(false);
  const [honeypot, setHoneypot] = useState("");
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState<PublicBooking>();
  const [retryAt, setRetryAt] = useState(0);
  const [now, setNow] = useState(Date.now());
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
      party_size: validParty ? partySize : 1,
    },
    validParty && hallId !== undefined && !success,
  );

  const hall = venue.halls.find((h) => h.id === hallId);
  const tables = useMemo(() => availability.data?.tables ?? [], [availability.data]);
  const availableIds = useMemo(() => new Set(tables.map((t) => t.id)), [tables]);
  const selectedTable = tables.find((t) => t.id === tableId);
  const selectedHallTable = hall?.tables.find((t) => t.id === tableId);
  const slots: AvailabilitySlot[] = selectedTable?.slots ?? [];

  const canBook = !!selectedTable && slots.length > 0;
  const waiting = retryAt > now;

  // Capture the venue's current business date once, and use it as the initial
  // date and the minimum selectable date (§16: bookings ≤ 2 months ahead).
  useEffect(() => {
    if (!date && availability.data?.business_date) {
      setDate(availability.data.business_date);
      setMinDate(availability.data.business_date);
    }
  }, [date, availability.data]);

  useEffect(() => {
    if (!retryAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [retryAt]);

  // If the selected start/end disappears from a fresh snapshot, drop it and
  // bounce back to the first time stage (never leave an invalid selection).
  useEffect(() => {
    if (!availability.data || availability.isFetching || uncertain.current) return;
    if (tableId === undefined || !startTime) return;
    const stillValid = slots.some(
      (slot) => slot.start === startTime && slot.end_options.includes(endTime),
    );
    if (!stillValid) {
      setStartTime("");
      setEndTime("");
      setStage((prev) =>
        prev === "endHour" || prev === "endMinute" || prev === "guest" ? "startHour" : prev,
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [availability.data, availability.isFetching]);

  function changed() {
    attempt.current = undefined;
    uncertain.current = false;
    setError("");
  }

  function resetTime() {
    changed();
    setStartTime("");
    setEndTime("");
  }

  function selectTable(id: number) {
    resetTime();
    setTableId(id);
    setStage("table");
    setCalendarOpen(false);
    void availability.refetch();
  }

  function changeDate(next: string) {
    if (!next) return;
    setCalendarOpen(false);
    if (next === date) return; // same day: keep the chosen time
    changed();
    setDate(next);
    setStartTime("");
    setEndTime("");
    // Return to the first stage regardless of where the date was changed.
    setStage((prev) => (prev === "table" ? "table" : "startHour"));
  }

  function changeParty(value: string) {
    resetTime();
    setParty(value);
    attempt.current = undefined;
  }

  function changeHall(value: number) {
    resetTime();
    setHallId(value);
    setTableId(undefined);
    setStage(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current || waiting) return;
    const slot = slots.find((s) => s.start === startTime);
    const slotValid = !!slot?.end_options.includes(endTime);
    if (
      !validParty ||
      tableId === undefined ||
      !startTime ||
      !endTime ||
      (!slotValid && !uncertain.current) ||
      !name.trim() ||
      !phone.trim() ||
      !consent
    ) {
      setError("Укажите имя, телефон и подтвердите согласие на обработку данных.");
      return;
    }
    const body = {
      table_id: tableId,
      party_size: partySize,
      starts_at: startTime,
      ends_at: endTime,
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
        setStartTime("");
        setEndTime("");
        attempt.current = undefined;
        setStage("startHour");
        setError(
          "Это время уже заняли или стол стал недоступен. Выберите другое время. Данные гостя сохранены.",
        );
        await availability.refetch();
      } else {
        uncertain.current = !(caught instanceof ApiError) || caught.status >= 500;
        setError(message(caught));
        if (caught instanceof ApiError && caught.status === 429) {
          setRetryAt(Date.now() + (caught.retryAfterSeconds ?? 60) * 1000);
          setNow(Date.now());
        }
      }
    } finally {
      inFlight.current = false;
    }
  }

  const timezone = venue.timezone;
  const maxDate = minDate ? addMonths(minDate, 2) : undefined;
  const shiftHourOptions = shiftHours(
    availability.data?.shift_start ?? null,
    availability.data?.shift_end ?? null,
    timezone,
  );
  const availableStartHours = startHours(slots, timezone);
  const chosenHour = startTime ? localTime(startTime, timezone).hour : undefined;
  const startSlot = slots.find((s) => s.start === startTime);
  const endHourOptions = startSlot && date ? endHours(startSlot, date, timezone) : [];
  const chosenEnd = endTime ? localTime(endTime, timezone) : undefined;
  const endMinuteOptions =
    startSlot && chosenEnd && date
      ? endMinutes(startSlot, chosenEnd.hour, date, timezone, dayOffset(date, chosenEnd.date))
      : [];

  const timeline = buildTimeline(slots);

  function renderDateBar() {
    return (
      <div className="booking-datebar">
        <div className="booking-datebar__value">
          <span className="booking-datebar__label">Дата</span>
          <strong data-testid="selected-date">{formatDateLabel(date) || "—"}</strong>
        </div>
        <button
          type="button"
          className="booking-datebar__calendar"
          aria-label="Изменить дату"
          aria-expanded={calendarOpen}
          onClick={() => setCalendarOpen((open) => !open)}
        >
          📅 Изменить
        </button>
        {calendarOpen ? (
          <div className="booking-calendar" role="dialog" aria-label="Календарь">
            <label className="field">
              <span>Дата брони</span>
              <input
                type="date"
                aria-label="Дата"
                value={date}
                min={minDate || undefined}
                max={maxDate}
                onChange={(event) => changeDate(event.target.value)}
              />
            </label>
          </div>
        ) : null}
      </div>
    );
  }

  function backFrom(stage: Stage): Stage | null {
    switch (stage) {
      case "startMinute":
        return "startHour";
      case "endHour":
        return "startMinute";
      case "endMinute":
        return "endHour";
      case "startHour":
        return "table";
      default:
        return "table";
    }
  }

  function stepTitle(stage: Stage | null): string {
    switch (stage) {
      case "startHour":
        return "Начало — выберите час";
      case "startMinute":
        return "Начало — выберите минуты";
      case "endHour":
        return "Окончание — выберите час";
      case "endMinute":
        return "Окончание — выберите минуты";
      default:
        return "";
    }
  }

  if (success)
    return (
      <div className="public-screen">
        <div className="booking-modal-backdrop">
          <section
            className="booking-modal booking-modal--success"
            role="status"
            aria-label="Бронирование подтверждено"
          >
            <p className="booking-eyebrow">{venue.name}</p>
            <h1>{`Бронь №${success.number} подтверждена`}</h1>
            <dl className="facts">
              <div>
                <dt>Стол</dt>
                <dd>
                  {success.table_ids
                    .map(
                      (id) =>
                        venue.halls.flatMap((h) => h.tables).find((t) => t.id === id)?.number ??
                        String(id),
                    )
                    .join(", ")}
                </dd>
              </div>
              <div>
                <dt>Дата</dt>
                <dd>{formatDateLabel(success.business_date)}</dd>
              </div>
              <div>
                <dt>Начало</dt>
                <dd>{clockLabel(success.starts_at, timezone)}</dd>
              </div>
              <div>
                <dt>Окончание</dt>
                <dd>{clockLabel(success.ends_at, timezone)}</dd>
              </div>
              <div>
                <dt>Гостей</dt>
                <dd>{success.party_size}</dd>
              </div>
            </dl>
            <p className="muted">Время указано для {timezone}. До встречи!</p>
          </section>
        </div>
      </div>
    );

  return (
    <div className="public-screen">
      <header className="public-screen__top">
        <div>
          <p className="booking-eyebrow">Забронировать стол</p>
          <h1>{venue.name}</h1>
        </div>
        <div className="public-screen__controls">
          {venue.halls.length > 1 ? (
            <div className="hall-tabs">
              {venue.halls.map((h) => (
                <button
                  key={h.id}
                  type="button"
                  aria-pressed={hallId === h.id}
                  onClick={() => changeHall(h.id)}
                >
                  {h.name}
                </button>
              ))}
            </div>
          ) : null}
          <label className="field field--inline">
            <span>Гостей</span>
            <input
              type="number"
              inputMode="numeric"
              min={1}
              max={200}
              step={1}
              aria-label="Количество гостей"
              value={party}
              onChange={(event) => changeParty(event.target.value)}
              aria-invalid={!validParty}
            />
          </label>
        </div>
      </header>
      {!validParty ? (
        <p role="alert" className="callout callout--error public-screen__notice">
          Укажите целое число гостей от 1 до 200.
        </p>
      ) : null}
      <main className="public-screen__stage" aria-label="Схема зала">
        {!hall ? (
          <p className="public-screen__notice">Залы пока недоступны.</p>
        ) : (
          <>
            {availability.isFetching ? (
              <p role="status" className="public-screen__notice">
                Проверяем свободные столы…
              </p>
            ) : null}
            {availability.isError ? (
              <p role="alert" className="public-screen__notice">
                {message(availability.error)}{" "}
                <button type="button" onClick={() => void availability.refetch()}>
                  Обновить
                </button>
              </p>
            ) : null}
            {availability.data && !availability.data.is_open && !availability.isFetching ? (
              <p role="status" className="public-screen__notice">
                В этот день заведение закрыто.
              </p>
            ) : null}
            <HallCanvas
              hall={hall}
              selectedTableId={tableId}
              availableTableIds={availableIds}
              onSelect={selectTable}
            />
          </>
        )}
      </main>

      {stage ? (
        <div
          className="booking-modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && stage === "table") setStage(null);
          }}
        >
          <section
            className="booking-modal"
            role="dialog"
            aria-modal="true"
            aria-label={
              stage === "table"
                ? `Стол ${selectedHallTable?.number ?? ""}`
                : stage === "guest"
                  ? "Данные гостя"
                  : stepTitle(stage)
            }
          >
            {stage === "table" ? (
              <>
                <div className="booking-modal__head">
                  <h2>Стол {selectedHallTable?.number}</h2>
                  <button type="button" aria-label="Закрыть" onClick={() => setStage(null)}>
                    ✕
                  </button>
                </div>
                <p className="muted">
                  Вместимость: {selectedHallTable?.capacity ?? 0} мест · {hall?.name}
                </p>
                {renderDateBar()}
                {canBook ? (
                  <>
                    <h3 className="booking-modal__subtitle">Свободно и занято</h3>
                    <div className="booking-timeline" role="img" aria-label="Занятость стола">
                      {timeline.map((segment, index) => {
                        const total = timeline.reduce((acc, s) => acc + (s.endMs - s.startMs), 0);
                        return (
                          <span
                            key={index}
                            className={`booking-timeline__segment booking-timeline__segment--${segment.kind}`}
                            style={{
                              flexGrow: Math.max(1, (segment.endMs - segment.startMs) / total),
                            }}
                          />
                        );
                      })}
                    </div>
                    <ul className="booking-timeline__legend">
                      {timeline.map((segment, index) => (
                        <li key={index}>
                          <span
                            className={`booking-timeline__dot booking-timeline__dot--${segment.kind}`}
                          />
                          {clockLabel(new Date(segment.startMs).toISOString(), timezone)}–
                          {clockLabel(new Date(segment.endMs).toISOString(), timezone)}{" "}
                          {segment.kind === "free" ? "свободно" : "занято"}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <p role="status" className="callout">
                    {availability.data?.is_open
                      ? "На выбранную дату стол полностью занят. Выберите другую дату или другой стол."
                      : "В этот день заведение закрыто."}
                  </p>
                )}
                <div className="booking-modal__actions">
                  <button
                    type="button"
                    className="booking-submit"
                    disabled={!canBook || availability.isFetching}
                    onClick={() => {
                      setError("");
                      setStage("startHour");
                    }}
                  >
                    Забронировать
                  </button>
                </div>
              </>
            ) : stage === "guest" ? (
              <>
                <div className="booking-modal__head">
                  <h2>Ваши данные</h2>
                  <button type="button" aria-label="Закрыть" onClick={() => setStage(null)}>
                    ✕
                  </button>
                </div>
                <dl className="booking-summary">
                  <div>
                    <dt>Стол</dt>
                    <dd>{selectedHallTable?.number}</dd>
                  </div>
                  <div>
                    <dt>Мест</dt>
                    <dd>{selectedHallTable?.capacity}</dd>
                  </div>
                  <div>
                    <dt>Дата</dt>
                    <dd>{formatDateLabel(date)}</dd>
                  </div>
                  <div>
                    <dt>Начало</dt>
                    <dd>{clockLabel(startTime, timezone)}</dd>
                  </div>
                  <div>
                    <dt>Окончание</dt>
                    <dd>
                      {clockLabel(endTime, timezone)}
                      {chosenEnd && date && dayOffset(date, chosenEnd.date) > 0 ? (
                        <span className="muted"> +1 день</span>
                      ) : null}
                    </dd>
                  </div>
                </dl>
                <form onSubmit={submit} noValidate className="booking-guest">
                  <fieldset disabled={mutation.isPending} className="booking-fields">
                    <label className="field">
                      Имя
                      <input
                        autoComplete="given-name"
                        value={name}
                        maxLength={100}
                        required
                        onChange={(event) => {
                          changed();
                          setName(event.target.value);
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
                        onChange={(event) => {
                          changed();
                          setPhone(event.target.value);
                        }}
                      />
                    </label>
                    <label className="field">
                      Комментарий
                      <textarea
                        value={comment}
                        maxLength={1000}
                        rows={2}
                        onChange={(event) => {
                          changed();
                          setComment(event.target.value);
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
                          onChange={(event) => {
                            changed();
                            setHoneypot(event.target.value);
                          }}
                        />
                      </label>
                    </div>
                    <label className="field field--checkbox">
                      <input
                        type="checkbox"
                        checked={consent}
                        required
                        onChange={(event) => {
                          changed();
                          setConsent(event.target.checked);
                        }}
                      />
                      <span>
                        Согласен на обработку имени, телефона и комментария для оформления брони
                        (версия политики {venue.privacy_policy_version}).
                      </span>
                    </label>
                  </fieldset>
                  {error ? (
                    <p role="alert" className="callout callout--error">
                      {error}
                    </p>
                  ) : null}
                  <div className="booking-modal__actions">
                    <button
                      type="button"
                      onClick={() => {
                        setError("");
                        setStage("endMinute");
                      }}
                    >
                      ← Изменить время
                    </button>
                    <button
                      className="booking-submit"
                      type="submit"
                      disabled={
                        mutation.isPending ||
                        waiting ||
                        (!uncertain.current && availability.isFetching)
                      }
                    >
                      {mutation.isPending
                        ? "Бронируем…"
                        : waiting
                          ? `Повторить через ${Math.max(1, Math.ceil((retryAt - now) / 1000))} сек.`
                          : "Подтвердить бронирование"}
                    </button>
                  </div>
                </form>
              </>
            ) : (
              <>
                <div className="booking-modal__head">
                  <button
                    type="button"
                    aria-label="Назад"
                    onClick={() => setStage(backFrom(stage))}
                  >
                    ← Назад
                  </button>
                  <h2>{stepTitle(stage)}</h2>
                  <span aria-hidden="true" />
                </div>
                {renderDateBar()}
                {startTime && stage !== "startHour" && stage !== "startMinute" ? (
                  <p className="muted" data-testid="chosen-start">
                    Начало: {formatDateLabel(localTime(startTime, timezone).date)}{" "}
                    {clockLabel(startTime, timezone)}
                  </p>
                ) : null}
                {!availability.data?.is_open ? (
                  <p role="status" className="callout">
                    В этот день заведение закрыто.
                  </p>
                ) : stage === "startHour" || stage === "startMinute" ? (
                  availableStartHours.length === 0 ? (
                    <p role="status" className="callout">
                      На выбранную дату нет свободного времени для этого стола.
                    </p>
                  ) : null
                ) : null}

                {stage === "startHour" ? (
                  <div className="booking-choice" role="group" aria-label="Час начала">
                    {shiftHourOptions.map(({ hour }) => (
                      <button
                        key={hour}
                        type="button"
                        disabled={!availableStartHours.includes(hour)}
                        aria-pressed={false}
                        onClick={() => {
                          const first = startMinutes(slots, hour, timezone)[0];
                          if (first) {
                            changed();
                            setStartTime(first.start);
                            setEndTime("");
                            setStage("startMinute");
                          }
                        }}
                      >
                        {String(hour).padStart(2, "0")}
                      </button>
                    ))}
                  </div>
                ) : null}

                {stage === "startMinute" ? (
                  <div className="booking-choice" role="group" aria-label="Минуты начала">
                    {Array.from({ length: 12 }, (_, index) => index * 5).map((minute) => {
                      const option = startMinutes(slots, chosenHour ?? 0, timezone).find(
                        (entry) => entry.minute === minute,
                      );
                      return (
                        <button
                          key={minute}
                          type="button"
                          disabled={!option}
                          aria-pressed={option?.start === startTime}
                          onClick={() => {
                            if (!option) return;
                            changed();
                            setStartTime(option.start);
                            setEndTime("");
                            setStage("endHour");
                          }}
                        >
                          {String(minute).padStart(2, "0")}
                        </button>
                      );
                    })}
                  </div>
                ) : null}

                {stage === "endHour" ? (
                  <div className="booking-choice" role="group" aria-label="Час окончания">
                    {endHourOptions.map(({ hour, dayOffset: offset }) => (
                      <button
                        key={`${offset}-${hour}`}
                        type="button"
                        aria-pressed={
                          chosenEnd?.hour === hour && dayOffset(date, chosenEnd.date) === offset
                        }
                        onClick={() => {
                          changed();
                          // Keep a valid end: default to the first valid minute
                          // in this hour so the choice stays consistent.
                          const options = endMinutes(startSlot!, hour, date, timezone, offset);
                          setEndTime(options[0]?.end ?? "");
                          setStage("endMinute");
                        }}
                      >
                        {String(hour).padStart(2, "0")}
                        {offset > 0 ? <small> +1 день</small> : null}
                      </button>
                    ))}
                  </div>
                ) : null}

                {stage === "endMinute" ? (
                  <div className="booking-choice" role="group" aria-label="Минуты окончания">
                    {Array.from({ length: 12 }, (_, index) => index * 5).map((minute) => {
                      const option = endMinuteOptions.find((entry) => entry.minute === minute);
                      return (
                        <button
                          key={minute}
                          type="button"
                          disabled={!option}
                          aria-pressed={option?.end === endTime}
                          onClick={() => {
                            if (!option) return;
                            changed();
                            setEndTime(option.end);
                            setStage("guest");
                          }}
                        >
                          {String(minute).padStart(2, "0")}
                        </button>
                      );
                    })}
                  </div>
                ) : null}

                {error ? (
                  <p role="alert" className="callout callout--error">
                    {error}
                  </p>
                ) : null}
              </>
            )}
          </section>
        </div>
      ) : null}
    </div>
  );
}
