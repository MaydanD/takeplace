# Takeplace

Онлайн-бронирование столов для заведений. Backend — FastAPI, frontend — React +
TypeScript + Vite, база данных — PostgreSQL.

Реализованы **Stage 1 — Foundation** (monorepo, PostgreSQL с ролями и таймаутами,
Alembic, health, security headers, CORS, OpenAPI → TypeScript, тесты, CI),
**Stage 2 — Tenant + Auth + CLI**, **Stage 3 — Schedule + Business Day**,
**Stage 4 — Halls + Tables + Read-only Canvas** и **Stage 5 — Booking Core**:
заведения (tenants), admin-аккаунт, server-side sessions, Argon2id, безопасные
cookies, tenant isolation, операторский CLI, недельное расписание и business day,
залы/столы и read-only схема, а также ядро бронирований — bookings со снимком
смены, `table_occupancies` с PostgreSQL exclusion constraint, append-only
`booking_events`, per-venue booking counter, idempotency по HMAC и concurrency
suite.
Двойная бронь запрещена технически: истина — constraint PostgreSQL, не проверка
в приложении. Публичный поток брони и lifecycle (WAIT/OPEN/CLOSE) — следующие
этапы.

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
| `import-layout <venue>` | upsert залов/столов/static elements из валидируемого JSON (Stage 4)   |

`<venue>` — это id или slug. Флаги `create-venue`: `--address`, `--phone`,
`--online-booking` (включить онлайн-бронирование сразу) и `--password` (задать
пароль вручную вместо генерации). `import-layout` принимает `--file <path>` и
`--dry-run` (проверить файл, ничего не записывая).

По умолчанию `create-venue` и `reset-password` генерируют криптографически
случайный пароль и печатают его **один раз**.

`create-venue` валидирует timezone через `zoneinfo` и отклоняет зоны с UTC-offset
переходами в rolling horizon **400 дней**; той же проверкой каждые сутки
сканируются активные заведения и поднимается `/health/ops` (§53, §47).

> `create-venue` создаёт venue, admin account, семь закрытых schedule rows,
> первый зал и строку booking counter (`venue_booking_counters`, §6.7, Stage 5).

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
- `20261003_0004` добавляет `halls` и `tables` с CHECK/UNIQUE/partial-unique и
  tenant-safe composite FK из §6.4–6.5.
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

## Залы и столы (Stage 4)

- **Halls** (`halls`) — tenant-scoped канвасы: `name`, `canvas_width`,
  `canvas_height`, `layout_revision`, `is_bookable`, `archived_at`,
  `static_elements JSONB` (§6.4). Один зал создаётся автоматически при
  `create-venue`.
- **Tables** (`tables`) — `number`, `capacity (>0)`, `is_bookable`,
  `archived_at` и geometry: `x, y, width, height, rotation, shape, z_index`
  (§6.5). Номер уникален среди неархивных столов зала:
  `UNIQUE (hall_id, number) WHERE archived_at IS NULL`.
- **Ownership** — `tables(hall_id, venue_id) → halls(id, venue_id)` composite
  FK: стол не может ссылаться на зал другого venue (последний арбитр, §7, §44).
- **Geometry contract** — shape: `rect | circle`; `x,y >= 0`; `width,height > 0`;
  `0 <= rotation <= 360`. Статические элементы — Pydantic discriminated union
  ровно из типов спеки (`wall | stage | bar | text | zone`), без arbitrary
  HTML/JS/SVG, с лимитами на число элементов и размер JSON (§30.3).
- **`is_bookable` vs archive** — это разные состояния: `is_bookable=false`
  запрещает новые брони, но не архивирует объект; `archived_at` выводит объект
  из эксплуатации и хранится для истории. Архивация зала запрещена, пока в нём
  есть неархивные столы (`409 HALL_ARCHIVE_BLOCKED`, §29.5). `PATCH /tables/{id}`
  меняет только `is_bookable` и **не** увеличивает `layout_revision` (§35).
- **API** (§35): `GET/POST /halls`, `GET/PATCH /halls/{id}`,
  `POST /halls/{id}/archive`, `GET /tables`, `PATCH /tables/{id}`,
  `POST /tables/{id}/archive`. Tenant — только из session.
- **Read-only схема + list view** в админке (`/admin/halls`): SVG-отрисовка
  залов и статических элементов, столы по координатам/размерам/форме,
  bookable/неактивные различаются цветом, плюс табличный список с hall/номер/
  capacity/bookable/архив. Без drag-and-drop и редактора (Stage 10).
- **JSON/CLI import** (`import-layout`) — детерминированный контракт: файл со
  списком `halls`, у каждого `name`, `canvas_width`, `canvas_height`,
  `static_elements`, `tables` (со всеми geometry-полями). Зал матчится по
  `name`, стол — по `number`; ничего не удаляется, повторный запуск идемпотентен.
  Невалидный payload отклоняется до записи (транзакция не оставляет частей).
  Пример: `examples/bar-layout.json`.

---

## Брони — ядро (Stage 5)

- **`bookings`** (§6.6) — бронь с **снимком смены** (`business_date`,
  `shift_starts_at`, `shift_ends_at`) и плановым интервалом `[starts_at,
  ends_at)`, party size, источником (`ONLINE | PHONE | VK | WALK_IN | OTHER`),
  статусом (`NEW | WAITING | OPEN | CLOSED | CANCELED`) и HMAC-полями
  идемпотентности. `create-venue` создаёт строку счётчика; номер human-readable
  `number` монотонно растёт на venue (§6.7).
- **`table_occupancies`** (§6.8) — эффективные интервалы резервирования
  (`BOOKING`/`BLOCK`). **Двойная бронь невозможна на уровне БД**: exclusion
  constraint `occupancy_no_overlap` (`btree_gist` +
  `EXCLUDE USING gist (table_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&) WHERE is_active`).
  Интервалы полуоткрытые, поэтому `20:00–22:00` и `22:00–00:00` совместимы, а
  `20:00–22:00` и `21:55–23:00` — нет. Python-проверка нужна только для
  красивого `409 BOOKING_CONFLICT`; истина — constraint (§12).
- **`booking_events`** (§6.10) — append-only история, канонический порядок по
  `id`, `ON DELETE SET NULL (admin_session_id)` сохраняет историю и tenant. Без
  PII/телефона/IP/comment в payload.
- **Идемпотентность** (§18.2/§19) — `POST /bookings` требует `Idempotency-Key`
  (UUID); хранится `admin_idempotency_key` + `admin_request_hmac`
  (HMAC-SHA-256 канонического payload на `IDEMPOTENCY_HMAC_KEY`, не bare hash).
  Lookup ключа — **до** mutable-валидаций: потерянный и повторённый ответ
  возвращает ту же бронь (`200`) даже если расписание/состояние изменились;
  тот же ключ с другим payload — `409 IDEMPOTENCY_KEY_REUSED`. Гонка
  double-click закрыта partial unique `(venue_id, admin_idempotency_key)`.
- **Порядок блокировок и время** (§32) — venue read без row lock → shared
  schedule advisory lock → halls ASC `FOR SHARE` → tables ASC `FOR SHARE` →
  `operation_now = clock_timestamp()` → booking counter → booking → occupancies
  → event. `23P01 → 409 BOOKING_CONFLICT`, `23505` по ключу → replay,
  `40P01/40001` → bounded retry, `55P03` → retry/503 (§32.5).
- **Capacity** (§14) — `party_size <= SUM(capacity)` по всем сегментам; проверка
  на согласованном (залоченном) состоянии. Уменьшение capacity не может сломать
  существующую будущую бронь (`409 CAPACITY_CHANGE_BLOCKED`, §29.4).
- **Cancel / reschedule** — `POST /bookings/{id}/cancel` деактивирует
  occupancies (никакой `CANCELED` + active occupancy); `POST
  /bookings/{id}/change-time` переносит NEW/WAITING на новый интервал и
  переписывает snapshot. Обе операции используют `expected_version`
  (`409 BOOKING_STALE`, §33) и единый `truncate_segment_at` (§15.1).
- **Archive guard** (§29.3) — стол нельзя архивировать, пока у него есть
  будущая active BOOKING occupancy; archive берёт table `FOR UPDATE` и
  сериализуется с booking create.
- **Schedule change guard** (§5.5) — изменение расписания не двигает
  существующие брони: если будущая бронь выпадает из нового графика, backend
  возвращает `409 SCHEDULE_CHANGE_REQUIRES_CONFIRMATION` со списком таких
  броней; применение требует `?confirm=true`.
- **API** (§35, booking-core subset): `POST /bookings`, `GET /bookings`
  (cursor/limit + фильтры `business_date`, `status`, `source`, `table_id`,
  `phone`), `GET /bookings/unresolved`, `GET /bookings/{id}`,
  `GET /bookings/{id}/history`, `POST /bookings/{id}/cancel`,
  `POST /bookings/{id}/change-time`. Tenant — только из session.
- **Тесты** — unit/property (`tests/test_booking_domain.py`, Hypothesis для
  segment/capacity algebra), integration (`tests/integration/test_bookings_api.py`)
  и **PostgreSQL concurrency suite** (`tests/integration/test_bookings_concurrency.py`,
  §54): same-slot, idempotent duplicate, adjacent/overlap, multi-table
  atomicity, cross-table lock order, archive/create, capacity/create,
  schedule/create, cancel/time-change и проверка, что exclusion constraint —
  последний арбитр. Этап закрыт только при зелёном concurrency suite.

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
│  │  ├─ api/            # /api/public/v1, /api/admin/v1 (auth/me/settings/schedule/halls), health
│  │  ├─ domain/         # slug, timezone, schedule/business-date (§5), layout (§6)
│  │  ├─ security/       # Argon2id, session tokens, rate limit, cookies
│  │  ├─ services/       # venues, auth (sessions), schedule, halls/tables/import, tz capability
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
│  │  ├─ pages/          # status / login / dashboard / schedule / halls / not-found
│  │  └─ ...
│  ├─ openapi/           # openapi.json (экспорт из backend)
│  └─ scripts/           # generate-api.mjs
├─ examples/             # пример layout JSON для импорта схемы (Stage 4)
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
