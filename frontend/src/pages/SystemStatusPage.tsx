import { useOps, useReadiness } from "@/api/queries";

function Badge({ ok, okText, badText }: { ok: boolean; okText: string; badText: string }) {
  return (
    <span className={`badge ${ok ? "badge--ok" : "badge--bad"}`}>{ok ? okText : badText}</span>
  );
}

/**
 * Stage 1 status page.
 *
 * Its only job is to make frontend -> backend -> database connectivity visible.
 * Booking UI belongs to later stages (PROJECT-SPEC §50).
 */
export function SystemStatusPage() {
  const readiness = useReadiness();
  const ops = useOps();

  const readinessOk = readiness.data?.status === "ok";
  const databaseOk = readiness.data?.database === "ok";

  return (
    <main className="page">
      <h1>Takeplace</h1>
      <p>Базовая инфраструктура. Публичное бронирование появится на следующих этапах.</p>

      <section className="card" aria-label="Состояние системы">
        <h2>Состояние системы</h2>

        <div className="status-row">
          <span>API</span>
          {readiness.isPending ? (
            <span>проверка…</span>
          ) : readiness.isError ? (
            <Badge ok={false} okText="ок" badText="недоступно" />
          ) : (
            <Badge ok={readinessOk} okText="ок" badText="недоступно" />
          )}
        </div>

        <div className="status-row">
          <span>PostgreSQL</span>
          {readiness.isPending ? (
            <span>проверка…</span>
          ) : (
            <Badge ok={databaseOk} okText="ок" badText="недоступно" />
          )}
        </div>

        <div className="status-row">
          <span>Окружение</span>
          <span>{ops.data?.environment ?? "—"}</span>
        </div>

        <div className="status-row">
          <span>Operational health</span>
          <span>{ops.data?.status ?? "—"}</span>
        </div>
      </section>

      {readiness.isError ? (
        <p role="alert">
          Backend недоступен. Запустите стек командой <code>docker compose up</code>.
        </p>
      ) : null}
    </main>
  );
}
