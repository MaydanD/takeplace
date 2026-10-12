/**
 * Pure derivations for the public booking flow.
 *
 * The server is the single source of truth for availability: the public
 * availability endpoint returns, per table, a list of valid 5-minute start
 * slots and, for each start, the exact set of valid end times
 * (`end_options`). Nothing here invents booking rules — these helpers only
 * group the server-provided ISO timestamps into the hour/minute steps of the
 * UI and turn them into a compact free/busy timeline.
 */

export interface AvailabilitySlot {
  start: string;
  earliest_end: string;
  latest_end: string;
  end_options: string[];
}

export interface LocalTime {
  /** Local calendar date in the venue timezone, `YYYY-MM-DD`. */
  date: string;
  hour: number;
  minute: number;
}

const formatterCache = new Map<string, Intl.DateTimeFormat>();

function partsFormatter(timezone: string): Intl.DateTimeFormat {
  let formatter = formatterCache.get(timezone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    formatterCache.set(timezone, formatter);
  }
  return formatter;
}

/** Local date/hour/minute of an ISO instant in the venue timezone. */
export function localTime(iso: string, timezone: string): LocalTime {
  const parts = partsFormatter(timezone).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "0";
  return {
    date: `${get("year")}-${get("month")}-${get("day")}`,
    hour: Number(get("hour")),
    minute: Number(get("minute")),
  };
}

function toUtcMs(date: string): number {
  const [year, month, day] = date.split("-").map(Number);
  return Date.UTC(year ?? 0, (month ?? 1) - 1, day ?? 1);
}

/** Whole-day difference `b - a` between two `YYYY-MM-DD` dates. */
export function dayOffset(a: string, b: string): number {
  return Math.round((toUtcMs(b) - toUtcMs(a)) / 86_400_000);
}

/** HH:MM label of an instant in the venue timezone. */
export function clockLabel(iso: string, timezone: string): string {
  const { hour, minute } = localTime(iso, timezone);
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

/** Distinct valid start hours, ascending. */
export function startHours(slots: AvailabilitySlot[], timezone: string): number[] {
  const hours = new Set<number>();
  for (const slot of slots) hours.add(localTime(slot.start, timezone).hour);
  return [...hours].sort((a, b) => a - b);
}

/** Valid 5-minute start times inside the chosen hour, with their ISO start. */
export function startMinutes(
  slots: AvailabilitySlot[],
  hour: number,
  timezone: string,
): { minute: number; start: string }[] {
  const seen = new Map<number, string>();
  for (const slot of slots) {
    const local = localTime(slot.start, timezone);
    if (local.hour === hour && !seen.has(local.minute)) seen.set(local.minute, slot.start);
  }
  return [...seen.entries()]
    .map(([minute, start]) => ({ minute, start }))
    .sort((a, b) => a.minute - b.minute);
}

export interface EndHourOption {
  hour: number;
  /** 0 = same shift date, 1 = after midnight. */
  dayOffset: number;
}

/** Distinct valid end hours for a chosen start, including after-midnight ones. */
export function endHours(
  slot: AvailabilitySlot,
  businessDate: string,
  timezone: string,
): EndHourOption[] {
  const seen = new Map<string, EndHourOption>();
  for (const iso of slot.end_options) {
    const local = localTime(iso, timezone);
    const offset = dayOffset(businessDate, local.date);
    const key = `${offset}:${local.hour}`;
    if (!seen.has(key)) seen.set(key, { hour: local.hour, dayOffset: offset });
  }
  return [...seen.values()].sort((a, b) => a.dayOffset - b.dayOffset || a.hour - b.hour);
}

/** Valid 5-minute end times inside the chosen end hour, with their ISO end. */
export function endMinutes(
  slot: AvailabilitySlot,
  hour: number,
  businessDate: string,
  timezone: string,
  offset = 0,
): { minute: number; end: string }[] {
  const seen = new Map<number, string>();
  for (const iso of slot.end_options) {
    const local = localTime(iso, timezone);
    if (
      local.hour === hour &&
      dayOffset(businessDate, local.date) === offset &&
      !seen.has(local.minute)
    ) {
      seen.set(local.minute, iso);
    }
  }
  return [...seen.entries()]
    .map(([minute, end]) => ({ minute, end }))
    .sort((a, b) => a.minute - b.minute);
}

export interface TimelineSegment {
  kind: "free" | "busy";
  startMs: number;
  endMs: number;
}

function mergeFreeIntervals(slots: AvailabilitySlot[]): { startMs: number; endMs: number }[] {
  const intervals = slots
    .map((slot) => ({ startMs: Date.parse(slot.start), endMs: Date.parse(slot.latest_end) }))
    .filter((interval) => interval.endMs > interval.startMs)
    .sort((a, b) => a.startMs - b.startMs);
  const merged: { startMs: number; endMs: number }[] = [];
  for (const interval of intervals) {
    const last = merged[merged.length - 1];
    if (last && interval.startMs <= last.endMs) {
      last.endMs = Math.max(last.endMs, interval.endMs);
    } else {
      merged.push({ ...interval });
    }
  }
  return merged;
}

/**
 * Free/busy timeline derived from the server slots: free spans are the union of
 * `[start, latest_end]`, and the gaps between them are the occupied spans. It
 * intentionally shows only the planning window the server returned, so it never
 * claims past time is "occupied".
 */
export function buildTimeline(slots: AvailabilitySlot[]): TimelineSegment[] {
  const free = mergeFreeIntervals(slots);
  const segments: TimelineSegment[] = [];
  free.forEach((interval, index) => {
    if (index > 0) {
      const previous = free[index - 1]!;
      if (interval.startMs > previous.endMs) {
        segments.push({ kind: "busy", startMs: previous.endMs, endMs: interval.startMs });
      }
    }
    segments.push({ kind: "free", startMs: interval.startMs, endMs: interval.endMs });
  });
  return segments;
}
