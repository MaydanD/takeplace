import { describe, expect, it } from "vitest";

import {
  seatSize,
  TABLE_SHAPES,
  tableSeatLocals,
  tableSeats,
  tableShape,
} from "@/components/tableSeats";

const base = { x: 100, y: 100, width: 120, height: 80, rotation: 0 };

describe("tableSeats", () => {
  it.each(TABLE_SHAPES)("renders exactly one seat square per capacity for %s", (shape) => {
    for (const capacity of [1, 2, 4, 6, 8]) {
      expect(tableSeats({ ...base, shape, capacity })).toHaveLength(capacity);
    }
  });

  it("places rectangular seats outside the table on all four sides", () => {
    const table = { ...base, shape: "rect" as const, capacity: 8 };
    const seats = tableSeats(table);
    const left = table.x;
    const right = table.x + table.width;
    const top = table.y;
    const bottom = table.y + table.height;
    for (const seat of seats) {
      const outside = seat.x <= left || seat.x >= right || seat.y <= top || seat.y >= bottom;
      expect(outside).toBe(true);
    }
  });

  it("distributes circular seats evenly around the centre", () => {
    const table = { ...base, width: 100, height: 100, shape: "circle" as const, capacity: 8 };
    const cx = table.x + table.width / 2;
    const cy = table.y + table.height / 2;
    const radii = tableSeats(table).map((seat) => Math.hypot(seat.x - cx, seat.y - cy));
    // All seats sit on the same ring, just outside the table edge.
    for (const radius of radii) expect(radius).toBeGreaterThan(table.width / 2);
    expect(Math.max(...radii) - Math.min(...radii)).toBeLessThan(0.001);
  });

  it("rotates seat positions with the table", () => {
    const upright = tableSeatLocals({ ...base, shape: "rect", capacity: 4, rotation: 0 });
    const turned = tableSeats({ ...base, shape: "rect", capacity: 4, rotation: 90 });
    const cx = base.x + base.width / 2;
    const cy = base.y + base.height / 2;
    upright.forEach((seat, index) => {
      const rotated = turned[index]!;
      // 90° clockwise: (x, y) -> (-y, x) around the centre.
      expect(rotated.x).toBeCloseTo(cx - seat.y, 5);
      expect(rotated.y).toBeCloseTo(cy + seat.x, 5);
    });
  });

  it("returns no seats for an empty capacity and clamps the seat size", () => {
    expect(tableSeats({ ...base, shape: "rect", capacity: 0 })).toEqual([]);
    expect(seatSize(1, 1)).toBeGreaterThan(0);
    expect(seatSize(10_000, 10_000)).toBeLessThanOrEqual(18);
  });

  it("falls back to rect for an unknown shape", () => {
    expect(tableShape("triangle")).toBe("rect");
  });
});
