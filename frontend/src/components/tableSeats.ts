/**
 * Shared seat-marker geometry for table shapes.
 *
 * One small square = one seat. The count always equals the table capacity and
 * the squares sit *outside* the table perimeter: on the circumference for a
 * circle, along the ellipse perimeter for an oval, and along the four sides for
 * a square/rectangle.
 *
 * This module is deliberately renderer-agnostic: it returns canvas-space centre
 * coordinates and a shared size, so the read-only SVG canvas (`HallCanvas`) and
 * the Konva editor (`HallEditorCanvas`) draw the exact same seats. It does not
 * touch booking data — seats are a pure visualisation of `capacity`.
 */

/** The four editor shapes. `rect`/`circle` are the original Stage 4 values. */
export const TABLE_SHAPES = ["rect", "square", "circle", "ellipse"] as const;
export type TableShape = (typeof TABLE_SHAPES)[number];

export interface SeatMark {
  /** Centre X in canvas coordinates (rotation already applied). */
  x: number;
  /** Centre Y in canvas coordinates (rotation already applied). */
  y: number;
  /** Side length of the seat square. */
  size: number;
}

export interface SeatTableInput {
  shape: string;
  x: number;
  y: number;
  width: number;
  height: number;
  rotation?: number;
  capacity: number;
}

const SEAT_MIN = 6;
const SEAT_MAX = 18;
/** Extra distance between the shape edge and the seat square, as a size ratio. */
const OUTER_GAP = 0.35;

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** Seat square size scaled to the table, clamped so tiny/huge tables stay usable. */
export function seatSize(width: number, height: number): number {
  return clamp(Math.min(width, height) * 0.16, SEAT_MIN, SEAT_MAX);
}

export function tableShape(value: string): TableShape {
  return (TABLE_SHAPES as readonly string[]).includes(value) ? (value as TableShape) : "rect";
}

/**
 * Largest-remainder allocation of `total` seats across weighted buckets. Keeps
 * the seat count exactly equal to capacity while distributing proportional to
 * each side's length, and breaks ties in the given bucket order so a small
 * square table alternates between opposite sides.
 */
function allocateSeats(total: number, weights: number[]): number[] {
  const sum = weights.reduce((acc, value) => acc + value, 0);
  if (sum <= 0) return weights.map(() => 0);
  const exact = weights.map((weight) => (total * weight) / sum);
  const counts = exact.map((value) => Math.floor(value));
  let remaining = total - counts.reduce((acc, value) => acc + value, 0);
  const order = exact
    .map((value, index) => ({ index, remainder: value - Math.floor(value) }))
    .sort((a, b) => b.remainder - a.remainder || a.index - b.index);
  for (const { index } of order) {
    if (remaining <= 0) break;
    counts[index] = (counts[index] ?? 0) + 1;
    remaining -= 1;
  }
  return counts;
}

function rectSeats(
  width: number,
  height: number,
  capacity: number,
  size: number,
): { x: number; y: number }[] {
  const halfW = width / 2;
  const halfH = height / 2;
  const offset = size / 2 + size * OUTER_GAP;
  // Sides in top/bottom/left/right order so ties favour opposite sides.
  const sides = [
    {
      len: width,
      normal: { x: 0, y: -1 },
      along: { x: 1, y: 0 },
      origin: { x: -halfW, y: -halfH },
    },
    { len: width, normal: { x: 0, y: 1 }, along: { x: 1, y: 0 }, origin: { x: -halfW, y: halfH } },
    {
      len: height,
      normal: { x: -1, y: 0 },
      along: { x: 0, y: 1 },
      origin: { x: -halfW, y: -halfH },
    },
    { len: height, normal: { x: 1, y: 0 }, along: { x: 0, y: 1 }, origin: { x: halfW, y: -halfH } },
  ];
  const counts = allocateSeats(
    capacity,
    sides.map((side) => side.len),
  );
  const seats: { x: number; y: number }[] = [];
  sides.forEach((side, index) => {
    const count = counts[index] ?? 0;
    for (let i = 0; i < count; i += 1) {
      const t = (i + 0.5) / count;
      seats.push({
        x: side.origin.x + side.along.x * side.len * t + side.normal.x * offset,
        y: side.origin.y + side.along.y * side.len * t + side.normal.y * offset,
      });
    }
  });
  return seats;
}

function ellipseSeats(
  width: number,
  height: number,
  capacity: number,
  size: number,
): { x: number; y: number }[] {
  const rx = width / 2;
  const ry = height / 2;
  const offset = size / 2 + size * OUTER_GAP;
  const samples = Math.max(64, capacity * 8);
  // Dense parametric sampling -> cumulative arc length, so seats are evenly
  // spaced along the actual perimeter (ends of an oval are not crowded).
  const points: { x: number; y: number; angle: number }[] = [];
  for (let i = 0; i < samples; i += 1) {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / samples;
    points.push({ x: rx * Math.cos(angle), y: ry * Math.sin(angle), angle });
  }
  const lengths = [0];
  for (let i = 1; i <= samples; i += 1) {
    const prev = points[i - 1]!;
    const current = i === samples ? points[0]! : points[i]!;
    lengths.push(lengths[i - 1]! + Math.hypot(current.x - prev.x, current.y - prev.y));
  }
  const perimeter = lengths[samples]!;
  const seats: { x: number; y: number }[] = [];
  if (perimeter <= 0) return seats;
  for (let j = 0; j < capacity; j += 1) {
    const target = (perimeter * j) / capacity;
    let index = 1;
    while (index < samples && lengths[index]! < target) index += 1;
    const point = points[Math.min(index, samples - 1)]!;
    // Outward normal of an ellipse at the parametric point.
    const nx = Math.cos(point.angle) / rx;
    const ny = Math.sin(point.angle) / ry;
    const norm = Math.hypot(nx, ny) || 1;
    seats.push({ x: point.x + (nx / norm) * offset, y: point.y + (ny / norm) * offset });
  }
  return seats;
}

/**
 * Seat square offsets relative to the table centre, *before* rotation, plus the
 * shared size. Used by the Konva editor, which already draws inside a rotated
 * group centred on the table.
 */
export function tableSeatLocals(table: SeatTableInput): SeatMark[] {
  const width = Math.max(1, table.width);
  const height = Math.max(1, table.height);
  const capacity = Math.max(0, Math.floor(table.capacity));
  if (capacity <= 0) return [];
  const size = seatSize(width, height);
  const shape = tableShape(table.shape);
  const local =
    shape === "circle" || shape === "ellipse"
      ? ellipseSeats(width, height, capacity, size)
      : rectSeats(width, height, capacity, size);
  return local.map((seat) => ({ x: seat.x, y: seat.y, size }));
}

/**
 * Seat square centres in canvas coordinates for one table, with the table's
 * rotation applied around its centre.
 */
export function tableSeats(table: SeatTableInput): SeatMark[] {
  const width = Math.max(1, table.width);
  const height = Math.max(1, table.height);
  const locals = tableSeatLocals(table);
  const cx = table.x + width / 2;
  const cy = table.y + height / 2;
  const radians = ((table.rotation ?? 0) * Math.PI) / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  return locals.map((seat) => ({
    x: cx + seat.x * cos - seat.y * sin,
    y: cy + seat.x * sin + seat.y * cos,
    size: seat.size,
  }));
}
