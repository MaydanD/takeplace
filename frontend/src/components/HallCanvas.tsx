import { useMemo } from "react";
import type { HallDetail, StaticElement, TableSummary } from "@/api/halls";
type CanvasTable = Pick<
  TableSummary,
  "id" | "number" | "capacity" | "x" | "y" | "width" | "height" | "rotation" | "shape" | "z_index"
> & { is_bookable?: boolean | undefined; archived_at?: string | null };

const TABLE_FILL = "#2f9e6a";
const TABLE_FILL_UNBOOKABLE = "#9aa0a6";
const STATIC_FILL = "color-mix(in srgb, currentColor 12%, transparent)";

function centre(x: number, y: number, width: number, height: number) {
  return { cx: x + width / 2, cy: y + height / 2 };
}

function StaticShape({ element }: { element: StaticElement }) {
  if (element.type === "text") {
    return (
      <text
        x={element.x}
        y={element.y}
        fontSize={element.font_size}
        transform={`rotate(${element.rotation} ${element.x} ${element.y})`}
      >
        {element.text}
      </text>
    );
  }
  const { x, y, width, height, rotation, type } = element;
  const { cx, cy } = centre(x, y, width, height);
  const label = "label" in element ? element.label : null;
  return (
    <g transform={`rotate(${rotation} ${cx} ${cy})`}>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx={type === "zone" ? 12 : 2}
        fill={STATIC_FILL}
        stroke="currentColor"
        strokeDasharray={type === "zone" ? "8 6" : undefined}
      />
      {label ? (
        <text x={x + 8} y={y + 20} fontSize={16}>
          {label}
        </text>
      ) : null}
    </g>
  );
}

function TableShape({ table, dim }: { table: CanvasTable; dim: boolean }) {
  const { x, y, width, height, rotation, shape, number, capacity, is_bookable } = table;
  const { cx, cy } = centre(x, y, width, height);
  const fill = is_bookable ? TABLE_FILL : TABLE_FILL_UNBOOKABLE;
  return (
    <g transform={`rotate(${rotation} ${cx} ${cy})`} opacity={dim ? 0.35 : 1}>
      {shape === "circle" ? (
        <circle cx={cx} cy={cy} r={Math.min(width, height) / 2} fill={fill} />
      ) : (
        <rect x={x} y={y} width={width} height={height} rx={6} fill={fill} />
      )}
      <text x={cx} y={cy - 2} textAnchor="middle" fontSize={16} fill="#ffffff">
        {number}
      </text>
      <text x={cx} y={cy + 16} textAnchor="middle" fontSize={12} fill="#ffffff">
        {capacity}
      </text>
    </g>
  );
}

export function HallCanvas({
  hall,
  showArchived = false,
  selectedTableId,
  availableTableIds,
  onSelect,
}: {
  hall: Pick<HallDetail, "name" | "canvas_width" | "canvas_height" | "static_elements"> & {
    tables: CanvasTable[];
  };
  showArchived?: boolean;
  selectedTableId?: number | undefined;
  availableTableIds?: Set<number>;
  onSelect?: (tableId: number) => void;
}) {
  const staticElements = useMemo(
    () => [...hall.static_elements].sort((a, b) => a.z_index - b.z_index),
    [hall.static_elements],
  );
  const tables = useMemo(
    () =>
      hall.tables
        .filter((table) => showArchived || !table.archived_at)
        .sort((a, b) => a.z_index - b.z_index),
    [hall.tables, showArchived],
  );
  return (
    <svg
      className="hall-canvas"
      viewBox={`0 0 ${hall.canvas_width} ${hall.canvas_height}`}
      role={onSelect ? "group" : "img"}
      aria-label={`Схема зала ${hall.name}`}
      preserveAspectRatio="xMidYMid meet"
    >
      <rect x={0} y={0} width={hall.canvas_width} height={hall.canvas_height} fill="transparent" />
      <g pointerEvents="none" aria-hidden="true">
        {staticElements.map((element, index) => (
          <StaticShape key={`static-${index}`} element={element} />
        ))}
      </g>
      {tables.map((table) => (
        <g
          key={table.id}
          role={onSelect ? "button" : undefined}
          aria-label={onSelect ? `Стол ${table.number}, мест: ${table.capacity}` : undefined}
          aria-pressed={onSelect ? table.id === selectedTableId : undefined}
          aria-disabled={onSelect ? !availableTableIds?.has(table.id) : undefined}
          tabIndex={onSelect && availableTableIds?.has(table.id) ? 0 : undefined}
          onClick={() => {
            if (availableTableIds?.has(table.id)) onSelect?.(table.id);
          }}
          onKeyDown={(event) => {
            if (
              onSelect &&
              availableTableIds?.has(table.id) &&
              (event.key === "Enter" || event.key === " ")
            ) {
              event.preventDefault();
              onSelect(table.id);
            }
          }}
          className={
            table.id === selectedTableId ? "canvas-table canvas-table--selected" : "canvas-table"
          }
        >
          <TableShape
            table={{
              ...table,
              is_bookable: availableTableIds ? availableTableIds.has(table.id) : table.is_bookable,
            }}
            dim={!!table.archived_at}
          />
        </g>
      ))}
    </svg>
  );
}
