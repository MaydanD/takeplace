import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VKIntegrationPage } from "@/pages/VKIntegrationPage";
import { OutboxBanner } from "@/components/OutboxBanner";
import { mockFetch, renderWithProviders } from "@/test/helpers";

const me = { admin: { id: 1, login: "admin", is_active: true }, venue: { id: 2 } };
const config = { enabled: false, community_id: 123, peer_id: 2000000001, has_token: true };
const job = {
  id: 17,
  type: "ONLINE_BOOKING",
  status: "DEAD",
  attempts: 8,
  last_error: "vk:5:permanent",
  created_at: "2026-10-07T00:00:00Z",
  expires_at: "2026-10-07T06:00:00Z",
  acknowledged_at: null,
};
const routes = {
  "GET /api/admin/v1/me": { status: 200, body: me },
  "GET /api/admin/v1/integrations/vk": { status: 200, body: config },
  "GET /api/admin/v1/outbox/dead": { status: 200, body: { jobs: [], unacknowledged: 0 } },
};
afterEach(() => vi.restoreAllMocks());

describe("VK settings", () => {
  it("keeps the stored token write-only and clears a replacement after save", async () => {
    const fetch = mockFetch({
      ...routes,
      "PUT /api/admin/v1/integrations/vk": { status: 200, body: config },
    });
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<VKIntegrationPage />);
    const token = await screen.findByLabelText(/Новый токен/);
    expect(token).toHaveAttribute("type", "password");
    expect(token).toHaveValue("");
    await userEvent.type(token, "replacement-secret");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить VK" }));
    await screen.findByText("Настройки VK сохранены.");
    expect(screen.getByLabelText(/Новый токен/)).toHaveValue("");
    const put = fetch.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(put?.[1]?.body)).access_token).toBe("replacement-secret");
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить VK" }));
    await waitFor(() =>
      expect(fetch.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(2),
    );
    const puts = fetch.mock.calls.filter(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(puts[1]?.[1]?.body))).not.toHaveProperty("access_token");
  });

  it("acknowledges DEAD and refetches the banner count while retaining history", async () => {
    let acknowledged = false;
    const fallback = mockFetch(routes);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const respond = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
        if (url.endsWith("/acknowledge")) {
          acknowledged = true;
          return respond({ ...job, acknowledged_at: "2026-10-07T01:00:00Z" });
        }
        if (url.endsWith("/system/status"))
          return respond({
            online_abuse_alert: false,
            outbox_unacknowledged_dead: acknowledged ? 0 : 1,
          });
        if (url.endsWith("/outbox/dead"))
          return respond({
            jobs: [{ ...job, acknowledged_at: acknowledged ? "2026-10-07T01:00:00Z" : null }],
            unacknowledged: acknowledged ? 0 : 1,
          });
        return fallback(input, init);
      }),
    );
    renderWithProviders(
      <>
        <OutboxBanner venueId={2} />
        <VKIntegrationPage />
      </>,
    );
    expect(await screen.findByText(/Не доставлено уведомлений: 1/)).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Подтвердить проблему" }));
    await waitFor(() =>
      expect(screen.queryByText(/Не доставлено уведомлений:/)).not.toBeInTheDocument(),
    );
    expect(await screen.findByText("Проблема подтверждена")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Подтвердить проблему" })).toBeDisabled();
  });

  it("retries the selected job and removes it from the DEAD list", async () => {
    let retried = false;
    const fallback = mockFetch(routes);
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/retry")) {
        retried = true;
        return new Response(JSON.stringify({ ...job, status: "RETRY" }), { status: 200 });
      }
      if (url.endsWith("/outbox/dead"))
        return new Response(
          JSON.stringify({ jobs: retried ? [] : [job], unacknowledged: retried ? 0 : 1 }),
          { status: 200 },
        );
      return fallback(input, init);
    });
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<VKIntegrationPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Повторить" }));
    expect(await screen.findByText("Недоставленных уведомлений нет.")).toBeInTheDocument();
    expect(
      fetch.mock.calls.some(
        ([url, init]) => String(url).endsWith("/17/retry") && init?.method === "POST",
      ),
    ).toBe(true);
  });
});
