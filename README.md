# Takeplace

Онлайн-бронирование столов для заведений. Backend — FastAPI, frontend — React +
TypeScript + Vite, база данных — PostgreSQL.

Этот репозиторий сейчас содержит **Stage 1 — Foundation**: рабочую
инфраструктуру (monorepo, PostgreSQL с ролями и таймаутами, Alembic, health,
security headers, CORS, OpenAPI → TypeScript, тесты, CI). Бизнес-логика
бронирования появится на следующих этапах.

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
- Проверка schema drift: `cd backend && alembic check`.

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
│  │  ├─ api/            # /api/public/v1, /api/admin/v1, health
│  │  ├─ domain/         # чистая бизнес-логика (следующие этапы)
│  │  ├─ services/       # транзакции и блокировки (следующие этапы)
│  │  ├─ queries/        # read-модели
│  │  ├─ db/             # engine, session, base, time-helper
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
│  │  ├─ api/            # typed client + generated/schema.ts
│  │  ├─ pages/          # status / not-found
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

`/health/ops` намеренно не используется как liveness-probe, чтобы проблема с
внешним сервисом (например, VK) не вызывала restart loop.
