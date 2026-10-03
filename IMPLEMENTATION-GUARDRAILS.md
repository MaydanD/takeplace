# Takeplace — IMPLEMENTATION-GUARDRAILS.md

**Назначение:** короткий operational-конспект для coding-агентов.  
**Источник истины:** `PROJECT-SPEC.md` v1.3.3.  
**Правило:** этот файл не вводит новых требований. При расхождении побеждает полный spec.

---

## 1. Не менять продукт молча
- Код/тест/задача конфликтуют со spec → сначала меняется spec.
- Не реализовывать будущие Stage «раз уж рядом».
- PostgreSQL 15+; concurrency/locks/exclusion не проверяются на SQLite.
- Production v1: один VPS, Docker Compose, FastAPI, React/TS.
- Канон: §0, §3, §45–46, §62.

## 2. Tenant isolation
- `Venue` = tenant; admin `venue_id` только из session.
- Чужой tenant → `404`; клиентский `venue_id` не доверенный.
- Критичные ссылки — tenant-safe composite FK.
- Каждый admin endpoint имеет cross-tenant test.
- Канон: §1.1, §7, §54.14.

## 3. Время, timezone, shift snapshot
- Клиентское время не источник истины.
- После бизнес-критичных locks один раз: `operation_now = clock_timestamp()`.
- `transaction_timestamp()` не использовать как business mutation time.
- Абсолютные timestamps — `timestamptz`; admin UI показывает timezone venue.
- Production v1 принимает только timezone без DST в rolling capability window.
- Schedule boundaries: минуты кратны 5, seconds/fraction = 0.
- Booking хранит `shift_starts_at/shift_ends_at`; schedule update snapshot не переписывает.
- Канон: §4–5, §6.6, §32, §53, §55.

## 4. Business date / schedule
- Смена принадлежит дате начала; ночная смена = один `business_date`.
- На business date максимум одна непрерывная смена; соседние смены не пересекаются.
- Create/reschedule используют shared schedule advisory lock; schedule mutation — exclusive.
- Канон: §5, §32.1–32.2.

## 5. State machine
```text
NEW -> WAITING | OPEN | CANCELED
WAITING -> OPEN | CANCELED
OPEN -> CLOSED
```
- `UNDO_OPEN` — только по §23.
- `OPEN -> CANCELED`, `CLOSED -> *`, `CANCELED -> *` запрещены.
- No-show = cancellation reason, не статус; автопереходов нет.
- Status ↔ timestamp CHECK обязателен.
- Канон: §9–11, §21–25.

## 6. Plan != live fact
- `table_occupancies` = план/резерв ресурса.
- `booking_live_tables` = фактическое размещение OPEN.
- Не объединять эти сущности.
- Active BOOKING/BLOCK защищает exclusion constraint `[start,end)`.
- Один table не live у двух bookings одного business day.
- Early/overdue состояние отражается dynamic overlay без magic buffer.
- Канон: §6.8–6.9, §12–17, §61–62.

## 7. 5-minute grid и segments
- `booking.starts_at/ends_at` всегда на 5-минутной сетке.
- Schedule open/close boundaries тоже на сетке.
- `opened_at/closed_at/reseat` могут быть в любую секунду.
- Любая фактическая обрезка BOOKING segment идёт через `truncate_segment_at()`.
- Канон: §15, §44, §55.

## 8. WALK_IN
```text
grid_shift_ends_at = floor_to_5_minutes(shift_ends_at)
assert grid_shift_ends_at == shift_ends_at
required_duration = min(45 min, grid_shift_ends_at - starts_at)
ends_at - starts_at >= required_duration
```
- Только `source=WALK_IN` может atomic `create + OPEN`.
- `starts_at = ceil_to_5_minutes(operation_now)`; `ends_at <= grid_shift_ends_at`.
- <45 минут разрешается только у конца смены.
- Телефон необязателен.
- Канон: §19–19.1, §55, §58.

## 9. Lock order
```text
venue read
-> operation advisory lock (если нужен)
-> booking(s) ASC
-> hall(s) ASC
-> tables ASC
-> booking counter (create only)
-> occupancies
-> event/outbox
```
- Public create: tables `FOR SHARE`; live/factual mutation: `FOR UPDATE`.
- Capacity-sensitive mutation lock-ит весь набор tables, влияющий на решение, минимум `FOR SHARE`.
- После locks повторить time/capacity/bookability/conflict validation.
- Layout-save сериализуется venue layout advisory lock.
- Канон: §20, §22, §31–33, §54.

## 10. Advisory key helper
- Один helper для всех advisory locks.
- Single-`bigint` key = stable signed 64-bit digest `takeplace:{namespace}:{venue_id}`.
- Namespace минимум `schedule` и `layout`; `venue_id` не приводить к int4.
- Hash collision допустима только как лишняя сериализация, не как потеря correctness.
- Helper имеет стабильные unit test vectors.
- Канон: §32.1, §55.

## 11. DB — последний арбитр
Обязательно держать в БД: exclusion, `ends_at > starts_at`, booking/schedule grid, tenant FK, status/timestamp, occupancy-kind, live uniqueness, shift snapshot bounds, source/idempotency invariants. Python pre-check нужен для UX, но не заменяет constraints.
- Канон: §12, §43–45.

## 12. Idempotency
**Public:** lookup `(venue,key)` идёт до mutable validation; fingerprint = HMAC-SHA-256; same key+payload → replay; same key+different payload → `IDEMPOTENCY_KEY_REUSED`; new=`201`, replay=`200`; `23505` после rollback → повторный lookup.

**Admin:** та же семантика через `admin_idempotency_key/admin_request_hmac`. Seed/import/test fixtures для non-ONLINE booking тоже генерируют пару через общий factory/helper; CHECK ради фикстур не ослаблять.
- Канон: §18.2, §19, §36, §54.2/54.14a.

## 13. Capacity
- Один стол: `party_size <= capacity`; несколько: `<= SUM(capacity)`.
- Проверка по всем сегментам постоянного набора tables; OPEN ещё проверяет live capacity.
- Capacity-sensitive mutation держит нужные table locks.
- Cross-hall layout-save сериализуется layout advisory lock.
- Канон: §14, §31–32, §54.10.

## 14. Operational bookability vs editor
- `is_bookable` не входит в layout draft.
- `table.is_bookable` меняется через `PATCH /tables/{id}`; endpoint не меняет geometry/number/capacity/shape.
- Toggle не увеличивает `layout_revision`.
- Editor-owned поля меняются через `PUT /halls/{id}/layout`.
- Канон: §29–31, §35.

## 15. Post-end scope v1
Не поддерживать: post-end reseat, post-end add/replace, продление уже просроченного OPEN. После plan end OPEN можно фактически завершить (`CLOSE`). Не добавлять hidden overrun buffer и авто-close. `open_overrun` измеряется пилотом.
- Канон: §20.3, §26.3, §49, §62, Stage 15.

## 16. PII / logging / retention
- Не логировать: полный телефон, guest comment, password, raw session token, VK token, raw client IP.
- `booking_events.payload` без PII; abuse IP = short-lived HMAC, не raw IP.
- Idempotency payload fingerprint = HMAC.
- Booking anonymization только terminal (`CLOSED/CANCELED`) и очищает поля по §42/44.
- Backup retention/recovery учитывают ПД отдельно.
- Канон: §39–42, §48, §65.

## 17. VK outbox
- Release v1 delivery type: ONLINE booking notification.
- Booking + outbox создаются атомарно; ошибка VK booking не откатывает.
- Outbox payload — минимальные non-PII ссылки; worker грузит актуальную booking перед send.
- At-least-once + stable provider dedup id; TTL/late-grace обязательны.
- Stale → `SKIPPED`; DEAD → retry/acknowledge.
- Abuse detector НЕ создаёт VK outbox в v1: только `/health/ops`, `/system/status`, admin banner.
- Канон: §6.11, §38, §40, §47/49, Stage 12.

## 18. Kill switch
- `online_booking_enabled=false` должен оставаться быстрым при abuse.
- Public create не держит `venues FOR SHARE`; перед insert есть final plain SELECT gate.
- Допускается только явно описанный in-flight хвост уже прошедшего final gate запроса.
- Канон: §32.2, §54.11a.

## 19. API contracts
- Frontend ориентируется на machine-readable `code`, не русский текст.
- Public API не раскрывает PII/чужие booking details.
- Public/admin create: `201` new, `200` replay.
- Schedule/capacity/archive используют канонические 409 codes §36.
- Канон: §34–36.

## 20. Realtime
- Mutations только HTTP; realtime = SSE + LISTEN/NOTIFY.
- `pg_notify` внутри transaction, payload короткий; клиенты refetch/invalidate.
- Потеря LISTEN → reconnect + `resync`; public постоянный realtime не нужен.
- Канон: §37.

## 21. Security baseline
- Argon2id вне async loop, bounded pool; login rate limit до hash verify.
- Raw session token только у клиента; DB хранит hash.
- Cookie `__Host-*`, HttpOnly, Secure, SameSite=Strict; state-changing admin requests проверяют Origin.
- Uvicorn наружу не публикуется; forwarded headers доверяются только Caddy/internal network.
- CSP/XSS baseline идёт с ранних Stage, не ждёт Stage 13.
- Канон: §39, Stage 1–2/13.

## 22. Не входит в v1
Не добавлять без изменения spec: admin override; guest account/self-edit/self-cancel; CRM guests; employees/roles; Redis/Celery/Kafka/Kubernetes; max online duration; post-end reseat/extension; bulk-cancel; magic overrun buffer; auto status transitions; raw IP storage; arbitrary HTML/JS/SVG; last-write-wins.
- Канон: §1.4, §62.

## 23. Testing
- Concurrency-critical tests — только настоящий PostgreSQL.
- Stage 5 не готов без concurrency suite.
- Property tests: schedule/time/segments/capacity.
- Каждый concurrency/state/tenant bug получает regression test.
- Проверять `23P01`, `23505`, deadlock retry, stale version/revision.
- Event order = `booking_events.id`; индекс `(booking_id,id)`.
- Канон: §45, §54–60.

## 24. Stage discipline
Каждая задача агенту получает: (1) этот файл; (2) релевантные разделы spec; (3) acceptance текущего Stage; (4) обязательные tests. После Stage: tests зелёные, migrations поднимают clean DB, drift от spec отсутствует. Новые решения не принимаются молча.
- Канон: §63, §66.

## 25. Freeze
`PROJECT-SPEC.md` v1.3.3 заморожен. Новая версия только при подтверждённом дефекте из реализации, PostgreSQL concurrency-suite, E2E/integration, пилота или legal/production gate. Теоретические новые edge cases без воспроизводимого сценария → backlog, не новая ревизия.
- Канон: §66.1.
