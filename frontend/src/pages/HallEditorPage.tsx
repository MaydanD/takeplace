/**
 * Hall layout editor route (PROJECT-SPEC §30, §31, §52).
 *
 * Server state lives in TanStack Query, editor state in the Zustand store
 * (draft + undo/redo + selection). The Konva canvas never owns authoritative
 * layout data. Save is a full-state PUT with `expected_revision`; on
 * `409 LAYOUT_STALE` the local draft is kept, Save is blocked and the user is
 * offered an explicit reload of the server schema — no automatic merge (§31).
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "@/api/client";
import type { HallDetail, StaticElement } from "@/api/halls";
import { useHall, useHalls, useSaveHallLayout } from "@/api/hallsQueries";
import { HallEditorCanvas } from "@/editor/HallEditorCanvas";
import {
  createDraftStatic,
  createDraftTable,
  draftToSave,
  useHallEditorStore,
  type DraftTable,
} from "@/editor/hallEditorStore";

const STATIC_TYPES: StaticElement["type"][] = ["wall", "stage", "bar", "zone", "text"];
const STATIC_TYPE_LABELS: Record<StaticElement["type"], string> = {
  wall: "Стена",
  stage: "Сцена",
  bar: "Бар",
  zone: "Зона",
  text: "Текст",
};

function saveErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "LAYOUT_STALE") return ""; // handled by the stale banner
    if (error.code === "TABLE_ARCHIVE_BLOCKED") {
      return "Нельзя архивировать стол: по нему есть будущие или активные брони.";
    }
    if (error.code === "TABLE_NUMBER_TAKEN") {
      return "Номер стола уже занят в этом зале.";
    }
    if (error.code === "LAYOUT_INVALID") {
      return "Схема содержит невалидные данные: " + error.message;
    }
    if (error.code === "CAPACITY_CHANGE_BLOCKED") {
      return "Изменение вместимости сломает существующие брони.";
    }
    return `Не удалось сохранить (код ${error.status}).`;
  }
  return "Не удалось сохранить. Проверьте соединение.";
}

function nextTableNumber(existing: string[]): string {
  const used = new Set(existing);
  for (let i = 1; i < 10_000; i += 1) {
    if (!used.has(String(i))) return String(i);
  }
  return `T${Date.now()}`;
}

export function HallEditorPage() {
  const halls = useHalls();
  const [hallId, setHallId] = useState<number | undefined>(undefined);
  const hall = useHall(hallId);
  const saveLayout = useSaveHallLayout();

  const draft = useHallEditorStore((state) => state.draft);
  const dirty = useHallEditorStore((state) => state.dirty);
  const selection = useHallEditorStore((state) => state.selection);
  const undoStack = useHallEditorStore((state) => state.undoStack);
  const redoStack = useHallEditorStore((state) => state.redoStack);
  const storeHallId = useHallEditorStore((state) => state.hallId);
  const baseRevision = useHallEditorStore((state) => state.baseRevision);
  const openHall = useHallEditorStore((state) => state.openHall);
  const commitDraft = useHallEditorStore((state) => state.commitDraft);
  const select = useHallEditorStore((state) => state.select);
  const undo = useHallEditorStore((state) => state.undo);
  const redo = useHallEditorStore((state) => state.redo);
  const discardDraft = useHallEditorStore((state) => state.discardDraft);
  const markSaved = useHallEditorStore((state) => state.markSaved);

  const [staleError, setStaleError] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Auto-select the first hall once the list arrives.
  useEffect(() => {
    const first = halls.data?.halls[0];
    if (hallId === undefined && first) setHallId(first.id);
  }, [halls.data, hallId]);

  // Initialise the store when a hall (first time or switched) is loaded.
  const serverHall: HallDetail | undefined = hall.data;
  useEffect(() => {
    if (serverHall && storeHallId !== serverHall.id) {
      openHall(serverHall, { restoreDraft: true });
      setStaleError(false);
      setSaveError(null);
    }
  }, [serverHall, storeHallId, openHall]);

  // A remote edit (realtime refetch) while a draft exists => stale conflict.
  const remoteRevision = serverHall?.layout_revision;
  const remoteChanged =
    dirty &&
    baseRevision !== null &&
    remoteRevision !== undefined &&
    remoteRevision !== baseRevision;
  const stale = staleError || remoteChanged;

  const selectedTable = useMemo(() => {
    if (!draft || selection?.kind !== "table") return null;
    return draft.tables.find((t) => t.key === selection.key) ?? null;
  }, [draft, selection]);
  const selectedStatic = useMemo(() => {
    if (!draft || selection?.kind !== "static") return null;
    return draft.static_elements.find((s) => s.key === selection.key) ?? null;
  }, [draft, selection]);

  async function handleSave() {
    if (!serverHall || !draft || baseRevision === null || stale) return;
    setSaveError(null);
    try {
      const fresh = await saveLayout.mutateAsync({
        hallId: serverHall.id,
        body: draftToSave(draft, baseRevision),
      });
      markSaved(fresh);
      setStaleError(false);
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "LAYOUT_STALE") {
        setStaleError(true);
      } else {
        setSaveError(saveErrorMessage(caught));
      }
    }
  }

  /**
   * Explicit user confirmation: refetch the server schema first, then drop the
   * local draft (§31 — discard happens only after this explicit action).
   */
  async function handleReloadServerSchema() {
    const result = await hall.refetch();
    const latest = result.data;
    if (!latest) return; // refetch failed: keep the draft and the banner
    discardDraft(latest);
    setStaleError(false);
    setSaveError(null);
  }

  function handleAddTable() {
    if (!draft) return;
    const table = createDraftTable({ number: nextTableNumber(draft.tables.map((t) => t.number)) });
    commitDraft({ ...draft, tables: [...draft.tables, table] });
    select({ kind: "table", key: table.key });
  }

  function handleAddStatic(type: StaticElement["type"]) {
    if (!draft) return;
    const item = createDraftStatic(type);
    commitDraft({ ...draft, static_elements: [...draft.static_elements, item] });
    select({ kind: "static", key: item.key });
  }

  function updateTable(key: string, patch: Partial<DraftTable>) {
    if (!draft) return;
    commitDraft({
      ...draft,
      tables: draft.tables.map((t) => (t.key === key ? { ...t, ...patch } : t)),
    });
  }

  function updateStatic(key: string, patch: Record<string, unknown>) {
    if (!draft) return;
    commitDraft({
      ...draft,
      static_elements: draft.static_elements.map((s) => {
        if (s.key !== key) return s;
        return { ...s, element: { ...s.element, ...patch } as StaticElement };
      }),
    });
  }

  function archiveSelectedTable() {
    if (!draft || !selectedTable) return;
    // Omitting the table from the payload archives it server-side (§29.3);
    // history rows keep their FK. A local-only table is simply removed.
    commitDraft({
      ...draft,
      tables: draft.tables.filter((t) => t.key !== selectedTable.key),
    });
    select(null);
  }

  function deleteSelectedStatic() {
    if (!draft || !selectedStatic) return;
    commitDraft({
      ...draft,
      static_elements: draft.static_elements.filter((s) => s.key !== selectedStatic.key),
    });
    select(null);
  }

  const canSave = dirty && !stale && !saveLayout.isPending && draft !== null;

  return (
    <main className="page page--wide">
      <header className="page-header">
        <h1>Редактор схемы</h1>
        <Link to="/admin/halls">← Залы и столы</Link>
      </header>

      <section className="card" aria-label="Выбор зала">
        <label className="field">
          <span>Зал</span>
          <select value={hallId ?? ""} onChange={(event) => setHallId(Number(event.target.value))}>
            {(halls.data?.halls ?? []).map((h) => (
              <option key={h.id} value={h.id}>
                {h.name}
              </option>
            ))}
          </select>
        </label>
      </section>

      {stale ? (
        <div role="alert" className="callout callout--error" data-testid="stale-banner">
          <strong>Схема была изменена другим сотрудником или устройством.</strong>
          <p>
            Ваша версия устарела, сохранение заблокировано, чтобы не затереть чужие изменения.
            Черновик сохранён на этом устройстве.
          </p>
          <button type="button" onClick={handleReloadServerSchema}>
            Загрузить актуальную схему и отменить мои правки
          </button>
        </div>
      ) : null}

      {saveError ? (
        <p role="alert" className="callout callout--error">
          {saveError}
        </p>
      ) : null}

      <section className="card" aria-label="Панель инструментов редактора">
        <div className="editor-toolbar">
          <button type="button" onClick={handleAddTable} disabled={!draft}>
            Добавить стол
          </button>
          <label className="field">
            <span>Элемент</span>
            <select id="static-type" defaultValue="wall">
              {STATIC_TYPES.map((type) => (
                <option key={type} value={type}>
                  {STATIC_TYPE_LABELS[type]}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={!draft}
            onClick={() => {
              const selectEl = document.getElementById("static-type") as HTMLSelectElement | null;
              handleAddStatic((selectEl?.value ?? "wall") as StaticElement["type"]);
            }}
          >
            Добавить элемент
          </button>
          <button
            type="button"
            onClick={undo}
            disabled={undoStack.length === 0}
            aria-label="Отменить"
          >
            Undo
          </button>
          <button
            type="button"
            onClick={redo}
            disabled={redoStack.length === 0}
            aria-label="Повторить"
          >
            Redo
          </button>
          <span className="muted" data-testid="dirty-state">
            {dirty ? "Есть несохранённые изменения" : "Сохранено"}
          </span>
          <button
            type="button"
            onClick={handleSave}
            disabled={!canSave}
            aria-label="Сохранить схему"
          >
            {saveLayout.isPending ? "Сохранение…" : "Сохранить"}
          </button>
        </div>
      </section>

      <section className="card" aria-label="Холст">
        {draft ? <HallEditorCanvas /> : <p>Загрузка схемы…</p>}
      </section>

      <section className="card" aria-label="Свойства выбранного объекта">
        <h2>Свойства</h2>
        {selectedTable ? (
          <div className="properties-panel" data-testid="table-properties">
            <label className="field">
              <span>Номер</span>
              <input
                value={selectedTable.number}
                onChange={(event) => updateTable(selectedTable.key, { number: event.target.value })}
              />
            </label>
            <label className="field">
              <span>Мест</span>
              <input
                type="number"
                min={1}
                value={selectedTable.capacity}
                onChange={(event) =>
                  updateTable(selectedTable.key, { capacity: Number(event.target.value) })
                }
              />
            </label>
            <label className="field">
              <span>Форма</span>
              <select
                value={selectedTable.shape}
                onChange={(event) => updateTable(selectedTable.key, { shape: event.target.value })}
              >
                <option value="rect">Прямоугольник</option>
                <option value="circle">Круг</option>
              </select>
            </label>
            {(["x", "y", "width", "height", "rotation"] as const).map((field) => (
              <label className="field" key={field}>
                <span>{field}</span>
                <input
                  type="number"
                  value={selectedTable[field]}
                  onChange={(event) =>
                    updateTable(selectedTable.key, { [field]: Number(event.target.value) })
                  }
                />
              </label>
            ))}
            <button type="button" className="button--danger" onClick={archiveSelectedTable}>
              {selectedTable.id === null ? "Удалить стол" : "Архивировать стол"}
            </button>
          </div>
        ) : selectedStatic ? (
          <div className="properties-panel" data-testid="static-properties">
            <p className="muted">Тип: {STATIC_TYPE_LABELS[selectedStatic.element.type]}</p>
            {"label" in selectedStatic.element ? (
              <label className="field">
                <span>Подпись</span>
                <input
                  value={selectedStatic.element.label ?? ""}
                  onChange={(event) =>
                    updateStatic(selectedStatic.key, { label: event.target.value })
                  }
                />
              </label>
            ) : null}
            {selectedStatic.element.type === "text" ? (
              <>
                <label className="field">
                  <span>Текст</span>
                  <input
                    value={selectedStatic.element.text}
                    onChange={(event) =>
                      updateStatic(selectedStatic.key, { text: event.target.value })
                    }
                  />
                </label>
                <label className="field">
                  <span>Размер шрифта</span>
                  <input
                    type="number"
                    min={1}
                    max={200}
                    value={selectedStatic.element.font_size}
                    onChange={(event) =>
                      updateStatic(selectedStatic.key, { font_size: Number(event.target.value) })
                    }
                  />
                </label>
              </>
            ) : null}
            {(["x", "y", "width", "height", "rotation"] as const).map((field) =>
              field in selectedStatic.element ? (
                <label className="field" key={field}>
                  <span>{field}</span>
                  <input
                    type="number"
                    value={(selectedStatic.element as unknown as Record<string, number>)[field]}
                    onChange={(event) =>
                      updateStatic(selectedStatic.key, { [field]: Number(event.target.value) })
                    }
                  />
                </label>
              ) : null,
            )}
            <button type="button" className="button--danger" onClick={deleteSelectedStatic}>
              Удалить элемент
            </button>
          </div>
        ) : (
          <p className="muted">Выберите стол или элемент на схеме.</p>
        )}
      </section>
    </main>
  );
}

export default HallEditorPage;
