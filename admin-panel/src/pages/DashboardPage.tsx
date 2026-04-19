import { FormEvent, useState } from 'react'
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
}

export default function DashboardPage() {
  const { token } = useAuth()
  const [phoneNumber, setPhoneNumber] = useState('+17547365909')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<OutboundCallResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

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
        }),
      }, token)
      setResult(response)
    } catch (err) {
      if (err instanceof ApiError) {
        const details = typeof err.details === 'object' && err.details && 'detail' in (err.details as Record<string, unknown>)
          ? (err.details as { detail?: { message?: string } }).detail
          : null
        setError(details?.message || err.message)
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
          Фиксированный маршрут для живого теста: <strong>Test Agent</strong>, <strong>DIRECT</strong>, voice strategy <strong>tts_primary</strong>.
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
