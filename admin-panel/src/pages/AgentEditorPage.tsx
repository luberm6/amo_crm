import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { ApiError, apiFetch } from '../lib/api'

type AgentProfile = {
  id: string
  name: string
  is_active: boolean
  system_prompt: string
  tone_rules?: string | null
  business_rules?: string | null
  sales_objectives?: string | null
  greeting_text?: string | null
  transfer_rules?: string | null
  prohibited_promises?: string | null
  voice_strategy: string
  config: Record<string, unknown>
  version: number
  created_at: string
  updated_at: string
  assembled_prompt_preview: string
}

type AgentFormState = {
  name: string
  is_active: boolean
  system_prompt: string
  tone_rules: string
  business_rules: string
  sales_objectives: string
  greeting_text: string
  transfer_rules: string
  prohibited_promises: string
  voice_strategy: string
  configText: string
}

type KnowledgeDocumentListItem = {
  id: string
  title: string
  category: string
  is_active: boolean
  created_at: string
  updated_at: string
}

type KnowledgeDocumentListResponse = {
  items: KnowledgeDocumentListItem[]
  total: number
}

type AgentKnowledgeBindingRead = {
  id: string
  agent_profile_id: string
  knowledge_document_id: string
  role?: string | null
  created_at: string
  knowledge_document: {
    id: string
    title: string
    category: string
    content: string
    is_active: boolean
    notes?: string | null
    metadata: Record<string, unknown>
    created_at: string
    updated_at: string
  }
}

type AgentKnowledgeBindingListRead = {
  items: AgentKnowledgeBindingRead[]
  total: number
}

const EMPTY_FORM: AgentFormState = {
  name: '',
  is_active: true,
  system_prompt: '',
  tone_rules: '',
  business_rules: '',
  sales_objectives: '',
  greeting_text: '',
  transfer_rules: '',
  prohibited_promises: '',
  voice_strategy: 'tts_primary',
  configText: '{\n  "locale": "ru-RU"\n}',
}

const STRATEGY_OPTIONS = [
  { value: 'tts_primary', label: 'tts_primary' },
  { value: 'gemini_primary', label: 'gemini_primary' },
  { value: 'experimental_hybrid', label: 'experimental_hybrid' },
  { value: 'disabled', label: 'disabled' },
]

function toFormState(profile: AgentProfile): AgentFormState {
  return {
    name: profile.name,
    is_active: profile.is_active,
    system_prompt: profile.system_prompt,
    tone_rules: profile.tone_rules || '',
    business_rules: profile.business_rules || '',
    sales_objectives: profile.sales_objectives || '',
    greeting_text: profile.greeting_text || '',
    transfer_rules: profile.transfer_rules || '',
    prohibited_promises: profile.prohibited_promises || '',
    voice_strategy: profile.voice_strategy,
    configText: JSON.stringify(profile.config || {}, null, 2),
  }
}

export default function AgentEditorPage() {
  const { token } = useAuth()
  const { agentId } = useParams()
  const navigate = useNavigate()
  const isCreateMode = agentId === 'new'

  const [form, setForm] = useState<AgentFormState>(EMPTY_FORM)
  const [profile, setProfile] = useState<AgentProfile | null>(null)
  const [loading, setLoading] = useState(!isCreateMode)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [knowledgeDocuments, setKnowledgeDocuments] = useState<KnowledgeDocumentListItem[]>([])
  const [bindings, setBindings] = useState<AgentKnowledgeBindingRead[]>([])
  const [knowledgeLoading, setKnowledgeLoading] = useState(false)
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null)
  const [bindingBusyDocumentId, setBindingBusyDocumentId] = useState<string | null>(null)

  useEffect(() => {
    if (isCreateMode || !token || !agentId) {
      return
    }

    let mounted = true
    setLoading(true)
    setError(null)
    apiFetch<AgentProfile>(`/v1/agents/${agentId}`, {}, token)
      .then((data) => {
        if (!mounted) {
          return
        }
        setProfile(data)
        setForm(toFormState(data))
      })
      .catch((err) => {
        if (mounted) {
          setError(err instanceof ApiError ? err.message : 'Failed to load agent profile.')
        }
      })
      .finally(() => {
        if (mounted) {
          setLoading(false)
        }
      })

    return () => {
      mounted = false
    }
  }, [agentId, isCreateMode, token])

  const loadKnowledgeState = useCallback(async () => {
    if (isCreateMode || !token || !agentId) {
      return
    }
    setKnowledgeLoading(true)
    setKnowledgeError(null)
    try {
      const [documentsResponse, bindingsResponse] = await Promise.all([
        apiFetch<KnowledgeDocumentListResponse>('/v1/knowledge-documents?active_only=true', {}, token),
        apiFetch<AgentKnowledgeBindingListRead>(`/v1/agents/${agentId}/knowledge`, {}, token),
      ])
      setKnowledgeDocuments(documentsResponse.items)
      setBindings(bindingsResponse.items)
    } catch (err) {
      setKnowledgeError(err instanceof ApiError ? err.message : 'Failed to load knowledge bindings.')
    } finally {
      setKnowledgeLoading(false)
    }
  }, [agentId, isCreateMode, token])

  useEffect(() => {
    void loadKnowledgeState()
  }, [loadKnowledgeState])

  const previewText = useMemo(() => {
    if (profile) {
      return profile.assembled_prompt_preview
    }
    return 'Preview появится после первого сохранения. Он собирается backend’ом из полей агента и будет использоваться runtime-слоем.'
  }, [profile])

  const bindingByDocumentId = useMemo(() => {
    const map = new Map<string, AgentKnowledgeBindingRead>()
    bindings.forEach((binding) => {
      map.set(binding.knowledge_document_id, binding)
    })
    return map
  }, [bindings])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token) {
      return
    }
    setSaving(true)
    setError(null)

    let parsedConfig: Record<string, unknown>
    try {
      parsedConfig = JSON.parse(form.configText || '{}') as Record<string, unknown>
    } catch {
      setSaving(false)
      setError('Config must be valid JSON.')
      return
    }

    const payload = {
      name: form.name,
      is_active: form.is_active,
      system_prompt: form.system_prompt,
      tone_rules: form.tone_rules,
      business_rules: form.business_rules,
      sales_objectives: form.sales_objectives,
      greeting_text: form.greeting_text,
      transfer_rules: form.transfer_rules,
      prohibited_promises: form.prohibited_promises,
      voice_strategy: form.voice_strategy,
      config: parsedConfig,
    }

    try {
      const response = await apiFetch<AgentProfile>(
        isCreateMode ? '/v1/agents' : `/v1/agents/${agentId}`,
        {
          method: isCreateMode ? 'POST' : 'PATCH',
          body: JSON.stringify(payload),
        },
        token,
      )
      setProfile(response)
      setForm(toFormState(response))
      if (isCreateMode) {
        navigate(`/agents/${response.id}`, { replace: true })
        return
      }
      await loadKnowledgeState()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save agent profile.')
    } finally {
      setSaving(false)
    }
  }

  async function handleBindingToggle(documentId: string) {
    if (!token || isCreateMode || !agentId) {
      return
    }
    const existingBinding = bindingByDocumentId.get(documentId)
    setBindingBusyDocumentId(documentId)
    setKnowledgeError(null)
    try {
      if (existingBinding) {
        await apiFetch<void>(`/v1/agents/${agentId}/knowledge/${existingBinding.id}`, { method: 'DELETE' }, token)
      } else {
        await apiFetch<AgentKnowledgeBindingRead>(
          `/v1/agents/${agentId}/knowledge/bind`,
          {
            method: 'POST',
            body: JSON.stringify({ knowledge_document_id: documentId }),
          },
          token,
        )
      }
      await loadKnowledgeState()
    } catch (err) {
      setKnowledgeError(err instanceof ApiError ? err.message : 'Failed to update knowledge binding.')
    } finally {
      setBindingBusyDocumentId(null)
    }
  }

  function updateField<K extends keyof AgentFormState>(key: K, value: AgentFormState[K]) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  return (
    <section className="stack-page">
      <article className="hero-card split-card">
        <div>
          <p className="eyebrow">Agent Editor</p>
          <h3>{isCreateMode ? 'Create agent profile' : form.name || 'Edit agent profile'}</h3>
          <p>
            UI редактирует только данные профиля. Backend централизованно собирает runtime prompt preview, а KB
            привязки живут отдельно и будут подключаться к runtime controlled context assembly.
          </p>
        </div>
        <div className="button-row">
          <Link to="/agents" className="ghost-link-button">
            Back to list
          </Link>
        </div>
      </article>

      {error ? <div className="error-banner">{error}</div> : null}
      {knowledgeError ? <div className="error-banner">{knowledgeError}</div> : null}

      {loading ? (
        <article className="panel-card empty-state">Загружаем профиль агента…</article>
      ) : (
        <div className="editor-grid">
          <form className="editor-form" onSubmit={handleSubmit}>
            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Identity</p>
                  <h4>Base profile</h4>
                </div>
              </div>
              <label>
                Name
                <input value={form.name} onChange={(event) => updateField('name', event.target.value)} required />
              </label>
              <label className="toggle-row boxed-toggle">
                <input
                  type="checkbox"
                  checked={form.is_active}
                  onChange={(event) => updateField('is_active', event.target.checked)}
                />
                <span>Agent is active</span>
              </label>
              <label>
                Voice strategy
                <select value={form.voice_strategy} onChange={(event) => updateField('voice_strategy', event.target.value)}>
                  {STRATEGY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Conversation</p>
                  <h4>Core prompt and greeting</h4>
                </div>
              </div>
              <label>
                System prompt
                <textarea
                  value={form.system_prompt}
                  onChange={(event) => updateField('system_prompt', event.target.value)}
                  rows={10}
                  required
                />
              </label>
              <label>
                Greeting text
                <textarea
                  value={form.greeting_text}
                  onChange={(event) => updateField('greeting_text', event.target.value)}
                  rows={4}
                />
              </label>
              <label>
                Tone rules
                <textarea
                  value={form.tone_rules}
                  onChange={(event) => updateField('tone_rules', event.target.value)}
                  rows={5}
                />
              </label>
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Business logic</p>
                  <h4>Goals and constraints</h4>
                </div>
              </div>
              <label>
                Business rules
                <textarea
                  value={form.business_rules}
                  onChange={(event) => updateField('business_rules', event.target.value)}
                  rows={5}
                />
              </label>
              <label>
                Sales objectives
                <textarea
                  value={form.sales_objectives}
                  onChange={(event) => updateField('sales_objectives', event.target.value)}
                  rows={5}
                />
              </label>
              <label>
                Transfer rules
                <textarea
                  value={form.transfer_rules}
                  onChange={(event) => updateField('transfer_rules', event.target.value)}
                  rows={5}
                />
              </label>
              <label>
                Prohibited promises
                <textarea
                  value={form.prohibited_promises}
                  onChange={(event) => updateField('prohibited_promises', event.target.value)}
                  rows={5}
                />
              </label>
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Config</p>
                  <h4>Metadata / runtime config</h4>
                </div>
              </div>
              <label>
                Config JSON
                <textarea
                  value={form.configText}
                  onChange={(event) => updateField('configText', event.target.value)}
                  rows={8}
                  className="mono-textarea"
                />
              </label>
              <div className="button-row">
                <button type="submit" className="primary-button" disabled={saving}>
                  {saving ? 'Saving…' : 'Save agent'}
                </button>
              </div>
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Knowledge Bindings</p>
                  <h4>Controlled context for this agent</h4>
                </div>
              </div>

              {isCreateMode ? (
                <div className="empty-state">
                  Сначала сохраните агента. После этого можно привязать нужные knowledge documents без смешивания их с
                  profile prompt.
                </div>
              ) : knowledgeLoading ? (
                <div className="empty-state">Загружаем доступные документы и текущие привязки…</div>
              ) : knowledgeDocuments.length === 0 ? (
                <div className="empty-state">
                  В knowledge base пока нет активных документов. Их можно создать в секции Knowledge Base.
                </div>
              ) : (
                <div className="knowledge-binding-list">
                  {knowledgeDocuments.map((document) => {
                    const binding = bindingByDocumentId.get(document.id)
                    return (
                      <label key={document.id} className="checkbox-card">
                        <input
                          type="checkbox"
                          checked={Boolean(binding)}
                          disabled={bindingBusyDocumentId === document.id}
                          onChange={() => void handleBindingToggle(document.id)}
                        />
                        <div>
                          <div className="table-primary">{document.title}</div>
                          <div className="inline-meta">
                            <span>{document.category}</span>
                            <span>{binding ? 'bound' : 'not bound'}</span>
                          </div>
                        </div>
                      </label>
                    )
                  })}
                </div>
              )}
            </section>
          </form>

          <aside className="editor-sidebar">
            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Preview</p>
                  <h4>Assembled prompt</h4>
                </div>
              </div>
              <pre className="preview-block">{previewText}</pre>
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Knowledge Summary</p>
                  <h4>What is attached</h4>
                </div>
              </div>
              {bindings.length === 0 ? (
                <div className="empty-state">
                  Для этого агента пока не выбраны knowledge documents. Runtime не сломается и продолжит работать
                  только на profile prompt.
                </div>
              ) : (
                <div className="binding-summary-list">
                  {bindings.map((binding) => (
                    <div key={binding.id} className="binding-summary-item">
                      <strong>{binding.knowledge_document.title}</strong>
                      <span>{binding.knowledge_document.category}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Metadata</p>
                  <h4>Versioning</h4>
                </div>
              </div>
              <div className="debug-list compact-debug">
                <div className="debug-row">
                  <span>version</span>
                  <strong>{profile ? `v${profile.version}` : 'new'}</strong>
                </div>
                <div className="debug-row">
                  <span>updated</span>
                  <strong>{profile ? new Date(profile.updated_at).toLocaleString() : '—'}</strong>
                </div>
                <div className="debug-row">
                  <span>active</span>
                  <strong>{form.is_active ? 'yes' : 'no'}</strong>
                </div>
                <div className="debug-row">
                  <span>voice strategy</span>
                  <strong>{form.voice_strategy}</strong>
                </div>
                <div className="debug-row">
                  <span>knowledge docs</span>
                  <strong>{bindings.length}</strong>
                </div>
              </div>
            </section>
          </aside>
        </div>
      )}
    </section>
  )
}
