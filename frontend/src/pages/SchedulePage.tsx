import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import {
  useBusinessDay,
  useDeleteScheduleException,
  useSchedule,
  useScheduleExceptions,
  useUpdateSchedule,
  useUpsertScheduleException,
} from "@/api/adminQueries";
import { ApiError } from "@/api/client";

const WEEKDAY_NAMES = [
  "Понедельник",
  "Вторник",
  "Среда",
  "Четверг",
  "Пятница",
  "Суббота",
  "Воскресенье",
];

/** 5-minute grid, matching the backend `SLOT_MINUTES` (PROJECT-SPEC §2). */
const TIME_STEP_SECONDS = 300;

interface DayState {
  weekday: number;
  isOpen: boolean;
  openTime: string;
  closeTime: string;
}

function scheduleErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "SCHEDULE_OVERLAP":
        return "Смены соседних дней пересекаются. Ночная смена предыдущего дня заходит на смену следующего.";
      case "SCHEDULE_INVALID":
        return "Недопустимое расписание: время должно быть кратно 5 минутам, а открытие и закрытие — различаться.";
      default:
        return `Не удалось сохранить расписание (код ${error.status}).`;
    }
  }
  return "Не удалось сохранить расписание. Проверьте соединение.";
}

function formatShift(start: string | null | undefined, end: string | null | undefined): string {
  if (!start || !end) {
    return "закрыто";
  }
  const time = (value: string) => value.slice(11, 16);
  return `${time(start)} – ${time(end)}`;
}

export function SchedulePage() {
  const schedule = useSchedule();
  const exceptions = useScheduleExceptions();
  const businessDay = useBusinessDay();
  const updateSchedule = useUpdateSchedule();
  const upsertException = useUpsertScheduleException();
  const deleteException = useDeleteScheduleException();

  const [days, setDays] = useState<DayState[]>([]);
  const [saved, setSaved] = useState(false);

  const [exceptionDate, setExceptionDate] = useState("");
  const [exceptionClosed, setExceptionClosed] = useState(true);
  const [exceptionOpen, setExceptionOpen] = useState("10:00");
  const [exceptionClose, setExceptionClose] = useState("22:00");

  useEffect(() => {
    if (schedule.data) {
      setDays(
        schedule.data.weekdays.map((day) => ({
          weekday: day.weekday,
          isOpen: day.is_open,
          openTime: day.open_time ?? "10:00",
          closeTime: day.close_time ?? "22:00",
        })),
      );
    }
  }, [schedule.data]);

  function updateDay(weekday: number, patch: Partial<DayState>) {
    setDays((previous) =>
      previous.map((day) => (day.weekday === weekday ? { ...day, ...patch } : day)),
    );
  }

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaved(false);
    try {
      await updateSchedule.mutateAsync({
        weekdays: days.map((day) => ({
          weekday: day.weekday,
          is_open: day.isOpen,
          open_time: day.isOpen ? day.openTime : null,
          close_time: day.isOpen ? day.closeTime : null,
        })),
      });
      setSaved(true);
    } catch {
      // The error is rendered from `updateSchedule.error` below.
    }
  }

  async function handleExceptionSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await upsertException.mutateAsync({
        date: exceptionDate,
        body: {
          is_closed: exceptionClosed,
          open_time: exceptionClosed ? null : exceptionOpen,
          close_time: exceptionClosed ? null : exceptionClose,
        },
      });
      setExceptionDate("");
    } catch {
      // The error is rendered from `upsertException.error` below.
    }
  }

  return (
    <main className="page">
      <header className="page-header">
        <h1>Расписание</h1>
        <Link to="/admin">← В админку</Link>
      </header>

      <section className="card" aria-label="Текущий рабочий день">
        <h2>Сейчас</h2>
        {businessDay.isError ? (
          <p role="alert">Не удалось определить рабочий день.</p>
        ) : (
          <dl className="facts">
            <div>
              <dt>Текущая business date</dt>
              <dd>{businessDay.data?.current_business_date ?? "—"}</dd>
            </div>
            <div>
              <dt>Открыто сейчас</dt>
              <dd>{businessDay.data?.is_open_now ? "да" : "нет"}</dd>
            </div>
            <div>
              <dt>Смена</dt>
              <dd>{formatShift(businessDay.data?.shift_start, businessDay.data?.shift_end)}</dd>
            </div>
            <div>
              <dt>Timezone</dt>
              <dd>{schedule.data?.timezone ?? businessDay.data?.timezone ?? "—"}</dd>
            </div>
          </dl>
        )}
      </section>

      <section className="card" aria-label="Недельное расписание">
        <h2>Недельное расписание</h2>
        <p>
          Смена через полночь задаётся как закрытие раньше открытия, например 16:00 → 02:00. Время
          кратно 5 минутам.
        </p>
        {schedule.isError ? (
          <p role="alert">Не удалось загрузить расписание.</p>
        ) : (
          <form onSubmit={handleSave}>
            <ul className="schedule-list">
              {days.map((day) => (
                <li key={day.weekday} className="schedule-row">
                  <span className="schedule-row__name">{WEEKDAY_NAMES[day.weekday]}</span>
                  <label className="field field--checkbox">
                    <input
                      type="checkbox"
                      checked={day.isOpen}
                      aria-label={`${WEEKDAY_NAMES[day.weekday]} открыто`}
                      onChange={(event) => updateDay(day.weekday, { isOpen: event.target.checked })}
                    />
                    <span>Открыто</span>
                  </label>
                  <label className="field">
                    <span>Открытие</span>
                    <input
                      type="time"
                      step={TIME_STEP_SECONDS}
                      value={day.openTime}
                      disabled={!day.isOpen}
                      aria-label={`${WEEKDAY_NAMES[day.weekday]} открытие`}
                      onChange={(event) => updateDay(day.weekday, { openTime: event.target.value })}
                    />
                  </label>
                  <label className="field">
                    <span>Закрытие</span>
                    <input
                      type="time"
                      step={TIME_STEP_SECONDS}
                      value={day.closeTime}
                      disabled={!day.isOpen}
                      aria-label={`${WEEKDAY_NAMES[day.weekday]} закрытие`}
                      onChange={(event) =>
                        updateDay(day.weekday, { closeTime: event.target.value })
                      }
                    />
                  </label>
                </li>
              ))}
            </ul>
            <button type="submit" disabled={updateSchedule.isPending}>
              {updateSchedule.isPending ? "Сохранение…" : "Сохранить расписание"}
            </button>
            {updateSchedule.isError ? (
              <p role="alert">{scheduleErrorMessage(updateSchedule.error)}</p>
            ) : null}
            {saved ? <p role="status">Сохранено.</p> : null}
          </form>
        )}
      </section>

      <section className="card" aria-label="Исключения расписания">
        <h2>Исключения по датам</h2>
        <p>Исключение полностью заменяет недельное расписание на конкретную business date.</p>

        {exceptions.data && exceptions.data.exceptions.length > 0 ? (
          <ul className="exception-list">
            {exceptions.data.exceptions.map((item) => (
              <li key={item.date} className="exception-row">
                <span className="exception-row__date">{item.date}</span>
                <span>
                  {item.is_closed
                    ? "выходной"
                    : `${item.open_time ?? "—"} – ${item.close_time ?? "—"}`}
                </span>
                <button
                  type="button"
                  className="button--danger"
                  onClick={() => deleteException.mutate(item.date)}
                  disabled={deleteException.isPending}
                >
                  Удалить
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p>Исключений нет.</p>
        )}

        <form onSubmit={handleExceptionSubmit}>
          <label className="field">
            <span>Дата</span>
            <input
              type="date"
              required
              value={exceptionDate}
              onChange={(event) => setExceptionDate(event.target.value)}
            />
          </label>
          <label className="field field--checkbox">
            <input
              type="checkbox"
              checked={exceptionClosed}
              onChange={(event) => setExceptionClosed(event.target.checked)}
            />
            <span>Выходной</span>
          </label>
          {!exceptionClosed ? (
            <>
              <label className="field">
                <span>Открытие</span>
                <input
                  type="time"
                  step={TIME_STEP_SECONDS}
                  value={exceptionOpen}
                  onChange={(event) => setExceptionOpen(event.target.value)}
                />
              </label>
              <label className="field">
                <span>Закрытие</span>
                <input
                  type="time"
                  step={TIME_STEP_SECONDS}
                  value={exceptionClose}
                  onChange={(event) => setExceptionClose(event.target.value)}
                />
              </label>
            </>
          ) : null}
          <button type="submit" disabled={upsertException.isPending}>
            {upsertException.isPending ? "Сохранение…" : "Сохранить исключение"}
          </button>
          {upsertException.isError ? (
            <p role="alert">{scheduleErrorMessage(upsertException.error)}</p>
          ) : null}
        </form>
      </section>
    </main>
  );
}
