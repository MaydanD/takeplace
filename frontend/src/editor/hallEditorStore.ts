/**
 * Hall layout editor store (PROJECT-SPEC §31): local draft only.
 *
 * `selectedElement`, `draftGeometry`, `dirty`, `undoStack`, `redoStack`.
 * The server is the source of truth; the draft is a serialisable snapshot of
 * editor-owned fields sent full-state on Save. Operational `is_bookable` is
 * deliberately NOT part of the draft.
 */
import { create } from "zustand";

import type { HallDetail, LayoutSaveTable, StaticElement } from "@/api/halls";

export interface DraftTable {
  key: string;
  id: number | null;
  number: string;
  capacity: number;
  shape: string;
  x: number;
  y: number;
  width: number;
  height: number;
  rotation: number;
  z_index: number;
}

export interface DraftStatic {
  key: string;
  element: StaticElement;
}

export interface DraftGeometry {
  canvas_width: number;
  canvas_height: number;
  tables: DraftTable[];
  static_elements: DraftStatic[];
}

export type Selection = { kind: "table"; key: string } | { kind: "static"; key: string } | null;

let localCounter = 0;

export function nextLocalKey(prefix: string): string {
  localCounter += 1;
  return `${prefix}-local-${localCounter}-${Date.now()}`;
}

export function draftFromHall(hall: HallDetail): DraftGeometry {
  return {
    canvas_width: hall.canvas_width,
    canvas_height: hall.canvas_height,
    // Archived tables stay in the DB for history/FK (§6.5) but are not part of
    // the editor state: the layout-save payload must only carry active tables,
    // otherwise a reloaded draft would send an archived id the service refuses
    // (`TABLE_NOT_FOUND`) and the editor could never save again (§29.3).
    tables: hall.tables
      .filter((t) => t.archived_at === null)
      .map((t) => ({
        key: `table-${t.id}`,
        id: t.id,
        number: t.number,
        capacity: t.capacity,
        shape: t.shape,
        x: t.x,
        y: t.y,
        width: t.width,
        height: t.height,
        rotation: t.rotation,
        z_index: t.z_index,
      })),
    static_elements: hall.static_elements.map((element) => ({
      key: nextLocalKey("static"),
      element: structuredClone(element),
    })),
  };
}

export function draftToSave(
  draft: DraftGeometry,
  expected_revision: number,
): {
  expected_revision: number;
  canvas_width: number;
  canvas_height: number;
  tables: LayoutSaveTable[];
  static_elements: StaticElement[];
} {
  return {
    expected_revision,
    canvas_width: draft.canvas_width,
    canvas_height: draft.canvas_height,
    tables: draft.tables.map((t) => ({
      // `id: null` explicitly means "create"; an existing id updates in place.
      id: t.id,
      number: t.number,
      capacity: t.capacity,
      shape: t.shape,
      x: t.x,
      y: t.y,
      width: t.width,
      height: t.height,
      rotation: t.rotation,
      z_index: t.z_index,
    })),
    static_elements: draft.static_elements.map((s) => s.element),
  };
}

function cloneDraft(draft: DraftGeometry): DraftGeometry {
  return structuredClone(draft);
}

/** localStorage: reload restores the draft with ITS base revision (§31).
 *
 * The revision travels with the draft: on restore we must send the revision the
 * draft was built from, not the current server one, otherwise a reload after a
 * remote edit would silently overwrite it (last-write-wins, forbidden).
 */
const DRAFT_STORAGE_PREFIX = "takeplace-hall-draft-";

interface PersistedDraft {
  baseRevision: number;
  draft: DraftGeometry;
}

function storageKey(hallId: number): string {
  return `${DRAFT_STORAGE_PREFIX}${hallId}`;
}

export function loadPersistedDraft(hallId: number): PersistedDraft | null {
  try {
    const raw = localStorage.getItem(storageKey(hallId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedDraft;
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      typeof parsed.baseRevision !== "number" ||
      typeof parsed.draft !== "object" ||
      parsed.draft === null ||
      typeof parsed.draft.canvas_width !== "number" ||
      typeof parsed.draft.canvas_height !== "number" ||
      !Array.isArray(parsed.draft.tables) ||
      !Array.isArray(parsed.draft.static_elements)
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function persistDraft(hallId: number, value: PersistedDraft | null): void {
  try {
    if (value === null) {
      localStorage.removeItem(storageKey(hallId));
    } else {
      localStorage.setItem(storageKey(hallId), JSON.stringify(value));
    }
  } catch {
    // Storage failures must never break editing.
  }
}

/** Bounded history: one gesture = one undo step (drag/resize = 1 step). */
export const MAX_UNDO_STEPS = 100;

/** Viewport zoom range (§31: the store owns `zoom`/`pan`). */
export const ZOOM_MIN = 0.25;
export const ZOOM_STEP = 1.2;
export const ZOOM_MAX = 4;

export function clampZoom(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value));
}

interface EditorState {
  hallId: number | null;
  baseRevision: number | null;
  draft: DraftGeometry | null;
  dirty: boolean;
  selection: Selection;
  undoStack: DraftGeometry[];
  redoStack: DraftGeometry[];
  /** Viewport transform (§31). `pan` is the stage offset in screen pixels. */
  zoom: number;
  pan: { x: number; y: number };
  setZoom: (zoom: number) => void;
  setPan: (pan: { x: number; y: number }) => void;
  openHall: (hall: HallDetail, options?: { restoreDraft?: boolean }) => void;
  commitDraft: (next: DraftGeometry, options?: { silent?: boolean }) => void;
  select: (selection: Selection) => void;
  undo: () => void;
  redo: () => void;
  discardDraft: (hall: HallDetail) => void;
  markSaved: (hall: HallDetail) => void;
}

export const useHallEditorStore = create<EditorState>()((set) => ({
  hallId: null,
  baseRevision: null,
  draft: null,
  dirty: false,
  selection: null,
  undoStack: [],
  redoStack: [],
  zoom: 1,
  pan: { x: 0, y: 0 },

  setZoom: (zoom) => set(() => ({ zoom: clampZoom(zoom) })),

  setPan: (pan) => set(() => ({ pan })),

  openHall: (hall, options) =>
    set(() => {
      const fresh = draftFromHall(hall);
      if (options?.restoreDraft) {
        const persisted = loadPersistedDraft(hall.id);
        if (persisted) {
          return {
            hallId: hall.id,
            // The draft's own revision, NOT the server's: saving must fail with
            // LAYOUT_STALE if the server moved on while the draft was stored.
            baseRevision: persisted.baseRevision,
            draft: persisted.draft,
            dirty: true,
            selection: null,
            undoStack: [fresh],
            redoStack: [],
            zoom: 1,
            pan: { x: 0, y: 0 },
          };
        }
      }
      persistDraft(hall.id, null);
      return {
        hallId: hall.id,
        baseRevision: hall.layout_revision,
        draft: fresh,
        dirty: false,
        selection: null,
        undoStack: [],
        redoStack: [],
        zoom: 1,
        pan: { x: 0, y: 0 },
      };
    }),

  commitDraft: (next, options) =>
    set((state) => {
      if (state.hallId === null || state.draft === null || state.baseRevision === null) {
        return state;
      }
      persistDraft(state.hallId, { baseRevision: state.baseRevision, draft: next });
      if (options?.silent) {
        return { draft: next, dirty: true };
      }
      const undoStack = [...state.undoStack, cloneDraft(state.draft)];
      return {
        draft: next,
        dirty: true,
        undoStack: undoStack.slice(-MAX_UNDO_STEPS),
        redoStack: [],
      };
    }),

  select: (selection) => set(() => ({ selection })),

  undo: () =>
    set((state) => {
      const previous = state.undoStack[state.undoStack.length - 1];
      if (state.hallId === null || state.draft === null || previous === undefined) {
        return state;
      }
      if (state.baseRevision !== null) {
        persistDraft(state.hallId, { baseRevision: state.baseRevision, draft: previous });
      }
      return {
        draft: cloneDraft(previous),
        undoStack: state.undoStack.slice(0, -1),
        redoStack: [...state.redoStack, cloneDraft(state.draft)].slice(-MAX_UNDO_STEPS),
        dirty: true,
        selection: null,
      };
    }),

  redo: () =>
    set((state) => {
      const next = state.redoStack[state.redoStack.length - 1];
      if (state.hallId === null || state.draft === null || next === undefined) {
        return state;
      }
      if (state.baseRevision !== null) {
        persistDraft(state.hallId, { baseRevision: state.baseRevision, draft: next });
      }
      return {
        draft: cloneDraft(next),
        undoStack: [...state.undoStack, cloneDraft(state.draft)].slice(-MAX_UNDO_STEPS),
        redoStack: state.redoStack.slice(0, -1),
        dirty: true,
        selection: null,
      };
    }),

  discardDraft: (hall) =>
    set(() => {
      persistDraft(hall.id, null);
      return {
        hallId: hall.id,
        baseRevision: hall.layout_revision,
        draft: draftFromHall(hall),
        dirty: false,
        selection: null,
        undoStack: [],
        redoStack: [],
        zoom: 1,
        pan: { x: 0, y: 0 },
      };
    }),

  markSaved: (hall) =>
    set(() => {
      persistDraft(hall.id, null);
      return {
        hallId: hall.id,
        baseRevision: hall.layout_revision,
        draft: draftFromHall(hall),
        dirty: false,
        selection: null,
        undoStack: [],
        redoStack: [],
        zoom: 1,
        pan: { x: 0, y: 0 },
      };
    }),
}));

export function createDraftStatic(type: StaticElement["type"]): DraftStatic {
  const base = { x: 40, y: 40, rotation: 0, z_index: 0 };
  if (type === "text") {
    return {
      key: nextLocalKey("static"),
      element: { type, ...base, text: "Текст", font_size: 16 },
    };
  }
  return {
    key: nextLocalKey("static"),
    element: { type, ...base, width: 120, height: 40 },
  };
}

export function createDraftTable(overrides: Partial<DraftTable> = {}): DraftTable {
  return {
    key: nextLocalKey("table"),
    id: null,
    number: "1",
    capacity: 2,
    shape: "rect",
    x: 40,
    y: 40,
    width: 80,
    height: 80,
    rotation: 0,
    z_index: 0,
    ...overrides,
  };
}
