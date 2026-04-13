# Mango API Connectivity & Inventory Audit

**Date**: 2026-04-13  
**Script**: `scripts/mango_connectivity_check.py`  
**Method**: Real HTTP calls via existing `MangoClient.from_settings()`

---

## 1. Executive Summary

| Check | Result |
|---|---|
| Real connection to Mango API | **YES** |
| Auth / signature working | **YES** |
| Inventory data available | **YES** (2 lines) |
| Usable for agent-number binding | **YES** |

Оба endpoint вернули HTTP 200. Аутентификация SHA256 работает корректно.  
Найдено **2 входящих линии**, одна из которых явно обозначена "ДЛЯ ИИ менеджера".  
Extensions (внутренние номера) — **пусто** (API вернул `users: []`).  
Webhook credentials не заданы — webhook integration пока невозможна без установки секрета.

---

## 2. Найденная конфигурация

| Параметр | Статус | Примечание |
|---|---|---|
| `MANGO_API_KEY` | ✅ Заполнен (32 символа) | Маска: `xj***a0` |
| `MANGO_API_SALT` | ✅ Заполнен (32 символа) | Маска: `4b***ai` |
| `MANGO_API_BASE_URL` | не задан → дефолт | `https://app.mango-office.ru/vpbx` |
| `MANGO_FROM_EXT` | ❌ Пуст | Нужен для исходящих звонков |
| `MANGO_WEBHOOK_SECRET` | ❌ Пуст | Нужен для верификации webhook |
| `MANGO_WEBHOOK_SHARED_SECRET` | ❌ Пуст | Нужен как fallback |
| `TELEPHONY_PROVIDER` | `stub` | Mango не активен в runtime сейчас |

Для read-only аудита (входящие линии) credentials достаточны.  
Для outbound и webhook integration нужны дополнительные переменные.

---

## 3. Реально выполненные запросы

### 3.1 POST `/incominglines`

```
Request:  POST https://app.mango-office.ru/vpbx/incominglines
Headers:  Content-Type: application/x-www-form-urlencoded
Body:     vpbx_api_key=<masked>  sign=<sha256>  json={}
Response: HTTP 200  (1278 ms)
```

Top-level keys в ответе: `lines`, `result`

### 3.2 POST `/config/users/request`

```
Request:  POST https://app.mango-office.ru/vpbx/config/users/request
Headers:  Content-Type: application/x-www-form-urlencoded
Body:     vpbx_api_key=<masked>  sign=<sha256>  json={}
Response: HTTP 200  (515 ms)
```

Top-level keys в ответе: `users`  
Результат: `users: []` — пустой массив.

---

## 4. Какие данные Mango реально отдаёт

### 4.1 Входящие линии (`/incominglines`) — 2 объекта

**Линия 1 — основная**

| Поле | Значение |
|---|---|
| `line_id` | `405519147` |
| `number` | `79585382099` |
| `name` | `null` |
| `comment` | `null` |
| `region` | `98` (код региона Mango) |
| `schema_id` | `11071988` |
| `schema_name` | `"По умолчанию"` |

**Линия 2 — для AI менеджера** ⭐

| Поле | Значение |
|---|---|
| `line_id` | `405622036` |
| `number` | `79300350609` |
| `name` | `null` |
| `comment` | `""` (пусто) |
| `region` | `98` |
| `schema_id` | `11086409` |
| `schema_name` | `"ДЛЯ ИИ менеджера"` |

Линия 2 явно обозначена в Mango-кабинете как предназначенная для AI агента.

### 4.2 Расширения / сотрудники (`/config/users/request`) — 0 объектов

API вернул корректный HTTP 200, но `users: []`.  
Возможные причины:
- В этом аккаунте нет настроенных IP-телефонии / SIP-пользователей
- Аккаунт является "простым" VATS без внутренних номеров
- Endpoint требует дополнительных параметров для фильтрации (не задокументировано)

---

## 5. Качество данных

| Аспект | Оценка |
|---|---|
| Уникальный ID линии | ✅ `line_id` (integer) — стабильный |
| Номер телефона | ✅ Присутствует, формат `7XXXXXXXXXX` |
| Display name | ⚠️ `name=null` для обеих линий |
| Human-readable label | ✅ `schema_name` используемо как fallback label |
| Inbound/outbound флаги | ⚠️ Сырой payload не содержит явных флагов — `MangoClient` ставит defaults (`inbound=True`, `outbound=False`) |
| Нормализация номера | ⚠️ Нужна: `79585382099` → `+79585382099` |
| Extension / внутренний номер | ❌ Отсутствует (extensions пусты) |
| Дубликаты | ✅ Нет — deduplicate по `(line_id, number)` |
| Стабильность ID | ✅ `line_id` — числовой идентификатор Mango, не изменяется |

**Вывод по качеству**: данные по линиям **usable with normalization** (нужно добавить `+` к номеру и использовать `schema_name` вместо `name`). Данные по extensions **unusable** (пусто).

---

## 6. Вывод по привязке номера к агенту

**Можно ли уже делать select в админке?** — **ДА** (2 линии есть).

### Рекомендуемый storage contract

```python
# Для каждой привязки Agent → Mango line:
mango_line_id: str           # "405622036"  — provider_resource_id
phone_number: str            # "+79300350609"  — нормализованный (+7...)
display_name: str | None     # schema_name если name=null: "ДЛЯ ИИ менеджера"
is_inbound_enabled: bool     # True
is_outbound_enabled: bool    # False (уточнить в кабинете)
raw_payload_snapshot: dict   # сохранять весь raw dict для будущей диагностики
```

**Ключ привязки**: `mango_line_id` = строковое представление `line_id` из API.  
`phone_number` — вторичный ключ (для отображения), не используй как primary — Mango может переподвязать номер к другой линии.

**Риски**:
- `name` null — нужно использовать `schema_name` или вводить display_name в UI вручную
- `is_outbound_enabled` требует уточнения — Mango не возвращает явный флаг, проверь в кабинете

---

## 7. Вывод по webhook / runtime readiness

| Параметр | Статус |
|---|---|
| `MANGO_WEBHOOK_SECRET` | ❌ Не задан — подпись webhook не будет верифицирована |
| `MANGO_WEBHOOK_SHARED_SECRET` | ❌ Не задан |
| `MANGO_FROM_EXT` | ❌ Не задан — исходящие звонки невозможны |
| Webhook handler | ✅ Код существует (`mango_events.py`, `mango.py`) |
| Event state machine | ✅ `_STATE_ALIASES` покрывает все стандартные события |
| FreeSWITCH media bridge | ⚠️ Disabled (`MEDIA_GATEWAY_ENABLED=false`) |

Живой webhook без реального входящего звонка в тесте не проверить.  
Код обработчика (`MangoEventProcessor`) выглядит полным для стандартных событий:  
`initiating → ringing → answered → bridged → hangup/failed`

Что нужно сделать перед webhook:
1. Задать `MANGO_WEBHOOK_SECRET` в `.env` и Render
2. Задать `MANGO_FROM_EXT` для outbound
3. Прописать публичный URL `/api/mango/webhook` в кабинете Mango

---

## 8. Что было изменено в коде

| Файл | Действие |
|---|---|
| `scripts/mango_connectivity_check.py` | **Создан** — read-only diagnostic script |
| `docs/mango_connectivity_report.md` | **Создан** — этот отчёт |

Рабочий runtime (session_manager, gemini_client, browser_calls) **не тронут**.

---

## 9. Что реально протестировано

| Проверка | Результат |
|---|---|
| DNS resolve `app.mango-office.ru` | ✅ ОК |
| HTTPS handshake | ✅ ОК |
| Auth: SHA256 подпись | ✅ ОК — API принял запросы |
| POST `/incominglines` | ✅ HTTP 200, 2 объекта |
| POST `/config/users/request` | ✅ HTTP 200, 0 объектов |
| Парсинг ответов через `MangoClient` | ✅ ОК — оба метода отработали |
| Masked output (секреты не утекают) | ✅ Проверено вручную |

---

## 10. Что НЕ протестировано

| Пробел | Причина |
|---|---|
| Реальный входящий звонок (webhook) | Нет возможности позвонить на номер в тестовой среде |
| Подпись webhook payload | `MANGO_WEBHOOK_SECRET` пуст |
| Исходящий звонок (originate) | `MANGO_FROM_EXT` пуст, реальный звонок не делаем в аудите |
| Bridge / transfer flow | Требует активной сессии |
| `/config/users/request` с параметрами | Не ясно, нужны ли фильтры — Mango API документация не публична |
| Другие Mango endpoints (CDR, recording) | За рамками текущей задачи |
| Webhook IP allowlist | `MANGO_WEBHOOK_IP_ALLOWLIST` пуст, не проверялось |

---

## 11. Final Verdict

| Пункт | Результат |
|---|---|
| Mango connectivity | ✅ **YES** — оба endpoint HTTP 200 |
| Mango data usability | ✅ **usable with normalization** — 2 линии, нужна нормализация номера и display_name fallback |
| Readiness for next step | ✅ **Ready** — можно делать UI select, сохранять binding по `mango_line_id` |
| Линия для AI агента | ✅ **Определена** — `line_id=405622036`, `+79300350609`, schema "ДЛЯ ИИ менеджера" |

### Следующий практический план (по порядку)

1. **Sync inventory endpoint** — `GET /api/v1/mango/lines` через `MangoClient.list_lines()` (read, no cache needed)
2. **UI select в агент-профиле** — показывать список линий + привязывать `mango_line_id` к агенту
3. **Нормализация номера** — добавить `+` перед `7XXXXXXXXXX` при сохранении
4. **display_name fallback** — `schema_name` если `name=null`
5. **Webhook setup** — задать `MANGO_WEBHOOK_SECRET`, зарегистрировать URL в кабинете Mango
6. **Outbound routing** — задать `MANGO_FROM_EXT`, переключить `TELEPHONY_PROVIDER=mango`
7. **Webhook smoke test** — реальный входящий звонок → проверить event flow

---

## 12. Git

Commit: _см. ниже после push_  
Pushed: yes
