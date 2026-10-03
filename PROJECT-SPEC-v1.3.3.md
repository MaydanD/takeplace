# Takeplace — PROJECT-SPEC.md

**Версия:** 1.3.3  
**Дата фиксации:** 2026-10-03  
**Статус:** архитектура Takeplace v1 зафиксирована для реализации.

**v1.3.3:** frozen erratum перед Stage 1: расписание и исключения привязаны к 5-минутной сетке; WALK_IN считает минимум по grid-aligned концу смены; abuse-alert оставлен operational-only без отдельного VK outbox type; индекс events приведён к каноническому `(booking_id, id)`; добавлен отдельный table bookability endpoint; advisory key construction зафиксирован; legal gate явно требует решить уведомление оператора ПД и договор поручения обработки; fixtures обязаны соблюдать admin idempotency invariant. После v1.3.3 архитектурная спецификация заморожена до дефекта, найденного реализацией/тестами.

**v1.3.2:** финальный erratum перед реализацией: зафиксирован PostgreSQL 15+, исправлены composite FK `ON DELETE SET NULL`, legal gates перенесены до первых реальных ПД, уточнена граница WALK_IN <45 минут, исправлен TTL срочных VK-уведомлений, layout-save сериализован на venue для cross-hall capacity, DST-проверка стала rolling; дешёвые DB/API/observability/retention уточнения разложены по соответствующим Stage.

**v1.3.1:** erratum перед реализацией: синхронизирована физическая схема outbox/VK/schedule с текстом; WALK_IN освобождён от 45-минутного минимума у конца смены; телефон сделан необязательным для VK/WALK_IN; добавлены pilot-метрики plan-overrun, явный legal gate по роли оператора ПД и сверка ONLINE-броней с VK при restore. Добавлены дешёвые read-cache/DST/runbook уточнения без расширения release-scope.

**v1.3:** усилены блокировки capacity-sensitive мутаций, HMAC/idempotency и явная анонимизация, responsive kill switch, short-lived abuse fingerprint, TTL/SKIPPED outbox, recovery secrets, WALK_IN без обязательного телефона, calendar-month clamp, session/public-GET limits; layout bookability отделена от draft. Release-scope упрощён: post-end reseat, продление просроченного OPEN и bulk-cancel отложены. Editor перенесён после первой рабочей booking-вертикали.

**v1.2:** уточнены порядок public idempotency, снимок границ смены в брони, late/walk-in сценарии, admin idempotency, lifecycle DEAD outbox, anti-abuse, backup/retention semantics, DB CHECK/FK, hall archive, admin filters/settings, timezone UI, RTO и порядок production stages.

**v1.1:** уточнены конкурентность live-операций, server-side время после locks, idempotency публичного POST, WAITING, outbox monitoring, анонимизация ПД и API-контракты.

---

## 0. Назначение документа

Этот документ — источник истины для Takeplace v1.

Если код, задача агента, тест или будущая реализация противоречат этой спецификации, нельзя молча менять поведение «для удобства». Сначала меняется `PROJECT-SPEC.md`, затем реализация.

Главные цели v1:

- онлайн-бронирование конкретных столов;
- отсутствие двойной брони даже при конкурентных запросах;
- ночные смены, переходящие через полночь;
- рабочая админка заведения;
- ручное создание и изменение броней;
- состояния `NEW / WAITING / OPEN / CLOSED / CANCELED`;
- несколько столов в одной брони;
- пересадка гостей;
- временные блокировки столов;
- редактор схемы зала;
- realtime между несколькими открытыми админками;
- уведомление рабочей VK-беседы о новой онлайн-брони;
- multi-tenant архитектура;
- простой production deploy на одном VPS.

---

# 1. Продуктовая модель

## 1.1. Venue = tenant

Один `Venue` — один независимый tenant.

У каждого заведения свои:

- общий админский аккаунт;
- публичная страница;
- залы;
- столы;
- схема;
- расписание;
- исключения расписания;
- брони;
- настройки;
- VK-интеграция.

Если одному владельцу принадлежат два заведения, используются два отдельных аккаунта и два отдельных `Venue`.

В v1 **нет**:

- сущности организации/сети над заведениями;
- переключателя заведений в одном кабинете;
- отдельных аккаунтов сотрудников;
- ролей сотрудников;
- разных уровней прав.

Один общий аккаунт заведения может одновременно использоваться на нескольких устройствах.

## 1.2. Гость

Гость:

- не регистрируется;
- не авторизуется;
- не подтверждает телефон;
- выбирает конкретный стол;
- выбирает дату;
- выбирает начало и конец;
- вводит имя;
- вводит телефон;
- вводит количество гостей;
- может оставить необязательный комментарий;
- принимает согласие на обработку персональных данных;
- после создания сам не может изменить или отменить бронь.

Личного кабинета гостя в v1 нет.

## 1.3. Администратор

Администратор может:

- создать бронь вручную;
- указать источник брони;
- изменить данные активной брони;
- перевести бронь в `WAITING`;
- открыть бронь по факту прихода гостей;
- отменить ошибочное открытие при соблюдении условий `UNDO_OPEN`;
- закрыть визит;
- отменить неоткрытую бронь;
- изменить время;
- перенести неоткрытую бронь;
- добавить стол;
- убрать стол;
- заменить стол;
- пересадить гостей;
- временно заблокировать стол;
- включить/выключить зал;
- включить/выключить стол;
- редактировать схему зала;
- менять расписание;
- менять исключения расписания;
- мгновенно отключить новые онлайн-брони.

## 1.4. Не входит в v1

В v1 нет:

- оплат;
- депозитов;
- SMS;
- email-уведомлений;
- уведомлений гостям в VK;
- Telegram;
- CRM гостей;
- отдельной таблицы `guests`;
- бонусов/лояльности;
- no-show как отдельного статуса;
- admin override бизнес-правил;
- отдельных сотрудников/ролей;
- Redis;
- Celery;
- RabbitMQ;
- Kafka;
- микросервисов;
- Kubernetes;
- нескольких смен/разрывов внутри одного `business_date` (например 12:00–16:00 и 18:00–02:00);
- гарантированной поддержки DST-переходов. Production v1 рассчитан на timezone без DST; DST требует отдельной спецификации.

---

# 2. Фиксированные продуктовые правила v1

В v1 это константы продукта, а не настройки конкретного venue:

```text
SLOT_MINUTES = 5
MIN_BOOKING_MINUTES = 45
BOOKING_HORIZON_MONTHS = 2
```

Добавление календарных месяцев использует `add_months_clamped(date, n)`: если такого дня в целевом месяце нет, берётся последний существующий день месяца. Например, `2026-12-31 + 2 months = 2027-02-28`, а в високосном году — 29 февраля. Максимальный `business_date` включителен.

Дополнительно:

- максимальная длительность отдельно не задаётся;
- бронь не может выходить за пределы одной рабочей смены;
- две брони могут стоять вплотную;
- пользователь может забронировать большой стол на одного человека;
- пользователь выбирает ровно один стол;
- дополнительные столы может добавить только администратор;
- телефон не подтверждается;
- онлайн-бронь сразу является действующей;
- ручного подтверждения заявки сотрудником нет;
- `MIN_BOOKING_MINUTES=45` применяется к ONLINE и обычным manual-бронированиям; атомарный `WALK_IN create+open` имеет отдельное исключение у конца текущей смены (§19.1).

---

# 3. Технологический стек

## 3.1. Frontend

- React
- TypeScript
- Vite
- React Router
- TanStack Query
- Zustand — только для локального состояния редактора
- React Hook Form
- Zod
- Tailwind CSS
- Radix/shadcn primitives
- Konva + react-konva
- `openapi-typescript` для генерации API-типов из OpenAPI FastAPI

Transport-типы HTTP API генерируются из OpenAPI и не дублируются вручную в Zod. Zod используется только для UI/form validation и локальных frontend-моделей.

## 3.2. Backend

- Python
- FastAPI
- Pydantic 2
- SQLAlchemy 2 async
- asyncpg
- Alembic

Рекомендуемая структура:

```text
backend/app/
├─ api/
│  ├─ public/
│  └─ admin/
├─ domain/
├─ services/
├─ queries/
├─ db/
├─ realtime/
├─ integrations/
│  └─ vk/
├─ worker/
├─ settings.py
└─ main.py
```

Отдельного repository-layer в v1 нет.

`domain/` содержит чистую бизнес-логику:

- разрешение смен;
- `business_date`;
- state machine;
- правила сегментов;
- расчёт capacity;
- availability overlay;
- проверки переходов.

`services/`:

- открывает транзакции;
- берёт нужные блокировки;
- вызывает domain logic;
- применяет изменения через SQLAlchemy.

## 3.3. Database

- PostgreSQL **15+** — минимальная поддерживаемая major-версия production/CI;
- расширение `btree_gist`

Критичные интеграционные тесты выполняются на настоящем PostgreSQL той же major-ветки не ниже 15. Минимум 15 нужен в том числе для column-list semantics `ON DELETE SET NULL (column)` у composite FK, чтобы tenant-column не занулялся вместе с nullable ссылкой на session.

SQLite не используется для проверки exclusion constraint, блокировок и конкурентных сценариев.

## 3.4. Realtime

- Server-Sent Events;
- PostgreSQL `LISTEN / NOTIFY`.

WebSocket не нужен: все мутации идут HTTP-запросами, realtime используется в основном как сигнал другим клиентам сделать refetch.

## 3.5. Background jobs

DB-backed outbox worker.

Без Redis/Celery.

## 3.6. Production

Docker Compose:

```text
caddy
api
worker
postgres
```

Frontend собирается в статику и отдаётся Caddy.

Основная production БД и backend размещаются на VPS в РФ.

---

# 4. Время и timezone

## 4.1. Timestamps

Все абсолютные моменты времени в PostgreSQL:

```text
timestamptz
```

Timezone заведения хранится как IANA timezone, например:

```text
Europe/Moscow
```

Для timezone-логики backend использует `zoneinfo`.

API передаёт абсолютные даты/время как ISO-8601 со смещением:

```text
2026-10-03T02:45:00+03:00
```

Нельзя передавать в API неоднозначное `02:45` без даты.

## 4.2. Единое server-side время операции

Клиентское время не является источником истины.

`transaction_timestamp()` не используется как бизнес-время мутации: он фиксируется на старте transaction и может устареть, пока запрос ждёт row lock.

Сначала операция получает все бизнес-критичные row locks. После этого backend один раз выполняет:

```sql
SELECT clock_timestamp();
```

Полученное значение становится `operation_now` для всей мутации.

Это исключает ситуацию, когда позднее реально применённая операция получает более ранний timestamp только потому, что transaction началась раньше и ждала lock.

`operation_now` используется для OPEN, CLOSE, CANCEL, WAITING, RESEAT, add/remove/replace table, раннего открытия, изменения времени OPEN, проверки прошлого времени и динамической availability.

Внешние HTTP-вызовы внутри такой transaction запрещены.

---

# 5. Business day и смены

## 5.1. Business date

Смена принадлежит дате её начала.

Пример:

```text
business_date = 2026-10-02
смена: 2026-10-02 16:00 → 2026-10-03 04:00
```

Бронь `2026-10-03 02:45–04:00` отображается в книге резервов за **2 октября**.

## 5.2. Недельное расписание

На один `business_date` может быть максимум одна непрерывная смена.

Разрывные смены внутри одного business date в v1 сознательно не моделируются.

```text
weekly_schedules
----------------
id bigint PK
venue_id bigint NOT NULL
weekday smallint NOT NULL
is_open boolean NOT NULL
open_time time NULL
close_time time NULL
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
UNIQUE (venue_id, weekday)
```

DB CHECK фиксирует согласованность строки: `weekday BETWEEN 0 AND 6`; для `is_open=true` оба времени обязательны, `open_time <> close_time`, секунды/микросекунды равны нулю и минуты кратны 5; для `is_open=false` оба времени `NULL`. API/domain validation применяет те же правила до записи. Таким образом границы любой resolved shift всегда лежат на 5-минутной сетке бронирований.

Если `close_time < open_time`, смена заканчивается на следующий календарный день.

`16:00 → 02:00` означает `D 16:00 → D+1 02:00`.

`open_time == close_time` запрещено.

Отдельное поле `closes_next_day` не хранится: иначе появились бы два источника истины.

Production v1 поддерживает venue в IANA timezone без DST-переходов. Ambiguous/nonexistent local time при DST не является поддержанным сценарием v1.

## 5.3. Исключения

```text
schedule_exceptions
-------------------
id bigint PK
venue_id bigint NOT NULL
date date NOT NULL
is_closed boolean NOT NULL
open_time time NULL
close_time time NULL
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
UNIQUE (venue_id, date)
```

DB CHECK фиксирует согласованность строки: для `is_closed=true` оба времени `NULL`; для `is_closed=false` оба времени обязательны, `open_time <> close_time`, секунды/микросекунды равны нулю и минуты кратны 5. API/domain validation применяет те же grid rules.

Исключение полностью переопределяет недельное расписание конкретного `business_date`.

## 5.4. Пересечение соседних смен

Смены соседних business dates не могут пересекаться.

Нельзя сохранить:

```text
Пятница 16:00 → Суббота 04:00
Суббота 03:00 → Воскресенье 02:00
```

При изменении недельного расписания backend проверяет соседние weekday-интервалы.

При изменении исключения backend проверяет предыдущий и следующий business date.

## 5.5. Изменение расписания при существующих бронях

Изменение расписания не удаляет, не отменяет и не двигает существующие брони.

При создании/переносе брони в ней фиксируются `shift_starts_at` и `shift_ends_at` — снимок границ смены, по которой бронь была валидирована. Последующие изменения weekly schedule или exception этот снимок у существующей брони не меняют.

Если после изменения будущая бронь оказывается вне нового графика, backend возвращает предупреждение со списком/числом таких броней, а UI требует явного подтверждения изменения расписания.

Новые брони и перенос существующей брони на другую смену используют актуальное расписание и записывают новый snapshot. Lifecycle уже созданной брони, overdue-логика и её граница смены опираются на snapshot самой брони, а не на повторный resolve изменяемого расписания.

## 5.6. current_business_date

Для локальной календарной даты `D`:

1. резолвится смена `D - 1`;
2. резолвится смена `D`;
3. если `operation_now` находится в смене `D - 1`, current business date = `D - 1`;
4. иначе если находится в смене `D`, current business date = `D`;
5. иначе current business date = `D`.

Логика существует в одном доменном месте и покрыта тестами.

---

# 6. Основные сущности БД

## 6.1. venues

```text
venues
------
id bigint PK
slug text UNIQUE NOT NULL
name text NOT NULL
address text NULL
phone text NULL
timezone text NOT NULL
is_active boolean NOT NULL default true
online_booking_enabled boolean NOT NULL default false
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
```

`slug`: lowercase, только `a-z`, `0-9`, `-`, длина 2–40, глобально уникален.

Зарезервированы как минимум:

```text
api
admin
assets
static
login
logout
health
robots.txt
favicon.ico
privacy
terms
booking
bookings
settings
```

`is_active=false` отключает venue целиком.

`online_booking_enabled=false` отключает только создание новых ONLINE-бронирований. Админка остаётся рабочей. Для нового venue значение по умолчанию `false`; online включается явно после настройки и smoke-test. Семантика аварийного выключателя описана в §32.2.

## 6.2. admin_accounts

Ровно один общий аккаунт на venue:

```text
admin_accounts
--------------
id bigint PK
venue_id bigint UNIQUE NOT NULL
login text UNIQUE NOT NULL
password_hash text NOT NULL
is_active boolean NOT NULL default true
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
```

`login` глобально уникален, потому что login screen не требует предварительного выбора venue. CLI должен запросить или сгенерировать уникальный login, например `dragon-admin`.

## 6.3. admin_sessions

```text
admin_sessions
--------------
id bigint PK
venue_id bigint NOT NULL
token_hash text UNIQUE NOT NULL
created_at timestamptz NOT NULL
expires_at timestamptz NOT NULL
last_seen_at timestamptz NOT NULL
```

В браузере хранится raw session token. В БД хранится только его hash.

`last_seen_at` обновляется не чаще одного раза примерно в 60 секунд на session, а не на каждый запрос.

Абсолютный TTL admin-session в v1 — 30 дней от `created_at`; sliding-продление срока жизни не используется. Просроченная session удаляется/отклоняется независимо от `last_seen_at`. Значение может быть сокращено deploy config, но не увеличено без отдельного решения.

## 6.4. halls

```text
halls
-----
id bigint PK
venue_id bigint NOT NULL
name text NOT NULL
is_bookable boolean NOT NULL default true
canvas_width integer NOT NULL
canvas_height integer NOT NULL
layout_revision bigint NOT NULL default 1
static_elements jsonb NOT NULL default '[]'
archived_at timestamptz NULL
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
```

`is_bookable=false` запрещает новые брони, но не уничтожает существующие.

Архивный hall не показывается в обычном public/admin UI и не участвует в новых бронированиях. Архивация hall описана в §29.

## 6.5. tables

```text
tables
------
id bigint PK
venue_id bigint NOT NULL
hall_id bigint NOT NULL
number text NOT NULL
capacity integer NOT NULL CHECK (capacity > 0)
is_bookable boolean NOT NULL default true
archived_at timestamptz NULL
x numeric NOT NULL
y numeric NOT NULL
width numeric NOT NULL
height numeric NOT NULL
rotation numeric NOT NULL default 0
shape text NOT NULL
z_index integer NOT NULL default 0
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
```

Номер стола уникален среди неархивных столов внутри одного зала:

```text
UNIQUE (hall_id, number) WHERE archived_at IS NULL
```

Архивный стол остаётся в БД для истории и FK.

## 6.6. bookings

```text
bookings
--------
id bigint PK
venue_id bigint NOT NULL
number bigint NOT NULL
business_date date NOT NULL
shift_starts_at timestamptz NOT NULL
shift_ends_at timestamptz NOT NULL
starts_at timestamptz NOT NULL
ends_at timestamptz NOT NULL
guest_name text NULL
guest_phone_raw text NULL
guest_phone_normalized text NULL
party_size integer NOT NULL CHECK (party_size > 0)
guest_comment text NULL

public_idempotency_key uuid NULL
public_request_hmac text NULL
admin_idempotency_key uuid NULL
admin_request_hmac text NULL

request_ip_hmac text NULL
request_ip_hmac_expires_at timestamptz NULL

source text NOT NULL
status text NOT NULL
waiting_at timestamptz NULL
opened_at timestamptz NULL
closed_at timestamptz NULL
canceled_at timestamptz NULL
cancellation_reason text NULL
cancellation_note text NULL
privacy_policy_version text NULL
privacy_accepted_at timestamptz NULL
anonymized_at timestamptz NULL

version bigint NOT NULL default 1
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
UNIQUE (venue_id, number)
```

Рекомендуемые лимиты:

```text
guest_name <= 100
guest_phone_raw <= 50
guest_comment <= 1000
cancellation_note <= 500
```

До анонимизации `guest_name` обязателен для всех source. `guest_phone_raw` обязателен для `ONLINE | PHONE | OTHER`, но для `VK | WALK_IN` может быть `NULL`: сотрудник не должен вводить фиктивный номер только ради схемы. Для `VK` номер часто отсутствует в самом диалоге и не должен подменяться заглушкой.

DB-инвариант до анонимизации:

```text
anonymized_at IS NOT NULL
OR
(
  guest_name IS NOT NULL
  AND (source IN ('WALK_IN', 'VK') OR guest_phone_raw IS NOT NULL)
)
```

После разрешённой retention policy анонимизации чувствительные/идентифицирующие поля очищаются в `NULL`, а `anonymized_at` фиксирует момент операции. Плейсхолдеры не используются. Полный список очистки приведён в §42.

Для create-сервис явно записывает `bookings.created_at = operation_now`; бизнес-порядок мутаций никогда не выводится из `created_at/updated_at`. Для истории порядок задаётся `booking_events.id` (§6.10).

`shift_starts_at < shift_ends_at`, а `[starts_at, ends_at)` должен находиться внутри `[shift_starts_at, shift_ends_at)`. Snapshot меняется только при операции, которая валидирует бронь против другой смены/даты.

Для неанонимизированной ONLINE-брони `public_idempotency_key` и `public_request_hmac` обязательны, а admin-поля NULL. Для manual booking public-поля NULL. `admin_idempotency_key` и `admin_request_hmac` всегда либо оба NULL, либо оба NOT NULL; admin API create требует их через `Idempotency-Key`, поэтому штатные ручные брони имеют оба поля. При анонимизации idempotency key/HMAC очищаются вместе; после этого исторический replay старого key больше не гарантируется.

Следствие DB-инварианта: любые seed/import/test fixtures, которые создают non-ONLINE booking напрямую мимо HTTP API, тоже обязаны выдавать валидную пару `admin_idempotency_key/admin_request_hmac` через общий factory/helper. Ослаблять CHECK ради фикстур запрещено.

## 6.7. venue_booking_counters

```text
venue_booking_counters
----------------------
venue_id bigint PK
last_number bigint NOT NULL
```

В транзакции создания:

```sql
UPDATE venue_booking_counters
SET last_number = last_number + 1
WHERE venue_id = :venue_id
RETURNING last_number;
```

Публичного `GET booking by number` нет.

Для ONLINE создаётся partial unique:

```text
UNIQUE (venue_id, public_idempotency_key)
WHERE public_idempotency_key IS NOT NULL
```

Он является DB-защитой idempotency публичного POST.

## 6.8. table_occupancies

`table_occupancies` описывает эффективные интервалы резервирования ресурса.

```text
table_occupancies
-----------------
id bigint PK
venue_id bigint NOT NULL
table_id bigint NOT NULL
kind text NOT NULL
booking_id bigint NULL
note text NULL
starts_at timestamptz NOT NULL
ends_at timestamptz NOT NULL
is_active boolean NOT NULL default true
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
```

`kind`: `BOOKING | BLOCK`.

Правила:

```text
BOOKING → booking_id IS NOT NULL
BLOCK   → booking_id IS NULL
ends_at > starts_at
```

`is_active` означает: строка является частью текущей эффективной модели резервирования. Это **не** означает, что интервал содержит `now`.

Прошедшие effective-сегменты могут оставаться `is_active=true`, пока бронь не отменена/перенесена.

## 6.9. booking_live_tables

`booking_live_tables` хранит только фактическое размещение гостей для `OPEN`-брони.

```text
booking_live_tables
-------------------
id bigint PK
venue_id bigint NOT NULL
booking_id bigint NOT NULL
business_date date NOT NULL
table_id bigint NOT NULL
live_since timestamptz NOT NULL
```

Внутри одного business day физический стол не может быть live у двух броней:

```text
UNIQUE (table_id, business_date)
```

Это намеренно не глобальный `UNIQUE(table_id)`: забытая вчерашняя OPEN после конца своей смены не должна блокировать следующий business day.

Для одной брони может быть несколько live-столов.

Чтобы `business_date` live-row не мог расходиться с бронью, на `bookings` существует `UNIQUE (id, venue_id, business_date)`, а `booking_live_tables` использует composite FK `(booking_id, venue_id, business_date) → bookings(id, venue_id, business_date)`.

При `CLOSED` live-строки этой брони удаляются.

История пересадок хранится в `booking_events`.

## 6.10. booking_events

```text
booking_events
--------------
id bigint PK
venue_id bigint NOT NULL
booking_id bigint NOT NULL
event_type text NOT NULL
actor_type text NOT NULL
admin_session_id bigint NULL
payload jsonb NOT NULL default '{}'
created_at timestamptz NOT NULL
```

`actor_type`: `PUBLIC | ADMIN | SYSTEM`.

Если `admin_session_id` заполнен, `(admin_session_id, venue_id) → admin_sessions(id, venue_id)` — composite FK с `ON DELETE SET NULL (admin_session_id)`. `venue_id` остаётся NOT NULL; удаление/cleanup session не должно удалять tenant identity события. Это требует PostgreSQL 15+.

Примеры event type:

```text
BOOKING_CREATED
BOOKING_EDITED
WAITING_SET
BOOKING_OPENED
OPEN_UNDONE
BOOKING_CLOSED
BOOKING_CANCELED
TIME_CHANGED
TABLE_ADDED
TABLE_REMOVED
TABLE_REPLACED
BOOKING_RESCHEDULED
```

История append-only. Канонический порядок событий одной booking определяется по `booking_events.id`, а не по `created_at`. Для бизнес-мутаций `created_at` явно записывается как соответствующий `operation_now`; timestamp нужен для отображения/аудита, но не является арбитром порядка при одинаковых/близких временах.

В `booking_events.payload` запрещено хранить полный телефон, raw IP, guest comment, request payload и другие PII, кроме случая, отдельно добавленного в спецификацию с retention/redaction rules. Для release v1 таких исключений нет.

## 6.11. notification_outbox

```text
notification_outbox
-------------------
id bigint PK
venue_id bigint NOT NULL
type text NOT NULL
dedup_key text NOT NULL
payload jsonb NOT NULL
status text NOT NULL
attempts integer NOT NULL default 0
next_attempt_at timestamptz NOT NULL
locked_until timestamptz NULL
provider_dedup_id text NULL
last_error text NULL
expires_at timestamptz NOT NULL
skipped_at timestamptz NULL
skip_reason text NULL
acknowledged_at timestamptz NULL
acknowledged_by_session_id bigint NULL
created_at timestamptz NOT NULL
sent_at timestamptz NULL
UNIQUE (type, dedup_key)
```

Статусы:

```text
PENDING
PROCESSING
RETRY
SENT
DEAD
SKIPPED
```

`SKIPPED` означает, что уведомление сознательно не отправлялось, потому что к моменту обработки оно потеряло операционный смысл. Минимальный стабильный набор `skip_reason`: `EXPIRED | BOOKING_INACTIVE | BOOKING_ANONYMIZED`. Сам факт наступления `booking.starts_at` **не** является причиной skip: срочная бронь в статусе `NEW/WAITING` ещё может быть полезна сотрудникам в пределах позднего grace-window (§38.2–38.3). Для `SKIPPED` обязательны `skipped_at` и `skip_reason`; для остальных статусов они `NULL`. `SKIPPED` не является ошибкой и не поднимает operational alert.

`DEAD` означает, что автоматические retry исчерпаны. DEAD остаётся в истории до retention cleanup, но operational alert учитывает только неacknowledged DEAD. Admin может вручную отправить задачу на retry либо подтвердить (`acknowledge`) известную проблему; это не удаляет строку. `acknowledged_by_session_id` вместе с `venue_id` образует composite FK на `admin_sessions(id, venue_id)` и использует **`ON DELETE SET NULL (acknowledged_by_session_id)`**. Нельзя применять обычный `ON DELETE SET NULL` без списка колонок: тогда PostgreSQL попытался бы занулить и `venue_id NOT NULL`.

## 6.12. venue_vk_integrations

Каноническая схема конфигурации VK-интеграции:

```text
venue_vk_integrations
---------------------
venue_id bigint PK
enabled boolean NOT NULL default false
community_id bigint NULL
peer_id bigint NULL
encrypted_access_token text NULL
encryption_key_version integer NULL
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
```

`venue_id` одновременно является PK и FK `venue_vk_integrations(venue_id) → venues(id)` с `ON DELETE CASCADE`, поэтому у venue не может быть больше одной VK-конфигурации и cross-tenant ссылка невозможна. DB CHECK: `NOT enabled OR (community_id IS NOT NULL AND peer_id IS NOT NULL AND encrypted_access_token IS NOT NULL AND encryption_key_version IS NOT NULL)`. Application validation дополнительно проверяет формат/диапазоны; токен никогда не возвращается frontend.

---

# 7. Tenant isolation

Tenant isolation обязательна и не должна зависеть только от frontend.

## 7.1. Admin API

`venue_id` для admin endpoint определяется только из session.

Клиент не может выбрать другой venue параметром.

Объект другого venue возвращает `404`, не раскрывая факт его существования.

## 7.2. Composite foreign keys

На критичных сущностях добавляются `UNIQUE (id, venue_id)` и composite FK, например:

```text
(table_id, venue_id)   → tables(id, venue_id)
(booking_id, venue_id) → bookings(id, venue_id)
(hall_id, venue_id)    → halls(id, venue_id)
```

RLS PostgreSQL в v1 не обязателен.

## 7.3. Тесты isolation

Для каждого admin endpoint:

```text
venue A пытается читать/менять объект venue B → 404
```

---

# 8. Источники брони

```text
ONLINE
PHONE
VK
WALK_IN
OTHER
```

Публичная бронь всегда `ONLINE`.

`ONLINE` зарезервирован за публичным endpoint и не может быть выбран в обычном admin create. При ручном создании source обязателен и выбирается из `PHONE | VK | WALK_IN | OTHER`.

---

# 9. Статусы и state machine

Допустимые статусы:

```text
NEW
WAITING
OPEN
CLOSED
CANCELED
```

`NEW` — бронь создана и действует, но сотрудник её ещё не обработал. Это не «ожидает подтверждения».

`WAITING` — время пришло, гости пока не пришли.

`OPEN` — гости фактически находятся за live-столами.

`CLOSED` — визит завершён.

`CANCELED` — неоткрытая бронь отменена.

No-show — причина CANCELED, не отдельный статус.

Разрешено:

```text
NEW → WAITING
NEW → OPEN
NEW → CANCELED
WAITING → OPEN
WAITING → CANCELED
OPEN → CLOSED
```

Отдельная команда `UNDO_OPEN` может вернуть ошибочно открытую бронь в предыдущий `NEW` или `WAITING`.

Запрещено:

```text
OPEN → CANCELED
CLOSED → *
CANCELED → *
```

---

# 10. Статус ↔ timestamp invariants

## NEW

```text
waiting_at IS NULL
opened_at IS NULL
closed_at IS NULL
canceled_at IS NULL
```

## WAITING

```text
waiting_at IS NOT NULL
opened_at IS NULL
closed_at IS NULL
canceled_at IS NULL
```

## OPEN

```text
opened_at IS NOT NULL
closed_at IS NULL
canceled_at IS NULL
```

`waiting_at` может быть NULL или NOT NULL.

## CLOSED

```text
opened_at IS NOT NULL
closed_at IS NOT NULL
canceled_at IS NULL
```

## CANCELED

```text
opened_at IS NULL
closed_at IS NULL
canceled_at IS NOT NULL
cancellation_reason IS NOT NULL
```

---

# 11. Причины отмены

```text
GUEST_CANCELED
NO_SHOW
DUPLICATE
UNREACHABLE
RESCHEDULED
GUEST_LATE
CREATION_ERROR
TERMS_REFUSED
INVALID_DATA
MOVED_ELSEWHERE
NO_TABLES
VENUE_CLOSED
ENTRY_REFUSED
OTHER
```

Для `OTHER` можно заполнить `cancellation_note`.

Бронь при отмене не удаляется.

---

# 12. PostgreSQL exclusion constraint

Все активные BOOKING/BLOCK интервалы одного стола не пересекаются.

Используется полуоткрытый диапазон `[start, end)`.

Поэтому `20:00–22:00` и `22:00–00:00` совместимы.

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE table_occupancies
ADD CONSTRAINT occupancy_no_overlap
EXCLUDE USING gist (
    table_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
)
WHERE (is_active);
```

Constraint защищает `BOOKING↔BOOKING`, `BOOKING↔BLOCK`, `BLOCK↔BLOCK`.

Предварительная проверка в Python нужна для UX. Финальная гарантия — PostgreSQL.

Constraint в v1.2 остаётся immediate/non-deferrable. Операции обязаны сначала корректно деактивировать/обрезать старые segments и только затем вставлять конфликтующие новые. Это уменьшает число commit-time веток и делает `23P01` локальным к statement, при этом transaction всё равно откатывается целиком.

---

# 13. Эффективные столы брони

Для момента `t` внутри планового интервала:

```text
assigned_tables_at(booking, t)
```

— это active BOOKING occupancies этой брони, для которых:

```text
starts_at <= t < ends_at
```

Для NEW/WAITING и OPEN до планового конца должен существовать хотя бы один назначенный стол на всём необходимом effective-интервале.

Для CLOSED покрытие после `closed_at` не требуется.

Для CANCELED active BOOKING occupancies быть не должно.

---

# 14. Capacity invariant

Для одного стола:

```text
party_size <= capacity
```

Для нескольких:

```text
party_size <= SUM(capacity назначенных столов)
```

При изменении столов, времени или party_size backend проверяет результирующий план на всех границах сегментов.

Для каждого будущего подинтервала, где набор assigned tables постоянен:

```text
SUM(capacity) >= party_size
```

Для OPEN дополнительно:

```text
SUM(capacity live tables) >= party_size
```

Минимальной загрузки нет.

---

# 15. Пяти минутам кратно только плановое время

`booking.starts_at` и `booking.ends_at` кратны 5 минутам.

Фактические события могут происходить в `21:07`, `21:13` и т.д.

Пример корректной пересадки:

```text
booking 20:00–23:00
стол 5: 20:00–21:07
стол 9: 21:07–23:00
```

## 15.1. Единая функция `truncate_segment_at`

Любая операция, которая завершает effective BOOKING-segment по фактическому времени, использует одну доменную функцию:

```text
truncate_segment_at(segment, t)
```

Семантика:

```text
if segment.starts_at >= t:
    segment.is_active = false
elif segment.starts_at < t < segment.ends_at:
    segment.ends_at = t
else:  # segment.ends_at <= t
    segment не меняется
```

Нельзя реализовывать разные варианты этой логики отдельно в `close`, `remove table`, `replace table` и других endpoint. Функция гарантирует, что не возникает `ends_at <= starts_at`, и покрывается unit + property-based тестами.

---

# 16. Публичная availability

Availability — снимок состояния и не гарантирует, что слот останется свободным до POST.

Для снижения дешёвой read-нагрузки production может использовать короткий cache TTL порядка 1–2 секунд на `GET availability` (ключ как минимум venue/business_date/hall и релевантные query params). Кэш не является correctness boundary: POST всегда выполняет полную серверную проверку и DB constraints. После admin mutation инвалидирование может быть best-effort, потому что даже кратковременно stale availability не даёт создать конфликтную бронь.

Публичный endpoint может отдавать:

- shift start/end;
- table id;
- capacity;
- bookability;
- busy intervals;
- block intervals;
- dynamic online unavailability.

Он не отдаёт PII и внутренние данные чужих броней.

---

# 17. Динамическая недоступность из фактического состояния

`table_occupancies` описывает план/эффективное резервирование.

`booking_live_tables` описывает факт.

## 17.1. OPEN вне собственного планового occupancy

Если table сейчас live, но на `operation_now` собственный BOOKING occupancy этой OPEN-брони его не покрывает, online считает стол занятым:

```text
[operation_now, start следующей active occupancy с starts_at > operation_now)
```

Если следующей occupancy в snapshot-смене брони нет:

```text
[operation_now, booking.shift_ends_at)
```

Это покрывает раннее открытие и просроченный OPEN без искусственного DB-буфера.

## 17.2. Просроченный WAITING

Если:

```text
status = WAITING
booking.ends_at < operation_now < booking.shift_ends_at
```

текущие хвостовые столы этой брони считаются online-недоступными до следующей active occupancy после `operation_now`, либо до `booking.shift_ends_at`.

Никакого буфера 30/60 минут нет.

## 17.3. Просроченный NEW

Просроченный `NEW` сам по себе не создаёт динамическую блокировку после `ends_at`.

Он показывается сотруднику как требующий обработки.

## 17.4. После конца смены

Старая `OPEN`/`WAITING` предыдущей смены не блокирует online следующего business day.

Она остаётся в списке `Требует закрытия / обработки`.

---

# 18. Публичное создание брони

Поток:

```text
venue → business date → hall → table → start → end → guest data → consent → create
```

Backend повторно проверяет:

- venue active;
- `online_booking_enabled=true`;
- business date в горизонте;
- смена существует;
- start/end внутри одной смены;
- start/end кратны 5 минутам;
- duration >=45 минут;
- start не раньше разрешённого server-side слота;
- hall bookable;
- table не архивирован;
- table bookable;
- party_size <= table.capacity;
- нет BLOCK overlap;
- нет BOOKING overlap;
- нет dynamic online busy overlay;
- privacy consent присутствует.

Самый ранний start:

```text
ceil_to_5_minutes(operation_now)
```

Пример:

```text
20:02 → 20:05
20:05:00 → 20:05
20:05:01 → 20:10
```

Максимальный `business_date`:

```text
add_months_clamped(current_business_date, 2)
```

Именно календарные месяцы, не фиксированные 60 дней; правило clamp описано в §2.

## 18.1. Race condition

Если два пользователя одновременно бронируют один slot, только одна transaction проходит exclusion constraint.

Вторая получает:

```text
409 BOOKING_CONFLICT
```

Frontend refetch-ит availability и показывает понятное сообщение.

## 18.2. Idempotency публичного POST

Перед первой отправкой формы frontend генерирует UUID `Idempotency-Key` и повторно использует тот же ключ при сетевом retry того же пользовательского действия.

Backend вычисляет `HMAC-SHA-256` канонического нормализованного business-payload с отдельным серверным `IDEMPOTENCY_HMAC_KEY` и сохраняет результат как `public_request_hmac`. Голый SHA/hash payload не используется: телефон и другие поля имеют слишком малую энтропию для безопасного неключевого отпечатка. HMAC-key хранится вне БД и входит в recovery secrets (§48).

Порядок обработки принципиален:

1. дешёвые transport guards: лимит размера, корректность UUID header, honeypot и грубый anti-flood burst limit с запасом на нормальный retry;
2. resolve venue по slug без mutable-state gate;
3. вычислить canonical request HMAC и lookup `(venue_id, Idempotency-Key)`;
4. если запись найдена — сравнить request HMAC **до** проверки `venue.is_active`, текущего расписания, availability, `online_booking_enabled`, горизонта, booking-specific quota и server-side start;
5. тот же HMAC → вернуть ту же booking identity/номер как успешный idempotent replay, не создавая новую бронь; текущее состояние брони может уже быть изменено администратором и отражается в ответе;
6. другой HMAC → `409 IDEMPOTENCY_KEY_REUSED`;
7. только если key отсутствует — применить hard gate `venue.is_active`, обычный create rate limit/rolling quota, все validations из §18 и создание.

Таким образом, потерянный успешный ответ нельзя превратить в `BOOKING_CONFLICT`, `ONLINE_BOOKING_DISABLED` или ошибку «start уже в прошлом» только потому, что состояние изменилось после первого успешного commit. Если бронь после создания была отменена/изменена администратором, replay всё равно относится к исходной броне и **никогда** не создаёт новую.

При конкурентных запросах с одинаковым key unique constraint является финальной защитой. Проигравшая transaction после rollback читает существующую бронь и применяет правила выше. Unique violation по idempotency key (`23505`) не маппится в обычный 500/BOOKING_CONFLICT: backend после rollback загружает запись по `(venue_id, key)` и возвращает идемпотентный результат либо `IDEMPOTENCY_KEY_REUSED`.

Idempotency не заменяет exclusion constraint: она решает потерянный HTTP-ответ и повтор того же POST.

---

# 19. Ручная бронь администратора

Администратор может создать бронь вручную.

Обязательны:

- дата/время;
- один или несколько столов;
- имя;
- party size;
- source.

Телефон обязателен для `PHONE | OTHER`; для `VK | WALK_IN` телефон необязателен. Комментарий необязателен.

Admin override в v1 нет.

Обычная ручная бронь подчиняется тем же правилам смены, min 45, 5-минутной сетки, capacity и exclusion constraint. Единственное исключение по минимальной длительности — атомарный `WALK_IN create+open` из §19.1. Обычная manual booking не может создаваться с `starts_at < ceil_to_5_minutes(operation_now)`; исторические записи задним числом через этот endpoint не создаются.

`POST /api/admin/v1/bookings` требует `Idempotency-Key` UUID. Frontend генерирует новый key на одно нажатие «Создать» и повторяет его при retry. После auth/CSRF и грубого anti-flood guard backend сначала делает lookup admin idempotency key и только при его отсутствии выполняет mutable booking validations. Backend хранит `admin_idempotency_key/admin_request_hmac`; `admin_request_hmac` — такой же HMAC-SHA-256 канонического payload на `IDEMPOTENCY_HMAC_KEY`. Одинаковый key+HMAC возвращает ту же booking identity, другой payload с тем же key → `409 IDEMPOTENCY_KEY_REUSED`. Это защищает от double-click и потерянного ответа. `source=ONLINE` через этот endpoint запрещён.

## 19.1. WALK_IN create + open

Для гостя, который уже стоит у стола, есть одна атомарная admin-команда `create + OPEN` с `source=WALK_IN`; два отдельных HTTP-действия не требуются. API использует тот же `POST /bookings` с `open_immediately=true`; этот флаг допустим только при `source=WALK_IN` и входит в idempotency request HMAC.

В transaction после locks берётся `operation_now`. `operation_now` обязан находиться внутри актуально resolved shift. Плановый `starts_at` для новой WALK_IN-брони = `ceil_to_5_minutes(operation_now)`, выбранный `ends_at` остаётся на 5-минутной сетке, `ends_at > starts_at`, не выходит за `shift_ends_at` и проходит capacity/conflict checks.

Для `WALK_IN create+open` используется grid-aligned конец resolved shift. Из инвариантов расписания §5 следует `grid_shift_ends_at = shift_ends_at`; helper всё равно вычисляет/проверяет его явно, чтобы правило не зависело от невалидного schedule input:

```text
grid_shift_ends_at = floor_to_5_minutes(shift_ends_at)
assert grid_shift_ends_at == shift_ends_at
required_duration = min(MIN_BOOKING_MINUTES, grid_shift_ends_at - starts_at)
ends_at - starts_at >= required_duration
ends_at <= grid_shift_ends_at
```

То есть посреди смены WALK_IN по-прежнему минимум 45 минут. Исключение срабатывает **только у конца смены**: если после grid-aligned `starts_at` до grid-aligned конца смены осталось меньше 45 минут, бронь допускается только если покрывает не меньше всего требуемого остатка; при выборе максимального конца это весь оставшийся положительный интервал. Если после `ceil_to_5_minutes(operation_now)` внутри смены уже не остаётся ни одного 5-минутного интервала, новую WALK_IN-бронь создать нельзя. Это исключение не распространяется на ONLINE и обычные manual booking.

Фактическое `opened_at=operation_now`; интервал до планового `starts_at` обрабатывается как обычный early OPEN и проверяется на live/occupancy conflict.

Команда создаёт booking, occupancies, live rows и event атомарно либо не создаёт ничего.

---

# 20. Операции со столами

Публичный пользователь выбирает ровно один стол.

Администратор может add/remove/replace/reseat в пределах планового интервала OPEN либо для будущей NEW/WAITING.

Все операции атомарны.

Все обрезки effective segment выполняются только через `truncate_segment_at()` из §15.1.

Любая booking mutation, которая читает `capacity`, `is_bookable`, `archived_at` или конфликтность occupancy затронутого стола, обязана взять этот table lock **до** финальной проверки. Для плановых мутаций без изменения live-state достаточно `tables FOR SHARE`; для `OPEN`/live-операций используется `tables FOR UPDATE`. Все table locks берутся по возрастанию `table_id`. Это правило отдельно продублировано в §32.3.

## 20.1. До начала брони

Если `operation_now < booking.starts_at`, замена стола меняет его на весь будущий интервал.

Старый future occupancy деактивируется, новый создаётся `[booking.starts_at, booking.ends_at)`.

## 20.2. После начала, но до конца

Если `booking.starts_at <= operation_now < booking.ends_at`, replace/reseat:

- старый effective occupancy обрабатывается через `truncate_segment_at(old_segment, operation_now)`;
- новый occupancy создаётся `[operation_now, booking.ends_at)`;
- если бронь OPEN, live set обновляется атомарно.

## 20.3. После планового конца OPEN

Если `operation_now >= booking.ends_at`, v1 **не** поддерживает add/remove/replace/reseat для этой OPEN-брони. Текущий live set остаётся фактом до `CLOSE`, а публичная availability блокирует занятый live-стол динамическим overlay по §17.

Post-end reseat сознательно отложен за пределы release v1: это уменьшает число специальных сегментных сценариев и не требует менять модель данных в будущем.

После `operation_now >= booking.shift_ends_at` бронь дополнительно считается stale previous-shift; разрешён только `CLOSE` и безопасное редактирование guest-текста, не меняющее live/plan state.

## 20.4. NEW/WAITING после ends_at

Если NEW/WAITING уже просрочены, add/replace/open на старом времени напрямую запрещены.

Нужно полностью перенести бронь на новый будущий интервал либо отменить её.

## 20.5. Remove

До начала future occupancy деактивируется.

Внутри интервала текущий segment обрабатывается через `truncate_segment_at(segment, operation_now)`; при OPEN table удаляется из live set.

После `operation_now >= booking.ends_at` remove запрещён правилом §20.3.

После операции capacity invariant обязан сохраниться.

## 20.6. Replace атомарен

`remove old + add new` выполняются в одной transaction.

# 21. WAITING

`NEW → WAITING` разрешён только когда:

```text
booking.starts_at <= operation_now < booking.ends_at
```

Просроченную `NEW` после `ends_at` нельзя переводить в WAITING: сотрудник должен изменить/перенести время либо отменить бронь.

Автоматического перехода нет.

Сотрудник вручную открывает или отменяет бронь.

---

# 22. OPEN

Разрешено:

```text
NEW → OPEN
WAITING → OPEN
```

только если:

```text
booking.shift_starts_at <= operation_now < booking.ends_at
```

То есть early OPEN разрешён только после начала snapshot-смены. Если плановый интервал уже закончился, сначала нужно изменить/перенести бронь.

## 22.1. Открытие

В transaction:

1. venue/session guard без row lock на `venues`;
2. booking `FOR UPDATE`;
3. `expected_version` check;
4. определяется текущий назначенный набор столов;
5. затронутые tables берутся `FOR UPDATE` по `table_id`;
6. после locks получается `operation_now = clock_timestamp()`;
7. повторно проверяются state/time/capacity;
8. проверяется live conflict и фактическая доступность;
9. создаются `booking_live_tables` с `business_date` брони;
10. `status=OPEN`;
11. `opened_at=operation_now`;
12. `version++`;
13. пишется event;
14. NOTIFY внутри transaction.

## 22.2. Раннее OPEN

Гости могут прийти раньше `starts_at`.

Это разрешено, если текущие назначенные столы реально свободны от `operation_now` до `starts_at`:

- нет live conflict;
- нет чужого BOOKING/BLOCK occupancy, пересекающего `[operation_now, booking.starts_at)`.

`booking.starts_at` автоматически не меняется.

Public availability получает dynamic busy overlay до начала планового occupancy.

## 22.3. Следующая бронь при засидевшихся гостях

Если table live у предыдущей OPEN-брони, следующую бронь на том же столе открыть нельзя.

Сотрудник должен закрыть предыдущую OPEN. Если предыдущая бронь уже вышла за plan end, post-end reseat в release v1 недоступен (§20.3).

Следующая плановая бронь сама не сдвигается. Если свободной альтернативы для следующей брони нет, release v1 действительно может потребовать от сотрудника организационного решения вне модели (попросить предыдущих гостей освободить стол, изменить/отменить следующую бронь либо закрыть OPEN, если визит фактически завершён). Закрывать OPEN при реально сидящих гостях только ради освобождения системы считается некорректным использованием. Частота такого конфликта измеряется в пилоте (§49, Stage 15) и определяет приоритет post-end reseat после v1.

---

# 23. UNDO_OPEN

Разрешено только если:

- current status = OPEN;
- `operation_now < booking.ends_at`;
- после соответствующего `BOOKING_OPENED` не было мутаций, меняющих state, plan/live tables, time или party size.

Соответствующий `BOOKING_OPENED` и все события после него определяются по `booking_events.id`, а не сортировкой по `created_at`.

Для UNDO значимыми считаются как минимум: `TABLE_ADDED`, `TABLE_REMOVED`, `TABLE_REPLACED`, reseat, `TIME_CHANGED`, `BOOKING_RESCHEDULED`, изменение `party_size`, CLOSE/CANCEL/state transition. Правка только `guest_name`, `guest_phone` или `guest_comment` не блокирует UNDO. Event payload `BOOKING_EDITED` обязан перечислять изменённые поля, чтобы это правило было детерминированным.

При UNDO:

- live rows удаляются;
- `opened_at=NULL`;
- status возвращается в исходный `NEW` или `WAITING`;
- `version++`;
- записывается `OPEN_UNDONE`.

Лимита «только N минут» нет, но действует условие `operation_now < booking.ends_at`. Поэтому ошибочно открытая бронь, замеченная уже после plan end, через `UNDO_OPEN` не откатывается и должна быть завершена `CLOSE`. Это осознанный v1-компромисс: после plan end не пытаемся задним числом реконструировать состояние plan/live.

---

# 24. CLOSE

Разрешено `OPEN → CLOSED`.

`closed_at = operation_now` из БД.

Для каждого active BOOKING occupancy этой брони:

```text
если starts_at >= closed_at:
    is_active = false
если starts_at < closed_at < ends_at:
    ends_at = closed_at
если ends_at <= closed_at:
    не менять
```

Дополнительно:

- status=CLOSED;
- live rows удаляются;
- version++;
- event.

`booking.ends_at` не меняется.

---

# 25. CANCEL

Разрешено:

```text
NEW → CANCELED
WAITING → CANCELED
```

В одной transaction:

- status=CANCELED;
- canceled_at=operation_now;
- сохраняется reason;
- все active BOOKING occupancies этой брони → `is_active=false`;
- live rows отсутствуют;
- version++;
- event.

`OPEN → CANCELED` запрещено.

---

# 26. Изменение времени

## 26.1. NEW/WAITING

Есть два режима.

**Полный перенос интервала** — меняется `starts_at` (и при необходимости дата). Тогда применяются правила новой admin-брони: новый start не раньше `ceil_to_5_minutes(operation_now)`, заново резолвится смена, пересчитывается `business_date`, записываются новые `shift_starts_at/shift_ends_at`.

**Изменение только конца** — если `operation_now < booking.ends_at`, администратор может продлить/сократить `ends_at`, не сдвигая уже наступивший `starts_at`. В этом режиме правило «start не раньше now» повторно не применяется; новый `ends_at` обязан быть `> operation_now`. Проверяются новая длительность, snapshot shift boundary, capacity и conflicts на изменяемом хвосте.

Если исходный интервал уже полностью просрочен (`operation_now >= booking.ends_at`), NEW/WAITING нельзя «оживить» простым продлением старого конца. Нужен полный перенос на новый будущий интервал либо отмена. После переноса бронь можно открыть раньше нового plan start по обычным правилам early OPEN.

Если WAITING переносится в будущий интервал:

```text
status → NEW
waiting_at → NULL
```

Старые effective occupancies деактивируются, новые создаются для текущего набора столов.

## 26.2. OPEN до plan end

У OPEN `starts_at` не двигается; можно менять только `ends_at`, пока `operation_now < old ends_at`.

Новый `ends_at`:

- кратен 5 минутам;
- > operation_now;
- не позже `booking.shift_ends_at`.

При продлении проверяются текущие live tables и будущие conflicts. Продлеваются/создаются только хвостовые effective segments; прошлые segments задним числом не растягиваются.

## 26.3. OPEN после plan end

Если `operation_now >= booking.ends_at`, изменение `ends_at` в release v1 запрещено. Гости могут фактически продолжать сидеть: `booking_live_tables` и dynamic availability по §17 отражают этот факт до `CLOSE`.

Продление просроченного OPEN сознательно отложено за пределы release v1. Оно может быть добавлено позже без изменения базового разделения plan occupancy / live fact.

# 27. Party size и guest data

Для NEW/WAITING можно менять name, phone, comment, party_size при сохранении инвариантов.

Для OPEN можно менять name/phone/comment; party_size — только если live capacity остаётся достаточной.

Для CLOSED/CANCELED обычная бизнес-редакция закрыта.

---

# 28. Временная блокировка стола

Отдельной таблицы `table_blocks` нет.

Используется `table_occupancies.kind = BLOCK`.

BLOCK:

- имеет table;
- starts_at/ends_at;
- note;
- не имеет booking_id;
- участвует в exclusion constraint.

Снятие BLOCK означает `is_active=false`, не физический DELETE.

Для длительного выключения без интервала используется `tables.is_bookable=false`.

---

# 29. Hall/table disable и archive

## 29.1. Hall `is_bookable=false`

Запрещает новые брони, не отменяя существующие.

## 29.2. Table `is_bookable=false`

Запрещает новые брони, не отменяя существующие.

## 29.3. Архивация table

Архивация запрещена, если есть:

- live row текущего business day;
- active future BOOKING occupancy.

Active BLOCK rows при архивации деактивируются.

История сохраняется.

## 29.4. Уменьшение capacity

Нельзя сохранить capacity, если это ломает current OPEN live capacity или capacity будущей active брони.

API возвращает затронутые bookings. Admin UI даёт переход на отфильтрованный список этих броней, где их можно последовательно перенести штатным `replace-table`; автоматический массовый перенос между столами в v1 не вводится.

Практический вывод стола из эксплуатации: сначала `is_bookable=false`, затем обработать/перенести показанные будущие брони, после исчезновения live/future occupancy — archive. Это штатный workflow, а не аварийный обход.

## 29.5. Архивация hall

У hall есть `archived_at`. Архивация запрещена, пока в нём есть неархивные tables. Сначала столы выводятся из эксплуатации по §29.3–29.4 и архивируются.

Архивный hall сохраняется для истории/layout references, но не показывается public UI и не принимает новые брони. В admin/editor archived halls по умолчанию скрыты; диагностический режим может запрашивать их явно.

---

# 30. Редактор зала

Редактор входит в v1.

## 30.1. Технология

Konva + react-konva.

Одна модель геометрии используется в public/admin/editor.

## 30.2. Возможности

- create table;
- archive table;
- drag;
- resize;
- rotate;
- shape;
- number;
- capacity;
- оперативный `is_bookable` через отдельную mutation (не часть layout draft/save);
- create hall;
- rename hall;
- archive hall;
- hall bookability;
- static wall/stage/bar/text/zone.

Перенос существующего table из одного hall в другой в v1 запрещён.

## 30.3. Static elements

`halls.static_elements JSONB` валидируется как Pydantic discriminated union.

Запрещены arbitrary HTML, JS и raw SVG markup.

Есть лимит числа элементов и общего размера JSON.

## 30.4. Konva Transformer

На `transformend`:

1. `scaleX/scaleY` переводятся в `width/height`;
2. draft обновляется;
3. scale сбрасывается в 1.

Накопленный scale не хранится в БД.

## 30.5. Производительность

Static layer в public/admin: `listening=false`.

Editor code lazy-loaded.

## 30.6. Mobile/accessibility

Публично:

- pan;
- pinch zoom;
- большие hit areas;
- альтернативный режим `Схема / Список`.

---

# 31. Layout optimistic concurrency

Frontend редактирует local draft.

Zustand хранит:

```text
selectedElement
zoom
pan
draftGeometry
dirty
undoStack
redoStack
```

Оперативный `is_bookable` **не входит** в layout draft и не перезаписывается layout-save. Его переключение выполняется отдельной admin mutation и не увеличивает `layout_revision`. Это исключает `LAYOUT_STALE` только из-за того, что сотрудник временно выключил стол/зал, пока другой редактировал геометрию.

Сохранение:

```text
PUT /api/admin/v1/halls/{id}/layout
```

передаёт `expected_revision` и полное желаемое состояние полей, которыми действительно владеет editor: geometry, number, capacity, shape, z-index, static elements, create/archive table и layout-размеры hall.

Backend:

1. venue guard;
2. exclusive **layout advisory lock** в отдельном namespace по `venue_id`;
3. hall `FOR UPDATE`;
4. сверка revision;
5. tables `FOR UPDATE` по id;
6. полная валидация всех затронутых future/live capacity invariants;
7. apply create/update/archive;
8. static elements;
9. revision++;
10. NOTIFY;
11. COMMIT.

Layout-save редок, поэтому release v1 сознательно сериализует все layout-save одного venue. Это закрывает cross-hall write skew: две параллельные правки capacity в разных halls не могут обе проверить multi-table booking на старых значениях соседнего hall.

На stale:

```text
409 LAYOUT_STALE
```

Frontend **не выбрасывает local draft автоматически**. Он показывает, что схема изменилась на сервере, блокирует слепой повтор Save и предлагает явно перезагрузить актуальную схему; discard draft происходит только после подтверждения пользователя. Автоматического merge/rebase в v1 нет.

`layout_revision` увеличивается только при изменении данных, которые layout-save способен перезаписать. Оперативные флаги `hall.is_bookable` / `table.is_bookable` версию layout не меняют.

# 32. Конкурентность и порядок блокировок

## 32.0. Isolation level

Основной isolation level v1:

```text
READ COMMITTED
```

Корректность обеспечивается явными row locks, exclusion/unique constraints, transaction-level advisory guards для schedule/layout и повторной валидацией после locks.

## 32.1. Booking transactions

Единый порядок ресурсов:

```text
venue read (без row lock)
→ operation-specific advisory lock (`schedule` либо `layout`, если нужен)
→ booking / bookings по возрастанию id (если существуют)
→ hall / halls по возрастанию id
→ tables по возрастанию id
→ booking counter (только create)
→ occupancies
→ event/outbox
```

Для сериализации create/reschedule с изменением расписания используется transaction-level PostgreSQL advisory lock с отдельным namespace по `venue_id`:

- booking create/reschedule: `pg_advisory_xact_lock_shared(...)`;
- изменение weekly schedule/exception: `pg_advisory_xact_lock(...)`.

Один helper строит advisory key; все операции соблюдают один порядок его взятия. Для release v1 используется single-`bigint` advisory key: helper берёт стабильный 64-bit signed digest от строки `takeplace:{namespace}:{venue_id}` (отдельные namespace как минимум `schedule` и `layout`) и передаёт его в `pg_advisory_xact_lock(bigint)` / shared-вариант. `venue_id` не приводится к `int4`. Возможная hash-коллизия может только избыточно сериализовать два независимых venue, но не ослабляет корректность. Реализация key helper одна и покрыта unit test vectors. Schedule guard защищает именно schedule consistency и не используется как общий mutex venue. Layout-save использует **другой advisory namespace** и exclusive lock по `venue_id`; booking operations layout-lock не берут, потому что их согласованность с capacity/bookability обеспечивается table row locks.

## 32.2. Booking create и аварийный kill switch

```text
venue SELECT без FOR SHARE
→ hard gate is_active / online_booking_enabled
→ shared schedule advisory lock
→ resolve schedule
→ hall FOR SHARE
→ tables ASC FOR SHARE
→ operation_now = clock_timestamp()
→ повторная валидация time/bookability/capacity/conflicts
→ финальный plain SELECT is_active / online_booking_enabled
→ booking counter
→ booking
→ occupancies
→ event
→ optional outbox
→ pg_notify
→ COMMIT
```

Public create **не держит row lock на `venues`**. Поэтому `PATCH /settings` с `online_booking_enabled=false` не должен ждать очередь `FOR SHARE` от потока booking-create и остаётся быстрым аварийным действием даже при abuse.

Kill-switch имеет явно ограниченную семантику: после commit `online_booking_enabled=false` новые запросы не проходят final gate; запрос, который уже прошёл последнюю проверку непосредственно перед insert, теоретически может завершить commit. v1 принимает этот небольшой in-flight race в обмен на responsive emergency switch. UI после выключения делает refetch последних броней, чтобы сотрудник видел такой возможный хвост.

Schedule change не использует venue row как mutex: он берёт exclusive schedule advisory lock. Поэтому schedule update не проходит между resolve смены и commit новой брони, а kill switch при этом не блокируется schedule/create row-lock contention.

Обычное создание использует `FOR SHARE` на tables. Любая операция, меняющая live/factual occupancy, использует `FOR UPDATE` на этих tables, поэтому public create и ранний OPEN/reseat не могут пройти гонкой мимо друг друга. После получения table locks live-операция обязана повторно проверить пересекающиеся BOOKING/BLOCK occupancies и live-state перед записью.

## 32.3. Existing booking mutation

Начинается с venue read. Если операция **может менять `business_date`/shift** (полный reschedule), она берёт shared schedule advisory lock **до** `booking FOR UPDATE`; для остальных mutation schedule lock не нужен. Затем:

```text
booking FOR UPDATE
if booking.version != expected_version:
    409 BOOKING_STALE
```

Так reschedule соблюдает общий lock order `schedule guard → booking` и не образует инверсию с schedule update.

Любая мутация, которая принимает решение на основании `capacity`, `is_bookable`, `archived_at` или occupancy конкретных tables, обязана заблокировать **весь набор tables, от которого зависит проверка**, до финальной валидации:

- `party_size` для NEW/WAITING/OPEN → текущие assigned/live tables `FOR SHARE`;
- `change-time` NEW/WAITING → все текущие assigned tables `FOR SHARE`;
- add/remove/replace для NEW/WAITING → старые и новые затронутые tables `FOR SHARE`;
- OPEN, early OPEN и любые операции, меняющие live set → затронутые tables `FOR UPDATE`.

Locks берутся по `table_id ASC`. Изменение `table.capacity/is_bookable/archive` берёт соответствующий table `FOR UPDATE`, поэтому оно сериализуется с перечисленными booking mutations и не создаёт write skew при `READ COMMITTED`.

После получения всех нужных hall/table locks операция получает `operation_now = clock_timestamp()`, повторяет time/capacity/conflict validation и только затем пишет изменения.

## 32.4. Hall/table mutation

Изменение hall/table блокирует hall/tables `FOR UPDATE`, но не пытается затем брать `booking FOR UPDATE` в обратном порядке.

`PATCH /halls/{id}`, включая `is_bookable`, берёт hall `FOR UPDATE`.

Изменения editor-owned полей увеличивают `layout_revision`; оперативный `is_bookable` не входит в editor-state и revision не меняет (§31).

Зависимые bookings проверяются согласованными SELECT после соответствующих table locks. Новые booking transactions после получения своих table locks заново валидируют capacity/bookability.

## 32.5. Retry

```text
23P01 → 409 BOOKING_CONFLICT
40P01 → retry 1–2 раза
40001 → retry 1–2 раза
55P03 → retry или 503
```

Retry открывает новую transaction и повторяет проверки заново.

## 32.6. Timeouts

Начальные production настройки:

```text
lock_timeout ≈ 3s
statement_timeout ≈ 10s
idle_in_transaction_session_timeout ≈ 15s
```

# 33. Booking version

Каждая admin mutation получает:

```text
expected_version
```

После `booking FOR UPDATE`:

```text
expected_version != booking.version
→ 409 BOOKING_STALE
```

Каждая успешная бизнес-мутация увеличивает `version` ровно на 1.

Last-write-wins запрещён.

---

# 34. Public API

Prefix:

```text
/api/public/v1
```

Минимум:

```text
GET  /venues/{slug}
GET  /venues/{slug}/availability
POST /venues/{slug}/bookings
```

`POST /venues/{slug}/bookings` требует client-generated `Idempotency-Key` UUID и следует §18.2.

`GET /venues/{slug}` возвращает только публичные данные:

- venue;
- halls;
- tables;
- capacity;
- geometry;
- минимально необходимую конфигурацию UI.

`GET availability` принимает как минимум:

```text
business_date
hall_id
```

и может дополнительно фильтровать `table_id`.

---

# 35. Admin API

Prefix:

```text
/api/admin/v1
```

Минимальная структура:

```text
POST /auth/login
POST /auth/logout
POST /auth/logout-all
GET  /me

GET  /bookings
GET  /bookings/unresolved
POST /bookings
GET  /bookings/{id}
GET  /bookings/{id}/history
PATCH /bookings/{id}

POST /bookings/{id}/wait
POST /bookings/{id}/open
POST /bookings/{id}/undo-open
POST /bookings/{id}/close
POST /bookings/{id}/cancel

POST   /bookings/{id}/tables
DELETE /bookings/{id}/tables/{table_id}
POST   /bookings/{id}/replace-table
POST   /bookings/{id}/change-time

GET  /halls
POST /halls
PATCH /halls/{id}
POST /halls/{id}/archive
PATCH /tables/{id}
PUT  /halls/{id}/layout

POST   /table-blocks
DELETE /table-blocks/{occupancy_id}

GET    /schedule
PUT    /schedule
PUT    /schedule/exceptions/{date}
DELETE /schedule/exceptions/{date}

GET   /settings
PATCH /settings

GET  /integrations/vk
PUT  /integrations/vk
GET  /outbox/dead
POST /outbox/{id}/retry
POST /outbox/{id}/acknowledge

GET /stream
GET /system/status
```

`GET /bookings` обязательно поддерживает cursor/limit pagination и фильтры как минимум: `business_date`, `status`, `source`, `table_id`, exact `guest_phone_normalized`. Поиск по телефону доступен только admin и не возвращает данные другого venue. Для ONLINE-брони с ещё не истёкшим abuse fingerprint карточка может дать действие «Показать брони с тем же сетевым отпечатком» в пределах этого venue и короткого retention window; сам HMAC пользователю не показывается.

Bulk-cancel с preview **не входит в release v1**. При abuse сначала используется kill switch, затем фильтры/сетевой fingerprint и обычная явная отмена выбранных броней. Массовая отмена может быть добавлена позже без изменения booking model.

`GET/PATCH /settings` покрывает настройки venue, которые уже существуют в модели v1: публичные name/address/phone и `online_booking_enabled`. `timezone` в release v1 через обычную admin UI не меняется после onboarding; если позже появится mutation timezone, она обязана использовать тот же rolling IANA/DST capability check, что `create-venue` (§53). Schedule, layout и VK token имеют отдельные endpoints и не дублируются в settings.

`PATCH /tables/{id}` — отдельная operational mutation для `table.is_bookable` (release v1 не использует её для geometry/number/capacity/shape). Она доступна до готовности Konva editor, берёт table `FOR UPDATE`, соблюдает tenant guard и **не** увеличивает `layout_revision`. Editor-owned поля меняются только через layout-save.

Имена endpoint могут уточняться без изменения семантики.

---

# 36. Стандартные API error codes

Create semantics для обоих idempotent endpoints (`public POST booking`, `admin POST booking`): новая бронь → `201 Created`; replay того же key+payload → `200 OK` с той же booking representation/identity и без повторных side effects. Frontend обязан считать оба статуса успешными.

Минимум:

```text
BOOKING_CONFLICT
BOOKING_STALE
BOOKING_RULE_VIOLATION
BOOKING_INVALID_STATE
TABLE_NOT_BOOKABLE
HALL_NOT_BOOKABLE
TABLE_LIVE_CONFLICT
LAYOUT_STALE
ONLINE_BOOKING_DISABLED
IDEMPOTENCY_KEY_REUSED
RATE_LIMITED
SCHEDULE_CHANGE_REQUIRES_CONFIRMATION
TABLE_ARCHIVE_BLOCKED
HALL_ARCHIVE_BLOCKED
CAPACITY_CHANGE_BLOCKED
```

Frontend ориентируется на machine-readable `code`, а не парсит русский текст backend. Операции schedule/capacity/archive, которые не могут быть применены без явного решения администратора, используют соответствующий 409-code выше и возвращают безопасный impact summary внутри текущего venue.

При admin-создании BLOCK конфликтный ответ может дополнительно вернуть IDs/номера конфликтующих броней этого же venue. Public API такие данные не раскрывает.

---

# 37. Realtime

## 37.1. SSE

Admin открывает:

```text
GET /api/admin/v1/stream
```

Venue определяется только из session.

`venue_id` параметром клиента не принимается.

## 37.2. PostgreSQL NOTIFY

`pg_notify` вызывается внутри той же transaction, что бизнес-изменение.

PostgreSQL доставляет NOTIFY только после commit.

Payload короткий:

```json
{
  "venue_id": 1,
  "type": "booking.updated",
  "ids": [123]
}
```

Полные объекты через NOTIFY не передаются. Payload обязан оставаться существенно меньше лимита PostgreSQL NOTIFY. Если одна mutation затрагивает слишком много ids (например крупный layout operation), вместо длинного массива отправляется короткий event `type="resync"` без списка ids; frontend делает полный refetch соответствующего venue-state.

## 37.3. LISTEN

Каждый API-process использует выделенное LISTEN connection.

Не использовать transaction-pooled connection для LISTEN.

При потере LISTEN connection:

1. backend переподключается;
2. всем SSE-клиентам отправляется `resync`.

## 37.4. Recovery

SSE:

- heartbeat примерно каждые 20 секунд;
- bounded queue на соединение;
- overflow → stream закрывается;
- reconnect → frontend делает полный refetch/invalidateQueries;
- истёкшая/удалённая session закрывает stream.

Caddy для SSE:

```text
flush_interval -1
```

или эквивалентная настройка без buffering.

Публичной странице постоянный realtime не нужен.

---

# 38. VK-интеграция

## 38.1. Назначение

VK используется только для сообщения в рабочую беседу сотрудников при создании новой `ONLINE`-брони.

Не отправляем VK-сообщения:

- гостю;
- при manual booking;
- при cancel;
- при edit;
- при close.

## 38.2. Transactional outbox

В transaction ONLINE booking создаются:

```text
booking
occupancies
booking_event
notification_outbox
```

Ошибка VK не откатывает бронь.

Outbox `payload` для booking notification хранит только минимальные неперсональные ссылки/тип (`booking_id`, notification kind и техническую версию formatter), а не снимок имени/телефона/comment. Перед фактической отправкой worker загружает актуальную booking и форматирует сообщение. Это уменьшает количество копий ПД внутри системы и позволяет не отправлять уже отменённую бронь.

При создании задачи задаётся `expires_at`:

```text
min(
  booking.ends_at,
  booking.starts_at + VK_NOTIFICATION_LATE_GRACE,
  outbox.created_at + VK_NOTIFICATION_MAX_AGE
)
```

Начальные deploy defaults: `VK_NOTIFICATION_MAX_AGE = 6 hours`, `VK_NOTIFICATION_LATE_GRACE = 30 minutes`. Это operational config, а не продуктовые константы. Такая схема не гасит срочную бронь ровно в момент `starts_at`, но и не позволяет восстановленному worker слать давно потерявшие смысл сообщения.

## 38.3. Worker lease

Worker не держит DB transaction во время HTTP-вызова.

Алгоритм:

1. `FOR UPDATE SKIP LOCKED` для due-задачи;
2. до claim проверить stale/skip conditions;
3. если `now >= expires_at` → `SKIPPED/EXPIRED`; если booking анонимизирована → `SKIPPED/BOOKING_ANONYMIZED`; если текущий booking status не `NEW/WAITING` → `SKIPPED/BOOKING_INACTIVE`; заполнить `skipped_at/skip_reason`, COMMIT и **не** делать HTTP;
4. иначе `status=PROCESSING`, `locked_until=...`, `attempts++`, COMMIT;
5. загрузить актуальную booking и повторно проверить skip condition непосредственно перед HTTP;
6. HTTP в VK;
7. отдельная transaction: `SENT`, либо `RETRY`, либо `DEAD`.

Истёкший `locked_until` позволяет подобрать зависшую задачу повторно.

Ручной `retry` разрешён только для `DEAD/RETRY`: он очищает acknowledgement и назначает `next_attempt_at=now`, но **не продлевает `expires_at`**. Если задача уже stale, retry завершит её как `SKIPPED`, а не отправит старое сообщение.

`acknowledge` не пытается отправить сообщение и не меняет delivery outcome; он только фиксирует, что сотрудник увидел проблему. Acknowledged DEAD остаётся доступен в истории, но перестаёт делать `/health/ops` degraded и исчезает из активного баннера.

Delivery semantics для неистёкших задач:

```text
at-least-once
```

Worker имеет per-venue send throttle, чтобы всплеск ONLINE-бронирований не превращался в мгновенный флуд рабочей беседы. Throttle задерживает только ещё актуальные PENDING/RETRY; stale-задачи переводятся в `SKIPPED`, поэтому восстановление после длительного сбоя не создаёт залп уведомлений о старых бронях. Бронь, которая уже началась, но всё ещё `NEW/WAITING` и находится внутри `VK_NOTIFICATION_LATE_GRACE`, не считается stale только из-за `starts_at`. Конкретные пороги throttle — deploy config.

## 38.4. Provider dedup

`provider_dedup_id` генерируется VK adapter и сохраняется неизменно на всех retry.

Нельзя напрямую считать `BIGINT outbox.id` гарантированно допустимым VK `random_id`, пока используемая версия API не подтверждена актуальной документацией.

Перед реализацией интеграции диапазон и семантика `random_id` проверяются отдельно.

## 38.5. VK secrets

Каноническая DB-схема `venue_vk_integrations` приведена в §6.12.

Token:

- encrypted at rest;
- ciphertext связан с `encryption_key_version` для ротации;
- encryption keys находятся вне БД;
- не логируется;
- после сохранения не возвращается frontend;
- API отдаёт только `has_token`.

## 38.6. Message formatting

Пример:

```text
🆕 Новая бронь №2999

Основной зал · стол №5
2 октября · 20:00–23:00

Виктор
4 гостя
+7 ...
```

User-controlled strings:

- ограничиваются по длине;
- очищаются от управляющих символов;
- не должны превращаться в нежелательные VK mention/markup.

---

# 39. Авторизация и безопасность

## 39.1. Password

Argon2id.

Hash/verify Argon2id — CPU-bound и не выполняется напрямую в async event loop FastAPI. Используется bounded threadpool/worker pool с ограничением параллелизма.

## 39.2. Sessions

Session token:

- минимум 256 бит энтропии;
- raw token только у клиента;
- в БД хранится SHA-256 hash.

Cookie:

```text
__Host-...
HttpOnly
Secure
SameSite=Strict
Path=/
без Domain
```

## 39.3. CSRF

State-changing admin request проверяет `Origin`.

CORS не открывается широко.

## 39.4. Logout

Есть:

```text
logout
logout-all
```

Сброс/смена пароля инвалидирует все sessions venue.

## 39.5. Login brute force

Неверный login и неверный password дают одинаковый внешний ответ.

До запуска дорогого Argon2 verify применяется дешёвый per-IP burst limit; затем — основной rate limit по `login + IP`. Это не позволяет забить bounded Argon2 pool большим числом заведомо плохих логинов.

Production topology: `Internet → Caddy → Uvicorn/FastAPI`. Порт Uvicorn наружу не публикуется. Forwarded client IP headers доверяются только Caddy/internal Docker network; произвольному внешнему `X-Forwarded-For` доверять нельзя.

## 39.6. XSS

Публичная и admin часть находятся на одном origin, поэтому XSS считается критичным.

Требования:

- CSP;
- без inline JS;
- `frame-ancestors 'none'`;
- guest text рендерится как text;
- `dangerouslySetInnerHTML` запрещён без отдельного обоснования.

## 39.7. Logs

В обычные application logs нельзя писать:

- полный телефон;
- guest comment;
- password;
- raw session token;
- VK token;
- raw client IP в application logs public booking flow (допускаются только инфраструктурные access logs по отдельно утверждённой retention policy).

---

# 40. Антиспам

v1 использует несколько дешёвых слоёв, ни один из которых сам по себе не считается доказанной identity:

- burst application rate limit по `venue + IP`;
- мягкая rolling/day quota по `venue + IP` для public create;
- отдельный более высокий soft rate limit для public GET/availability, чтобы дешёвое чтение не могло бесконтрольно грузить БД;
- honeypot;
- server-side time validation;
- responsive `online_booking_enabled` kill switch (§32.2);
- DB conflict protection;
- exact admin-фильтр по `guest_phone_normalized`;
- short-lived network fingerprint для ONLINE abuse investigation;
- per-venue throttle + TTL VK worker;
- агрегатный venue abuse detector: rolling count ONLINE-create за короткое окно (initial ориентир 10 минут) с configurable threshold/cooldown. Он **не** отменяет и не выключает бронирование автоматически. В release v1 detector влияет только на `/health/ops`, `/system/status` и баннер админки; отдельный VK/outbox type для abuse-alert **не входит в v1**.

Телефон может использоваться как дополнительный abuse signal, но не является доказанной identity. Hard-limit «не более N будущих броней на номер» в v1 не вводится: номер не подтверждён, лимит легко обходится и способен ломать легитимные сценарии. Аналогично не вводится max booking duration только ради anti-abuse.

Для ONLINE create backend вычисляет `request_ip_hmac = HMAC-SHA-256(ABUSE_HMAC_KEY, venue_id || canonical_client_ip)`. Raw IP в booking/event не сохраняется. Обычный salt + быстрый hash не используется, потому что пространство IPv4 слишком мало для защиты от перебора. HMAC служит только краткосрочному объединению подозрительных запросов **внутри одного venue**.

`request_ip_hmac_expires_at` имеет короткий operational retention; initial default — 7 дней. Housekeeping зануляет fingerprint после TTL. Админка не показывает значение HMAC, а только может найти другие ещё неистёкшие ONLINE-брони с тем же fingerprint. `ABUSE_HMAC_KEY` хранится вне БД; его потеря/ротация не влияет на booking correctness, только на возможность сопоставлять старые fingerprints.

При подозрении на атаку сотрудник сначала отключает ONLINE, затем использует фильтры по телефону/времени/столу и совпадающему сетевому fingerprint и отменяет очевидные злоупотребления. Bulk-cancel не входит в release v1.

CAPTCHA не является обязательной внешней зависимостью release v1, но на Stage 6 существует adapter/feature flag и UI/server hook для её включения без изменения booking domain. Провайдер выбирается отдельно; для production РФ допустимый вариант должен быть подтверждён на момент запуска.

Если rate limit реализован in-memory, он считается soft-limit на один API-process и не является security boundary. При масштабировании процессов лимит либо переносится в общий storage, либо effective limits пересчитываются с учётом числа процессов. Rate-limit counters не должны содержать raw PII/IP.

# 41. Телефон

Хранятся:

```text
guest_phone_raw
guest_phone_normalized NULL
```

Нормализация через `phonenumbers`, если возможна. Для номера без явного `+<country code>` v1 использует default region `RU`; например, локальный российский ввод вида `8...` нормализуется в российском контексте. Международный номер должен передаваться с `+`.

Если Takeplace позже выходит за российский production-контекст, default region выносится в venue-setting отдельным изменением спецификации.

Неуспешная нормализация не делает номер identity и сама по себе не является подтверждением/авторизацией. Для `VK | WALK_IN` телефон может отсутствовать полностью; отсутствие номера не заменяется фиктивным значением.

# 42. Персональные данные

ONLINE-бронирование требует consent со ссылкой на актуальную privacy policy.

В booking:

```text
privacy_policy_version
privacy_accepted_at
```

Manual booking может иметь эти поля NULL.

VK-сообщение с телефоном — отдельная копия ПД вне БД Takeplace. Локальная retention job не способна удалить уже отправленное сообщение из рабочей VK-беседы; этот канал и его срок хранения должны быть явно учтены в production legal/operational gate. Если юридическое решение потребует минимизации, formatter должен уметь не включать полный телефон без изменения booking domain.

## 42.1. Idempotency HMAC

`public_request_hmac` и `admin_request_hmac` — HMAC-SHA-256 канонического payload на серверном `IDEMPOTENCY_HMAC_KEY`, а не обычный hash. Секрет хранится вне БД. Это защищает низкоэнтропийные поля payload (в частности телефон) от офлайн-перебора по одному дампу БД.

## 42.2. Явная анонимизация booking

Автоматическая booking-анонимизация применяется только к **терминальным** статусам `CLOSED | CANCELED` после достижения утверждённого retention срока. `NEW | WAITING | OPEN` не анонимизируются автоматически: они остаются operationally unresolved, пока сотрудник не завершит/отменит их. Вечно висящий OPEN — операционный инцидент, а не повод стереть ПД у активной записи. После достижения retention терминальной booking одной housekeeping-операцией очищаются как минимум:

```text
guest_name = NULL
guest_phone_raw = NULL
guest_phone_normalized = NULL
guest_comment = NULL
cancellation_note = NULL
public_idempotency_key = NULL
public_request_hmac = NULL
admin_idempotency_key = NULL
admin_request_hmac = NULL
request_ip_hmac = NULL
request_ip_hmac_expires_at = NULL
anonymized_at = operation_now
```

`cancellation_reason`, source/status, party_size, table/time history и неперсональные operational timestamps могут сохраняться для статистики/целостности модели, если финальная legal policy не требует более строгого удаления. `privacy_policy_version/privacy_accepted_at` могут сохраняться как доказательство версии/момента consent после удаления прямых идентификаторов; окончательное юридическое решение — production gate.

После анонимизации старый idempotency replay больше не гарантируется. Это допустимо: retention window многократно длиннее нормального HTTP retry-window, а запрос со старым временем после очистки всё равно проходит обычные текущие validations вместо восстановления ПД.

`booking_events` по умолчанию не содержит имени, телефона, IP, comment или request payload. Исполняемый audit должен выявлять запрещённые PII keys в event payload. Outbox booking notification также хранит только минимальные ссылки (§38), а не копию телефона.

Short-lived `request_ip_hmac` очищается по собственному TTL (§40), не ожидая общего PII retention.

## 42.3. Backup semantics

До **первого ввода реальных ПД** обязательно отдельно зафиксировать как минимум legal gates §65 (1–3, 5, 6):

- юридический текст и форму consent;
- обязанности оператора/обработчиков;
- срок хранения и правила удаления/анонимизации;
- допустимую инфраструктуру хранения/backup ПД;
- описание передачи данных внешним сервисам, если применимо.

То, что публичный ONLINE ещё выключен, не делает параллельный pilot с реальными именами/телефонами «не-production» с точки зрения этих gates.

Спецификация не придумывает юридический срок хранения и не утверждает конкретное требование к форме согласия без отдельной актуальной юридической проверки.

Местонахождение offsite backup с ПД и роли сторон входят в юридический production-gate. До отдельной юридической проверки operational default — хранить такие резервные копии в инфраструктуре на территории РФ.

После анонимизации live-БД старые значения могут оставаться внутри зашифрованных immutable backup до истечения backup retention. Restore runbook обязан **до возврата восстановленной БД в production traffic** запустить retention catch-up/anonymization по текущему времени, чтобы restore не «воскресил» просроченные ПД.

# 43. Индексы

## bookings

```text
UNIQUE (id, venue_id)
UNIQUE (id, venue_id, business_date)
UNIQUE (venue_id, number)
INDEX (venue_id, business_date, status)
INDEX (venue_id, starts_at)
INDEX (venue_id, guest_phone_normalized)
INDEX (venue_id, request_ip_hmac) WHERE request_ip_hmac IS NOT NULL
PARTIAL UNIQUE (venue_id, public_idempotency_key)
    WHERE public_idempotency_key IS NOT NULL
PARTIAL UNIQUE (venue_id, admin_idempotency_key)
    WHERE admin_idempotency_key IS NOT NULL
```

## table_occupancies

GiST index создаётся exclusion constraint.

Дополнительно:

```text
INDEX (booking_id)
INDEX (venue_id, starts_at) WHERE is_active
```

## tables

```text
UNIQUE (id, venue_id)
INDEX (venue_id, hall_id)
PARTIAL UNIQUE (hall_id, number) WHERE archived_at IS NULL
```

## halls

```text
UNIQUE (id, venue_id)
INDEX (venue_id)
```

## booking_live_tables

```text
UNIQUE (table_id, business_date)
INDEX (booking_id)
INDEX (venue_id, business_date)
```

## admin_sessions

```text
UNIQUE (token_hash)
UNIQUE (id, venue_id)
INDEX (expires_at)
INDEX (venue_id)
```

## booking_events

```text
INDEX (booking_id, id)
```

## notification_outbox

```text
INDEX (next_attempt_at)
WHERE status IN ('PENDING', 'RETRY')

INDEX (locked_until)
WHERE status = 'PROCESSING'

UNIQUE (type, dedup_key)
INDEX (acknowledged_at) WHERE status = 'DEAD'
INDEX (expires_at) WHERE status IN ('PENDING', 'RETRY', 'PROCESSING')
```

## schedule

```text
weekly_schedules: PK (id), UNIQUE (venue_id, weekday)
schedule_exceptions: PK (id), UNIQUE (venue_id, date)
```

## venue_vk_integrations

```text
PK (venue_id)
FK (venue_id) → venues(id) ON DELETE CASCADE
```

---

# 44. Что обязательно держать в БД

В БД:

- `ends_at > starts_at`;
- `party_size > 0`;
- `capacity > 0`;
- допустимые enum-like text values через CHECK;
- status ↔ timestamp consistency;
- occupancy kind ↔ booking_id consistency;
- `starts_at/ends_at` на 5-минутной сетке;
- `shift_starts_at < shift_ends_at` и booking interval внутри snapshot shift;
- для non-anonymized `source='ONLINE'` public idempotency key/HMAC присутствуют; для non-ONLINE public key/HMAC отсутствуют; после anonymization они NULL;
- admin idempotency key/HMAC либо оба NULL, либо оба NOT NULL; после anonymization оба NULL;
- tenant composite FK, включая `(booking_id, venue_id, business_date)` для live rows;
- unique `(venue_id, booking.number)`;
- unique active table number in hall;
- live-table uniqueness per business day;
- exclusion constraint.

В application/domain:

- schedule resolution;
- business_date;
- min 45 для ONLINE/обычных manual; WALK_IN create+open использует исключение §19.1;
- 2 calendar months + end-of-month clamp;
- capacity across segments;
- state machine;
- dynamic OPEN/WAITING online blocking;
- editor geometry;
- archive safety;
- schedule overlap;
- abuse rules.

## 44.1. Рекомендуемые DB CHECK/FK для v1.3.3

Минимум:

```text
CHECK (ends_at > starts_at)
CHECK (shift_ends_at > shift_starts_at)
CHECK (weekly_schedules.weekday BETWEEN 0 AND 6)
CHECK (weekly_schedules: (is_open AND open_time IS NOT NULL AND close_time IS NOT NULL AND open_time <> close_time AND minute(open_time)%5=0 AND second(open_time)=0 AND minute(close_time)%5=0 AND second(close_time)=0) OR (NOT is_open AND open_time IS NULL AND close_time IS NULL))
CHECK (schedule_exceptions: (is_closed AND open_time IS NULL AND close_time IS NULL) OR (NOT is_closed AND open_time IS NOT NULL AND close_time IS NOT NULL AND open_time <> close_time AND minute(open_time)%5=0 AND second(open_time)=0 AND minute(close_time)%5=0 AND second(close_time)=0))
CHECK (starts_at >= shift_starts_at AND ends_at <= shift_ends_at)
CHECK (epoch(starts_at) % 300 = 0)
CHECK (epoch(ends_at) % 300 = 0)
CHECK ((public_idempotency_key IS NULL) = (public_request_hmac IS NULL))
CHECK ((admin_idempotency_key IS NULL) = (admin_request_hmac IS NULL))
CHECK (
  anonymized_at IS NOT NULL
  OR (
    (source = 'ONLINE' AND public_idempotency_key IS NOT NULL AND admin_idempotency_key IS NULL)
    OR
    (source <> 'ONLINE' AND public_idempotency_key IS NULL AND admin_idempotency_key IS NOT NULL)
  )
)
CHECK (anonymized_at IS NULL OR (
  public_idempotency_key IS NULL AND public_request_hmac IS NULL
  AND admin_idempotency_key IS NULL AND admin_request_hmac IS NULL
  AND request_ip_hmac IS NULL
))
CHECK (
  anonymized_at IS NOT NULL
  OR (guest_name IS NOT NULL AND (source IN ('WALK_IN', 'VK') OR guest_phone_raw IS NOT NULL))
)
CHECK ((status = 'SKIPPED') = (skipped_at IS NOT NULL)) -- notification_outbox
CHECK (status = 'SKIPPED' OR skip_reason IS NULL)       -- notification_outbox
CHECK (status <> 'SKIPPED' OR skip_reason IS NOT NULL) -- notification_outbox
CHECK (skip_reason IS NULL OR skip_reason IN ('EXPIRED','BOOKING_INACTIVE','BOOKING_ANONYMIZED'))
CHECK (venue_vk_integrations: NOT enabled OR (community_id IS NOT NULL AND peer_id IS NOT NULL AND encrypted_access_token IS NOT NULL AND encryption_key_version IS NOT NULL))
```

Точная SQL-форма `minute()/second()` выше — псевдокод; миграция PostgreSQL использует `EXTRACT(MINUTE/SECOND FROM ...)` (и исключает fractional seconds). Инвариант 5-минутной сетки защищается и для booking timestamps, и для открытых schedule/exception boundaries. Для `booking_live_tables` обязателен composite FK с `business_date`, описанный в §6.9.

---

# 45. Alembic

Alembic migrations.

`btree_gist` и exclusion constraint создаются явной ручной миграцией.

Нельзя рассчитывать только на autogenerate.

CI проверяет:

1. clean DB from zero;
2. полный `upgrade head`;
3. наличие constraint;
4. реальный overlapping insert;
5. конкурентные transactions;
6. отрицательные inserts для 5-minute/idempotency/shift snapshot CHECK;
7. non-anonymized ONLINE без public key/HMAC и manual без admin key/HMAC отклоняются;
8. VK/WALK_IN без phone проходят, ONLINE/PHONE/OTHER без phone отклоняются до anonymization;
9. live row с неверным `business_date` отклоняется composite FK;
10. schedule rows с `open_time == close_time` и несогласованными NULL отклоняются;
11. SKIPPED outbox без `skipped_at/skip_reason`, неизвестный `skip_reason` и non-SKIPPED с заполненным skip metadata отклоняются;
12. `weekly_schedules.weekday` вне `0..6` и `venue_vk_integrations.enabled=true` с неполным набором обязательных полей отклоняются;
13. удаление `admin_sessions` row, на который ссылаются `booking_events.admin_session_id` и/или `notification_outbox.acknowledged_by_session_id`, успешно зануляет **только session-id**, сохраняя `venue_id NOT NULL` и сами history rows.
14. schedule/exception boundary с минутами не кратными 5, ненулевыми секундами или fractional seconds отклоняется DB CHECK; closed-day rows с NULL times проходят.

## 45.1. Deployment и rollback migrations

До production deploy миграции прогоняются в staging/pre-production окружении на репрезентативной схеме/данных.

- migrations по возможности forward-compatible;
- destructive change разбивается на несколько deploy;
- Alembic downgrade не считается основной recovery-стратегией;
- перед потенциально destructive migration создаётся проверенный backup;
- rollback приложения допустим только пока старая версия совместима с новой schema;
- иначе используется forward-fix либо restore по runbook.

Для нетривиальной migration в задаче/PR должен быть rollback/recovery plan.

---

# 46. DB roles

Минимум:

```text
takeplace_migrator
takeplace_app
```

`migrator` имеет DDL/migrations.

`app` имеет только необходимые DML права и не имеет CREATE/ALTER/DROP.

PostgreSQL наружу не публикуется.

---

# 47. Caddy и health

Caddy отвечает за:

- HTTPS;
- frontend static;
- reverse proxy `/api`;
- SSE без buffering;
- security headers.

Health endpoints не раскрывают PII/secrets:

```text
/health/live
/health/ready
/health/ops
```

- `live` — процесс жив;
- `ready` — приложение может обслуживать запросы и видит БД;
- `ops` — operational health для внешнего мониторинга.

`/health/ops` считается degraded/error, если есть **неacknowledged** `DEAD` outbox rows, слишком старый `PENDING/RETRY`, просроченный worker heartbeat или rolling timezone capability check обнаружил UTC-offset transition в поддерживаемом production horizon для активного venue.

`ops` не используется как container liveness probe, чтобы проблема VK не создавала restart loop всего приложения.

---

# 48. Backup и recovery

Цели v1:

```text
RPO ≈ максимум 1 час
RTO target <= 4 часа
```

RTO — операционная цель восстановления одного VPS/БД по runbook, а не обещание high availability. Один VPS остаётся single point of failure; автоматического failover в v1 нет.

Политика:

```text
каждый час → pg_dump
каждый час → encrypted offsite copy

retention:
48 hourly
14 daily
8 weekly
```

Backup только на том же VPS не считается offsite backup.

## 48.1. БД и recovery secrets — разные артефакты

Один `pg_dump` недостаточен для восстановления сервиса. Отдельно от БД существует зашифрованный recovery bundle / secret escrow, содержащий или позволяющий восстановить как минимум:

- production `.env` / secret-store values;
- все версии ключей шифрования VK token, на которые ещё ссылается `encryption_key_version`;
- `IDEMPOTENCY_HMAC_KEY`;
- backup encryption key/credentials;
- Caddy/app deployment config;
- production domain и документированный DNS target/records;
- доступы к VPS/offsite storage, необходимые по runbook.

`ABUSE_HMAC_KEY` желательно сохранять, но его потеря не ломает booking correctness: исчезает только сопоставимость ещё неистёкших abuse fingerprints. Потеря VK encryption keys или `IDEMPOTENCY_HMAC_KEY` считается recovery defect.

Recovery bundle нельзя хранить **только** внутри того же backup, который требует этих секретов для расшифровки/запуска. Доступ к нему должен быть проверяемым и ограниченным.

## 48.2. Restore test

Не реже раза в месяц выполняется restore test в отдельную БД/окружение. Он включает:

1. восстановление DB dump;
2. восстановление runtime config/secrets;
3. проверку, что существующий VK token расшифровывается ключом нужной версии;
4. migration compatibility;
5. retention catch-up/anonymization до имитации возврата traffic;
6. smoke-test приложения;
7. проверку ожидаемого domain/DNS runbook без фактического переключения production DNS;
8. для реального disaster restore — операционную сверку ONLINE-броней в восстановленном RPO-окне с сообщениями рабочей VK-беседы: VK может содержать уведомление о брони, потерянной между последним dump и аварией. Такая сверка выполняется человеком по runbook и не создаёт бронь автоматически.

Continuous PITR/WAL archiving не является обязательным условием первого v1 release: осознанный v1 target — RPO до 1 часа при исправном hourly backup. После появления нескольких production venue или требования меньшего RPO решение пересматривается; предпочтительный следующий шаг — WAL-G/pgBackRest с PITR restore-test.

# 49. Observability

Минимум:

- structured logs;
- request/correlation id;
- startup/shutdown logs;
- migration logs;
- worker attempt/failure logs без PII;
- external uptime check;
- Docker log rotation;
- outbox metrics: oldest pending age, pending/retry count, processing count, unacknowledged dead count, total dead count, **SKIPPED count by `skip_reason`**, worker heartbeat;
- pilot metric `overdue_waiting_block_table_minutes`: сколько table-minutes публичной availability были динамически заблокированы WAITING после `booking.ends_at` до обработки либо `shift_ends_at`;
- pilot metric `open_overrun`: сколько OPEN пересекли `booking.ends_at`, длительность overrun и был ли в это время следующий плановый booking на тех же столах. Метрика не содержит PII и нужна для решения, стоит ли после пилота возвращать post-end reseat/extension.

`GET /api/admin/v1/system/status` возвращает безопасный operational status без PII/secrets.

Если есть unacknowledged DEAD или заметно просроченные уведомления, админка показывает баннер `Не доставлено уведомлений: N`.

Sentry/GlitchTip можно подключить позже.

---

# 50. Public UI

Минимальные экраны:

1. venue;
2. выбор business date;
3. hall;
4. `Схема / Список`;
5. выбор table;
6. окно start/end;
7. guest form;
8. success.

Success screen показывает:

```text
номер брони
заведение
дата
время
зал
стол
количество гостей
```

Нет кнопок:

- изменить;
- отменить;
- login;
- account.

---

# 51. Admin UI

Все даты/время в admin UI отображаются в `venue.timezone`, а не в timezone устройства администратора. UI может отдельно показывать техническое смещение (`+03:00`) в диагностике, но бизнес-время всегда venue-local.

Главный экран business day:

- date;
- halls;
- hall map;
- bookings list;
- statuses;
- live tables;
- следующая бронь;
- overdue warnings;
- unresolved прошлых business days.

Operational banner по outbox даёт переход к списку DEAD с действиями `Повторить` и `Подтвердить проблему`; acknowledged DEAD не считается активной аварией.

Карточка брони показывает только допустимые действия:

```text
WAIT
OPEN
UNDO OPEN
CLOSE
CANCEL
EDIT
CHANGE TIME
ADD TABLE
REMOVE TABLE
REPLACE TABLE
```

---

# 52. Editor UI

Отдельный admin route.

Минимум:

- hall selector;
- canvas;
- add table;
- add static element;
- selection;
- properties panel;
- drag;
- resize;
- rotate;
- archive;
- undo;
- redo;
- dirty state;
- save;
- stale revision conflict.

До Save изменения остаются draft.

---

# 53. Venue onboarding

Superadmin UI в v1 нет.

CLI:

```text
create-venue
reset-password
suspend-venue
enable-venue
```

`create-venue` валидирует IANA timezone через `zoneinfo`; неизвестный timezone отклоняется. Поскольку UTC-offset transitions/DST не поддержаны v1, единый helper сканирует offset по UTC-часам на rolling horizon **400 дней** от момента проверки. Если offset меняется хотя бы один раз, production venue создать нельзя, пока отдельная DST-спецификация не включена. Тот же helper запускается периодически (как минимум ежедневно после старта приложения/обновления tzdata) для всех active venue; обнаружение будущего transition поднимает `/health/ops` alert задолго до попадания даты в booking horizon. Для `Europe/Moscow` и других зон без предстоящего перехода это не мешает работе.

По умолчанию CLI генерирует криптографически случайный первоначальный admin password и выводит его один раз; ручной ввод остаётся явной опцией.

`create-venue` создаёт:

- venue;
- booking counter;
- admin account;
- базовые schedule rows;
- первый hall при необходимости.

`reset-password` инвалидирует все sessions venue.

---

# 54. Обязательные конкурентные тесты

## 54.1. Same slot

N параллельных бронирований одного table/time:

```text
ровно 1 success
остальные conflict
```

## 54.2. Public idempotency

Потерянный успешный HTTP-ответ + повтор с тем же `Idempotency-Key` и тем же payload возвращает ту же бронь и не создаёт вторую.

Тот же key с другим payload → `409 IDEMPOTENCY_KEY_REUSED`.

Replay того же key+payload после изменения расписания, выключения online, наступления starts_at или admin-cancel возвращает ту же booking identity и не запускает новые creation validations.

## 54.3. Live operation vs public create

Ранний OPEN/reseat и параллельное публичное создание на тот же стол не могут оба пройти, если их фактические/плановые интервалы несовместимы.

Тест обязан доказать, что `tables FOR UPDATE` у live-операции и повторная валидация после locks закрывают гонку, которую не покрывает один exclusion constraint.

## 54.4. Adjacent

```text
20:00–22:00
22:00–00:00
```

обе проходят.

## 54.5. Overlap

```text
20:00–22:00
21:55–23:00
```

вторая отклоняется.

## 54.6. Booking vs block

Overlap запрещён.

## 54.7. Multi-table atomicity

Если конфликтует один стол — rollback всей операции.

## 54.8. Cross-table operations

Перекрёстные операции с tables 5/6:

- не зависают;
- следуют единому lock order;
- deadlock retry работает.

## 54.9. Archive vs booking

Невозможно получить `archived table + новая active booking` из конкурентных transactions.

## 54.10. Capacity vs booking/mutation

Невозможно получить состояние, нарушающее capacity из-за конкурентного уменьшения `table.capacity` не только при create, но и при `party_size`, `change-time`, add/remove/replace. Тесты обязаны покрывать как минимум:

- capacity decrease vs public/admin create;
- capacity decrease vs party_size increase;
- capacity decrease vs NEW/WAITING change-time;
- capacity decrease vs add/remove/replace table;
- **capacity decrease vs capacity decrease** в двух разных halls, когда одна multi-table booking зависит от суммы capacity обоих столов.

Booking-vs-table сценарии сериализуются table `FOR SHARE/FOR UPDATE` из §32.3. Cross-hall layout-save vs layout-save дополнительно сериализуется venue layout advisory lock из §31.

## 54.11. Schedule vs booking

Невозможно успешно создать бронь по уже неактуальному расписанию из-за конкурентного schedule update.

## 54.11a. Kill switch responsiveness

Параллельный поток public create не держит `venues` row lock и не должен блокировать `online_booking_enabled=false` до `lock_timeout`. После commit kill-switch новые запросы, ещё не прошедшие final gate, получают `ONLINE_BOOKING_DISABLED`; допускается только явно описанный in-flight хвост §32.2.

## 54.12. Cancel vs time change

Одна операция побеждает, вторая получает stale/state error.

Невозможно:

```text
CANCELED + active BOOKING occupancy
```

## 54.13. Close truncation

Никогда не создаётся `ends_at <= starts_at`.

## 54.14. Cross-tenant FK

Нельзя привязать booking venue A к table venue B. Нельзя создать `booking_live_tables` с `business_date`, отличным от business_date его booking.

## 54.14a. Admin create idempotency

Double-click/сетевой retry `POST /admin/bookings` с одинаковым key+payload создаёт ровно одну бронь; тот же key с другим payload → `IDEMPOTENCY_KEY_REUSED`.

## 54.14b. WALK_IN atomicity

Параллельный WALK_IN create+open и public/admin create на несовместимый table/time не могут оба успешно commit.

## 54.15. Constraint conflict mapping

Immediate exclusion violation `23P01` на insert/update корректно маппится в `409 BOOKING_CONFLICT`, transaction полностью откатывается и не оставляет booking/event/outbox частично записанными.

---

# 55. Domain unit/property tests

Для алгебры occupancy segments, `truncate_segment_at`, capacity sweep и time/schedule edge cases используются property-based тесты Hypothesis в дополнение к фиксированным unit cases.

Service/domain слой получает время через единый DB-time helper; в тестах он подменяется детерминированным значением. Production path использует `clock_timestamp()` после locks.

Обязательно:

- shift across midnight;
- exceptions;
- current business date;
- neighbor schedule overlap;
- weekly/exception open+close boundaries строго на 5-минутной сетке, seconds/fraction=0;
- ceil to 5 min;
- 2 calendar months;
- state machine;
- UNDO_OPEN;
- replace before start;
- replace during interval;
- early OPEN;
- overdue OPEN dynamic availability;
- overdue WAITING dynamic availability;
- stale previous-shift OPEN does not block next shift;
- close before future segment start;
- close inside segment;
- close after segment;
- multi-table capacity sweep;
- schedule change не меняет booking shift snapshot;
- NEW/WAITING end-only extension после starts_at;
- просроченный NEW/WAITING требует полного reschedule;
- WALK_IN create+open атомарен;
- WALK_IN с остатком смены <45 минут разрешён только на весь оставшийся grid-aligned интервал до `shift_ends_at`;
- WALK_IN посреди смены на 10/30 минут отклоняется, если до `shift_ends_at` доступно >=45 минут;
- WALK_IN near-close использует grid-aligned shift end; невалидный non-grid schedule не может попасть в domain через persistence/API;
- advisory key helper стабилен по test vectors, разводит `schedule/layout` namespaces и не приводит bigint `venue_id` к int4;
- UNDO_OPEN: guest text edit не блокирует, table/time/party-size mutation блокирует;
- hall archive guard;
- VK/WALK_IN phone optional, ONLINE/PHONE/OTHER требуют phone;
- request IP HMAC cleanup TTL;
- OPEN after plan end rejects time/table mutation.

---

# 56. Worker tests

Обязательно:

- claim;
- lease;
- retry;
- DEAD;
- dedup key;
- stable provider dedup id;
- worker crash after provider send but before `SENT`;
- expired PROCESSING returns to queue;
- manual retry DEAD сохраняет provider dedup id;
- acknowledge DEAD убирает active ops alert, но не удаляет row;
- expired notification → SKIPPED без HTTP;
- CANCELED/OPEN/CLOSED/anonymized booking перед send → SKIPPED с правильным `skip_reason`;
- NEW/WAITING booking, которая уже пересекла `starts_at`, но ещё внутри late-grace/TTL, **не** скипается только из-за времени старта;
- manual retry не продлевает TTL;
- длительный backlog после восстановления не отправляет залп stale-уведомлений.

---

# 57. Frontend tests

Vitest + Testing Library:

- public form;
- API error mapping;
- BOOKING_CONFLICT;
- BOOKING_STALE;
- LAYOUT_STALE;
- status buttons;
- UNDO_OPEN visibility;
- Konva transform normalization;
- editor dirty/save;
- LAYOUT_STALE сохраняет local draft до явного reload/discard;
- online booking disabled;
- table `is_bookable` operational toggle работает без `layout_revision` bump;
- admin time renders in venue timezone;
- DEAD outbox retry/ack UI.

---

# 58. Playwright E2E

Минимум:

- full ONLINE booking;
- two clients race for one slot;
- manual booking;
- atomic WALK_IN create+open;
- WALK_IN менее чем за 45 минут до конца смены;
- WAIT → OPEN → CLOSE;
- cancel;
- add table;
- replace;
- editor save;
- two admin browser contexts realtime;
- SSE reconnect;
- online booking off / responsive kill switch.

---

# 59. Transaction checklist

После любой booking mutation committed state обязан удовлетворять:

1. tenant FK корректны, включая live `business_date`;
2. booking interval находится внутри сохранённого shift snapshot;
3. active occupancies не пересекаются;
4. CANCELED не имеет active BOOKING occupancies;
5. CLOSED не имеет live rows;
6. OPEN внутри snapshot-смены имеет хотя бы один live table;
7. один table не live у двух OPEN-бронирований одного business day;
8. capacity достаточна;
9. version увеличена ровно один раз;
10. booking event записан;
11. NOTIFY поставлен внутри transaction;
12. outbox, если необходим, создан в той же transaction.

---

# 60. Исполняемый audit инвариантов

Критичные инварианты существуют не только в тексте. В проекте есть SQL/integration audit queries, выявляющие как минимум:

- overlapping active occupancies;
- CANCELED с active BOOKING occupancy;
- CLOSED с live rows;
- OPEN, у которой `operation_now` попадает в сохранённый shift snapshot, без live rows;
- cross-tenant broken references;
- зависшие PROCESSING outbox lease;
- DEAD без acknowledgement для active operational alert.

Они запускаются после соответствующих PostgreSQL integration tests, в pre-release audit и периодической production maintenance job. Production audit только обнаруживает/алертит и ничего не исправляет автоматически.

---

# 61. Stale OPEN после конца смены

Если OPEN не закрыли до `booking.shift_ends_at`:

- status автоматически не меняется;
- booking остаётся OPEN;
- старый live row остаётся привязан к старому business_date;
- он не блокирует live/open операции следующего business day;
- он не создаёт online dynamic block после сохранённого `booking.shift_ends_at`;
- admin UI показывает бронь в unresolved list;
- сотрудник позже может нажать CLOSE;
- после plan end нельзя продлить `ends_at` или менять live tables в release v1;
- CLOSE запишет фактический поздний `closed_at`, но не продлит старые plan occupancies.

---

# 62. Архитектурные запреты

Без изменения спецификации нельзя:

- заменить PostgreSQL на SQLite в production;
- убрать DB exclusion constraint;
- доверять frontend availability как финальной проверке;
- автоматически подтверждать/отменять гостя;
- давать гостю self-edit/self-cancel;
- автоматически менять `booking.ends_at`, потому что гости засиделись;
- в release v1 продлевать OPEN после plan end или делать post-end reseat;
- создавать магический overrun buffer 30/60 минут;
- смешивать live fact и plan occupancy в одну сущность;
- вводить admin override «на всякий случай»;
- вводить max online booking duration без нового продуктового решения;
- пересчитывать lifecycle существующей брони по изменённому расписанию вместо её сохранённого shift snapshot;
- добавлять CRM guests «на будущее»;
- добавлять сотрудников/roles;
- добавлять Redis/Celery/Kafka/Kubernetes без конкретной измеримой необходимости;
- доверять клиентскому `venue_id`;
- доверять клиентскому времени;
- использовать last-write-wins для booking/layout;
- хранить session/VK token plaintext;
- хранить idempotency payload как обычный неключевой hash;
- хранить raw client IP в bookings/events ради abuse detection;
- физически удалять отменённые bookings;
- хранить arbitrary HTML/JS/SVG в layout.

---

# 63. Этапы разработки

## Stage 1 — Foundation

- monorepo;
- backend/frontend;
- Docker Compose dev;
- PostgreSQL;
- Alembic;
- отдельные DB roles `migrator/app`;
- dev/prod DB timeouts;
- config;
- health;
- structured log redaction baseline;
- trusted proxy baseline;
- базовые security headers/CSP config;
- CI;
- OpenAPI TS generation.

Готово, когда clean install поднимается одной понятной командой.

## Stage 2 — Tenant + Auth + CLI

- venues (`online_booking_enabled=false` default);
- admin account;
- sessions с 30-day absolute TTL;
- Argon2id + bounded pool;
- pre-Argon2 per-IP limit + login/IP rate limit;
- cookies;
- login/logout/logout-all;
- CLI с rolling 400-day timezone capability validation и generated password default;
- periodic timezone capability check helper + health signal;
- isolation tests.

## Stage 3 — Schedule + Business Day

- weekly schedules;
- exceptions;
- cross-midnight;
- current business day;
- schedule advisory lock helper;
- overlap validation;
- 5-minute schedule-boundary validation + DB CHECK;
- admin schedule UI.

## Stage 4 — Halls + Tables + Read-only Canvas

- halls;
- tables;
- capacity;
- geometry;
- bookability + отдельный `PATCH /tables/{id}` operational toggle;
- public/admin canvas;
- list mode;
- static element schema;
- CLI/import команды для seed схемы из валидируемого JSON, чтобы реальная booking-вертикаль не зависела от готовности Konva editor.

## Stage 5 — Booking Core

- bookings + shift snapshot;
- HMAC idempotency fields;
- short-lived abuse fingerprint fields;
- counter;
- occupancies;
- btree_gist;
- exclusion;
- events;
- status checks;
- locks/retries;
- capacity-sensitive mutation lock rules;
- domain functions.

Stage считается завершённым только после PostgreSQL concurrency suite.

## Stage 6 — Public Booking

- availability;
- date/hall/table/time;
- guest form;
- privacy consent;
- booking transaction;
- public HMAC idempotency;
- conflict UX;
- success;
- POST burst + rolling/day soft limits;
- отдельный public GET/availability limit;
- honeypot;
- CAPTCHA adapter/feature flag hook;
- responsive online_booking_enabled kill switch;
- aggregate per-venue ONLINE-create abuse alert с cooldown (без auto-disable; только ops/status/admin banner, без VK outbox type).

## Stage 7 — Admin Booking Book

- business-day screen;
- list/map;
- card;
- manual booking + admin HMAC idempotency;
- atomic WALK_IN create+open без обязательного телефона;
- manual `source=VK` допускает отсутствие телефона без фиктивного значения;
- source;
- booking filters/pagination/phone search;
- same-network-fingerprint investigation;
- edit guest fields;
- expected_version.

Bulk-cancel в release v1 не входит.

## Stage 8 — Lifecycle + Live State

- WAITING;
- OPEN;
- early OPEN;
- UNDO_OPEN;
- CLOSE;
- CANCEL;
- live tables;
- overdue OPEN/WAITING dynamic availability;
- unresolved previous shifts;
- явный запрет post-end table/time mutations.

## Stage 9 — Realtime

- NOTIFY in transaction;
- LISTEN;
- SSE;
- heartbeat;
- bounded queue;
- resync;
- Caddy config;
- TanStack invalidation.

К концу Stage 9 существует первая рабочая вертикаль, которую можно гонять на seed-схеме реального бара без визуального editor.

## Stage 10 — Hall Editor

- Konva editor;
- create/archive table;
- create/rename/archive hall;
- drag/resize/rotate;
- static elements;
- Zustand;
- undo/redo;
- full-state save только editor-owned полей;
- layout revision;
- venue-scoped layout advisory serialization;
- stale UX без автоматической потери draft;
- `is_bookable` остаётся отдельной operational mutation.

## Stage 11 — Multi-table + Re-seat + Time Changes

- add/remove;
- replace;
- reseat только до plan end;
- NEW/WAITING reschedule;
- OPEN extend/shorten только до текущего plan end;
- capacity sweep.

Post-end reseat и продление уже просроченного OPEN отложены за пределы release v1.

## Stage 12 — VK Outbox

- integration config;
- encrypted token;
- minimal non-PII outbox payload;
- TTL / late-grace / SKIPPED semantics;
- SKIPPED metrics by reason;
- worker lease;
- retry/dead;
- manual retry/acknowledge;
- provider dedup;
- per-venue send throttle;
- outbox health/status banner;
- ONLINE booking notification — единственный outbox delivery type release v1; abuse-alert через VK не входит в v1.

## Stage 13 — Security + Retention Hardening

Stage 13 не является первым моментом, когда появляются security basics: DB roles/timeouts, CSP baseline, proxy trust, login limits и Argon2 pool уже введены на Stage 1–2. Здесь они проверяются и доводятся до production.

- Origin/CSRF verification;
- CSP/security headers verification;
- rate-limit abuse tests;
- log redaction verification;
- PII/event audit;
- cleanup jobs;
- explicit **terminal-only** booking anonymization;
- request-IP HMAC TTL cleanup;
- backup-aware retention rules;
- invariant audit queries;
- production env validation.

## Stage 14 — Deploy + Backup + Recovery

- production Compose;
- Caddy;
- HTTPS;
- hourly backup;
- encrypted offsite;
- separate recovery-secret bundle;
- restore test including VK-key decrypt;
- retention catch-up после restore;
- RPO/RTO drill;
- staging deploy rehearsal;
- migration rollback/recovery runbook;
- uptime;
- log rotation.

Stage 14 обязан быть завершён **до первых реальных ПД/броней**, а не после пилота. Кроме технической готовности Stage 14, до ввода первых реальных имён/телефонов должны быть закрыты legal gates §65: пункты 1–3, 5 и 6.

## Stage 15 — Pilot одного venue

**Precondition:** Stage 14 завершён и legal gates §65 (1–3, 5, 6) документированно закрыты. В терминах этой спецификации «launch с ПД» начинается с **первого ввода реальных персональных данных**, даже если `online_booking_enabled=false` и публичный ONLINE ещё не включён.

- один реальный бар;
- стартово `online_booking_enabled=false`;
- админка используется параллельно с текущим способом учёта;
- проверяются mobile admin, смена через полночь, реальные столы/layout, backup/restore, VK retry/dead/skip и operational runbook;
- отдельно собираются `open_overrun`, `overdue_waiting_block_table_minutes` и outbox `SKIPPED by reason`;
- для `open_overrun`: как часто гости остаются после plan end, возникает ли следующая бронь на том же столе и какое действие реально выбирает сотрудник;
- отдельно фиксируются abuse-инциденты и трудозатраты на ручную отмену; если это становится заметной операционной болью, bulk-cancel with preview — первый кандидат после пилота, но не release blocker;
- после устранения P0/P1 включается ONLINE для ограниченного пилота;
- после стабильного пилота — final audit.

## Stage 16 — Final Release Audit

Обязательно:

- fresh install;
- migrations;
- backend suite;
- frontend suite;
- Playwright;
- PostgreSQL concurrency;
- tenant isolation;
- backup + recovery secrets restore;
- security/PII pass;
- mobile public flow;
- mobile admin flow;
- VK failure/retry/TTL test.

Известные P0/P1 блокируют release. Stage 16 также проверяет, что production legal gates из §65 закрыты документированными решениями.

# 64. Definition of Done v1

Takeplace v1 готов, когда:

- гость может выбрать конкретный стол и свободный интервал;
- бронь создаётся сразу;
- double booking технически невозможен;
- ночная смена работает как один business day;
- админ может создать ручную бронь;
- source хранится;
- `NEW / WAITING / OPEN / CLOSED / CANCELED` работают по state machine;
- плановое время отделено от фактического визита;
- раннее открытие работает;
- overrun не требует магического буфера;
- stale вчерашний OPEN не ломает следующий business day;
- multi-table работает;
- add/remove/replace/reseat работают до plan end;
- capacity invariant сохраняется;
- редактор схемы работает;
- layout stale conflict не теряет данные;
- realtime работает на нескольких устройствах;
- ONLINE booking создаёт VK outbox;
- ошибка VK не влияет на бронь;
- отказ/DEAD VK-outbox виден monitoring и баннером в админке;
- tenant isolation покрыта тестами;
- hourly offsite backup настроен;
- restore проверен;
- privacy consent реализован;
- существующая бронь сохраняет shift snapshot при изменении расписания;
- public/admin create idempotency выдерживает retry/double-click;
- WALK_IN создаётся и открывается атомарно, телефон для VK/WALK_IN необязателен; WALK_IN можно корректно отразить и при остатке смены менее 45 минут;
- DEAD outbox можно retry/acknowledge без удаления истории;
- RPO/RTO restore drill пройден вместе с recovery secrets;
- stale VK notifications переходят в SKIPPED и не флудят чат после outage, а срочная NEW/WAITING не теряется ровно в `starts_at`;
- capacity-sensitive mutations сериализуются с изменением table capacity;
- обязательные concurrency tests проходят.

---

# 65. Production gates до первых реальных ПД / final release

Эти пункты не блокируют начало разработки. Для целей этой спецификации **production launch с ПД начинается в момент первого ввода реальных имени/телефона в Stage 15**, а не только при включении публичного ONLINE. Поэтому пункты 1–3, 5 и 6 обязательны до Stage 15; оставшиеся gates закрываются до соответствующей production-функции/final release:

1. целевая продуктовая модель — оператором ПД гостя выступает соответствующее заведение (`Venue`); до запуска это подтверждается актуальной юридической проверкой, одновременно документируется роль Takeplace/владельца инфраструктуры как обработчика по поручению либо иная юридически корректная роль, если проверка потребует её изменить;
2. финальный юридический текст политики обработки ПД и форма consent согласованы именно с этой моделью ролей: чьё имя указано гостю, кто публикует политику и принимает обращения субъектов;
3. срок хранения/анонимизации персональных данных, включая semantics encrypted backup retention и данных, уже отправленных в рабочую VK-беседу;
   - юрист отдельно фиксирует, требуется ли и в какой форме уведомление Роскомнадзора для выбранной модели оператора ПД, и gate считается закрытым только после выполнения применимого требования;
   - юрист отдельно фиксирует необходимость и форму договора/поручения обработки между заведением как оператором и Takeplace/владельцем инфраструктуры как обработчиком; если он требуется, договор должен быть оформлен до первых реальных ПД;
4. актуальная проверка VK API:
   - community token;
   - `peer_id` групповой беседы;
   - допустимый `random_id`/dedup semantics;
5. выбранный российский VPS/provider;
6. выбранный offsite backup storage и подтверждена допустимость его географии/юрисдикции для ПД;
7. production domain;
8. smoke-test восстановленной БД вместе с recovery-secret bundle;
9. подтверждено восстановление/расшифровка VK token по `encryption_key_version`;
10. фактическая проверка VK-уведомления в рабочей беседе и runbook-сверки VK ↔ восстановленные ONLINE-брони в пределах RPO.

Все остальные основные архитектурные решения v1 считаются зафиксированными этим документом.


---

# 66. Работа с coding-агентами

Полный `PROJECT-SPEC.md` остаётся единственным источником истины, но не должен без необходимости целиком вставляться в каждую задачу агента.

В репозитории поддерживается короткий `IMPLEMENTATION-GUARDRAILS.md` (ориентир: до ~200 строк), который **не вводит новых правил**, а только конспектирует и ссылается на канонические разделы спеки:

- tenant isolation;
- state machine;
- time/shift snapshot;
- lock order и `operation_now`;
- table/capacity locking;
- exclusion/idempotency;
- PII/logging;
- «не входит в v1».

Для каждого Stage задача агенту должна включать `IMPLEMENTATION-GUARDRAILS.md` + только релевантные разделы полного спека + acceptance/tests этого Stage. Если краткий guardrails-файл расходится с `PROJECT-SPEC.md`, побеждает полный spec и guardrails исправляется.

## 66.1. Freeze после v1.3.3

После фиксации v1.3.3 `PROJECT-SPEC.md` считается архитектурно замороженным. Новая версия спеки создаётся только если реализация, PostgreSQL concurrency-suite, пилот или юридический production-gate обнаружили подтверждённое противоречие/дефект, который нельзя исправить в коде без изменения обещанного поведения. Новые «а что если» без воспроизводимого сценария оформляются как issue/backlog, а не как очередная ревизия архитектуры.
