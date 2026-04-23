import { FormEvent, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { ApiError, apiFetch } from '../lib/api'

const cards = [
  { to: '/agents',         title: 'Агенты',           body: 'Карточки агентов, голосовые настройки, телеметрия и маршрутизация.' },
  { to: '/prompts',        title: 'Промпты',           body: 'Системные инструкции, правила общения и история изменений.' },
  { to: '/knowledge-base', title: 'База знаний',       body: 'Документы компании, категории знаний и контекст для агентов.' },
  { to: '/browser-call',   title: 'Браузерный звонок', body: 'Реальный контур ручной проверки поверх direct-runtime контура.' },
]

type OutboundCallResponse = {
  accepted: boolean
  call_id?: string | null
  id?: string | null
  status?: string | null
  error?: unknown
  phone?: string | null
  mode?: string | null
  route_used?: string | null
  telephony_leg_id?: string | null
  last_failure_stage?: string | null
  last_failure_reason?: string | null
  last_disconnect_reason?: string | null
  last_runtime_error?: string | null
}

function formatOperatorError(message: string | null, details: unknown): string | null {
  const raw = message || ''
  if (raw.includes('Timed out waiting for leg') && raw.includes('to answer after')) {
    return 'Звонок запущен, но провайдер не подтвердил ответ абонента за ожидаемое время.'
  }
  if (raw.includes('subscriber unavailable') || raw.includes('unavailable')) {
    return 'Абонент недоступен или провайдер завершил вызов до соединения.'
  }
  if (typeof details === 'object' && details && 'detail' in (details as Record<string, unknown>)) {
    const nested = (details as { detail?: { message?: string } }).detail?.message
    if (nested && nested !== raw) {
      return nested
    }
  }
  return message
}

export default function DashboardPage() {
  const { token } = useAuth()
  const [phoneNumber, setPhoneNumber] = useState('+17547365909')
  const [voiceStrategy, setVoiceStrategy] = useState<'tts_primary' | 'gemini_primary'>('tts_primary')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<OutboundCallResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollTimerRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (pollTimerRef.current !== null) {
        window.clearTimeout(pollTimerRef.current)
      }
    }
  }, [])

  useEffect(() => {
    const callId = result?.call_id || result?.id
    const status = result?.status || null
    if (!callId || !status || !token) {
      return
    }
    if (status !== 'IN_PROGRESS' && status !== 'RINGING' && status !== 'DIALING' && status !== 'CREATED') {
      return
    }

    let cancelled = false

    async function pollCall(): Promise<void> {
      try {
        const next = await apiFetch<OutboundCallResponse>(`/v1/calls/${callId}`, undefined, token)
        if (cancelled) {
          return
        }
        setResult((current) => ({
          ...(current || {}),
          ...next,
          accepted: current?.accepted ?? true,
          call_id: current?.call_id || current?.id || callId,
          id: current?.id || current?.call_id || callId,
        }))
        if (next.status && !['IN_PROGRESS', 'RINGING', 'DIALING', 'CREATED'].includes(next.status)) {
          return
        }
      } catch {
        return
      }
      pollTimerRef.current = window.setTimeout(() => {
        void pollCall()
      }, 2000)
    }

    pollTimerRef.current = window.setTimeout(() => {
      void pollCall()
    }, 2000)

    return () => {
      cancelled = true
      if (pollTimerRef.current !== null) {
        window.clearTimeout(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }
  }, [result?.call_id, result?.id, result?.status, token])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    setResult(null)

    try {
      const response = await apiFetch<OutboundCallResponse>('/v1/calls', {
        method: 'POST',
        body: JSON.stringify({
          phone_number: phoneNumber,
          agent_name: 'Test Agent',
          mode: 'DIRECT',
          voice_strategy_override: voiceStrategy,
        }),
      }, token)
      setResult(response)
    } catch (err) {
      if (err instanceof ApiError) {
        const details = typeof err.details === 'object' && err.details && 'detail' in (err.details as Record<string, unknown>)
          ? (err.details as { detail?: { message?: string } }).detail
          : null
        setError(formatOperatorError(details?.message || err.message, err.details))
        setResult({
          accepted: false,
          status: 'failed',
          error: err.details,
        })
      } else {
        setError('Не удалось запустить звонок.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="page-grid">
      <article className="hero-card">
        <p className="eyebrow">Панель управления</p>
        <h3>Внутренняя админка для настройки агентов и голосового контура.</h3>
        <p>
          Backend остаётся на FastAPI, а настройка агентов, провайдеров и голосовой проверки теперь живёт
          в полноценной веб-панели, а не во временных страницах и ручных скриптах.
        </p>
      </article>
      <article className="panel-card form-section outbound-dial-panel">
        <div className="outbound-dial-head">
          <div>
            <p className="eyebrow">Live Outbound Direct</p>
            <h4>Быстрый прозвон через Mango</h4>
          </div>
          <span className={`status-pill${result?.accepted ? ' live' : result ? ' error' : ''}`}>
            {result?.status || 'ожидание'}
          </span>
        </div>
        <p className="compact-copy">
          Фиксированный маршрут для живого теста: <strong>Test Agent</strong>, <strong>DIRECT</strong>, voice strategy <strong>{voiceStrategy}</strong>.
        </p>
        <form className="form-section" onSubmit={(event) => void handleSubmit(event)}>
          <label className="field-label" htmlFor="outbound-phone-number">Номер для звонка</label>
          <input
            id="outbound-phone-number"
            value={phoneNumber}
            onChange={(event) => setPhoneNumber(event.target.value)}
            placeholder="+17547365909"
            autoComplete="tel"
          />
          <label className="field-label" htmlFor="outbound-voice-strategy">Голосовой тракт</label>
          <select
            id="outbound-voice-strategy"
            value={voiceStrategy}
            onChange={(event) => setVoiceStrategy(event.target.value as 'tts_primary' | 'gemini_primary')}
          >
            <option value="tts_primary">tts_primary</option>
            <option value="gemini_primary">gemini_primary</option>
          </select>
          <div className="button-row">
            <button type="submit" className="primary-button" disabled={submitting}>
              {submitting ? 'Запускаем звонок…' : 'Call'}
            </button>
          </div>
        </form>
        {error ? <div className="error-banner">{error}</div> : null}
        {result ? (
          <div className="outbound-dial-result">
            <p><strong>accepted:</strong> {String(result.accepted)}</p>
            <p><strong>call_id:</strong> {result.call_id || result.id || '—'}</p>
            <p><strong>status:</strong> {result.status || '—'}</p>
            <p><strong>error:</strong> {result.error ? JSON.stringify(result.error) : '—'}</p>
            <p><strong>failure_stage:</strong> {result.last_failure_stage || '—'}</p>
            <p><strong>failure_reason:</strong> {result.last_failure_reason || '—'}</p>
            <p><strong>disconnect_reason:</strong> {result.last_disconnect_reason || '—'}</p>
            <p><strong>runtime_error:</strong> {result.last_runtime_error || '—'}</p>
          </div>
        ) : null}
      </article>
      <div className="dashboard-cards">
        {cards.map((card) => (
          <Link key={card.to} to={card.to} className="info-card">
            <h4>{card.title}</h4>
            <p>{card.body}</p>
          </Link>
        ))}
      </div>
    </section>
  )
}
