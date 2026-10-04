import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useLogin } from "@/api/adminQueries";
import { ApiError } from "@/api/client";

function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "INVALID_CREDENTIALS":
        return "Неверный логин или пароль.";
      case "RATE_LIMITED":
        return "Слишком много попыток. Попробуйте позже.";
      case "VENUE_SUSPENDED":
        return "Заведение приостановлено.";
      case "ADMIN_DISABLED":
        return "Аккаунт администратора отключён.";
      default:
        return `Не удалось войти (код ${error.status}).`;
    }
  }
  return "Не удалось войти. Проверьте соединение.";
}

interface LocationState {
  from?: { pathname?: string };
}

export function AdminLoginPage() {
  const [loginValue, setLoginValue] = useState("");
  const [password, setPassword] = useState("");
  const loginMutation = useLogin();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as LocationState | null)?.from?.pathname ?? "/admin";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await loginMutation.mutateAsync({ login: loginValue, password });
      navigate(from, { replace: true });
    } catch {
      // The error is rendered from `loginMutation.error` below.
    }
  }

  return (
    <main className="page">
      <h1>Вход в админку</h1>
      <p>Введите логин и пароль администратора заведения.</p>

      <form className="card" onSubmit={handleSubmit}>
        <label className="field">
          <span>Логин</span>
          <input
            name="login"
            autoComplete="username"
            value={loginValue}
            onChange={(event) => setLoginValue(event.target.value)}
            required
          />
        </label>

        <label className="field">
          <span>Пароль</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>

        <button type="submit" disabled={loginMutation.isPending}>
          {loginMutation.isPending ? "Вход…" : "Войти"}
        </button>

        {loginMutation.isError ? (
          <p role="alert">{loginErrorMessage(loginMutation.error)}</p>
        ) : null}
      </form>
    </main>
  );
}
