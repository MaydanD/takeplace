import { beforeEach, describe, expect, it } from "vitest";

import type { HallDetail } from "@/api/halls";
import {
  createDraftTable,
  draftToSave,
  loadPersistedDraft,
  useHallEditorStore,
} from "@/editor/hallEditorStore";

const HALL: HallDetail = {
  id: 7,
  name: "Зал",
  is_bookable: true,
  canvas_width: 800,
  canvas_height: 600,
  layout_revision: 3,
  archived_at: null,
  static_elements: [],
  tables: [
    {
      id: 11,
      hall_id: 7,
      number: "1",
      capacity: 4,
      is_bookable: true,
      archived_at: null,
      x: 10,
      y: 20,
      width: 80,
      height: 80,
      rotation: 0,
      shape: "rect",
      z_index: 0,
    },
  ],
};

function state() {
  return useHallEditorStore.getState();
}

beforeEach(() => {
  localStorage.clear();
  useHallEditorStore.setState({
    hallId: null,
    baseRevision: null,
    draft: null,
    dirty: false,
    selection: null,
    undoStack: [],
    redoStack: [],
    zoom: 1,
    pan: { x: 0, y: 0 },
  });
});

describe("hall editor store", () => {
  it("opens a hall as a clean draft", () => {
    state().openHall(HALL);
    expect(state().dirty).toBe(false);
    expect(state().baseRevision).toBe(3);
    expect(state().draft?.tables).toHaveLength(1);
  });

  it("commits as one undo step and clears the redo stack on new changes", () => {
    state().openHall(HALL);
    const withTable = { ...state().draft!, tables: [...state().draft!.tables, createDraftTable()] };
    state().commitDraft(withTable);
    expect(state().dirty).toBe(true);
    expect(state().undoStack).toHaveLength(1);
    expect(state().redoStack).toHaveLength(0);

    state().undo();
    expect(state().draft?.tables).toHaveLength(1);
    expect(state().redoStack).toHaveLength(1);

    state().redo();
    expect(state().draft?.tables).toHaveLength(2);

    // A new change after undo wipes the redo stack.
    state().undo();
    state().commitDraft({
      ...state().draft!,
      canvas_width: 900,
    });
    expect(state().redoStack).toHaveLength(0);
    expect(state().draft?.canvas_width).toBe(900);
  });

  it("restores a persisted draft with its own base revision", () => {
    state().openHall(HALL);
    const opened = state().draft!;
    state().commitDraft({ ...opened, tables: [{ ...opened.tables[0]!, x: 500 }] });

    // Simulate a reload: fresh state, then open with restore.
    const persisted = loadPersistedDraft(7);
    expect(persisted?.baseRevision).toBe(3);
    useHallEditorStore.setState({
      hallId: null,
      baseRevision: null,
      draft: null,
      dirty: false,
      selection: null,
      undoStack: [],
      redoStack: [],
    });
    state().openHall(HALL, { restoreDraft: true });
    expect(state().dirty).toBe(true);
    expect(state().baseRevision).toBe(3);
    expect(state().draft!.tables[0]!.x).toBe(500);
  });

  it("clears the persisted draft on save and discard", () => {
    state().openHall(HALL);
    const opened = state().draft!;
    state().commitDraft({ ...opened, tables: [{ ...opened.tables[0]!, x: 500 }] });
    expect(loadPersistedDraft(7)).not.toBeNull();

    state().markSaved({ ...HALL, layout_revision: 4 });
    expect(loadPersistedDraft(7)).toBeNull();
    expect(state().dirty).toBe(false);
    expect(state().baseRevision).toBe(4);

    state().commitDraft({ ...state().draft!, canvas_height: 700 });
    state().discardDraft({ ...HALL, layout_revision: 5 });
    expect(loadPersistedDraft(7)).toBeNull();
    expect(state().dirty).toBe(false);
    expect(state().baseRevision).toBe(5);
  });

  it("owns the viewport transform and clamps zoom", () => {
    state().openHall(HALL);
    expect(state().zoom).toBe(1);
    expect(state().pan).toEqual({ x: 0, y: 0 });

    state().setZoom(2);
    expect(state().zoom).toBe(2);
    state().setZoom(100);
    expect(state().zoom).toBe(4);
    state().setZoom(0);
    expect(state().zoom).toBe(0.25);

    state().setPan({ x: 30, y: -10 });
    expect(state().pan).toEqual({ x: 30, y: -10 });

    // Opening a hall resets the viewport.
    state().openHall(HALL);
    expect(state().zoom).toBe(1);
    expect(state().pan).toEqual({ x: 0, y: 0 });
  });

  it("excludes archived tables from the draft and the save payload (§29.3)", () => {
    const archived = {
      ...HALL.tables[0]!,
      id: 99,
      number: "9",
      archived_at: "2026-01-01T00:00:00Z",
    };
    state().openHall({ ...HALL, tables: [HALL.tables[0]!, archived] });

    expect(state().draft!.tables.map((t) => t.id)).toEqual([11]);
    const payload = draftToSave(state().draft!, state().baseRevision!);
    // The archived id must never be sent: the service would answer 404 and
    // block every later save.
    expect(payload.tables.map((t) => t.id)).toEqual([11]);
  });

  it("builds a full-state save payload without is_bookable", () => {
    state().openHall(HALL);
    const payload = draftToSave(state().draft!, state().baseRevision!);
    expect(payload.expected_revision).toBe(3);
    expect(payload.tables[0]!.id).toBe(11);
    expect(payload.tables[0]).not.toHaveProperty("is_bookable");
    expect(payload).not.toHaveProperty("is_bookable");
  });
});
