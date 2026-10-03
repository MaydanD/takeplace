import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SystemStatusPage } from "@/pages/SystemStatusPage";

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function mockFetch(routes: Record<string, { status: number; body: unknown }>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const match = Object.entries(routes).find(([path]) => url.includes(path));
    if (!match) {
      return new Response("not found", { status: 404 });
    }
    const [, response] = match;
    return new Response(JSON.stringify(response.body), {
      status: response.status,
      headers: { "Content-Type": "application/json" },
    });
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SystemStatusPage", () => {
  it("shows API and database as healthy when the backend is ready", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/health/ready": { status: 200, body: { status: "ok", database: "ok" } },
        "/health/ops": {
          status: 200,
          body: {
            status: "ok",
            database: "ok",
            environment: "development",
            outbox_unacknowledged_dead: 0,
            timezone_capability: "ok",
          },
        },
      }),
    );

    renderWithClient(<SystemStatusPage />);

    await waitFor(() => {
      expect(screen.getAllByText("ок").length).toBeGreaterThanOrEqual(2);
    });
    expect(screen.getByText("development")).toBeInTheDocument();
  });

  it("surfaces an alert when the backend is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network error");
      }),
    );

    renderWithClient(<SystemStatusPage />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });

  it("shows database as unavailable when readiness reports 503", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/health/ready": { status: 503, body: { status: "unavailable", database: "unavailable" } },
        "/health/ops": {
          status: 200,
          body: {
            status: "degraded",
            database: "unavailable",
            environment: "development",
            outbox_unacknowledged_dead: 0,
            timezone_capability: "ok",
          },
        },
      }),
    );

    renderWithClient(<SystemStatusPage />);

    await waitFor(() => {
      expect(screen.getAllByText("недоступно").length).toBeGreaterThanOrEqual(2);
    });
    expect(screen.getByText("degraded")).toBeInTheDocument();
  });
});
