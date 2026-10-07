import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiRequest } from "@/api/client";
import { useMe } from "@/api/adminQueries";
import type { components } from "@/api/generated/schema";

type Config = components["schemas"]["VKIntegrationSummary"];
type Update = components["schemas"]["VKIntegrationUpdate"];
const VK_URL = "/api/admin/v1/integrations/vk";
const OUTBOX_URL = "/api/admin/v1/outbox";

function IntegrationForm({ config, saved }: { config: Config; saved: (config: Config) => void }) {
  const [enabled, setEnabled] = useState(config.enabled);
  const [community, setCommunity] = useState(String(config.community_id ?? ""));
  const [peer, setPeer] = useState(String(config.peer_id ?? ""));
  const [token, setToken] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(false);
    const body: Update = {
      enabled,
      community_id: community ? Number(community) : null,
      peer_id: peer ? Number(peer) : null,
      ...(token ? { access_token: token } : {}),
    };
    try {
      // Keep the write-only token out of React Query's mutation cache.
      const result = await apiRequest<Config>(VK_URL, { method: "PUT", body });
      setToken("");
      saved(result);
    } catch {
      setError(true);
    } finally {
      setPending(false);
    }
  }
  return (
    <form onSubmit={submit}>
      <p>Сообщения о новых онлайн-бронях в рабочую беседу сотрудников.</p>
      <p>Токен: {config.has_token ? "сохранён" : "не задан"}</p>
      <label className="field field--checkbox">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Включить уведомления VK
      </label>
      <label className="field">
        <span>ID сообщества</span>
        <input
          type="number"
          min="1"
          step="1"
          required={enabled}
          value={community}
          onChange={(e) => setCommunity(e.target.value)}
        />
      </label>
      <label className="field">
        <span>ID рабочей беседы (peer_id)</span>
        <input
          type="number"
          min="1"
          step="1"
          required={enabled}
          value={peer}
          onChange={(e) => setPeer(e.target.value)}
        />
      </label>
      <label className="field">
        <span>
          {config.has_token
            ? "Новый токен (оставьте пустым, чтобы сохранить текущий)"
            : "Токен доступа"}
        </span>
        <input
          type="password"
          autoComplete="new-password"
          maxLength={8192}
          required={enabled && !config.has_token}
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
      </label>
      <button disabled={pending} type="submit">
        {pending ? "Сохранение…" : "Сохранить VK"}
      </button>
      {error && (
        <p role="alert">
          Не удалось сохранить. Проверьте параметры интеграции и повторите попытку.
        </p>
      )}
    </form>
  );
}

export function VKIntegrationPage() {
  const me = useMe();
  const venueId = me.data?.venue.id;
  const client = useQueryClient();
  const configKey = ["admin", "vk", venueId];
  const [saved, setSaved] = useState(false);
  const [cursor, setCursor] = useState<number | undefined>();
  const config = useQuery({
    queryKey: configKey,
    queryFn: ({ signal }) => apiRequest<Config>(VK_URL, { signal }),
    enabled: !!venueId,
    retry: false,
  });
  const dead = useQuery({
    queryKey: ["admin", "outbox", venueId, cursor],
    queryFn: ({ signal }) =>
      apiRequest<components["schemas"]["OutboxDeadList"]>(
        `${OUTBOX_URL}/dead${cursor ? `?before_id=${cursor}` : ""}`,
        { signal },
      ),
    enabled: !!venueId,
    refetchInterval: 30000,
  });
  const action = useMutation({
    mutationFn: ({ id, operation }: { id: number; operation: "retry" | "acknowledge" }) =>
      apiRequest(`${OUTBOX_URL}/${id}/${operation}`, { method: "POST" }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["admin", "outbox"] }),
        client.invalidateQueries({ queryKey: ["admin", "system"] }),
      ]);
    },
  });
  return (
    <main className="page">
      <header className="page-header">
        <h1>Уведомления VK</h1>
        <Link to="/admin">Настройки заведения</Link>
      </header>
      <section className="card" aria-label="Интеграция VK">
        <h2>Интеграция</h2>
        {config.isPending && <p>Загрузка…</p>}
        {config.isError && (
          <p role="alert">
            Не удалось загрузить интеграцию.{" "}
            <button onClick={() => void config.refetch()}>Повторить загрузку</button>
          </p>
        )}
        {config.data && (
          <IntegrationForm
            key={config.dataUpdatedAt}
            config={config.data}
            saved={(value) => {
              client.setQueryData(configKey, value);
              setSaved(true);
            }}
          />
        )}
        {saved && <p role="status">Настройки VK сохранены.</p>}
      </section>
      <section className="card" aria-label="Недоставленные уведомления">
        <h2>Недоставленные уведомления</h2>
        <p>
          Повторная попытка не продлевает срок уведомления. Подтверждение убирает проблему из
          баннера и сохраняет историю.
        </p>
        {dead.isPending && <p>Загрузка…</p>}
        {dead.isError && (
          <p role="alert">
            Не удалось загрузить список.{" "}
            <button onClick={() => void dead.refetch()}>Обновить список</button>
          </p>
        )}
        {dead.data?.jobs.length === 0 && <p>Недоставленных уведомлений нет.</p>}
        {dead.data?.jobs.map((job) => (
          <article className="card" key={job.id}>
            <h3>Уведомление №{job.id}</h3>
            <p>
              Попыток: {job.attempts}. Ошибка: {job.last_error ?? "не указана"}.
            </p>
            <p>{job.acknowledged_at ? "Проблема подтверждена" : "Требует внимания"}</p>
            <button
              disabled={action.isPending}
              onClick={() => action.mutate({ id: job.id, operation: "retry" })}
            >
              Повторить
            </button>{" "}
            <button
              disabled={action.isPending || !!job.acknowledged_at}
              onClick={() => action.mutate({ id: job.id, operation: "acknowledge" })}
            >
              Подтвердить проблему
            </button>
          </article>
        ))}
        {cursor && <button onClick={() => setCursor(undefined)}>К новым уведомлениям</button>}
        {dead.data?.next_cursor && (
          <button onClick={() => setCursor(dead.data?.next_cursor ?? undefined)}>
            Более ранние уведомления
          </button>
        )}
        {action.isError && (
          <p role="alert">Действие не выполнено. Обновите список и повторите попытку.</p>
        )}
      </section>
    </main>
  );
}
