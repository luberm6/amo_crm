import { FormEvent, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '../lib/api'
import { useAuth } from '../auth/AuthContext'

export default function LoginPage() {
  const { login, isAuthenticated } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const nextPath = (location.state as { from?: string } | null)?.from || '/'

  useEffect(() => {
    if (isAuthenticated) {
      navigate(nextPath, { replace: true })
    }
  }, [isAuthenticated, navigate, nextPath])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await login(email, password)
      navigate(nextPath, { replace: true })
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError('Login failed.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-shell">
      <section className="login-card">
        <div>
          <p className="eyebrow">Admin Auth</p>
          <h1>AMO CRM Voice Admin</h1>
          <p className="login-copy">Вход для внутренней панели. Используется минимальный backend admin auth без отдельной user-management системы.</p>
        </div>
        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            Email
            <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" placeholder="admin@example.com" required />
          </label>
          <label>
            Password
            <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" placeholder="••••••••" required />
          </label>
          {error ? <div className="error-banner">{error}</div> : null}
          <button type="submit" className="primary-button" disabled={submitting}>
            {submitting ? 'Входим…' : 'Login'}
          </button>
        </form>
      </section>
    </div>
  )
}
