# TTS Providers — Руководство

## Поддерживаемые провайдеры

| Провайдер | Задержка | Язык | Клон голоса | Статус |
|-----------|----------|------|-------------|--------|
| Cartesia | ~80–150 мс | Любой | ✅ | Production-ready |
| Gemini Native | ~300–500 мс | Любой | ❌ | Production-ready |
| Yandex SpeechKit | ~400–700 мс | 🇷🇺 лучший | ❌ | Готов к интеграции |
| Sber SaluteSpeech | ~500–800 мс | 🇷🇺 | ❌ | Готов к интеграции |
| T-Bank VoiceKit | ~500–900 мс | 🇷🇺 | ❌ | Адаптер (нужен контракт) |
| ElevenLabs | ~1–1.5 сек | Любой | ✅ | Production-ready |

## Приоритет выбора провайдера

При старте сервера выбирается первый **configured** провайдер в порядке:

```
Cartesia → Yandex SpeechKit → Sber SaluteSpeech → T-Bank VoiceKit → ElevenLabs → Stub
```

## Cartesia (текущий по умолчанию)

**Как получить ключ:** https://cartesia.ai → Sign up → API Keys

**Env vars:**
```env
CARTESIA_ENABLED=true
CARTESIA_API_KEY=sk_car_...
CARTESIA_VOICE_ID=<UUID голоса из дашборда>
```

**Клонирование голоса:** Dashboard → Voices → Clone → загрузи 1+ минуту записи.

---

## Yandex SpeechKit

**Как получить ключ:** https://console.cloud.yandex.ru → Create service account → API key

**Env vars:**
```env
YANDEX_SPEECHKIT_ENABLED=true
YANDEX_SPEECHKIT_API_KEY=AQVN...
YANDEX_SPEECHKIT_FOLDER_ID=b1g...  # опционально
YANDEX_SPEECHKIT_VOICE=alena
YANDEX_SPEECHKIT_EMOTION=good
```

**Рекомендуемые голоса для первого прослушивания:**

| Voice ID | Пол | Эмоции | Описание |
|----------|-----|--------|---------|
| `alena:good` | Жен | neutral, good | Главный голос Яндекс, очень натуральный |
| `dasha:friendly` | Жен | neutral, friendly, strict | Современный, молодой |
| `lera:friendly` | Жен | neutral, friendly, strict | Тёплый, дружелюбный |
| `zahar:good` | Муж | neutral, good | Уверенный, деловой |

**Voice ID формат:** `voice_name` или `voice_name:emotion`
Пример: `alena:good`, `dasha:friendly`, `zahar:neutral`

---

## Sber SaluteSpeech

**Как получить ключ:** https://developers.sber.ru/portal/products/smartspeech
→ Создать проект → Получить client_id и client_secret

**Env vars:**
```env
SBER_SALUTESPEECH_ENABLED=true
SBER_SALUTESPEECH_CLIENT_ID=...
SBER_SALUTESPEECH_CLIENT_SECRET=...
SBER_SALUTESPEECH_SCOPE=SALUTE_SPEECH_PERS  # или SALUTE_SPEECH_CORP
SBER_SALUTESPEECH_VOICE=Nec_24000
```

**Голоса:**
| Voice ID | Пол | Описание |
|----------|-----|---------|
| `Nec_24000` | Жен | Наталья — деловой, нейтральный |
| `May_24000` | Жен | Майя — тёплый, дружелюбный |
| `Bys_24000` | Муж | Борис — уверенный |

---

## T-Bank VoiceKit

**Примечание:** Полный доступ требует корпоративного аккаунта T-Bank.
Trial/sandbox: https://voicekit.tinkoff.ru/

**Env vars:**
```env
TBANK_VOICEKIT_ENABLED=true
TBANK_VOICEKIT_API_KEY=...
TBANK_VOICEKIT_SECRET_KEY=...  # опционально
TBANK_VOICEKIT_ENDPOINT=https://api.tinkoff.ai
TBANK_VOICEKIT_VOICE=alyona
```

---

## Voice Lab в Admin Panel

Перейди в **Admin Panel → 🎙 Voice Lab** для:
- Тестирования голосов разных провайдеров
- Сравнения задержки и качества
- Выбора голоса для агента

**Endpoint:** `POST /v1/tts/preview`
```json
{
  "provider": "yandex_speechkit",
  "voice_id": "alena:good",
  "text": "Здравствуйте! Кафе Любава, помогу с подбором зала.",
  "emotion": null
}
```

Response: WAV base64 + latency_ms + duration_ms

---

## Переключение активного провайдера

Через Render Dashboard → Environment:
```
CARTESIA_ENABLED=false
YANDEX_SPEECHKIT_ENABLED=true
YANDEX_SPEECHKIT_API_KEY=...
YANDEX_SPEECHKIT_VOICE=alena
YANDEX_SPEECHKIT_EMOTION=good
DIRECT_VOICE_STRATEGY=tts_primary
```

Или через Admin Panel → Провайдеры → найти нужный → включить + ввести ключи → Проверить подключение.

---

## Рекомендации для кафе «Любава»

**Для первого прослушивания клиентом рекомендуем:**

1. **`alena:good`** (Yandex) — самый натуральный русский голос, тёплый
2. **`dasha:friendly`** (Yandex) — молодой, современный, живой
3. **Cartesia cloned voice** — если есть запись голоса сотрудника
4. **`Nec_24000`** (Sber) — деловой, чёткий

Тестовые фразы для прослушивания:
- «Здравствуйте! Кафе Любава, помогу с подбором зала. Какой праздник планируете?»
- «Есть залы на левом берегу от тридцати до ста персон. Какой район вам удобен?»
- «Отлично, записала вас! Подтверждение придёт на email.»
