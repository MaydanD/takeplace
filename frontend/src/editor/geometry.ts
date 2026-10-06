/**
 * Shared geometry reads for the hall editor (PROJECT-SPEC §30.4).
 *
 * On `transformend` the accumulated `scaleX/scaleY` is converted to
 * `width/height` and dropped: scale is never persisted to the backend.
 */
import type Konva from "konva";

export const MIN_SIZE = 10;

export function normalizeRotation(deg: number): number {
  const wrapped = deg % 360;
  return wrapped < 0 ? wrapped + 360 : wrapped;
}

/**
 * Read a node's post-gesture geometry back into draft coordinates.
 *
 * `centered` nodes (rect tables, rect statics, circles) are positioned by
 * their centre, so the top-left is the centre minus half the new size; text
 * nodes are top-left anchored like in the public/admin SVG renderer. `x/y` are
 * clamped to the canvas non-negativity rule of the geometry contract.
 */
export function readNodeBack(
  node: Konva.Node,
  base: {
    x: number;
    y: number;
    width: number;
    height: number;
    rotation: number;
    centered: boolean;
  },
): { x: number; y: number; width: number; height: number; rotation: number } {
  const width = Math.max(MIN_SIZE, base.width * node.scaleX());
  const height = Math.max(MIN_SIZE, base.height * node.scaleY());
  const left = base.centered ? node.x() - width / 2 : node.x();
  const top = base.centered ? node.y() - height / 2 : node.y();
  return {
    x: Math.max(0, left),
    y: Math.max(0, top),
    width,
    height,
    rotation: normalizeRotation(node.rotation()),
  };
}
