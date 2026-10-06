/**
 * Konva canvas of the hall layout editor (PROJECT-SPEC §30, §30.4).
 *
 * The store draft is the single source of truth: Konva nodes are pure views.
 * Gestures write back once — on `dragend` / `transformend` — so a whole drag or
 * resize is exactly one undo step and no API traffic happens per mousemove.
 * Per §30.4, `scaleX/scaleY` are converted to `width/height` on `transformend`
 * and reset to 1; accumulated scale is never persisted.
 */
import { useEffect, useMemo, useRef } from "react";
import type Konva from "konva";
import { Circle, Group, Layer, Rect, Stage, Text, Transformer } from "react-konva";

import { MIN_SIZE, readNodeBack } from "@/editor/geometry";
import { clampZoom, ZOOM_STEP } from "@/editor/hallEditorStore";
import type { DraftStatic, DraftTable } from "@/editor/hallEditorStore";
import { useHallEditorStore } from "@/editor/hallEditorStore";

const TABLE_FILL = "#2f9e6a";
const STATIC_FILL = "rgba(120, 120, 120, 0.25)";

function TableShape({
  table,
  nodeRef,
  selected,
  onClick,
  onDragEnd,
  onTransformEnd,
}: {
  table: DraftTable;
  nodeRef: (node: Konva.Node | null) => void;
  selected: boolean;
  onClick: () => void;
  onDragEnd: () => void;
  onTransformEnd: () => void;
}) {
  const common = {
    name: `table-${table.key}`,
    ref: nodeRef,
    x: table.x + table.width / 2,
    y: table.y + table.height / 2,
    rotation: table.rotation,
    draggable: true,
    onClick,
    onTap: onClick,
    onDragEnd,
    onTransformEnd,
  };
  const stroke = selected ? "#1d4ed8" : "rgba(0,0,0,0.25)";
  const strokeWidth = selected ? 2 : 1;
  const caption = `${table.number} · ${table.capacity}`;
  if (table.shape === "circle") {
    const radius = Math.min(table.width, table.height) / 2;
    return (
      <Group {...common}>
        <Circle
          x={0}
          y={0}
          radius={radius}
          fill={TABLE_FILL}
          stroke={stroke}
          strokeWidth={strokeWidth}
        />
        <Text
          x={-radius}
          y={-10}
          width={radius * 2}
          align="center"
          text={caption}
          fontSize={14}
          fill="#ffffff"
        />
      </Group>
    );
  }
  return (
    <Group {...common} offsetX={table.width / 2} offsetY={table.height / 2}>
      <Rect
        x={0}
        y={0}
        width={table.width}
        height={table.height}
        cornerRadius={6}
        fill={TABLE_FILL}
        stroke={stroke}
        strokeWidth={strokeWidth}
      />
      <Text
        x={0}
        y={table.height / 2 - 9}
        width={table.width}
        align="center"
        text={caption}
        fontSize={14}
        fill="#ffffff"
      />
    </Group>
  );
}

function StaticShape({
  item,
  nodeRef,
  selected,
  onClick,
  onDragEnd,
  onTransformEnd,
}: {
  item: DraftStatic;
  nodeRef: (node: Konva.Node | null) => void;
  selected: boolean;
  onClick: () => void;
  onDragEnd: () => void;
  onTransformEnd: () => void;
}) {
  const element = item.element;
  const common = {
    name: `static-${item.key}`,
    ref: nodeRef,
    rotation: element.rotation,
    draggable: true,
    onClick,
    onTap: onClick,
    onDragEnd,
    onTransformEnd,
  };
  const stroke = selected ? "#1d4ed8" : "rgba(0,0,0,0.3)";
  const strokeWidth = selected ? 2 : 1;
  if (element.type === "text") {
    return (
      <Text
        {...common}
        x={element.x}
        y={element.y}
        text={element.text}
        fontSize={element.font_size}
        fill="#111827"
        stroke={stroke}
        strokeWidth={strokeWidth}
      />
    );
  }
  const { x, y, width, height } = element;
  const label = "label" in element ? element.label : null;
  const dashProps = element.type === "zone" ? { dash: [8, 6] } : {};
  return (
    <Group
      {...common}
      x={x + width / 2}
      y={y + height / 2}
      offsetX={width / 2}
      offsetY={height / 2}
    >
      <Rect
        x={0}
        y={0}
        width={width}
        height={height}
        fill={STATIC_FILL}
        stroke={stroke}
        strokeWidth={strokeWidth}
        {...dashProps}
      />
      {label ? <Text x={8} y={6} text={label} fontSize={16} fill="#374151" /> : null}
    </Group>
  );
}

export function HallEditorCanvas() {
  const draft = useHallEditorStore((state) => state.draft);
  const selection = useHallEditorStore((state) => state.selection);
  const commitDraft = useHallEditorStore((state) => state.commitDraft);
  const select = useHallEditorStore((state) => state.select);
  const zoom = useHallEditorStore((state) => state.zoom);
  const pan = useHallEditorStore((state) => state.pan);
  const setZoom = useHallEditorStore((state) => state.setZoom);
  const setPan = useHallEditorStore((state) => state.setPan);
  const transformerRef = useRef<Konva.Transformer>(null);
  const nodeRefs = useRef(new Map<string, Konva.Node>());

  const sortedTables = useMemo(
    () => [...(draft?.tables ?? [])].sort((a, b) => a.z_index - b.z_index),
    [draft],
  );
  const sortedStatic = useMemo(
    () => [...(draft?.static_elements ?? [])].sort((a, b) => a.element.z_index - b.element.z_index),
    [draft],
  );

  // Attach the Transformer to the selected node (§30.4).
  useEffect(() => {
    const transformer = transformerRef.current;
    if (!transformer) return;
    if (!selection) {
      transformer.nodes([]);
      transformer.getLayer()?.batchDraw();
      return;
    }
    const node = nodeRefs.current.get(selection.key);
    transformer.nodes(node ? [node] : []);
    transformer.getLayer()?.batchDraw();
  }, [selection, draft]);

  if (!draft) return null;

  // Wheel zoom keeps the pointer anchored in world coordinates (§31 viewport).
  const handleWheel = (event: Konva.KonvaEventObject<WheelEvent>) => {
    event.evt.preventDefault();
    const stage = event.target.getStage();
    const pointer = stage?.getPointerPosition();
    if (!stage || !pointer) return;
    const next = clampZoom(event.evt.deltaY > 0 ? zoom / ZOOM_STEP : zoom * ZOOM_STEP);
    if (next === zoom) return;
    const worldX = (pointer.x - pan.x) / zoom;
    const worldY = (pointer.y - pan.y) / zoom;
    setZoom(next);
    setPan({ x: pointer.x - worldX * next, y: pointer.y - worldY * next });
  };

  const commitNode = (key: string, kind: "table" | "static") => {
    const node = nodeRefs.current.get(key);
    if (!node) return;
    if (kind === "table") {
      const table = draft.tables.find((t) => t.key === key);
      if (!table) return;
      // Both rect and circle nodes are positioned by their centre, so the
      // shared `centered` reader applies to each.
      const next = readNodeBack(node, {
        x: table.x,
        y: table.y,
        width: table.width,
        height: table.height,
        rotation: table.rotation,
        centered: true,
      });
      commitDraft({
        ...draft,
        tables: draft.tables.map((t) => (t.key === key ? { ...t, ...next } : t)),
      });
    } else {
      const item = draft.static_elements.find((s) => s.key === key);
      if (!item) return;
      const element = item.element;
      if (element.type === "text") {
        const next = readNodeBack(node, {
          x: element.x,
          y: element.y,
          width: 0,
          height: 0,
          rotation: element.rotation,
          centered: false,
        });
        const fontScale = node.scaleY();
        commitDraft({
          ...draft,
          static_elements: draft.static_elements.map((s) =>
            s.key === key
              ? {
                  ...s,
                  element: {
                    ...element,
                    x: next.x,
                    y: next.y,
                    rotation: next.rotation,
                    font_size: Math.min(
                      200,
                      Math.max(1, Math.round(element.font_size * fontScale)),
                    ),
                  },
                }
              : s,
          ),
        });
      } else {
        const { x, y, width, height, rotation } = element;
        const next = readNodeBack(node, { x, y, width, height, rotation, centered: true });
        commitDraft({
          ...draft,
          static_elements: draft.static_elements.map((s) =>
            s.key === key ? { ...s, element: { ...element, ...next } } : s,
          ),
        });
      }
    }
    // §30.4: never keep accumulated scale on the node.
    node.scaleX(1);
    node.scaleY(1);
  };

  return (
    <div className="editor-canvas" aria-label="Редактор схемы зала">
      <div className="editor-canvas__zoom">
        <button
          type="button"
          aria-label="Уменьшить масштаб"
          onClick={() => setZoom(zoom / ZOOM_STEP)}
        >
          −
        </button>
        <span data-testid="zoom-level">{Math.round(zoom * 100)}%</span>
        <button
          type="button"
          aria-label="Увеличить масштаб"
          onClick={() => setZoom(zoom * ZOOM_STEP)}
        >
          +
        </button>
        <button
          type="button"
          onClick={() => {
            setZoom(1);
            setPan({ x: 0, y: 0 });
          }}
        >
          Сбросить масштаб
        </button>
      </div>
      <Stage
        width={draft.canvas_width}
        height={draft.canvas_height}
        scaleX={zoom}
        scaleY={zoom}
        x={pan.x}
        y={pan.y}
        draggable
        onDragEnd={(event) => {
          // Only a real stage drag pans the viewport; a table/resize drag must not.
          if (event.target === event.target.getStage()) {
            setPan({ x: event.target.x(), y: event.target.y() });
          }
        }}
        onWheel={handleWheel}
        onMouseDown={(event) => {
          if (event.target === event.target.getStage()) select(null);
        }}
      >
        <Layer listening={false}>
          <Rect
            x={0}
            y={0}
            width={draft.canvas_width}
            height={draft.canvas_height}
            fill="#fafafa"
          />
        </Layer>
        <Layer>
          {sortedStatic.map((item) => (
            <StaticShape
              key={item.key}
              item={item}
              nodeRef={(node) => {
                if (node) nodeRefs.current.set(item.key, node);
                else nodeRefs.current.delete(item.key);
              }}
              selected={selection?.key === item.key}
              onClick={() => select({ kind: "static", key: item.key })}
              onDragEnd={() => commitNode(item.key, "static")}
              onTransformEnd={() => commitNode(item.key, "static")}
            />
          ))}
          {sortedTables.map((table) => (
            <TableShape
              key={table.key}
              table={table}
              nodeRef={(node) => {
                if (node) nodeRefs.current.set(table.key, node);
                else nodeRefs.current.delete(table.key);
              }}
              selected={selection?.key === table.key}
              onClick={() => select({ kind: "table", key: table.key })}
              onDragEnd={() => commitNode(table.key, "table")}
              onTransformEnd={() => commitNode(table.key, "table")}
            />
          ))}
          <Transformer
            ref={transformerRef}
            rotateEnabled
            resizeEnabled
            boundBoxFunc={(oldBox, newBox) =>
              newBox.width < MIN_SIZE || newBox.height < MIN_SIZE ? oldBox : newBox
            }
          />
        </Layer>
      </Stage>
    </div>
  );
}
