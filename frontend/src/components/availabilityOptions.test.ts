import { describe, expect, it } from "vitest";

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

const TZ = "Europe/Moscow";
const slot = (start: string, ends: string[]): AvailabilitySlot => ({
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
const S2330 = slot("2026-10-23T23:30:00+03:00", [
  "2026-10-24T01:30:00+03:00",
  "2026-10-24T01:45:00+03:00",
]);

describe("availabilityOptions", () => {
  it("reads local time in the venue timezone", () => {
    expect(localTime(S18.start, TZ)).toEqual({ date: "2026-10-23", hour: 18, minute: 0 });
    expect(clockLabel(S2330.end_options[1]!, TZ)).toBe("01:45");
    expect(dayOffset("2026-10-23", "2026-10-24")).toBe(1);
  });

  it("groups valid start hours and minutes from the server slots", () => {
    expect(startHours([S18, S2330], TZ)).toEqual([18, 23]);
    expect(startMinutes([S18, S2330], 18, TZ)).toEqual([{ minute: 0, start: S18.start }]);
    expect(startMinutes([S18, S2330], 23, TZ)).toEqual([{ minute: 30, start: S2330.start }]);
  });

  it("includes after-midnight end hours with the correct day offset", () => {
    expect(endHours(S18, "2026-10-23", TZ)).toEqual([
      { hour: 18, dayOffset: 0 },
      { hour: 19, dayOffset: 0 },
    ]);
    expect(endHours(S2330, "2026-10-23", TZ)).toEqual([{ hour: 1, dayOffset: 1 }]);
  });

  it("filters end minutes to the chosen hour and day", () => {
    expect(endMinutes(S18, 19, "2026-10-23", TZ)).toEqual([
      { minute: 0, end: "2026-10-23T19:00:00+03:00" },
      { minute: 30, end: "2026-10-23T19:30:00+03:00" },
    ]);
    expect(endMinutes(S2330, 1, "2026-10-23", TZ, 1)).toEqual([
      { minute: 30, end: "2026-10-24T01:30:00+03:00" },
      { minute: 45, end: "2026-10-24T01:45:00+03:00" },
    ]);
  });

  it("builds free and occupied spans from the slots", () => {
    const free = slot("2026-10-23T18:00:00+03:00", ["2026-10-23T19:30:00+03:00"]);
    const later = slot("2026-10-23T21:00:00+03:00", ["2026-10-23T23:00:00+03:00"]);
    const timeline = buildTimeline([free, later]);
    expect(timeline.map((segment) => segment.kind)).toEqual(["free", "busy", "free"]);
    expect(clockLabel(new Date(timeline[1]!.startMs).toISOString(), TZ)).toBe("19:30");
    expect(clockLabel(new Date(timeline[1]!.endMs).toISOString(), TZ)).toBe("21:00");
  });
});
