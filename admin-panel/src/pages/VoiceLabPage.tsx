import { useCallback, useEffect, useRef, useState } from 'react'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

type VoiceOption = {
  id: string
  name: string
  gender?: string
  description?: string
  emotions?: string[]
}

type ProviderInfo = {
  provider: string
  display_name: string
  enabled: boolean
  configured: boolean
  voices: VoiceOption[]
  note?: string
}

type PreviewResponse = {
  provider: string
  voice_id: string
  wav_base64: string
  duration_ms?: number
  latency_ms: number
  cached: boolean
  byte_size: number
}

const DEMO_TEXTS = [
  'Здравствуйте! Кафе «Любава», помогу с подбором зала. Какой праздник планируете?',
  'Отлично, записала вас! Подтверждение придёт на ваш email. Хорошего дня!',
  'На левом берегу есть зал Звёздный на тридцать–сто персон, и зал Лазурный на тридцать–семьдесят человек.',
  'Банкетное меню начинается от девятисот рублей на человека.',
]

function getAuthHeader(): string {
  const token = localStorage.getItem('admin_token') || sessionStorage.getItem('admin_token') || ''
  return token ? `Bearer ${token}` : ''
}

export default function VoiceLabPage() {
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [loading, setLoading] = useState(true)

  const [selectedProvider, setSelectedProvider] = useState<string>('')
  const [selectedVoice, setSelectedVoice] = useState<string>('')
  const [selectedEmotion, setSelectedEmotion] = useState<string>('')
  const [text, setText] = useState(DEMO_TEXTS[0])

  const [generating, setGenerating] = useState(false)
  const [result, setResult] = useState<PreviewResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const audioRef = useRef<HTMLAudioElement>(null)

  useEffect(() => {
    fetch(`${API_BASE}/v1/tts/providers`, {
      headers: { Authorization: getAuthHeader() },
    })
      .then((r) => r.json())
      .then((data: ProviderInfo[]) => {
        setProviders(data)
        // Auto-select first configured provider
        const first = data.find((p) => p.configured && p.provider !== 'gemini_native')
        if (first) {
          setSelectedProvider(first.provider)
          if (first.voices.length > 0) setSelectedVoice(first.voices[0].id)
        }
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const currentProvider = providers.find((p) => p.provider === selectedProvider)
  const currentVoice = currentProvider?.voices.find((v) => v.id === selectedVoice)
  const availableEmotions = currentVoice?.emotions ?? []

  const handleProviderChange = (provider: string) => {
    setSelectedProvider(provider)
    setSelectedEmotion('')
    setResult(null)
    const prov = providers.find((p) => p.provider === provider)
    if (prov && prov.voices.length > 0) {
      setSelectedVoice(prov.voices[0].id)
    } else {
      setSelectedVoice('')
    }
  }

  const handleVoiceChange = (voice: string) => {
    setSelectedVoice(voice)
    setSelectedEmotion('')
    setResult(null)
  }

  const generatePreview = useCallback(async () => {
    if (!selectedProvider || !text.trim()) return
    setGenerating(true)
    setError(null)
    setResult(null)

    try {
      const resp = await fetch(`${API_BASE}/v1/tts/preview`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: getAuthHeader(),
        },
        body: JSON.stringify({
          provider: selectedProvider,
          voice_id: selectedVoice,
          text: text.trim(),
          emotion: selectedEmotion || null,
        }),
      })

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Ошибка сервера' }))
        throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail))
      }

      const data: PreviewResponse = await resp.json()
      setResult(data)

      // Auto-play
      if (audioRef.current) {
        const wavBytes = atob(data.wav_base64)
        const arr = new Uint8Array(wavBytes.length)
        for (let i = 0; i < wavBytes.length; i++) arr[i] = wavBytes.charCodeAt(i)
        const blob = new Blob([arr], { type: 'audio/wav' })
        audioRef.current.src = URL.createObjectURL(blob)
        audioRef.current.play().catch(() => {})
      }
    } catch (e) {
      setError(String(e))
    } finally {
      setGenerating(false)
    }
  }, [selectedProvider, selectedVoice, selectedEmotion, text])

  if (loading) return <div className="page-loading">Загрузка провайдеров…</div>

  return (
    <div className="voice-lab-page">
      <div className="voice-lab-header">
        <h1 className="page-title">🎙 Voice Lab</h1>
        <p className="page-subtitle">
          Тестируйте голоса разных TTS-провайдеров и выбирайте лучший для агента
        </p>
      </div>

      <div className="voice-lab-layout">
        {/* ── Left panel: controls ── */}
        <div className="voice-lab-controls panel-card">

          {/* Provider selector */}
          <div className="vl-section">
            <p className="field-label">Провайдер</p>
            <div className="vl-provider-grid">
              {providers.map((p) => (
                <button
                  key={p.provider}
                  type="button"
                  className={`vl-provider-btn${selectedProvider === p.provider ? ' active' : ''}${!p.configured ? ' disabled' : ''}`}
                  onClick={() => p.configured && handleProviderChange(p.provider)}
                  title={p.note ?? ''}
                >
                  <span className="vl-provider-name">{p.display_name}</span>
                  <span className={`vl-provider-badge ${p.configured ? 'ok' : 'off'}`}>
                    {p.configured ? '✓' : 'не задан'}
                  </span>
                </button>
              ))}
            </div>
            {currentProvider?.note && (
              <p className="vl-provider-note">{currentProvider.note}</p>
            )}
          </div>

          {/* Voice selector */}
          {currentProvider && currentProvider.voices.length > 0 && (
            <div className="vl-section">
              <label className="field-label" htmlFor="vl-voice-select">Голос</label>
              <select
                id="vl-voice-select"
                className="vl-select"
                value={selectedVoice}
                onChange={(e) => handleVoiceChange(e.target.value)}
              >
                {currentProvider.voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}{v.gender ? ` — ${v.gender === 'female' ? 'жен.' : 'муж.'}` : ''}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Emotion selector */}
          {availableEmotions.length > 0 && (
            <div className="vl-section">
              <label className="field-label" htmlFor="vl-emotion-select">Эмоция / стиль</label>
              <select
                id="vl-emotion-select"
                className="vl-select"
                value={selectedEmotion}
                onChange={(e) => setSelectedEmotion(e.target.value)}
              >
                <option value="">— по умолчанию —</option>
                {availableEmotions.map((e) => (
                  <option key={e} value={e}>{e}</option>
                ))}
              </select>
            </div>
          )}

          {/* Text */}
          <div className="vl-section">
            <label className="field-label" htmlFor="vl-text">Текст для синтеза</label>
            <div className="vl-demo-pills">
              {DEMO_TEXTS.map((t, i) => (
                <button
                  key={i}
                  type="button"
                  className={`vl-demo-pill${text === t ? ' active' : ''}`}
                  onClick={() => setText(t)}
                >
                  Фраза {i + 1}
                </button>
              ))}
            </div>
            <textarea
              id="vl-text"
              className="vl-textarea"
              rows={4}
              maxLength={300}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Введите текст для синтеза…"
            />
            <span className="vl-char-count">{text.length}/300</span>
          </div>

          <button
            type="button"
            className="vl-generate-btn"
            disabled={generating || !selectedProvider || !text.trim()}
            onClick={generatePreview}
          >
            {generating ? '⏳ Генерация…' : '▶ Сгенерировать'}
          </button>
        </div>

        {/* ── Right panel: result ── */}
        <div className="voice-lab-result panel-card">
          <p className="field-label">Результат</p>

          {error && (
            <div className="vl-error">
              <strong>Ошибка:</strong> {error}
            </div>
          )}

          {!result && !error && !generating && (
            <div className="vl-placeholder">
              Нажмите «Сгенерировать» чтобы услышать голос
            </div>
          )}

          {generating && (
            <div className="vl-generating">
              <div className="vl-spinner" />
              <span>Синтез аудио…</span>
            </div>
          )}

          {result && (
            <div className="vl-result-content">
              <audio ref={audioRef} controls className="vl-audio-player" />

              <div className="vl-metrics">
                <div className="vl-metric">
                  <span className="vl-metric-label">Задержка</span>
                  <span className="vl-metric-value">{result.latency_ms.toFixed(0)} мс</span>
                </div>
                {result.duration_ms && (
                  <div className="vl-metric">
                    <span className="vl-metric-label">Длина аудио</span>
                    <span className="vl-metric-value">{(result.duration_ms / 1000).toFixed(1)} сек</span>
                  </div>
                )}
                <div className="vl-metric">
                  <span className="vl-metric-label">Размер</span>
                  <span className="vl-metric-value">{(result.byte_size / 1024).toFixed(0)} КБ</span>
                </div>
                <div className="vl-metric">
                  <span className="vl-metric-label">Кеш</span>
                  <span className="vl-metric-value">{result.cached ? 'Да' : 'Нет'}</span>
                </div>
              </div>

              <div className="vl-result-info">
                <strong>{currentProvider?.display_name ?? result.provider}</strong>
                {' · '}
                {result.voice_id}
                {selectedEmotion ? ` · ${selectedEmotion}` : ''}
              </div>
            </div>
          )}

          {/* Comparison table */}
          <div className="vl-comparison">
            <p className="field-label" style={{ marginTop: '24px' }}>Сравнение провайдеров</p>
            <table className="vl-compare-table">
              <thead>
                <tr>
                  <th>Провайдер</th>
                  <th>Задержка</th>
                  <th>Язык</th>
                  <th>Клон</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Cartesia</td>
                  <td>~80–150 мс</td>
                  <td>Любой</td>
                  <td>✅</td>
                  <td>{providers.find(p => p.provider === 'cartesia')?.configured ? '✅ готов' : '⬜ не настроен'}</td>
                </tr>
                <tr>
                  <td>Gemini Native</td>
                  <td>~300–500 мс</td>
                  <td>Любой</td>
                  <td>❌</td>
                  <td>{providers.find(p => p.provider === 'gemini_native')?.configured ? '✅ готов' : '⬜ не настроен'}</td>
                </tr>
                <tr>
                  <td>Yandex SpeechKit</td>
                  <td>~400–700 мс</td>
                  <td>🇷🇺 лучший</td>
                  <td>❌</td>
                  <td>{providers.find(p => p.provider === 'yandex_speechkit')?.configured ? '✅ готов' : '⬜ не настроен'}</td>
                </tr>
                <tr>
                  <td>Sber SaluteSpeech</td>
                  <td>~500–800 мс</td>
                  <td>🇷🇺</td>
                  <td>❌</td>
                  <td>{providers.find(p => p.provider === 'sber_salutespeech')?.configured ? '✅ готов' : '⬜ не настроен'}</td>
                </tr>
                <tr>
                  <td>ElevenLabs</td>
                  <td>~1–1.5 сек</td>
                  <td>Любой</td>
                  <td>✅</td>
                  <td>{providers.find(p => p.provider === 'elevenlabs')?.configured ? '✅ готов' : '⬜ не настроен'}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
