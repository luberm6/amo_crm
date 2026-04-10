# Mango ↔ FreeSWITCH Call Flow

## 1. Проблема старого flow
- `connect()` мог вернуть успех до фактического `ANSWERED`.
- Для `wait_for_answered` основным источником были Mango webhook/polling, без учёта FreeSWITCH channel events.
- Correlation Mango leg ↔ FreeSWITCH uuid была частичной и не использовалась как источник подтверждения ANSWERED.

## 2. Новый путь Mango ↔ FreeSWITCH
1. Backend делает `originate_call` через Mango (`/commands/callback`) и получает `mango_leg_id`.
2. Mango leg сохраняется в Mango state store + correlation store.
3. При attach audio bridge backend открывает FreeSWITCH media session (`session_id`) и связывает:
   - `call_id` (internal),
   - `mango_leg_id`,
   - `freeswitch_uuid`,
   - `freeswitch_session_id`.
4. FreeSWITCH ESL event loop нормализует события:
   - `CHANNEL_CREATE`, `CHANNEL_ANSWER`, `CHANNEL_HANGUP(_COMPLETE)`,
   - `PLAYBACK_START/STOP`, `CHANNEL_BRIDGE`, `CUSTOM ai::barge_in`.
5. Эти события обновляют FreeSWITCH side state в correlation store.
6. Mango webhook обновляет Mango side state в том же correlation store.
7. `wait_for_answered` в `MangoTelephonyAdapter`:
   - сначала проверяет effective state из correlation store (event-driven),
   - затем использует Mango polling fallback.

## 3. Source Of Truth
- Технически используется merged source:
  - Mango state store (webhook + polling),
  - Mango↔FreeSWITCH correlation store (effective state).
- Для подтверждения `ANSWERED` приоритет событийный:
  - `CHANNEL_ANSWER`/`CHANNEL_BRIDGE` с FreeSWITCH или Mango `answered/bridged`.
- Для fail-before-answer:
  - `CHANNEL_HANGUP(_COMPLETE)` или Mango `terminated/failed` завершают ожидание с ошибкой.

## 4. Routing assumptions (явно)
- Базовый сценарий: Mango control-plane + SIP routing в FreeSWITCH через trunk.
- Варианты входа в FreeSWITCH зависят от конкретной PBX настройки:
  - trunk на SIP profile (`external`),
  - routing на extension/DID/context.
- Командные шаблоны (`uuid_media_reneg`, `uuid_kill`) deployment-specific и требуют настройки на живом контуре.

## 5. Что code-complete
- Event-driven `wait_for_answered` (Mango + FreeSWITCH states + polling fallback).
- Correlation layer:
  - `internal call_id` ↔ `mango_leg_id` ↔ `freeswitch_uuid` ↔ `freeswitch_session_id`.
- Edge-cases в тестах:
  - delayed answer,
  - duplicate answer events,
  - hangup before answer,
  - stale Mango state recovery через FreeSWITCH correlation.
- Логируемые переходы состояния в gateway/webhook path.

## 6. Что infra-dependent
- Реальный Mango trunk routing в FreeSWITCH (DID/extension/context).
- FreeSWITCH event profile/ACL/ESL и соответствие event payload ожидаемым полям.
- Сетевые параметры (NAT, RTP ranges, firewall, SRTP if required).

## 7. Что real-world validated
- В коде и тестах: integration-like simulations.
- На живом Mango tenant: **не подтверждено** (нужен ручной e2e прогон).

## 8. Manual checklist (Mango ↔ FreeSWITCH)
1. Включить backend env:
   - `MEDIA_GATEWAY_ENABLED=true`
   - `MEDIA_GATEWAY_MODE=esl_rtp`
   - Mango API creds + webhook secret/guard.
2. Настроить Mango callback/webhook URL на backend `/v1/webhooks/mango`.
3. Настроить Mango SIP routing на FreeSWITCH trunk (extension/DID/context).
4. Инициировать вызов через backend.
5. Проверить:
   - Mango leg создан,
   - FreeSWITCH получил channel (`CHANNEL_CREATE`),
   - `CHANNEL_ANSWER` пришёл и `wait_for_answered` завершился,
   - hangup событие дошло обратно и состояние завершилось корректно.

## 9. Readiness
- `code-complete`: control/correlation/answer confirmation path.
- `infra-dependent`: trunk/routing/ESL profile/network.
- `real-world validated`: no.
- `mock-only`: `MEDIA_GATEWAY_MODE=mock`.
