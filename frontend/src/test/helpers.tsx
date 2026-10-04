import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

export interface RenderOptions {
  route?: string;
}

export function renderWithProviders(ui: ReactNode, options: RenderOptions = {}): RenderResult {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[options.route ?? "/"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

export interface MockRoute {
  status: number;
  body?: unknown;
}

/**
 * Install a fetch mock keyed by "METHOD /path". Keys are checked in insertion
 * order, so put more specific paths (e.g. `/auth/logout-all`) first.
 */
export function mockFetch(routes: Record<string, MockRoute>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const match = Object.entries(routes).find(([key]) => {
      const spaceIndex = key.indexOf(" ");
      const routeMethod = spaceIndex === -1 ? "GET" : key.slice(0, spaceIndex);
      const path = spaceIndex === -1 ? key : key.slice(spaceIndex + 1);
      return method === routeMethod.toUpperCase() && url.includes(path);
    });
    if (!match) {
      return new Response(null, { status: 404 });
    }
    const [, route] = match;
    return new Response(route.body === undefined ? null : JSON.stringify(route.body), {
      status: route.status,
      headers: { "Content-Type": "application/json" },
    });
  });
}
