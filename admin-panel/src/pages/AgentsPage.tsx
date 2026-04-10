import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { ApiError, apiFetch } from '../lib/api'

type AgentListItem = {
  id: string
  name: string
  is_active: boolean
  voice_strategy: string
  version: number
  created_at: string
  updated_at: string
}

type AgentListResponse = {
  items: AgentListItem[]
  total: number
}

export default function AgentsPage() {
  const { token } = useAuth()
  const [items, setItems] = useState<AgentListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showActiveOnly, setShowActiveOnly] = useState(false)

  const loadAgents = useCallback(async () => {
    if (!token) {
      return
    }
    setLoading(true)
    setError(null)
    try {
      const query = showActiveOnly ? '?active_only=true' : ''
      const response = await apiFetch<AgentListResponse>(`/v1/agents${query}`, {}, token)
      setItems(response.items)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load agents.')
    } finally {
      setLoading(false)
    }
  }, [showActiveOnly, token])

  useEffect(() => {
    void loadAgents()
  }, [loadAgents])

  async function handleDisable(agentId: string) {
    if (!token) {
      return
    }
    setError(null)
    try {
      await apiFetch(`/v1/agents/${agentId}`, { method: 'DELETE' }, token)
      await loadAgents()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to disable agent.')
    }
  }

  return (
    <section className="stack-page">
      <article className="hero-card split-card">
        <div>
          <p className="eyebrow">Agent Profiles</p>
          <h3>Управление конфигурацией общения агентов</h3>
          <p>
            Здесь живут реальные runtime-поля: system prompt, greeting, business rules, sales objectives,
            transfer guardrails и voice strategy. Prompt preview собирается backend’ом, а не UI.
          </p>
        </div>
        <div className="button-row">
          <label className="toggle-row">
            <input
              type="checkbox"
              checked={showActiveOnly}
              onChange={(event) => setShowActiveOnly(event.target.checked)}
            />
            <span>Only active</span>
          </label>
          <Link to="/agents/new" className="primary-link-button">Create agent</Link>
        </div>
      </article>

      {error ? <div className="error-banner">{error}</div> : null}

      <article className="panel-card">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Agent list</p>
            <h4>{loading ? 'Loading…' : `${items.length} profiles`}</h4>
          </div>
        </div>

        {loading ? (
          <div className="empty-state">Загружаем профили агентов…</div>
        ) : items.length === 0 ? (
          <div className="empty-state">Пока нет agent profiles. Можно создать первый профиль и сразу использовать его в browser call runtime.</div>
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Voice strategy</th>
                  <th>Version</th>
                  <th>Updated</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((agent) => (
                  <tr key={agent.id}>
                    <td>
                      <div className="table-primary">{agent.name}</div>
                      <div className="table-secondary mono-inline">{agent.id}</div>
                    </td>
                    <td>
                      <span className={`status-pill${agent.is_active ? ' live' : ''}`}>
                        {agent.is_active ? 'active' : 'disabled'}
                      </span>
                    </td>
                    <td>{agent.voice_strategy}</td>
                    <td>v{agent.version}</td>
                    <td>{new Date(agent.updated_at).toLocaleString()}</td>
                    <td>
                      <div className="button-row compact-actions">
                        <Link to={`/agents/${agent.id}`} className="ghost-link-button">Edit</Link>
                        {agent.is_active ? (
                          <button
                            type="button"
                            className="inline-danger-button"
                            onClick={() => void handleDisable(agent.id)}
                          >
                            Disable
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </article>
    </section>
  )
}
