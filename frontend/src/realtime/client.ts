/**
 * Admin realtime client (PROJECT-SPEC §37).
 *
 * Exactly one `EventSource` exists per admin application/session, no matter how
 * many React components care about realtime. The connection is reference-counted
 * and lives at the application level, so navigating between admin pages never
 * opens a second connection and unmounting never leaks one.
 *
 * The stream carries only refetch signals (`{venue_id, type, ids}`), never
 * authoritative state. On every (re)connect the `open` event triggers a full
 * resync, which is what makes missed events harmless: correctness comes from the
 * ordinary HTTP API, not from receiving every notification (§37.4). Heartbeats
 * arrive as SSE comment lines, which the browser never turns into a `message`
 * event, so they cannot provoke a refetch storm.
 *
 * Availability model: while at least one subscriber holds the client open
 * (`refCount > 0`), the client never permanently gives up. The browser's native
 * `EventSource` reconnect is used for the common transient blip; after
 * `MAX_CONSECUTIVE_ERRORS` fast failures — which means the backend or network is
 * genuinely down — the client closes the doomed `EventSource` and takes over with
 * its own slowed reconnect timer, retrying forever at a steady cadence until the
 * connection comes back or the last subscriber releases it. This is what lets a
 * long outage recover without an F5, while still never running two `EventSource`s
 * or two timers at once.
 */
import { API_BASE_URL } from "@/api/client";

const STREAM_PATH = "/api/admin/v1/stream";

//: After this many consecutive fast failures the browser's own retry is not
//: working, so the client stops relying on it and switches to a slowed,
//: self-managed reconnect (about one attempt per `RECONNECT_DELAY_MS`).
const MAX_CONSECUTIVE_ERRORS = 5;

//: Slowed reconnect cadence used while the backend is unreachable. Kept well
//: below the heartbeat interval so a recovered backend is noticed quickly.
export const RECONNECT_DELAY_MS = 12_000;

export interface RealtimeSignal {
  venue_id?: number;
  type?: string;
  ids?: number[];
}

type SignalListener = (signal: RealtimeSignal) => void;

function parseSignal(data: string): RealtimeSignal | null {
  try {
    const parsed: unknown = JSON.parse(data);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
    const signal = parsed as RealtimeSignal;
    if (signal.type !== undefined && typeof signal.type !== "string") return null;
    if (signal.ids !== undefined && !Array.isArray(signal.ids)) return null;
    return signal;
  } catch {
    // A malformed frame must never break the client.
    return null;
  }
}

export class RealtimeClient {
  private source: EventSource | null = null;
  private refCount = 0;
  private errorCount = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly signalListeners = new Set<SignalListener>();
  private readonly resyncListeners = new Set<() => void>();

  /** Whether a live `EventSource` is currently open (tests/diagnostics). */
  get isConnected(): boolean {
    return this.source !== null;
  }

  /** Whether a slowed reconnect is scheduled (tests/diagnostics). */
  get isReconnecting(): boolean {
    return this.reconnectTimer !== null;
  }

  acquire(): void {
    this.refCount += 1;
    this.ensure();
  }

  release(): void {
    this.refCount = Math.max(0, this.refCount - 1);
    if (this.refCount === 0) this.close();
  }

  /** Force-close the connection (logout / explicit teardown). */
  disconnect(): void {
    this.refCount = 0;
    this.close();
  }

  subscribe(listener: SignalListener): () => void {
    this.signalListeners.add(listener);
    return () => this.signalListeners.delete(listener);
  }

  /** Register a resync handler: full refetch on (re)connect or fatal error. */
  onResync(listener: () => void): () => void {
    this.resyncListeners.add(listener);
    return () => this.resyncListeners.delete(listener);
  }

  private ensure(): void {
    // Never open a connection without a subscriber, never while one already
    // exists, and never while a slowed reconnect is already pending: exactly one
    // `EventSource` and at most one timer at any moment.
    if (this.refCount === 0 || this.source || this.reconnectTimer) return;
    if (typeof EventSource === "undefined") return;
    const source = new EventSource(`${API_BASE_URL}${STREAM_PATH}`, { withCredentials: true });
    this.source = source;
    this.errorCount = 0;
    source.onopen = () => {
      this.errorCount = 0;
      this.emitResync();
    };
    source.onmessage = (event: MessageEvent<string>) => {
      const signal = parseSignal(event.data);
      if (signal === null) return;
      for (const listener of [...this.signalListeners]) listener(signal);
    };
    source.onerror = () => {
      this.errorCount += 1;
      if (this.errorCount < MAX_CONSECUTIVE_ERRORS) {
        // The browser retries on its own with a short backoff; let it.
        return;
      }
      // Repeated fast failures: close the doomed source and take over with a
      // slowed reconnect so we never spin and never abandon realtime.
      this.closeSource();
      this.emitResync();
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null || this.refCount === 0) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.ensure();
    }, RECONNECT_DELAY_MS);
  }

  /** Close only the current `EventSource`, keeping the client usable. */
  private closeSource(): void {
    const source = this.source;
    this.source = null;
    source?.close();
  }

  /** Tear down the connection and any pending reconnect (refCount is 0). */
  private close(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.closeSource();
    this.errorCount = 0;
  }

  private emitResync(): void {
    for (const listener of [...this.resyncListeners]) listener();
  }
}

/** The one process-wide realtime client. */
export const realtimeClient = new RealtimeClient();
