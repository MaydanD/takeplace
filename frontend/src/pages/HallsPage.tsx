import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "@/api/client";
import {
  useArchiveHall,
  useArchiveTable,
  useCreateHall,
  useHall,
  useHalls,
  useTables,
  useUpdateTable,
} from "@/api/hallsQueries";
import type { HallDetail, StaticElement, TableSummary } from "@/api/halls";

function archiveErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "HALL_ARCHIVE_BLOCKED") {
      return "Нельзя архивировать зал, пока в нём есть неархивные столы.";
    }
    if (error.code === "TABLE_ARCHIVE_BLOCKED") {
      return "Нельзя архивировать стол: по нему есть будущие или активные брони.";
    }
    return `Не удалось выполнить операцию (код ${error.status}).`;
  }
  return "Не удалось выполнить операцию. Проверьте соединение.";
}

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

function TableShape({ table, dim }: { table: TableSummary; dim: boolean }) {
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

function HallCanvas({ hall, showArchived }: { hall: HallDetail; showArchived: boolean }) {
  const staticElements = useMemo(
    () => [...hall.static_elements].sort((a, b) => a.z_index - b.z_index),
    [hall.static_elements],
  );
  const tables = useMemo(
    () =>
      hall.tables
        .filter((table) => showArchived || table.archived_at === null)
        .sort((a, b) => a.z_index - b.z_index),
    [hall.tables, showArchived],
  );
  return (
    <svg
      className="hall-canvas"
      viewBox={`0 0 ${hall.canvas_width} ${hall.canvas_height}`}
      role="img"
      aria-label={`Схема зала ${hall.name}`}
      preserveAspectRatio="xMidYMid meet"
    >
      <rect x={0} y={0} width={hall.canvas_width} height={hall.canvas_height} fill="transparent" />
      {staticElements.map((element, index) => (
        <StaticShape key={`static-${index}`} element={element} />
      ))}
      {tables.map((table) => (
        <TableShape key={table.id} table={table} dim={table.archived_at !== null} />
      ))}
    </svg>
  );
}

export function HallsPage() {
  const [showArchived, setShowArchived] = useState(false);
  const [selectedHallId, setSelectedHallId] = useState<number | undefined>(undefined);
  const [newHallName, setNewHallName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const halls = useHalls(showArchived);
  const selectedHall = useHall(selectedHallId);
  const tables = useTables({ includeArchived: showArchived });
  const createHall = useCreateHall();
  const archiveHall = useArchiveHall();
  const updateTable = useUpdateTable();
  const archiveTable = useArchiveTable();

  // Auto-select the first hall once the list arrives.
  useEffect(() => {
    const first = halls.data?.halls[0];
    if (selectedHallId === undefined && first) {
      setSelectedHallId(first.id);
    }
  }, [halls.data, selectedHallId]);

  async function handleCreateHall(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      const hall = await createHall.mutateAsync({
        name: newHallName,
        canvas_width: 1200,
        canvas_height: 800,
        is_bookable: true,
      });
      setNewHallName("");
      setSelectedHallId(hall.id);
    } catch (caught) {
      setError(archiveErrorMessage(caught));
    }
  }

  async function handleArchiveHall(hallId: number) {
    setError(null);
    try {
      await archiveHall.mutateAsync(hallId);
      if (selectedHallId === hallId) {
        setSelectedHallId(undefined);
      }
    } catch (caught) {
      setError(archiveErrorMessage(caught));
    }
  }

  async function handleToggleBookable(table: TableSummary) {
    setError(null);
    try {
      await updateTable.mutateAsync({
        tableId: table.id,
        body: { is_bookable: !table.is_bookable },
      });
    } catch (caught) {
      setError(archiveErrorMessage(caught));
    }
  }

  async function handleArchiveTable(table: TableSummary) {
    setError(null);
    try {
      await archiveTable.mutateAsync(table.id);
    } catch (caught) {
      setError(archiveErrorMessage(caught));
    }
  }

  return (
    <main className="page page--wide">
      <header className="page-header">
        <h1>Залы и столы</h1>
        <Link to="/admin">← В админку</Link>
      </header>

      {error ? (
        <p role="alert" className="callout callout--error">
          {error}
        </p>
      ) : null}

      <label className="field field--checkbox">
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(event) => setShowArchived(event.target.checked)}
        />
        <span>Показывать архивные</span>
      </label>

      <section className="card" aria-label="Залы">
        <h2>Залы</h2>
        <ul className="hall-tabs">
          {(halls.data?.halls ?? []).map((hall) => (
            <li key={hall.id}>
              <button
                type="button"
                className={hall.id === selectedHallId ? "hall-tab hall-tab--active" : "hall-tab"}
                onClick={() => setSelectedHallId(hall.id)}
              >
                {hall.name} <span className="hall-tab__count">{hall.table_count}</span>
              </button>
            </li>
          ))}
        </ul>
        {halls.data && halls.data.halls.length === 0 ? <p>Залы не найдены.</p> : null}

        <form className="inline-form" onSubmit={handleCreateHall}>
          <label className="field">
            <span>Новый зал</span>
            <input
              value={newHallName}
              required
              onChange={(event) => setNewHallName(event.target.value)}
              placeholder="Название зала"
            />
          </label>
          <button type="submit" disabled={createHall.isPending}>
            Добавить зал
          </button>
        </form>
      </section>

      <section className="card" aria-label="Схема зала">
        <div className="card-heading">
          <h2>Схема{selectedHall.data ? `: ${selectedHall.data.name}` : ""}</h2>
          {selectedHall.data && selectedHall.data.archived_at === null ? (
            <button
              type="button"
              className="button--danger"
              onClick={() => handleArchiveHall(selectedHall.data.id)}
              disabled={archiveHall.isPending}
            >
              Архивировать зал
            </button>
          ) : null}
        </div>
        {selectedHall.data ? (
          <>
            <HallCanvas hall={selectedHall.data} showArchived={showArchived} />
            <p className="muted">
              Столы: {selectedHall.data.tables.length}. Bookable — зелёные, неактивные — серые.
            </p>
          </>
        ) : (
          <p>Выберите зал.</p>
        )}
      </section>

      <section className="card" aria-label="Список столов">
        <h2>Столы</h2>
        {tables.isError ? (
          <p role="alert">Не удалось загрузить список столов.</p>
        ) : (
          <table className="table-view">
            <thead>
              <tr>
                <th>Зал</th>
                <th>Номер</th>
                <th>Capacity</th>
                <th>Форма</th>
                <th>Bookable</th>
                <th>Статус</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(tables.data?.tables ?? []).map((table) => (
                <tr key={table.id}>
                  <td>{table.hall_name ?? table.hall_id}</td>
                  <td>{table.number}</td>
                  <td>{table.capacity}</td>
                  <td>{table.shape}</td>
                  <td>
                    <input
                      type="checkbox"
                      checked={table.is_bookable}
                      aria-label={`${table.number} bookable`}
                      onChange={() => handleToggleBookable(table)}
                    />
                  </td>
                  <td>{table.archived_at ? "архив" : "активен"}</td>
                  <td>
                    {table.archived_at === null ? (
                      <button type="button" onClick={() => handleArchiveTable(table)}>
                        Архивировать
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}
