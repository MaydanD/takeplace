import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiRequest } from "@/api/client";
import type { components } from "@/api/generated/schema";

export function OutboxBanner({ venueId }: { venueId: number }) {
  const status = useQuery({
    queryKey: ["admin", "system", venueId],
    queryFn: ({ signal }) =>
      apiRequest<components["schemas"]["SystemStatusResponse"]>("/api/admin/v1/system/status", {
        signal,
      }),
    refetchInterval: 30000,
    retry: false,
  });
  const count = status.data?.outbox_unacknowledged_dead ?? 0;
  return count > 0 ? (
    <aside className="page" aria-label="Уведомления VK">
      <p role="alert" className="callout callout--error">
        Не доставлено уведомлений: {count}. <Link to="/admin/vk">Проверить уведомления</Link>
      </p>
    </aside>
  ) : null;
}
