import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { ApiError, apiFetch } from '../lib/api'

type AgentCreateRead = {
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

type TelephonyLine = {
  id: string
  provider: string
  provider_resource_id: string
  remote_line_id: string
  phone_number: string
  schema_name?: string | null
  display_name?: string | null
  label: string
  extension?: string | null
  is_active: boolean
  is_inbound_enabled: boolean
  is_outbound_enabled: boolean
  synced_at?: string | null
}

type TelephonyLineListResponse = {
  items: TelephonyLine[]
  total: number
}

type TelephonyLineSyncResponse = TelephonyLineListResponse & {
  synced_count: number
  deactivated_count: number
  source: string
  synced_at: string
}

type TelephonyExtension = {
  provider_resource_id: string
  extension: string
  display_name?: string | null
  line_provider_resource_id?: string | null
  line_phone_number?: string | null
}

type TelephonyExtensionListResponse = {
  items: TelephonyExtension[]
  total: number
  source: string
}

type MangoReadiness = {
  api_configured: boolean
  webhook_secret_configured: boolean
  from_ext_configured: boolean
  from_ext_auto_discoverable?: boolean
  warnings: string[]
}

type ApiErrorPayload = {
  detail?: {
    error?: string
    message?: string
    detail?: {
      http_status?: number
    }
  }
}

type AgentSettingsRead = {
  agent_profile_id: string
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
  voice_provider: 'elevenlabs' | 'gemini'
  telephony_provider?: string | null
  telephony_line_id?: string | null
  telephony_remote_line_id?: string | null
  telephony_extension?: string | null
  telephony_line?: TelephonyLine | null
  user_settings: Record<string, unknown>
  knowledge_document_ids: string[]
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
  voiceProvider: 'elevenlabs' | 'gemini'
  telephonyRemoteLineId: string
  telephonyExtension: string
  userSettingsText: string
  knowledgeDocumentIds: string[]
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
  voiceProvider: 'gemini',
  telephonyRemoteLineId: '',
  telephonyExtension: '',
  userSettingsText: '{\n  "locale": "ru-RU",\n  "gemini_voice_name": "Aoede"\n}',
  knowledgeDocumentIds: [],
}

const GEMINI_VOICES = [
  { value: 'Aoede', label: 'Aoede — женский, мягкий' },
  { value: 'Puck', label: 'Puck — мужской, живой' },
  { value: 'Charon', label: 'Charon — мужской, глубокий' },
  { value: 'Kore', label: 'Kore — женский, чёткий' },
  { value: 'Fenrir', label: 'Fenrir — мужской, уверенный' },
  { value: 'Leda', label: 'Leda — женский, спокойный' },
  { value: 'Zephyr', label: 'Zephyr — нейтральный, лёгкий' },
  { value: 'Orus', label: 'Orus — мужской, строгий' },
]

function toFormState(settings: AgentSettingsRead): AgentFormState {
  return {
    name: settings.name,
    is_active: settings.is_active,
    system_prompt: settings.system_prompt,
    tone_rules: settings.tone_rules || '',
    business_rules: settings.business_rules || '',
    sales_objectives: settings.sales_objectives || '',
    greeting_text: settings.greeting_text || '',
    transfer_rules: settings.transfer_rules || '',
    prohibited_promises: settings.prohibited_promises || '',
    voiceProvider: settings.voice_provider,
    telephonyRemoteLineId: settings.telephony_remote_line_id || settings.telephony_line?.remote_line_id || '',
    telephonyExtension: settings.telephony_extension || settings.telephony_line?.extension || '',
    userSettingsText: JSON.stringify(settings.user_settings || {}, null, 2),
    knowledgeDocumentIds: settings.knowledge_document_ids || [],
  }
}

function voiceStrategyFromProvider(provider: AgentFormState['voiceProvider']): 'gemini_primary' | 'tts_primary' {
  return provider === 'gemini' ? 'gemini_primary' : 'tts_primary'
}

function formatTelephonyLineLabel(line: TelephonyLine): string {
  const primary = line.schema_name || line.label || line.display_name || line.phone_number
  return primary === line.phone_number ? line.phone_number : `${primary} (${line.phone_number})`
}

function mapMangoWarning(warning: string): string {
  if (warning.includes('MANGO_WEBHOOK_SECRET')) {
    return 'Inbound webhook verification not configured. Задайте MANGO_WEBHOOK_SECRET перед боевым inbound routing.'
  }
  if (warning.includes('auto-discovered Mango extension')) {
    return 'Outbound calling will use an auto-discovered Mango extension. Для предсказуемого боевого originate всё ещё лучше явно задать MANGO_FROM_EXT.'
  }
  if (warning.includes('MANGO_FROM_EXT')) {
    return 'Outbound calling not configured. Задайте MANGO_FROM_EXT перед боевым originate/callback.'
  }
  return warning
}

function getApiErrorCode(err: ApiError): string | null {
  const payload = err.details as ApiErrorPayload | null
  return payload?.detail?.error || null
}

function getNestedHttpStatus(err: ApiError): number | null {
  const payload = err.details as ApiErrorPayload | null
  return payload?.detail?.detail?.http_status ?? null
}

function mapTelephonyApiError(err: unknown, fallback: string): string {
  if (!(err instanceof ApiError)) {
    return fallback
  }

  const code = getApiErrorCode(err)
  if (code === 'mango_not_configured') {
    return 'Mango не настроен. Задайте MANGO_API_KEY и MANGO_API_SALT в backend environment.'
  }
  if (code === 'mango_api_unavailable') {
    if (getNestedHttpStatus(err) === 429) {
      return 'Mango временно ограничил extensions API по rate limit. Привязка линии остаётся доступной, повторите попытку позже.'
    }
    return 'Mango API временно недоступен. Проверьте доступность tenant и попробуйте ещё раз.'
  }
  if (code === 'mango_sync_failed') {
    return 'Не удалось синхронизировать линии Mango. Проверьте live API ответ и повторите sync.'
  }
  if (code === 'telephony_line_not_found') {
    return 'Выбранная линия Mango не найдена. Сначала обновите inventory и выберите существующую линию.'
  }
  if (code === 'telephony_line_inactive') {
    return 'Выбранная линия Mango неактивна. Сохранение заблокировано, пока вы не выберете активную линию.'
  }
  if (code === 'invalid_voice_provider') {
    return 'Выбран неподдерживаемый voice provider. Используйте Gemini или ElevenLabs.'
  }

  return err.message || fallback
}

export default function AgentEditorPage() {
  const { token } = useAuth()
  const { agentId } = useParams()
  const navigate = useNavigate()
  const isCreateMode = agentId === 'new'

  const [form, setForm] = useState<AgentFormState>(EMPTY_FORM)
  const [settings, setSettings] = useState<AgentSettingsRead | null>(null)
  const [loading, setLoading] = useState(!isCreateMode)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [knowledgeDocuments, setKnowledgeDocuments] = useState<KnowledgeDocumentListItem[]>([])
  const [knowledgeLoading, setKnowledgeLoading] = useState(false)
  const [knowledgeError, setKnowledgeError] = useState<string | null>(null)

  const [telephonyLines, setTelephonyLines] = useState<TelephonyLine[]>([])
  const [telephonyExtensions, setTelephonyExtensions] = useState<TelephonyExtension[]>([])
  const [telephonyLoading, setTelephonyLoading] = useState(false)
  const [telephonyError, setTelephonyError] = useState<string | null>(null)
  const [telephonyNotice, setTelephonyNotice] = useState<string | null>(null)
  const [syncingTelephony, setSyncingTelephony] = useState(false)
  const [telephonySuccess, setTelephonySuccess] = useState<string | null>(null)
  const [mangoReadiness, setMangoReadiness] = useState<MangoReadiness | null>(null)

  const loadKnowledgeDocuments = useCallback(async () => {
    if (!token) {
      return
    }
    setKnowledgeLoading(true)
    setKnowledgeError(null)
    try {
      const response = await apiFetch<KnowledgeDocumentListResponse>('/v1/knowledge-documents?active_only=true', {}, token)
      setKnowledgeDocuments(response.items)
    } catch (err) {
      setKnowledgeError(err instanceof ApiError ? err.message : 'Не удалось загрузить knowledge base.')
    } finally {
      setKnowledgeLoading(false)
    }
  }, [token])

  const loadTelephonyState = useCallback(async () => {
    if (!token) {
      return
    }
    setTelephonyLoading(true)
    setTelephonyError(null)
    setTelephonyNotice(null)
    try {
      const [linesResponse, extensionsResponse, readinessResponse] = await Promise.all([
        apiFetch<TelephonyLineListResponse>('/v1/telephony/mango/lines', {}, token),
        apiFetch<TelephonyExtensionListResponse>('/v1/telephony/mango/extensions', {}, token).catch((err) => {
          if (err instanceof ApiError) {
            setTelephonyNotice(mapTelephonyApiError(
              err,
              'Mango extensions временно недоступны. Привязка линии остаётся доступной.',
            ))
          }
          return { items: [], total: 0, source: 'mango_api' } satisfies TelephonyExtensionListResponse
        }),
        apiFetch<MangoReadiness>('/v1/telephony/mango/readiness', {}, token).catch(() => null),
      ])
      setTelephonyLines(linesResponse.items)
      setTelephonyExtensions(extensionsResponse.items)
      setMangoReadiness(readinessResponse)
    } catch (err) {
      setTelephonyError(mapTelephonyApiError(err, 'Не удалось загрузить Mango inventory.'))
    } finally {
      setTelephonyLoading(false)
    }
  }, [token])

  const loadAgentSettings = useCallback(async () => {
    if (isCreateMode || !token || !agentId) {
      return
    }
    setLoading(true)
    setError(null)
    try {
      const response = await apiFetch<AgentSettingsRead>(`/v1/agent-profiles/${agentId}/settings`, {}, token)
      setSettings(response)
      setForm(toFormState(response))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Не удалось загрузить настройки агента.')
    } finally {
      setLoading(false)
    }
  }, [agentId, isCreateMode, token])

  useEffect(() => {
    void loadKnowledgeDocuments()
    void loadTelephonyState()
    void loadAgentSettings()
  }, [loadAgentSettings, loadKnowledgeDocuments, loadTelephonyState])

  const previewText = useMemo(() => {
    if (settings) {
      return settings.assembled_prompt_preview
    }
    return 'Preview появится после первого сохранения. Backend собирает runtime prompt централизованно и отдельно подключает knowledge base.'
  }, [settings])

  const selectedKnowledgeIds = useMemo(() => new Set(form.knowledgeDocumentIds), [form.knowledgeDocumentIds])
  const selectedKnowledgeDocuments = useMemo(
    () => knowledgeDocuments.filter((item) => selectedKnowledgeIds.has(item.id)),
    [knowledgeDocuments, selectedKnowledgeIds],
  )

  const selectedTelephonyLine = useMemo(
    () => telephonyLines.find((line) => line.remote_line_id === form.telephonyRemoteLineId) || null,
    [form.telephonyRemoteLineId, telephonyLines],
  )

  const extensionOptions = useMemo(() => {
    if (!selectedTelephonyLine) {
      return telephonyExtensions
    }
    const matched = telephonyExtensions.filter((item) => (
      item.line_provider_resource_id === selectedTelephonyLine.provider_resource_id
      || item.line_phone_number === selectedTelephonyLine.phone_number
    ))
    return matched.length > 0 ? matched : telephonyExtensions
  }, [selectedTelephonyLine, telephonyExtensions])

  const suggestedLineId = useMemo(() => {
    if (form.telephonyRemoteLineId || telephonyLines.length === 0) {
      return null
    }
    const aiLine = telephonyLines.find(
      (line) => line.is_active && ((line.schema_name || '').trim() === 'ДЛЯ ИИ менеджера'),
    )
    return aiLine?.remote_line_id ?? null
  }, [form.telephonyRemoteLineId, telephonyLines])

  const orderedTelephonyLines = useMemo(() => {
    const aiRemoteId = suggestedLineId
    return [...telephonyLines].sort((left, right) => {
      if (aiRemoteId && left.remote_line_id === aiRemoteId) {
        return -1
      }
      if (aiRemoteId && right.remote_line_id === aiRemoteId) {
        return 1
      }
      if (left.is_active !== right.is_active) {
        return left.is_active ? -1 : 1
      }
      return formatTelephonyLineLabel(left).localeCompare(formatTelephonyLineLabel(right), 'ru')
    })
  }, [suggestedLineId, telephonyLines])

  const mangoWarningMessages = useMemo(
    () => (mangoReadiness?.warnings || []).map(mapMangoWarning),
    [mangoReadiness],
  )

  const geminiVoiceName = useMemo<string>(() => {
    try {
      const parsed = JSON.parse(form.userSettingsText || '{}') as Record<string, unknown>
      return typeof parsed.gemini_voice_name === 'string' ? parsed.gemini_voice_name : 'Aoede'
    } catch {
      return 'Aoede'
    }
  }, [form.userSettingsText])

  const setGeminiVoiceName = useCallback((name: string) => {
    setForm((current) => {
      let parsed: Record<string, unknown> = {}
      try {
        parsed = JSON.parse(current.userSettingsText || '{}') as Record<string, unknown>
      } catch {
        parsed = {}
      }
      parsed.gemini_voice_name = name
      return { ...current, userSettingsText: JSON.stringify(parsed, null, 2) }
    })
  }, [])

  function updateField<K extends keyof AgentFormState>(key: K, value: AgentFormState[K]) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  function toggleKnowledgeDocument(documentId: string) {
    setForm((current) => {
      const selected = new Set(current.knowledgeDocumentIds)
      if (selected.has(documentId)) {
        selected.delete(documentId)
      } else {
        selected.add(documentId)
      }
      return { ...current, knowledgeDocumentIds: Array.from(selected) }
    })
  }

  async function handleSyncNumbers() {
    if (!token) {
      return
    }
    setSyncingTelephony(true)
    setTelephonyError(null)
    setTelephonyNotice(null)
    setTelephonySuccess(null)
    try {
      const response = await apiFetch<TelephonyLineSyncResponse>(
        '/v1/telephony/mango/sync-lines',
        { method: 'POST' },
        token,
      )
      setTelephonyLines(response.items)
      setTelephonySuccess(`Mango sync завершён: ${response.synced_count} линий обновлено, ${response.deactivated_count} деактивировано.`)
      try {
        const extensions = await apiFetch<TelephonyExtensionListResponse>('/v1/telephony/mango/extensions', {}, token)
        setTelephonyExtensions(extensions.items)
      } catch (err) {
        setTelephonyNotice(mapTelephonyApiError(
          err,
          'Номера обновились, но extensions загрузить не удалось.',
        ))
      }
    } catch (err) {
      setTelephonyError(mapTelephonyApiError(err, 'Не удалось синхронизировать номера из Mango.'))
    } finally {
      setSyncingTelephony(false)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token) {
      return
    }

    setSaving(true)
    setError(null)
    setKnowledgeError(null)

    let parsedSettings: Record<string, unknown>
    try {
      parsedSettings = JSON.parse(form.userSettingsText || '{}') as Record<string, unknown>
    } catch {
      setSaving(false)
      setError('Пользовательские настройки должны быть валидным JSON.')
      return
    }

    try {
      if (isCreateMode) {
        const response = await apiFetch<AgentCreateRead>(
          '/v1/agents',
          {
            method: 'POST',
            body: JSON.stringify({
              name: form.name,
              is_active: form.is_active,
              system_prompt: form.system_prompt,
              tone_rules: form.tone_rules,
              business_rules: form.business_rules,
              sales_objectives: form.sales_objectives,
              greeting_text: form.greeting_text,
              transfer_rules: form.transfer_rules,
              prohibited_promises: form.prohibited_promises,
              voice_strategy: voiceStrategyFromProvider(form.voiceProvider),
              config: parsedSettings,
            }),
          },
          token,
        )
        navigate(`/agents/${response.id}`, { replace: true })
        return
      }

      const response = await apiFetch<AgentSettingsRead>(
        `/v1/agent-profiles/${agentId}/settings`,
        {
          method: 'PATCH',
          body: JSON.stringify({
            name: form.name,
            is_active: form.is_active,
            system_prompt: form.system_prompt,
            tone_rules: form.tone_rules,
            business_rules: form.business_rules,
            sales_objectives: form.sales_objectives,
            greeting_text: form.greeting_text,
              transfer_rules: form.transfer_rules,
              prohibited_promises: form.prohibited_promises,
              voice_provider: form.voiceProvider,
              telephony_provider: form.telephonyRemoteLineId ? 'mango' : null,
              telephony_remote_line_id: form.telephonyRemoteLineId || null,
              telephony_extension: form.telephonyExtension || null,
              user_settings: parsedSettings,
              knowledge_document_ids: form.knowledgeDocumentIds,
          }),
        },
        token,
      )
      setSettings(response)
      setForm(toFormState(response))
      setTelephonySuccess('Настройки агента сохранены. Привязка Mango и voice/runtime поля обновлены.')
    } catch (err) {
      setError(mapTelephonyApiError(err, 'Не удалось сохранить настройки агента.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="stack-page">
      <article className="hero-card split-card">
        <div>
          <p className="eyebrow">Редактор агента</p>
          <h3>{isCreateMode ? 'Создать агента' : form.name || 'Редактировать агента'}</h3>
          <p>
            Здесь собираются реальные настройки runtime: voice provider, prompt/rules, knowledge base и привязка к Mango
            номеру. Browser sandbox остаётся отдельным путём и не ломается, а PSTN-binding сохраняется на уровне
            конкретного агента.
          </p>
        </div>
        <div className="button-row">
          <Link to="/agents" className="ghost-link-button">
            Назад к списку
          </Link>
        </div>
      </article>

      {error ? <div className="error-banner">{error}</div> : null}
      {knowledgeError ? <div className="error-banner">{knowledgeError}</div> : null}
      {telephonyError ? <div className="error-banner">{telephonyError}</div> : null}
      {telephonyNotice ? <div className="warning-banner">{telephonyNotice}</div> : null}
      {telephonySuccess ? <div className="success-banner">{telephonySuccess}</div> : null}

      {loading ? (
        <article className="panel-card empty-state">Загружаем настройки агента…</article>
      ) : (
        <div className="editor-grid">
          <form className="editor-form" onSubmit={handleSubmit}>
            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Идентификация</p>
                  <h4>Основной профиль</h4>
                </div>
              </div>
              <label>
                Название
                <input value={form.name} onChange={(event) => updateField('name', event.target.value)} required />
              </label>
              <label className="toggle-row boxed-toggle">
                <input
                  type="checkbox"
                  checked={form.is_active}
                  onChange={(event) => updateField('is_active', event.target.checked)}
                />
                <span>Агент активен</span>
              </label>
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Telephony / Mango</p>
                  <h4>Привязка номера</h4>
                </div>
                <button type="button" className="ghost-link-button" onClick={() => void handleSyncNumbers()} disabled={syncingTelephony}>
                  {syncingTelephony ? 'Синхронизация…' : 'Sync numbers from Mango'}
                </button>
              </div>

              <div className="debug-list compact-debug">
                <div className="debug-row">
                  <span>provider</span>
                  <strong>Mango</strong>
                </div>
                <div className="debug-row">
                  <span>inventory</span>
                  <strong>{telephonyLoading ? 'загрузка…' : `${telephonyLines.length} линий`}</strong>
                </div>
                <div className="debug-row">
                  <span>extensions</span>
                  <strong>{telephonyLoading ? 'загрузка…' : `${telephonyExtensions.length}`}</strong>
                </div>
                <div className="debug-row">
                  <span>binding</span>
                  <strong>{form.telephonyRemoteLineId ? 'linked' : 'not linked'}</strong>
                </div>
              </div>

              {selectedTelephonyLine ? (
                <div className="info-banner">
                  <strong>Selected line:</strong> {formatTelephonyLineLabel(selectedTelephonyLine)}
                  <br />
                  <span>remote_line_id: {selectedTelephonyLine.remote_line_id}</span>
                </div>
              ) : null}

              <label>
                Номер Mango
                <select
                  value={form.telephonyRemoteLineId}
                  onChange={(event) => {
                    const remoteLineId = event.target.value
                    const matched = telephonyLines.find((line) => line.remote_line_id === remoteLineId)
                    updateField('telephonyRemoteLineId', remoteLineId)
                    if (matched && !form.telephonyExtension) {
                      updateField('telephonyExtension', matched.extension || '')
                    }
                  }}
                  disabled={isCreateMode || telephonyLoading}
                >
                  <option value="">Не привязывать номер</option>
                  {orderedTelephonyLines.map((line) => (
                    <option key={line.remote_line_id} value={line.remote_line_id} disabled={!line.is_active}>
                      {formatTelephonyLineLabel(line)}
                      {suggestedLineId === line.remote_line_id ? ' — suggested' : ''}
                      {!line.is_active ? ' — неактивна' : ''}
                    </option>
                  ))}
                </select>
              </label>

              {suggestedLineId ? (
                <div className="info-banner">
                  Рекомендуемая линия для ИИ-агента найдена.{' '}
                  <button
                    type="button"
                    className="ghost-link-button"
                    onClick={() => updateField('telephonyRemoteLineId', suggestedLineId)}
                  >
                    Выбрать
                  </button>
                </div>
              ) : null}

              {mangoWarningMessages.map((warning) => (
                <div key={warning} className="warning-banner">
                  {warning}
                </div>
              ))}

              {!telephonyLoading && telephonyExtensions.length === 0 ? (
                <div className="info-banner">
                  Mango extensions not configured in this tenant. Line binding remains available without extension binding.
                </div>
              ) : null}

              <label>
                Extension / сотрудник
                <select
                  value={form.telephonyExtension}
                  onChange={(event) => updateField('telephonyExtension', event.target.value)}
                  disabled={isCreateMode || telephonyLoading}
                >
                  <option value="">Не задан</option>
                  {extensionOptions.map((item) => (
                    <option key={`${item.provider_resource_id}-${item.extension}`} value={item.extension}>
                      {item.extension} {item.display_name ? `— ${item.display_name}` : ''}
                    </option>
                  ))}
                </select>
              </label>

              {isCreateMode ? (
                <div className="empty-state">
                  Сначала сохраните агента. После этого номер Mango можно закрепить за конкретным профилем.
                </div>
              ) : null}
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Voice</p>
                  <h4>Voice provider</h4>
                </div>
              </div>

              <div className="voice-strategy-block">
                <p className="field-label">Путь голоса агента</p>
                <div className="voice-toggle-group">
                  <button
                    type="button"
                    className={`voice-toggle-btn${form.voiceProvider === 'gemini' ? ' active' : ''}`}
                    onClick={() => updateField('voiceProvider', 'gemini')}
                  >
                    <span className="voice-toggle-icon">🤖</span>
                    <span className="voice-toggle-title">Gemini voice</span>
                    <span className="voice-toggle-desc">Gemini native audio / gemini_primary</span>
                  </button>
                  <button
                    type="button"
                    className={`voice-toggle-btn${form.voiceProvider === 'elevenlabs' ? ' active' : ''}`}
                    onClick={() => updateField('voiceProvider', 'elevenlabs')}
                  >
                    <span className="voice-toggle-icon">🎙️</span>
                    <span className="voice-toggle-title">ElevenLabs voice</span>
                    <span className="voice-toggle-desc">Gemini text + ElevenLabs / tts_primary</span>
                  </button>
                </div>

                {form.voiceProvider === 'gemini' ? (
                  <div className="voice-sub-options">
                    <label className="field-label" htmlFor="gemini-voice-select">
                      Выбор голоса Gemini
                    </label>
                    <select
                      id="gemini-voice-select"
                      value={geminiVoiceName}
                      onChange={(event) => setGeminiVoiceName(event.target.value)}
                    >
                      {GEMINI_VOICES.map((voice) => (
                        <option key={voice.value} value={voice.value}>{voice.label}</option>
                      ))}
                    </select>
                  </div>
                ) : (
                  <div className="voice-sub-options voice-sub-info">
                    Для ElevenLabs используется глобальный voice configuration из Providers. На агенте фиксируется сам voice path.
                  </div>
                )}
              </div>
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Диалог</p>
                  <h4>Промпт и приветствие</h4>
                </div>
              </div>
              <label>
                Системный промпт
                <textarea
                  value={form.system_prompt}
                  onChange={(event) => updateField('system_prompt', event.target.value)}
                  rows={10}
                  required
                />
              </label>
              <label>
                Приветствие
                <textarea
                  value={form.greeting_text}
                  onChange={(event) => updateField('greeting_text', event.target.value)}
                  rows={4}
                />
              </label>
              <label>
                Правила тона
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
                  <p className="eyebrow">Бизнес-логика</p>
                  <h4>Цели и ограничения</h4>
                </div>
              </div>
              <label>
                Бизнес-правила
                <textarea
                  value={form.business_rules}
                  onChange={(event) => updateField('business_rules', event.target.value)}
                  rows={5}
                />
              </label>
              <label>
                Цели продаж
                <textarea
                  value={form.sales_objectives}
                  onChange={(event) => updateField('sales_objectives', event.target.value)}
                  rows={5}
                />
              </label>
              <label>
                Правила передачи
                <textarea
                  value={form.transfer_rules}
                  onChange={(event) => updateField('transfer_rules', event.target.value)}
                  rows={5}
                />
              </label>
              <label>
                Запрещённые обещания
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
                  <p className="eyebrow">User settings</p>
                  <h4>JSON настройки runtime</h4>
                </div>
              </div>
              <label>
                Пользовательские настройки
                <textarea
                  value={form.userSettingsText}
                  onChange={(event) => updateField('userSettingsText', event.target.value)}
                  rows={8}
                  className="mono-textarea"
                />
              </label>
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Knowledge Base</p>
                  <h4>Контролируемый контекст агента</h4>
                </div>
              </div>

              {knowledgeLoading ? (
                <div className="empty-state">Загружаем доступные документы…</div>
              ) : knowledgeDocuments.length === 0 ? (
                <div className="empty-state">В knowledge base пока нет активных документов.</div>
              ) : (
                <div className="knowledge-binding-list">
                  {knowledgeDocuments.map((document) => (
                    <label key={document.id} className="checkbox-card">
                      <input
                        type="checkbox"
                        checked={selectedKnowledgeIds.has(document.id)}
                        disabled={isCreateMode}
                        onChange={() => toggleKnowledgeDocument(document.id)}
                      />
                      <div>
                        <div className="table-primary">{document.title}</div>
                        <div className="inline-meta">
                          <span>{document.category}</span>
                          <span>{selectedKnowledgeIds.has(document.id) ? 'привязан' : 'не привязан'}</span>
                        </div>
                      </div>
                    </label>
                  ))}
                </div>
              )}

              {isCreateMode ? (
                <div className="empty-state">
                  Сначала сохраните агента. После этого выбранные документы можно будет записать в agent settings одним PATCH.
                </div>
              ) : null}
            </section>

            <section className="panel-card form-section">
              <div className="button-row">
                <button type="submit" className="primary-button" disabled={saving}>
                  {saving ? 'Сохранение…' : isCreateMode ? 'Создать агента' : 'Сохранить настройки агента'}
                </button>
              </div>
            </section>
          </form>

          <aside className="editor-sidebar">
            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Превью</p>
                  <h4>Собранный промпт</h4>
                </div>
              </div>
              <pre className="preview-block">{previewText}</pre>
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Сводка знаний</p>
                  <h4>Что подключено</h4>
                </div>
              </div>
              {selectedKnowledgeDocuments.length === 0 ? (
                <div className="empty-state">Для этого агента пока не выбраны knowledge documents.</div>
              ) : (
                <div className="binding-summary-list">
                  {selectedKnowledgeDocuments.map((document) => (
                    <div key={document.id} className="binding-summary-item">
                      <strong>{document.title}</strong>
                      <span>{document.category}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Телефония</p>
                  <h4>Текущая привязка</h4>
                </div>
              </div>
              <div className="debug-list compact-debug">
                <div className="debug-row">
                  <span>provider</span>
                  <strong>{form.telephonyRemoteLineId ? 'mango' : '—'}</strong>
                </div>
                <div className="debug-row">
                  <span>selected line</span>
                  <strong>{selectedTelephonyLine ? formatTelephonyLineLabel(selectedTelephonyLine) : 'не привязан'}</strong>
                </div>
                <div className="debug-row">
                  <span>remote_line_id</span>
                  <strong>{selectedTelephonyLine?.remote_line_id || '—'}</strong>
                </div>
                <div className="debug-row">
                  <span>номер</span>
                  <strong>{selectedTelephonyLine?.phone_number || 'не привязан'}</strong>
                </div>
                <div className="debug-row">
                  <span>extension</span>
                  <strong>{form.telephonyExtension || '—'}</strong>
                </div>
                <div className="debug-row">
                  <span>inbound</span>
                  <strong>{selectedTelephonyLine ? (selectedTelephonyLine.is_inbound_enabled ? 'yes' : 'no') : '—'}</strong>
                </div>
                <div className="debug-row">
                  <span>outbound</span>
                  <strong>{selectedTelephonyLine ? (selectedTelephonyLine.is_outbound_enabled ? 'yes' : 'no') : '—'}</strong>
                </div>
              </div>
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">Метаданные</p>
                  <h4>Версионирование</h4>
                </div>
              </div>
              <div className="debug-list compact-debug">
                <div className="debug-row">
                  <span>версия</span>
                  <strong>{settings ? `v${settings.version}` : 'новый'}</strong>
                </div>
                <div className="debug-row">
                  <span>обновлён</span>
                  <strong>{settings ? new Date(settings.updated_at).toLocaleString() : '—'}</strong>
                </div>
                <div className="debug-row">
                  <span>voice path</span>
                  <strong>{voiceStrategyFromProvider(form.voiceProvider)}</strong>
                </div>
                <div className="debug-row">
                  <span>voice provider</span>
                  <strong>{form.voiceProvider}</strong>
                </div>
                <div className="debug-row">
                  <span>документы знаний</span>
                  <strong>{form.knowledgeDocumentIds.length}</strong>
                </div>
              </div>
            </section>
          </aside>
        </div>
      )}
    </section>
  )
}
