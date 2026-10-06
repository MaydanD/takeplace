import { HallCanvas } from "@/components/HallCanvas";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "@/api/client";
import {
  useArchiveHall,
  useArchiveTable,
  useCreateHall,
  useHall,
  useHalls,
  useTables,
  useUpdateHall,
  useUpdateTable,
} from "@/api/hallsQueries";
import type { TableSummary } from "@/api/halls";

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
  const updateHall = useUpdateHall();
  const updateTable = useUpdateTable();
  const archiveTable = useArchiveTable();
  const [renameValue, setRenameValue] = useState("");

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

  async function handleRenameHall(hallId: number) {
    if (!renameValue.trim()) return;
    setError(null);
    try {
      await updateHall.mutateAsync({ hallId, body: { name: renameValue.trim() } });
      setRenameValue("");
    } catch (caught) {
      setError(archiveErrorMessage(caught));
    }
  }

  async function handleToggleHallBookable(hallId: number, isBookable: boolean) {
    setError(null);
    try {
      await updateHall.mutateAsync({ hallId, body: { is_bookable: !isBookable } });
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
        <nav>
          <Link to="/admin/editor">Редактор схемы</Link>
          {" · "}
          <Link to="/admin">← В админку</Link>
        </nav>
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
            <>
              <label className="field field--inline">
                <span>Для бронирования</span>
                <input
                  type="checkbox"
                  checked={selectedHall.data.is_bookable}
                  aria-label={`${selectedHall.data.name} bookable`}
                  onChange={() =>
                    handleToggleHallBookable(selectedHall.data!.id, selectedHall.data!.is_bookable)
                  }
                />
              </label>
              <button
                type="button"
                className="button--danger"
                onClick={() => handleArchiveHall(selectedHall.data!.id)}
                disabled={archiveHall.isPending}
              >
                Архивировать зал
              </button>
            </>
          ) : null}
        </div>
        {selectedHall.data && selectedHall.data.archived_at === null ? (
          <form
            className="inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              void handleRenameHall(selectedHall.data!.id);
            }}
          >
            <label className="field">
              <span>Новое имя</span>
              <input
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
                placeholder={selectedHall.data.name}
              />
            </label>
            <button type="submit" disabled={!renameValue.trim() || updateHall.isPending}>
              Переименовать
            </button>
          </form>
        ) : null}
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
