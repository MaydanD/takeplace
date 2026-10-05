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
 */
import { API_BASE_URL } from "@/api/client";

const STREAM_PATH = "/api/admin/v1/stream";

//: After this many consecutive errors without a successful open, give up and
//: ask the app to resync (which surfaces an expired session as a 401 / logout).
const MAX_CONSECUTIVE_ERRORS = 5;

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
  private readonly signalListeners = new Set<SignalListener>();
  private readonly resyncListeners = new Set<() => void>();

  /** Number of live `EventSource` connections (for tests/diagnostics). */
  get isConnected(): boolean {
    return this.source !== null;
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
    if (this.source || typeof EventSource === "undefined") return;
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
      // EventSource reconnects on its own; only give up after repeated failures.
      if (this.errorCount >= MAX_CONSECUTIVE_ERRORS) {
        this.close();
        this.emitResync();
      }
    };
  }

  private close(): void {
    const source = this.source;
    this.source = null;
    this.errorCount = 0;
    source?.close();
  }

  private emitResync(): void {
    for (const listener of [...this.resyncListeners]) listener();
  }
}

/** The one process-wide realtime client. */
export const realtimeClient = new RealtimeClient();
