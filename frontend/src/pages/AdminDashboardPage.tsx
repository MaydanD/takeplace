import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/api/client";
import type { components } from "@/api/generated/schema";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useLogout, useLogoutAll, useMe, useSettings, useUpdateSettings } from "@/api/adminQueries";

/**
 * Minimal admin area for Stage 2.
 *
 * It shows the authenticated venue/admin (both resolved from the session) and
 * manages the settings that already exist in the v1 model. Booking operations
 * arrive in later stages.
 */
export function AdminDashboardPage() {
  const system = useQuery({
    queryKey: ["admin", "system", "status"],
    queryFn: () =>
      apiRequest<components["schemas"]["SystemStatusResponse"]>("/api/admin/v1/system/status"),
    refetchInterval: 30000,
  });
  const me = useMe();
  const settings = useSettings();
  const updateSettings = useUpdateSettings();
  const logoutMutation = useLogout();
  const logoutAllMutation = useLogoutAll();
  const navigate = useNavigate();

  const venue = settings.data ?? me.data?.venue;
  const admin = me.data?.admin;

  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [phone, setPhone] = useState("");
  const [onlineBooking, setOnlineBooking] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings.data) {
      setName(settings.data.name);
      setAddress(settings.data.address ?? "");
      setPhone(settings.data.phone ?? "");
      setOnlineBooking(settings.data.online_booking_enabled);
    }
  }, [settings.data]);

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaved(false);
    await updateSettings.mutateAsync({
      name,
      address: address || null,
      phone: phone || null,
      online_booking_enabled: onlineBooking,
    });
    setSaved(true);
  }

  async function handleLogout() {
    await logoutMutation.mutateAsync();
    navigate("/admin/login", { replace: true });
  }

  async function handleLogoutAll() {
    await logoutAllMutation.mutateAsync();
    navigate("/admin/login", { replace: true });
  }

  return (
    <main className="page">
      {system.data?.online_abuse_alert && (
        <p role="alert" className="callout callout--error">
          Необычно много онлайн-бронирований. Проверьте новые брони; при необходимости отключите
          онлайн-бронирование в настройках ниже.
        </p>
      )}
      <header className="page-header">
        <h1>Админка</h1>
        <div className="page-header__actions">
          <Link to="/admin/vk" className="button-link">
            Уведомления VK
          </Link>
          <Link to="/admin/bookings" className="button-link">
            Книга броней
          </Link>
          <Link to="/admin/halls" className="button-link">
            Залы
          </Link>
          <Link to="/admin/schedule" className="button-link">
            Расписание
          </Link>
          <button type="button" onClick={handleLogout} disabled={logoutMutation.isPending}>
            Выйти
          </button>
          <button
            type="button"
            className="button--danger"
            onClick={handleLogoutAll}
            disabled={logoutAllMutation.isPending}
          >
            Выйти на всех устройствах
          </button>
        </div>
      </header>

      <section className="card" aria-label="Заведение">
        <h2>{venue?.name ?? "—"}</h2>
        <dl className="facts">
          <div>
            <dt>Slug</dt>
            <dd>{venue?.slug ?? "—"}</dd>
          </div>
          <div>
            <dt>Timezone</dt>
            <dd>{venue?.timezone ?? "—"}</dd>
          </div>
          <div>
            <dt>Администратор</dt>
            <dd>{admin?.login ?? "—"}</dd>
          </div>
          <div>
            <dt>Online booking</dt>
            <dd>{venue?.online_booking_enabled ? "включён" : "выключен"}</dd>
          </div>
        </dl>
      </section>

      <section className="card" aria-label="Настройки заведения">
        <h2>Настройки</h2>
        {settings.isError ? (
          <p role="alert">Не удалось загрузить настройки.</p>
        ) : (
          <form onSubmit={handleSave}>
            <label className="field">
              <span>Название</span>
              <input value={name} onChange={(event) => setName(event.target.value)} required />
            </label>
            <label className="field">
              <span>Адрес</span>
              <input value={address} onChange={(event) => setAddress(event.target.value)} />
            </label>
            <label className="field">
              <span>Телефон</span>
              <input value={phone} onChange={(event) => setPhone(event.target.value)} />
            </label>
            <label className="field field--checkbox">
              <input
                type="checkbox"
                checked={onlineBooking}
                onChange={(event) => setOnlineBooking(event.target.checked)}
              />
              <span>Разрешить онлайн-бронирование</span>
            </label>
            <button type="submit" disabled={updateSettings.isPending}>
              {updateSettings.isPending ? "Сохранение…" : "Сохранить"}
            </button>
            {updateSettings.isError ? <p role="alert">Не удалось сохранить.</p> : null}
            {saved ? <p role="status">Сохранено.</p> : null}
          </form>
        )}
      </section>
    </main>
  );
}
