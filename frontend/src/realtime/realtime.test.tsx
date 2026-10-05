import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RECONNECT_DELAY_MS, realtimeClient } from "@/realtime/client";
import {
  bookingRealtimeKeys,
  realtimeInvalidationKeys,
  useRealtimeSync,
  venueRealtimeKeys,
} from "@/realtime/useRealtime";

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  withCredentials: boolean;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(url: string, init?: { withCredentials?: boolean }) {
    this.url = url;
    this.withCredentials = init?.withCredentials ?? false;
    MockEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  emit(data: unknown) {
    const payload = typeof data === "string" ? data : JSON.stringify(data);
    this.onmessage?.({ data: payload } as MessageEvent);
  }

  open() {
    this.onopen?.(new Event("open"));
  }

  fail() {
    this.onerror?.(new Event("error"));
  }

  static reset() {
    MockEventSource.instances = [];
  }
}

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function spyClient() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(queryClient, "invalidateQueries");
  return { queryClient, spy };
}

function sourceAt(index: number): MockEventSource {
  const instance = MockEventSource.instances[index];
  if (!instance) throw new Error(`no EventSource at index ${index}`);
  return instance;
}

function source(): MockEventSource {
  return sourceAt(0);
}

beforeEach(() => {
  vi.stubGlobal("EventSource", MockEventSource);
  MockEventSource.reset();
});

afterEach(() => {
  realtimeClient.disconnect();
  MockEventSource.reset();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("realtime invalidation mapping", () => {
  it("maps booking signals to booking queries", () => {
    expect(realtimeInvalidationKeys({ type: "booking.created" })).toBe(bookingRealtimeKeys);
    expect(realtimeInvalidationKeys({ type: "booking.updated" })).toBe(bookingRealtimeKeys);
  });

  it("maps resync and unknown signals to the full venue set", () => {
    expect(realtimeInvalidationKeys({ type: "resync" })).toBe(venueRealtimeKeys);
    // Forward-compatible: an unseen type is treated as a full resync.
    expect(realtimeInvalidationKeys({ type: "future.event" })).toBe(venueRealtimeKeys);
    expect(realtimeInvalidationKeys({})).toBe(venueRealtimeKeys);
  });
});

describe("useRealtimeSync", () => {
  it("opens exactly one EventSource across mounts and closes it on cleanup", () => {
    const { queryClient } = spyClient();
    const first = renderHook(() => useRealtimeSync(true), { wrapper: wrapper(queryClient) });
    const second = renderHook(() => useRealtimeSync(true), { wrapper: wrapper(queryClient) });
    expect(MockEventSource.instances).toHaveLength(1);
    expect(source().url).toContain("/api/admin/v1/stream");
    expect(realtimeClient.isConnected).toBe(true);

    first.unmount();
    // Still mounted once -> connection stays.
    expect(source().closed).toBe(false);
    second.unmount();
    expect(source().closed).toBe(true);
  });

  it("does not connect while disabled", () => {
    const { queryClient } = spyClient();
    const { unmount } = renderHook(() => useRealtimeSync(false), { wrapper: wrapper(queryClient) });
    expect(MockEventSource.instances).toHaveLength(0);
    unmount();
  });

  it("resyncs every admin query on (re)connect", () => {
    const { queryClient, spy } = spyClient();
    renderHook(() => useRealtimeSync(true), { wrapper: wrapper(queryClient) });
    act(() => source().open());
    const invalidated = spy.mock.calls.map((call) => call[0]?.queryKey);
    expect(invalidated).toContainEqual(["admin"]);
    expect(invalidated).toContainEqual(["booking-book"]);
  });

  it("invalidates booking queries (not the whole admin surface) on a booking event", () => {
    const { queryClient, spy } = spyClient();
    renderHook(() => useRealtimeSync(true), { wrapper: wrapper(queryClient) });
    act(() => source().open());
    spy.mockClear();
    act(() => source().emit({ venue_id: 1, type: "booking.created", ids: [7] }));
    const invalidated = spy.mock.calls.map((call) => call[0]?.queryKey);
    expect(invalidated).toContainEqual(["booking-book"]);
    expect(invalidated).toContainEqual(["booking-live"]);
    expect(invalidated).not.toContainEqual(["admin"]);
  });

  it("does not refetch on heartbeat / idle time", () => {
    vi.useFakeTimers();
    const { queryClient, spy } = spyClient();
    renderHook(() => useRealtimeSync(true), { wrapper: wrapper(queryClient) });
    act(() => source().open());
    spy.mockClear();
    // Heartbeats arrive as SSE comment lines, which never produce a message
    // event; simply letting time pass must not invalidate anything.
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    expect(spy).not.toHaveBeenCalled();
  });

  it("ignores malformed frames and treats unknown types as resync", () => {
    const { queryClient, spy } = spyClient();
    renderHook(() => useRealtimeSync(true), { wrapper: wrapper(queryClient) });
    act(() => source().open());
    spy.mockClear();
    act(() => source().emit("not-json"));
    expect(spy).not.toHaveBeenCalled();
    act(() => source().emit({ venue_id: 1, type: "mystery.event" }));
    expect(spy.mock.calls.map((call) => call[0]?.queryKey)).toContainEqual(["admin"]);
  });

  it("stops invalidating after unmount", () => {
    const { queryClient, spy } = spyClient();
    const { unmount } = renderHook(() => useRealtimeSync(true), { wrapper: wrapper(queryClient) });
    const opened = source();
    act(() => opened.open());
    spy.mockClear();
    unmount();
    act(() => opened.emit({ venue_id: 1, type: "booking.updated", ids: [1] }));
    expect(spy).not.toHaveBeenCalled();
  });

  it("gives up the fast path after repeated errors and asks for a resync", () => {
    vi.useFakeTimers();
    const { queryClient, spy } = spyClient();
    renderHook(() => useRealtimeSync(true), { wrapper: wrapper(queryClient) });
    spy.mockClear();
    act(() => {
      for (let i = 0; i < 5; i += 1) source().fail();
    });
    // The doomed EventSource is closed, but the client is only backoff-waiting,
    // not permanently dead: a slowed reconnect is scheduled while subscribed.
    expect(realtimeClient.isConnected).toBe(false);
    expect(realtimeClient.isReconnecting).toBe(true);
    expect(spy.mock.calls.map((call) => call[0]?.queryKey)).toContainEqual(["admin"]);
  });

  // F-01 regression: a long backend/network outage must never kill realtime for
  // good while a subscriber is still mounted. After the fast retries are
  // exhausted the client slows down and keeps trying until the backend returns,
  // with no extra `acquire()` and no component remount.
  it("reconnects on its own after a long outage and resyncs on reopen", () => {
    vi.useFakeTimers();
    const { queryClient, spy } = spyClient();
    renderHook(() => useRealtimeSync(true), { wrapper: wrapper(queryClient) });
    const first = source();
    act(() => first.open());
    spy.mockClear();

    // Five consecutive errors while the backend is down -> slowed reconnect.
    act(() => {
      for (let i = 0; i < 5; i += 1) first.fail();
    });
    expect(first.closed).toBe(true);
    expect(realtimeClient.isConnected).toBe(false);
    expect(realtimeClient.isReconnecting).toBe(true);

    // No new EventSource until the slowed backoff elapses.
    act(() => {
      vi.advanceTimersByTime(RECONNECT_DELAY_MS - 1);
    });
    expect(MockEventSource.instances).toHaveLength(1);
    expect(realtimeClient.isReconnecting).toBe(true);

    // Backoff elapses -> the client reconnects automatically.
    act(() => {
      vi.advanceTimersByTime(2);
    });
    expect(MockEventSource.instances).toHaveLength(2);
    expect(realtimeClient.isConnected).toBe(true);
    expect(realtimeClient.isReconnecting).toBe(false);

    // Reopening resyncs every admin query exactly like a first connect.
    const second = sourceAt(1);
    act(() => second.open());
    expect(spy.mock.calls.map((call) => call[0]?.queryKey)).toContainEqual(["admin"]);

    // A period of continued failure keeps retrying without piling up sources.
    spy.mockClear();
    act(() => {
      for (let i = 0; i < 5; i += 1) second.fail();
    });
    act(() => {
      vi.advanceTimersByTime(RECONNECT_DELAY_MS + 1);
    });
    expect(MockEventSource.instances).toHaveLength(3);
  });

  it("cancels the pending reconnect when the last subscriber releases", () => {
    vi.useFakeTimers();
    const { queryClient } = spyClient();
    const { unmount } = renderHook(() => useRealtimeSync(true), { wrapper: wrapper(queryClient) });
    act(() => {
      for (let i = 0; i < 5; i += 1) source().fail();
    });
    expect(realtimeClient.isReconnecting).toBe(true);

    unmount();
    expect(realtimeClient.isReconnecting).toBe(false);

    // Even after the backoff would have elapsed, nothing new is created.
    act(() => {
      vi.advanceTimersByTime(RECONNECT_DELAY_MS * 2);
    });
    expect(MockEventSource.instances).toHaveLength(1);
  });
});
