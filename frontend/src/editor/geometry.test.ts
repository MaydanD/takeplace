import { describe, expect, it } from "vitest";
import type Konva from "konva";

import { normalizeRotation, readNodeBack } from "@/editor/geometry";

/** A minimal fake Konva node exposing only what `readNodeBack` reads. */
function fakeNode(values: {
  x: number;
  y: number;
  scaleX?: number;
  scaleY?: number;
  rotation?: number;
}) {
  return {
    x: () => values.x,
    y: () => values.y,
    scaleX: () => values.scaleX ?? 1,
    scaleY: () => values.scaleY ?? 1,
    rotation: () => values.rotation ?? 0,
  } as unknown as Konva.Node;
}

describe("normalizeRotation", () => {
  it("wraps into [0, 360)", () => {
    expect(normalizeRotation(0)).toBe(0);
    expect(normalizeRotation(361)).toBe(1);
    expect(normalizeRotation(-90)).toBe(270);
    expect(normalizeRotation(720)).toBe(0);
  });
});

describe("readNodeBack (§30.4 transform normalization)", () => {
  const base = { x: 100, y: 100, width: 80, height: 60, rotation: 0, centered: true };

  it("converts scale into width/height and keeps the centre anchor", () => {
    const node = fakeNode({ x: 140, y: 130, scaleX: 2, scaleY: 1.5, rotation: 45 });
    const next = readNodeBack(node, base);
    expect(next.width).toBe(160);
    expect(next.height).toBe(90);
    // top-left = centre - half of the NEW size
    expect(next.x).toBe(140 - 80);
    expect(next.y).toBe(130 - 45);
    expect(next.rotation).toBe(45);
  });

  it("clamps the top-left to the canvas (x/y >= 0)", () => {
    const node = fakeNode({ x: 10, y: 5 });
    const next = readNodeBack(node, base);
    expect(next.x).toBe(0);
    expect(next.y).toBe(0);
  });

  it("enforces a minimum size", () => {
    const node = fakeNode({ x: 200, y: 200, scaleX: 0.01, scaleY: 0.01 });
    const next = readNodeBack(node, base);
    expect(next.width).toBeGreaterThanOrEqual(10);
    expect(next.height).toBeGreaterThanOrEqual(10);
  });

  it("reads top-left anchored nodes (text) without centre conversion", () => {
    const node = fakeNode({ x: 30, y: 40 });
    const next = readNodeBack(node, { ...base, centered: false });
    expect(next.x).toBe(30);
    expect(next.y).toBe(40);
  });
});
