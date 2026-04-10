# PRODUCTION HARDENING REPORT

Дата: 2026-04-04  
Область: Mango → FreeSWITCH → backend → Gemini/TTS control/media runtime (без добавления новых product features)

## 1) Summary

Проведён reliability-pass с фокусом на утечки сессий, зависания lifecycle и silent failures.  
Критичный воспроизводимый баг cleanup в Direct audio loop исправлен.  
Stress-like сценарии (10 последовательных, 5 параллельных) в simulation-тестах пройдены без зависших сессий.

Важно: live E2E на реальном Mango/FreeSWITCH контуре в рамках этого прогона не выполнялся.

## 2) Найденные проблемы

| проблема | где | критичность | статус |
|---|---|---:|---|
| При падении `bridge_audio_reader` сессия оставалась в `DirectSessionManager`, ресурсы не закрывались автоматически | `app/integrations/direct/session_manager.py` | Critical | FIXED |
| Silent failure при `send_audio` в FreeSWITCH gateway для несуществующей/закрытой session | `app/integrations/media_gateway/freeswitch.py` | High | FIXED |
| Silent ignore outbound/control вызовов у закрытого bridge | `app/integrations/telephony/freeswitch_bridge.py` | Medium | FIXED |
| Runtime-окружение содержит повреждённые пакеты `.venv` (`yarl`, `pip`) с `IndentationError` | local `.venv` | High | NOT VERIFIED (infra/env) |
| Live E2E quality path (latency/jitter/NAT/SRTP) не подтверждён на боевом контуре | external infra | Critical | NOT VERIFIED |

## 3) Исправления

### FIXED: cleanup leak при ошибке bridge reader
- До: при exception в `_bridge_audio_reader()` ставился `stop_event`, но cleanup мог не завершиться через `terminate_session()`, сессия зависала в памяти.
- После:
  - при ошибке reader запускается `terminate_session()` в фоне;
  - добавлен safety-net в `_run_audio_loop()`: если loop завершился, но сессия ещё зарегистрирована, запускается штатное terminate.
- Файл:
  - `app/integrations/direct/session_manager.py`

### FIXED: явная сигнализация вместо silent fail на media send path
- До: `send_audio()` для неизвестной session просто `return`.
- После: warning + error metric `send_audio_missing_session`.
- Файл:
  - `app/integrations/media_gateway/freeswitch.py`

### FIXED: явные warning-логи на закрытом bridge
- До: `audio_out/send_interrupt/propagate_hangup` тихо игнорировались, если bridge закрыт.
- После: warning-логи с `session_id` и контекстом.
- Файл:
  - `app/integrations/telephony/freeswitch_bridge.py`

## 4) Тесты и проверка

### Добавлены/обновлены тесты
- `tests/test_direct_audio_runtime.py`
  - `test_bridge_reader_exception_triggers_auto_terminate`
  - `test_stress_sequential_10_sessions_no_leak`
  - `test_stress_parallel_5_sessions_no_leak`

### Прогон тестов
- `tests/test_direct_audio_runtime.py` → `10 passed`
- `tests/test_media_gateway.py tests/test_transfer_service.py tests/test_mango_control_plane.py` → `27 passed`
- `tests/test_call_service_unit.py tests/test_transfer_hardening.py` → `25 passed`
- Широкий прогон (без bot/rate-limit файлов из-за повреждённого `.venv`) → `298 passed, 3 skipped`

### Отдельно воспроизведённый дефект до фикса
- Сценарий: exception в `audio_bridge.audio_in()`
- До фикса: `active_count=1`, сессия оставалась живой
- После фикса: авто-terminate, `active_count=0`

## 5) Классификация статусов

- `REPRODUCED BUG`: cleanup leak при exception reader, silent send path/bridge path.
- `FIXED`: все три пункта выше.
- `POTENTIAL ISSUE`: повреждённый `.venv` (ботовые тесты), live media quality в бою.
- `NOT VERIFIED`: live Mango/FreeSWITCH E2E и SLA-параметры RTP в прод-контуре.

## 6) Ограничения и оставшиеся риски

1. Нет подтверждённого live E2E smoke на реальном Mango tenant.
2. Нет валидации latency/jitter/NAT/SRTP/codec в production network path.
3. Повреждён локальный `.venv` (влияет на bot/aiogram path), требуется пересборка окружения.
4. Stress-тесты выполнены в simulation-режиме, не в живой телефонии.
5. Alerting зависит от внешней системы метрик; в коде есть instrumentation, но не факт, что оно подключено в инфре.

## 7) Итог по readiness (честно)

- `code-complete`: частично (core cleanup/race hardening покрыт).
- `infra-dependent`: да (Mango/FreeSWITCH trunk, сетевые параметры, webhook delivery).
- `real-world validated`: нет.
- `mock/simulation only`: stress-подтверждение стабильности lifecycle.
