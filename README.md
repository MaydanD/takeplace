# Takeplace

Онлайн-бронирование столов для заведений. Backend — FastAPI, frontend — React +
TypeScript + Vite, база данных — PostgreSQL.

Сейчас реализованы **Stage 1 — Foundation** (инфраструктура: monorepo, PostgreSQL
с ролями и таймаутами, Alembic, health, security headers, CORS, OpenAPI →
TypeScript, тесты, CI) и **Stage 2 — Tenant + Auth + CLI**: заведения (tenants),
admin-аккаунт, server-side sessions, Argon2id, безопасные cookies, tenant
isolation, операторский CLI, валидация timezone и минимальная изолированная
админка (login + настройки заведения). Бизнес-логика бронирования появится на
следующих этапах.

Источник истины — [`PROJECT-SPEC-v1.3.3.md`](PROJECT-SPEC-v1.3.3.md).
Краткий operational-конспект — [`IMPLEMENTATION-GUARDRAILS.md`](IMPLEMENTATION-GUARDRAILS.md).

---

## Требования

- **Docker** с Docker Compose v2 (для запуска всего стека одной командой).
- Для локальной разработки без Docker:
  - Python **3.11+**;
  - Node.js **20+** (npm);
  - PostgreSQL **15+** (для интеграционных тестов и миграций).

---

## Быстрый старт (одна команда)

```bash
bash scripts/dev.sh
```

Скрипт:

1. создаёт `.env` из [`.env.example`](.env.example) со сгенерированными секретами
   (если `.env` ещё нет);
2. собирает образы;
3. поднимает PostgreSQL и **ждёт его readiness** (не `sleep`);
4. применяет миграции Alembic (one-shot `migrate`-сервис);
5. запускает API и frontend.

Эквивалент вручную:

```bash
cp .env.example .env      # затем заполнить секреты, либо: bash scripts/bootstrap-env.sh
docker compose up --build
```

После старта:

| Сервис        | URL                                             |
| ------------- | ----------------------------------------------- |
| Frontend      | http://localhost:5173                           |
| API           | http://localhost:8000                           |
| Swagger (dev) | http://localhost:8000/docs                      |
| OpenAPI JSON  | http://localhost:8000/openapi.json              |
| Liveness      | http://localhost:8000/health/live               |
| Readiness     | http://localhost:8000/health/ready              |
| Ops health    | http://localhost:8000/health/ops                |
| PostgreSQL    | `127.0.0.1:5432` (только loopback, не наружу)   |

Frontend в dev общается с backend через Vite-прокси (`/api`, `/health`), поэтому
браузер видит один origin — как в production за Caddy.

Остановить:

```bash
docker compose down          # сохранить данные
docker compose down -v       # удалить и данные PostgreSQL (пересоздаст роли)
```

---

## Управление заведениями (CLI)

Superadmin UI в v1 нет: заведения создаются и обслуживаются из командной строки
(PROJECT-SPEC §53). CLI работает от роли приложения (`takeplace_app`) и выполняет
только DML.

В Docker:

```bash
docker compose run --rm api takeplace list-venues
docker compose run --rm api takeplace create-venue \
  --slug dragon --name "Dragon Hall" --timezone Europe/Moscow --login dragon-admin
```

Локально: `cd backend && takeplace ...` (или `python -m app.cli ...`).

| Команда                | Назначение                                                             |
| ---------------------- | ---------------------------------------------------------------------- |
| `create-venue`         | создаёт venue + admin account; валидирует slug и IANA timezone         |
| `reset-password <venue>` | меняет пароль и инвалидирует **все** sessions заведения              |
| `suspend-venue <venue>` | выключает venue и отзывает все его sessions                           |
| `enable-venue <venue>` | включает venue обратно                                                 |
| `list-venues`          | список заведений и их статусов                                         |

`<venue>` — это id или slug. Флаги `create-venue`: `--address`, `--phone`,
`--online-booking` (включить онлайн-бронирование сразу) и `--password` (задать
пароль вручную вместо генерации).

По умолчанию `create-venue` и `reset-password` генерируют криптографически
случайный пароль и печатают его **один раз**.

`create-venue` валидирует timezone через `zoneinfo` и отклоняет зоны с UTC-offset
переходами в rolling horizon **400 дней**; той же проверкой каждые сутки
сканируются активные заведения и поднимается `/health/ops` (§53, §47).

> Онбординг полного набора (booking counter, базовое расписание, первый зал)
> выполняется на этапах, которым принадлежат эти таблицы (Stage 3–4); таблиц
> ещё не существует, поэтому CLI их не создаёт.

---

## Конфигурация

Вся конфигурация — через переменные окружения (`.env` для локальной разработки).
В `alembic.ini` и исходниках **нет** credentials, URL или origins.

Ключевые переменные (полный список — в [`.env.example`](.env.example)):

| Переменная                             | Назначение                                              |
| -------------------------------------- | ------------------------------------------------------- |
| `TAKEPLACE_ENV`                        | `development` / `staging` / `production`                |
| `TAKEPLACE_DB_HOST` / `_PORT` / `_NAME`| Адрес и имя базы                                        |
| `TAKEPLACE_DB_APP_USER` / `_PASSWORD`  | Роль приложения (только DML)                            |
| `TAKEPLACE_DB_MIGRATOR_USER` / `_PASSWORD` | Роль миграций (DDL)                                 |
| `TAKEPLACE_DB_STATEMENT_TIMEOUT_MS`    | `statement_timeout` (prod baseline: 10000)              |
| `TAKEPLACE_DB_LOCK_TIMEOUT_MS`         | `lock_timeout` (prod baseline: 3000)                    |
| `TAKEPLACE_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` | idle-in-transaction timeout (prod baseline: 15000) |
| `TAKEPLACE_CORS_ORIGINS`               | Точные origins через запятую; `*` запрещён              |
| `TAKEPLACE_FORWARDED_ALLOW_IPS`        | Кому доверять `X-Forwarded-*` (только reverse proxy; `*` отклоняется в prod) |
| `TAKEPLACE_IDEMPOTENCY_HMAC_KEY`       | HMAC-ключ идемпотентности (PROJECT-SPEC §42.1)          |
| `TAKEPLACE_ABUSE_HMAC_KEY`             | HMAC-ключ anti-abuse fingerprint (§40)                  |
| `TAKEPLACE_VK_ENCRYPTION_KEYS`         | Key ring шифрования VK-токена (§38.5)                   |

### Production

`TAKEPLACE_ENV=production` включает **fail-fast валидацию**: приложение не
стартует, если

- `TAKEPLACE_LOG_LEVEL=DEBUG`;
- CORS origins пусты или используют не `https://`;
- секреты/пароли БД похожи на placeholder;
- ключи `TAKEPLACE_VK_ENCRYPTION_KEYS` не декодируются в 32 байта.

Интерактивная документация (`/docs`, `/redoc`) в production отключается.

---

## База данных

Используется PostgreSQL 15+ (в compose и CI — 16). SQLite нигде не применяется,
включая тесты: проверять exclusion constraints, блокировки и конкурентность можно
только на настоящем PostgreSQL.

### Роли

Создаются один раз скриптами в [`infra/postgres/init/`](infra/postgres/init):

- `takeplace_migrator` — владеет объектами схемы, выполняет DDL/миграции;
- `takeplace_app` — только DML (`SELECT/INSERT/UPDATE/DELETE`), **без**
  `CREATE/ALTER/DROP`.

Роль суперпользователя (`takeplace_admin`) приложением не используется.

> Роли создаются только при **первой** инициализации тома данных. Если изменить
> пароли ролей в `.env`, пересоздайте том: `docker compose down -v && docker compose up -d`.

### Миграции

```bash
bash scripts/migrate.sh          # применить в контейнере (docker compose)
bash scripts/migrate.sh local    # применить Alembic напрямую (env должен указывать на БД)
```

- Baseline-миграция устанавливает расширение `btree_gist`, необходимое для
  exclusion constraint в ядре бронирования (§12, §45).
- `20261003_0002` добавляет `venues`, `admin_accounts`, `admin_sessions` с
  CHECK/UNIQUE/индексами из §6.1–6.3.
- `20261003_0003` добавляет `weekly_schedules` и `schedule_exceptions` с
  CHECK/UNIQUE из §5.2–5.3.
- Проверка schema drift: `cd backend && alembic check`.

Инварианты Stage 2 держатся в самой БД (последний арбитр, §44): формат и reserved-
список slug — CHECK; один admin-аккаунт на venue и глобально уникальный login —
UNIQUE; `admin_sessions.token_hash` — UNIQUE (хранится только хэш);
`expires_at > created_at` — CHECK; составной `UNIQUE (id, venue_id)` готовит
tenant-safe FK для будущих таблиц (§7.2).

---

## Авторизация админки и tenant isolation

- Пароли — **Argon2id**, hashing/verify вне event loop в bounded pool (§39.1).
- Сессии — server-side (`admin_sessions`); raw token живёт только в cookie
  `__Host-takeplace_admin` (`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/`,
  без `Domain`), в БД хранится только SHA-256 хэш (§39.2).
- Браузеры отклоняют `__Host-` + `Secure` cookie на обычном http-origin
  (включая `http://localhost`), поэтому вне production сессионная cookie
  ослабляется: имя `takeplace_admin`, без `Secure`. Production-валидация
  запрещает такую настройку — там всегда `__Host-` + `Secure` (§39.2).
- Абсолютный TTL 30 дней **без** sliding-продления; `last_seen_at` обновляется не
  чаще раза в ~60 секунд на сессию (§6.3).
- CSRF — проверка `Origin` на всех изменяющих admin-запросах (§39.3).
- Endpoints: `POST /api/admin/v1/auth/login|logout|logout-all`, `GET /me`,
  `GET/PATCH /settings`.
- Неверный login и неверный пароль дают **одинаковый** ответ; до дорогого Argon2
  применяется дешёвый per-IP burst-limit, затем основной rate limit по `login + IP`
  (§39.5). Ключи лимитеров — HMAC-отпечатки, raw IP не сохраняется.

Tenant для admin-запроса определяется **только из session**: клиент не может
выбрать другой venue ни параметром, ни телом запроса (§7.1). Изоляция закреплена
FK/UNIQUE в БД и покрыта интеграционными тестами (§7.3).

Raw-пароли и raw session tokens никогда не сохраняются, не логируются и не
возвращаются в API (§39.7); structured logging редактирует PII/секреты.

---

## Расписание и business day (Stage 3)

- **Weekly schedule** — ровно одна строка на `(venue_id, weekday)`
  (`weekly_schedules`); новый venue создаётся с семью закрытыми днями.
  `is_open=false` — выходной. Отдельного поля `closes_next_day` нет: смена через
  полночь кодируется как `close_time < open_time` (§5.2).
- **Exceptions** (`schedule_exceptions`) — одна строка на `(venue_id, date)`;
  исключение полностью переопределяет недельное расписание конкретной business
  date (§5.3).
- **5-minute grid** — время и в weekly, и в exception обязано быть кратным
  5 минутам с нулевыми секундами; это CHECK в БД и domain-валидация до записи
  (§5.2–5.3, §44.1).
- **business_date** — каноническая логика в одном доменном месте
  (`backend/app/domain/schedule.py`, §5.6): для локальной даты `D` резолвятся
  смены `D-1` и `D`; если `operation_now` внутри смены `D-1` — business date
  `D-1`, иначе `D`. Поэтому бронь в 02:45 после смены 16:00→02:00 относится к
  предыдущему дню (§5.1).
- **Adjacent shifts** — смены соседних business dates не пересекаются (§5.4):
  изменение weekly проверяет соседние weekday-интервалы, изменение/удаление
  exception — предыдущую и следующую business date по эффективному расписанию.
  Ошибка — `409 SCHEDULE_OVERLAP`; невалидная сетка — `422 SCHEDULE_INVALID`.
- **Advisory lock** — изменения расписания берут exclusive
  `pg_advisory_xact_lock` по `venue_id` (namespace `schedule`); booking
  create/reschedule берёт shared-вариант (§32.3).
- Endpoints (§35): `GET/PUT /api/admin/v1/schedule`,
  `GET /schedule/exceptions`, `PUT/DELETE /schedule/exceptions/{date}`,
  `GET /schedule/business-day` (вычисленное состояние). Tenant — только из
  session.

---

## OpenAPI → TypeScript

Backend — источник типов API. Процесс воспроизводим и generated-код не
редактируется вручную.

```bash
# 1. Экспорт OpenAPI из FastAPI (без запуска сервера и БД)
cd backend && python scripts/export_openapi.py ../frontend/openapi/openapi.json

# 2. Генерация TypeScript
cd frontend && npm run api:generate      # -> src/api/generated/schema.ts

# Проверка рассинхронизации (для CI/локально)
cd frontend && npm run api:check
```

Generated-файл `frontend/src/api/generated/schema.ts` закоммичен. CI-джоба
`openapi` падает, если схема или типы расходятся с backend.

---

## Разработка

### Backend

```bash
cd backend
pip install -e ".[dev]"

ruff check .              # lint
ruff format --check .     # format check
ruff format .             # автоформат
mypy app                  # typecheck
pytest -m "not integration"   # unit-тесты
```

Интеграционные тесты требуют настоящий PostgreSQL:

```bash
TAKEPLACE_TEST_DATABASE_URL="postgresql+asyncpg://takeplace_app:<pass>@127.0.0.1:5432/takeplace_test" \
TAKEPLACE_DB_MIGRATOR_PASSWORD="<pass>" \
pytest -m integration
```

Прибор применяет `alembic upgrade head` к тестовой БД через реальный путь
миграций. Без `TAKEPLACE_TEST_DATABASE_URL` интеграционные тесты пропускаются.

### Frontend

```bash
cd frontend
npm ci
npm run dev            # dev-сервер
npm run lint           # eslint
npm run format:check   # prettier
npm run typecheck      # tsc
npm run test           # vitest
npm run build          # production build (tsc -b && vite build)
```

### Всё сразу

```bash
bash scripts/verify.sh
```

Скрипт повторяет CI: backend lint/format/typecheck/tests, экспорт OpenAPI,
генерацию типов, frontend lint/format/typecheck/tests/build.

---

## Структура проекта

```text
takeplace/
├─ backend/
│  ├─ app/
│  │  ├─ api/            # /api/public/v1, /api/admin/v1 (auth/me/settings/schedule), health
│  │  ├─ domain/         # slug, timezone, schedule/business-date (§5)
│  │  ├─ security/       # Argon2id, session tokens, rate limit, cookies
│  │  ├─ services/       # venues, auth (sessions), schedule, timezone capability
│  │  ├─ queries/        # read-модели
│  │  ├─ db/             # engine, session, base, time-helper, models/
│  │  ├─ cli.py          # операторский CLI (§53)
│  │  ├─ realtime/       # SSE + LISTEN/NOTIFY (Stage 9)
│  │  ├─ integrations/vk/# VK-адаптер (Stage 12)
│  │  ├─ worker/         # outbox worker (Stage 12)
│  │  ├─ logging_config.py  # structured logs + PII redaction
│  │  ├─ middleware.py   # request-id, security headers
│  │  ├─ settings.py     # env-конфиг + fail-fast production
│  │  └─ main.py         # FastAPI app factory
│  ├─ alembic/           # env.py + versions/
│  ├─ scripts/           # export_openapi.py
│  └─ tests/             # unit + integration/
├─ frontend/
│  ├─ src/
│  │  ├─ api/            # typed client, admin API + hooks, generated/schema.ts
│  │  ├─ components/     # RequireAdmin (route guard)
│  │  ├─ pages/          # status / admin login / dashboard / schedule / not-found
│  │  └─ ...
│  ├─ openapi/           # openapi.json (экспорт из backend)
│  └─ scripts/           # generate-api.mjs
├─ infra/postgres/init/  # роли, привилегии, тестовая БД
├─ scripts/              # dev.sh, bootstrap-env.sh, migrate.sh, verify.sh
├─ docker-compose.yml
└─ .github/workflows/ci.yml
```

---

## Health-эндпоинты

| Эндпоинт        | Смысл                                                        |
| --------------- | ------------------------------------------------------------ |
| `/health/live`  | Процесс жив; не трогает БД (Docker liveness)                 |
| `/health/ready` | Приложение видит БД (`SELECT 1`); 503 если нет (Docker readiness) |
| `/health/ops`   | Operational health для мониторинга; всегда 200, может быть `degraded` |

`/health/ops` считается `degraded`, если БД недоступна или rolling timezone
capability check нашёл UTC-offset переход у активного заведения (§47). Он
намеренно не используется как liveness-probe, чтобы проблема с внешним сервисом
(например, VK) не вызывала restart loop.
