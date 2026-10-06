import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HallEditorPage } from "@/pages/HallEditorPage";
import { useHallEditorStore } from "@/editor/hallEditorStore";
import { mockFetch, renderWithProviders, type MockRoute } from "@/test/helpers";

// jsdom has no canvas: render Konva nodes as plain clickable divs so the tests
// exercise user-level behaviour (selection, toolbar, save, stale UX), not
// Konva's internal rendering.
vi.mock("react-konva", () => {
  const element = (name: string) => {
    const Component = (props: Record<string, unknown>) => {
      const { children, onClick, text, ...rest } = props;
      void rest;
      // react-konva positions `Text` via a `text` prop, not JSX children.
      return (
        <div data-konva={name} onClick={onClick as (() => void) | undefined}>
          {(children ?? text) as React.ReactNode}
        </div>
      );
    };
    Component.displayName = `Mock(${name})`;
    return Component;
  };
  return {
    Stage: element("Stage"),
    Layer: element("Layer"),
    Group: element("Group"),
    Rect: element("Rect"),
    Circle: element("Circle"),
    Text: element("Text"),
    Transformer: element("Transformer"),
  };
});

const HALL_SUMMARY = {
  id: 1,
  name: "Главный зал",
  is_bookable: true,
  canvas_width: 800,
  canvas_height: 600,
  layout_revision: 1,
  archived_at: null,
  table_count: 1,
};

const TABLE = {
  id: 10,
  hall_id: 1,
  number: "1",
  capacity: 4,
  is_bookable: true,
  archived_at: null,
  x: 100,
  y: 100,
  width: 80,
  height: 80,
  rotation: 0,
  shape: "rect",
  z_index: 0,
};

const HALL_DETAIL = {
  ...HALL_SUMMARY,
  static_elements: [] as unknown[],
  tables: [TABLE],
};

function baseRoutes(): Record<string, MockRoute> {
  return {
    "PUT /api/admin/v1/halls/1/layout": {
      status: 200,
      body: { ...HALL_DETAIL, layout_revision: 2 },
    },
    "GET /api/admin/v1/halls/1": { status: 200, body: HALL_DETAIL },
    "GET /api/admin/v1/halls": { status: 200, body: { halls: [HALL_SUMMARY] } },
  };
}

function resetStore() {
  localStorage.clear();
  useHallEditorStore.setState({
    hallId: null,
    baseRevision: null,
    draft: null,
    dirty: false,
    selection: null,
    undoStack: [],
    redoStack: [],
  });
}

beforeEach(resetStore);
afterEach(() => {
  vi.restoreAllMocks();
});

describe("HallEditorPage", () => {
  it("loads the layout and starts clean", async () => {
    vi.stubGlobal("fetch", mockFetch(baseRoutes()));
    renderWithProviders(<HallEditorPage />, { route: "/admin/editor" });

    // Wait until the canvas replaced the "loading" placeholder.
    await waitFor(() => expect(screen.queryByText("Загрузка схемы…")).not.toBeInTheDocument());
    expect(await screen.findByTestId("dirty-state")).toHaveTextContent("Сохранено");
    expect(screen.getByLabelText("Редактор схемы зала")).toBeInTheDocument();
  });

  it("creates a table, edits it and saves full state with expected_revision", async () => {
    const fetchMock = mockFetch(baseRoutes());
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithProviders(<HallEditorPage />, { route: "/admin/editor" });
    await screen.findByTestId("dirty-state");

    await user.click(screen.getByRole("button", { name: "Добавить стол" }));
    expect(screen.getByTestId("dirty-state")).toHaveTextContent("Есть несохранённые изменения");
    const props = await screen.findByTestId("table-properties");
    expect(within(props).getByLabelText("Мест")).toHaveValue(2);

    await user.click(screen.getByRole("button", { name: "Сохранить схему" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const putCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url).includes("/halls/1/layout") && init?.method === "PUT",
    );
    expect(putCall).toBeTruthy();
    const body = JSON.parse(String(putCall![1]!.body)) as {
      expected_revision: number;
      tables: { id: number | null; number: string }[];
      is_bookable?: boolean;
    };
    expect(body.expected_revision).toBe(1);
    // Existing table keeps its server id; the new one has none (create).
    expect(body.tables.some((t) => t.id === 10)).toBe(true);
    expect(body.tables.some((t) => t.id === null)).toBe(true);
    // Operational flag is not part of the layout draft (§31).
    expect(body.is_bookable).toBeUndefined();
    expect(body.tables[0]).not.toHaveProperty("is_bookable");
  });

  it("supports undo/redo and clears redo after a new change", async () => {
    vi.stubGlobal("fetch", mockFetch(baseRoutes()));
    const user = userEvent.setup();
    renderWithProviders(<HallEditorPage />, { route: "/admin/editor" });
    await screen.findByTestId("dirty-state");

    await waitFor(() => expect(useHallEditorStore.getState().draft).not.toBeNull());

    const undoButton = screen.getByRole("button", { name: "Отменить" });
    const redoButton = screen.getByRole("button", { name: "Повторить" });
    expect(undoButton).toBeDisabled();
    expect(redoButton).toBeDisabled();

    // One add gesture = exactly one undo step.
    await user.click(screen.getByRole("button", { name: "Добавить стол" }));
    await waitFor(() => expect(useHallEditorStore.getState().draft?.tables).toHaveLength(2));
    expect(undoButton).toBeEnabled();

    await user.click(undoButton);
    await waitFor(() => expect(useHallEditorStore.getState().draft?.tables).toHaveLength(1));
    expect(redoButton).toBeEnabled();

    await user.click(redoButton);
    await waitFor(() => expect(useHallEditorStore.getState().draft?.tables).toHaveLength(2));

    // New change after an undo wipes the redo stack.
    await user.click(undoButton);
    await waitFor(() => expect(redoButton).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Добавить элемент" }));
    await waitFor(() => expect(redoButton).toBeDisabled());
  });

  it("adds a static element through the toolbar", async () => {
    vi.stubGlobal("fetch", mockFetch(baseRoutes()));
    const user = userEvent.setup();
    renderWithProviders(<HallEditorPage />, { route: "/admin/editor" });
    await screen.findByTestId("dirty-state");

    await user.click(screen.getByRole("button", { name: "Добавить элемент" }));
    await screen.findByTestId("static-properties");
    expect(screen.getByTestId("dirty-state")).toHaveTextContent("Есть несохранённые изменения");
  });

  it("archives the selected table out of the draft", async () => {
    vi.stubGlobal("fetch", mockFetch(baseRoutes()));
    const user = userEvent.setup();
    renderWithProviders(<HallEditorPage />, { route: "/admin/editor" });
    await screen.findByTestId("dirty-state");

    // Select the existing table on the mocked canvas.
    const tableNode = await screen.findByText("1 · 4");
    await user.click(tableNode.closest("[data-konva]") as HTMLElement);
    await screen.findByTestId("table-properties");

    await user.click(screen.getByRole("button", { name: "Архивировать стол" }));
    await waitFor(() => expect(screen.queryByTestId("table-properties")).not.toBeInTheDocument());
    expect(screen.getByTestId("dirty-state")).toHaveTextContent("Есть несохранённые изменения");
  });

  it("keeps the draft on LAYOUT_STALE, blocks save and reloads only on confirm", async () => {
    const routes = baseRoutes();
    routes["PUT /api/admin/v1/halls/1/layout"] = {
      status: 409,
      body: { code: "LAYOUT_STALE", detail: "stale" },
    };
    const fetchMock = mockFetch(routes);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithProviders(<HallEditorPage />, { route: "/admin/editor" });
    await screen.findByTestId("dirty-state");

    await user.click(screen.getByRole("button", { name: "Добавить стол" }));
    await screen.findByTestId("table-properties");
    await user.click(screen.getByRole("button", { name: "Сохранить схему" }));

    // Conflict is explained in Russian, not a generic error.
    const banner = await screen.findByTestId("stale-banner");
    expect(banner).toHaveTextContent("изменена другим сотрудником");
    expect(banner).toHaveTextContent("устарела");
    // Draft survived; save is blocked.
    expect(screen.getByTestId("dirty-state")).toHaveTextContent("Есть несохранённые изменения");
    expect(screen.getByRole("button", { name: "Сохранить схему" })).toBeDisabled();

    // Explicit confirmation reloads the server schema and drops the draft.
    await user.click(
      screen.getByRole("button", { name: "Загрузить актуальную схему и отменить мои правки" }),
    );
    await waitFor(() => expect(screen.queryByTestId("stale-banner")).not.toBeInTheDocument());
    expect(screen.getByTestId("dirty-state")).toHaveTextContent("Сохранено");
  });

  it("restores a persisted draft after reload with its base revision", async () => {
    // Simulate an interrupted session: draft persisted for hall 1 at revision 1.
    localStorage.setItem(
      "takeplace-hall-draft-1",
      JSON.stringify({
        baseRevision: 1,
        draft: {
          canvas_width: 800,
          canvas_height: 600,
          tables: [
            {
              key: "table-10",
              id: 10,
              number: "1",
              capacity: 4,
              shape: "rect",
              x: 555,
              y: 100,
              width: 80,
              height: 80,
              rotation: 0,
              z_index: 0,
            },
          ],
          static_elements: [],
        },
      }),
    );
    vi.stubGlobal("fetch", mockFetch(baseRoutes()));
    renderWithProviders(<HallEditorPage />, { route: "/admin/editor" });

    await waitFor(() =>
      expect(screen.getByTestId("dirty-state")).toHaveTextContent("Есть несохранённые изменения"),
    );
    const state = useHallEditorStore.getState();
    // The draft keeps the revision it was built from, not the server's.
    expect(state.baseRevision).toBe(1);
    expect(state.draft!.tables[0]!.x).toBe(555);
  });
});
