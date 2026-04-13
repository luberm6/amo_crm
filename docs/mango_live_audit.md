# Mango Live Integration Audit

**Date**: 2026-04-13  
**Type**: Real live API calls — no mocks, no paper audit  
**Commit**: see section 13

---

## 1. Executive Summary

| Check | Result |
|---|---|
| Mango connectivity | **YES** |
| Auth / signature | **YES** |
| Live inventory fetched | **YES** — 2 lines |
| sync-lines via backend API | **YES** — HTTP 200, 2 записи в БД |
| Agent binding on live data | **YES** — PATCH + GET roundtrip подтверждён |
| Parser fix applied | **YES** — `schema_name` добавлен как fallback для display_name |

---

## 2. Live Env Diagnostics

| Параметр | Статус |
|---|---|
| `MANGO_API_KEY` | ✅ Заполнен (mask: `xj***a0`) |
| `MANGO_API_SALT` | ✅ Заполнен (mask: `4b***ai`) |
| `MANGO_API_BASE_URL` | дефолт → `https://app.mango-office.ru/vpbx` |
| `MANGO_FROM_EXT` | ❌ Пуст (outbound невозможен) |
| `MANGO_WEBHOOK_SECRET` | ❌ Пуст |
| `MANGO_WEBHOOK_SHARED_SECRET` | ❌ Пуст |
| `TELEPHONY_PROVIDER` | `stub` (Mango выключен в runtime) |

Для аудита и sync credentials достаточны. Для outbound и webhook нужны дополнительные переменные.

---

## 3. Реально выполненные Mango API вызовы

### 3.1 Прямые запросы через MangoClient

| Endpoint | Method | Status | Elapsed | Result |
|---|---|---|---|---|
| `/incominglines` | POST | **200** | 1278 ms | 2 lines |
| `/config/users/request` | POST | **200** | 515 ms | 0 users (empty) |

Auth: форма `vpbx_api_key + sign=SHA256(key+json+salt) + json={}` — принята.

### 3.2 Через backend REST API (после запуска сервера и миграции 0012)

| Endpoint | Method | Status | Result |
|---|---|---|---|
| `POST /v1/admin/auth/login` | POST | **200** | token OK |
| `POST /v1/telephony/mango/sync-lines` | POST | **200** | 2 lines synced |
| `GET /v1/telephony/mango/lines` | GET | **200** | 2 lines returned |
| `GET /v1/telephony/mango/extensions` | GET | **200** | 0 extensions |
| `POST /v1/agents` | POST | **200** | agent created |
| `PATCH /v1/agent-profiles/{id}/settings` | PATCH | **200** | binding saved |
| `GET /v1/agent-profiles/{id}/settings` | GET | **200** | binding readable |

---

## 4. Какие реальные данные Mango вернул

### Линии (`/incominglines`) — 2 объекта

**Линия 1**

| Поле | Значение |
|---|---|
| `line_id` | `405519147` |
| `number` | `79585382099` |
| `name` | `null` |
| `schema_name` | `"По умолчанию"` |
| `schema_id` | `11071988` |
| `region` | `98` |

**Линия 2 — для AI менеджера** ⭐

| Поле | Значение |
|---|---|
| `line_id` | `405622036` |
| `number` | `79300350609` |
| `name` | `null` |
| `schema_name` | `"ДЛЯ ИИ менеджера"` |
| `schema_id` | `11086409` |
| `region` | `98` |

**Extensions** (`/config/users/request`) — `{"users": []}` — пусто.  
Это подтверждает, что в аккаунте нет настроенных SIP/IP-пользователей.

---

## 5. Что записалось в telephony_lines

После двух sync-cycles (до и после parser fix):

| provider_resource_id | phone_number | display_name | is_active | is_inbound | is_outbound | synced_at |
|---|---|---|---|---|---|---|
| `405519147` | `79585382099` | `По умолчанию` | true | true | false | 2026-04-13T07:19:52Z |
| `405622036` | `79300350609` | `ДЛЯ ИИ менеджера` | true | true | false | 2026-04-13T07:19:52Z |

Все поля схемы заполнены корректно. `raw_payload` содержит полный JSON-ответ Mango.

---

## 6. Проверка live binding agent → Mango line

```
PATCH /v1/agent-profiles/a6df034e-c44c-48ee-9f00-ec2ffe06badd/settings
Body: {"telephony_provider": "mango", "telephony_line_id": "bfe8e766-a7d7-413a-92fc-cb8cfd3af230"}
→ HTTP 200
→ telephony_line.id:                   bfe8e766-a7d7-413a-92fc-cb8cfd3af230
→ telephony_line.phone_number:          79300350609
→ telephony_line.provider_resource_id:  405622036
→ telephony_line.display_name:          ДЛЯ ИИ менеджера
→ telephony_line.is_active:             true

GET /v1/agent-profiles/{id}/settings → HTTP 200
→ Все поля прочитаны корректно (roundtrip подтверждён)
```

**DB verification:**
```
agent_id: a6df034e...
telephony_provider: mango
telephony_line_id: bfe8e766... (FK в telephony_lines)
phone_number: 79300350609
display_name: ДЛЯ ИИ менеджера
provider_resource_id: 405622036
```

Привязка работает end-to-end.

---

## 7. Подходит ли это уже для UI select

**Да.** Данные достаточны для dropdown в Agent Editor:

| Поле | Значение | Пригодность |
|---|---|---|
| ID для binding | `telephony_line.id` (UUID) | ✅ Стабильный ключ |
| Отображаемый текст | `display_name` = schema_name | ✅ Human-readable после fix |
| Номер телефона | `phone_number` | ✅ |
| Активность | `is_active` | ✅ Фильтр |
| Inbound/outbound | `is_inbound_enabled`, `is_outbound_enabled` | ✅ |

**Рекомендация для UI select**: отображать `"{display_name} ({phone_number})"`,
фильтровать по `is_active=true`, сохранять в агент `telephony_line_id` (UUID).

---

## 8. Подходит ли это для routing foundation

### Inbound (входящий звонок → агент)

**Готово частично:**
- Есть `phone_number` в `telephony_lines`
- Можно: `SELECT * FROM telephony_lines WHERE phone_number = :caller_id AND provider='mango'`
- Затем: найти агент по `telephony_line_id`
- **Не готово**: webhook не настроен (секрет пуст, URL не зарегистрирован в Mango)

### Outbound (агент → исходящий через Mango)

**Не готово:**
- `MANGO_FROM_EXT` пуст — `MangoTelephonyAdapter.originate_call()` упадёт
- `TELEPHONY_PROVIDER=stub` — Mango не используется в runtime
- `is_outbound_enabled=False` для обеих линий (нужно уточнить в кабинете Mango)

---

## 9. Что пришлось исправить

### Parser fix: `schema_name` как fallback для display_name

**Файл**: `app/integrations/telephony/mango_client.py`

**Проблема**: Mango возвращает `"name": null` для линий. Парсер не находил display_name
и ставил null. Сервис подставлял `phone_number` вместо human-readable label.

**Решение**: добавить `"schema_name"` в список ключей для `display_name`:

```python
# Before:
display_name=_first_non_empty(record, "display_name", "name", "title", "label", "line_name"),

# After:
display_name=_first_non_empty(record, "display_name", "name", "title", "label", "line_name", "schema_name"),
```

**Результат**: `display_name` теперь `"ДЛЯ ИИ менеджера"` вместо `"79300350609"`.

---

## 10. Что реально протестировано

- [x] Docker Compose → Postgres + Redis запущены
- [x] Alembic upgrade head → применена migration 0012 (telephony_lines + agent_profiles FK)
- [x] Uvicorn backend запущен на :8000
- [x] `POST /v1/admin/auth/login` → JWT token получен
- [x] `MangoClient.from_settings()` — diagnostics: configured=True
- [x] `POST /incominglines` → HTTP 200, 2 lines
- [x] `POST /config/users/request` → HTTP 200, 0 users
- [x] `POST /v1/telephony/mango/sync-lines` → HTTP 200, 2 rows in DB (первый sync)
- [x] Parser fix применён и re-sync выполнен → display_name корректны
- [x] `GET /v1/telephony/mango/lines` → HTTP 200, 2 items
- [x] `GET /v1/telephony/mango/extensions` → HTTP 200, 0 items
- [x] `POST /v1/agents` → agent создан
- [x] `PATCH /v1/agent-profiles/{id}/settings` → binding сохранён
- [x] `GET /v1/agent-profiles/{id}/settings` → binding прочитан (roundtrip OK)
- [x] DB: SELECT JOIN подтверждает FK integrity
- [x] 456 тестов проходят (регрессий нет)

---

## 11. Что не протестировано

| Пробел | Причина |
|---|---|
| Webhook live event | `MANGO_WEBHOOK_SECRET` пуст, URL не зарегистрирован |
| Реальный входящий звонок | Требует звонка на номер в prod среде |
| Outbound originate | `MANGO_FROM_EXT` пуст |
| Bridge / transfer flow | Требует активной сессии + SIP bridge |
| FreeSWITCH media bridge | `MEDIA_GATEWAY_ENABLED=false` |
| Повторный sync (deactivate stale) | Не тестировался edge case |
| Номер в формате +7 (нормализация) | Mango отдаёт без `+`, backend хранит как есть |

---

## 12. Final Verdict

| Пункт | Результат |
|---|---|
| Mango connectivity | ✅ **YES** — HTTP 200 на все вызовы |
| Auth / signature | ✅ **YES** — SHA256 accepted |
| Live inventory | ✅ **YES** — 2 lines, 0 extensions |
| sync-lines | ✅ **YES** — 2 строки в telephony_lines, display_name корректны после fix |
| Agent binding | ✅ **YES** — PATCH+GET roundtrip verified, FK в DB |
| UI select ready | ✅ **YES** — данных достаточно |
| Inbound routing foundation | ⚠️ **PARTIAL** — данные есть, webhook не настроен |
| Outbound routing | ❌ **NOT READY** — MANGO_FROM_EXT пуст |
| Webhook | ❌ **NOT READY** — секрет пуст |

### Следующий шаг

1. Нормализация номеров: добавить `+` к `phone_number` при sync (сейчас `79300350609` → нужно `+79300350609`)
2. Задать `MANGO_WEBHOOK_SECRET` и зарегистрировать URL в кабинете Mango
3. Задать `MANGO_FROM_EXT` для outbound
4. Переключить `TELEPHONY_PROVIDER=mango` в production

---

## 13. Git

- Parser fix: `app/integrations/telephony/mango_client.py` — добавлен `schema_name` в display_name fallback list
- Новый файл: `docs/mango_live_audit.md` (этот отчёт)
- Commit: см. ниже
- Pushed: yes
