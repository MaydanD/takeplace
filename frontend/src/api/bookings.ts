import { apiRequest, ApiError } from "@/api/client";
import type { components, operations } from "@/api/generated/schema";

export type Booking = components["schemas"]["BookingSummary"];
export type BookingCreate = components["schemas"]["BookingCreate"];
export type GuestEdit = components["schemas"]["BookingGuestEdit"];
export type BookingCancel = components["schemas"]["BookingCancel"];
export type ChangeTime = components["schemas"]["BookingChangeTime"];
export type BookingFilters = NonNullable<
  operations["get_bookings_api_admin_v1_bookings_get"]["parameters"]["query"]
>;
const base = "/api/admin/v1/bookings";

export function fetchBookings(filters: BookingFilters, signal?: AbortSignal) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  });
  return apiRequest<components["schemas"]["BookingListResponse"]>(`${base}?${params}`, { signal });
}
export const fetchBooking = (id: number) => apiRequest<Booking>(`${base}/${id}`);
export const fetchHistory = (id: number) =>
  apiRequest<components["schemas"]["BookingHistoryResponse"]>(`${base}/${id}/history`);
export const createBooking = (body: BookingCreate, key: string) =>
  apiRequest<Booking>(base, { method: "POST", body, headers: { "Idempotency-Key": key } });
export const editGuest = (id: number, body: GuestEdit) =>
  apiRequest<Booking>(`${base}/${id}`, { method: "PATCH", body });
export const cancelBooking = (id: number, body: BookingCancel) =>
  apiRequest<Booking>(`${base}/${id}/cancel`, { method: "POST", body });
export const changeTime = (id: number, body: ChangeTime) =>
  apiRequest<Booking>(`${base}/${id}/change-time`, { method: "POST", body });

export function bookingError(error: unknown): string {
  if (!(error instanceof ApiError))
    return "Ошибка сети. Повторите запрос; при создании сохранится ключ операции.";
  const messages: Record<string, string> = {
    BOOKING_STALE:
      "Бронь уже изменена другим администратором. Загружена новая версия. Проверьте данные перед повторным изменением.",
    BOOKING_CONFLICT: "Стол уже занят в выбранное время. Выберите другое время или стол.",
    TABLE_LIVE_CONFLICT: "За столом уже находятся гости. Выберите другой стол.",
    BOOKING_RULE_VIOLATION:
      "Проверьте время смены, шаг 5 минут, длительность и вместимость выбранных столов. Для PHONE и OTHER нужен телефон.",
    TABLE_NOT_BOOKABLE: "Стол сейчас недоступен для бронирования.",
    HALL_NOT_BOOKABLE: "Зал сейчас недоступен для бронирования.",
    BOOKING_INVALID_STATE: "Статус брони изменился. Это действие больше недоступно.",
    IDEMPOTENCY_KEY_REUSED:
      "Ключ создания уже использован с другими данными. Обновите книгу и проверьте созданную бронь.",
    RATE_LIMITED: "Слишком много запросов. Подождите и повторите.",
  };
  return (
    messages[error.code ?? ""] ?? "Не удалось выполнить действие. Проверьте данные и повторите."
  );
}

export function venueInput(instant: string, timezone: string): string {
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(instant));
  return parts.replace(" ", "T");
}

export function venueInstant(local: string, timezone: string): string {
  const date = new Date(`${local}:00Z`);
  const offset =
    new Intl.DateTimeFormat("en", { timeZone: timezone, timeZoneName: "longOffset" })
      .formatToParts(date)
      .find((p) => p.type === "timeZoneName")
      ?.value.replace("GMT", "") || "+00:00";
  return new Date(`${local}:00${offset}`).toISOString();
}
