import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'

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
  is_recommended_for_ai?: boolean
  is_protected?: boolean
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

function hasMangoInventory(items: TelephonyLine[]) {
  return items.length > 0
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
  suggested_telephony_remote_line_id?: string | null
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
  userLocale: string
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
  userLocale: 'ru-RU',
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
    userLocale: typeof settings.user_settings?.locale === 'string' ? settings.user_settings.locale : 'ru-RU',
    userSettingsText: JSON.stringify(settings.user_settings || {}, null, 2),
    knowledgeDocumentIds: settings.knowledge_document_ids || [],
  }
}

function voiceStrategyFromProvider(provider: AgentFormState['voiceProvider']): 'gemini_primary' | 'tts_primary' {
  return provider === 'gemini' ? 'gemini_primary' : 'tts_primary'
}

function buildAgentUserSettings(form: AgentFormState): Record<string, unknown> {
  let parsedSettings: Record<string, unknown> = {}
  try {
    parsedSettings = JSON.parse(form.userSettingsText || '{}') as Record<string, unknown>
  } catch {
    parsedSettings = {}
  }

  parsedSettings.locale = form.userLocale || 'ru-RU'
  if (form.voiceProvider === 'gemini') {
    parsedSettings.gemini_voice_name =
      typeof parsedSettings.gemini_voice_name === 'string' ? parsedSettings.gemini_voice_name : 'Aoede'
  }
  return parsedSettings
}

function formatTelephonyLineLabel(line: TelephonyLine): string {
  const primary = line.schema_name || line.label || line.display_name || line.phone_number
  return primary === line.phone_number ? line.phone_number : `${primary} (${line.phone_number})`
}

function getTelephonyLineBadge(line: TelephonyLine): string | null {
  if (isProtectedTelephonyLine(line)) {
    return 'Резерв / не трогать'
  }
  if (line.is_recommended_for_ai) {
    return 'Основная AI-линия'
  }
  return null
}

function isProtectedTelephonyLine(line: Pick<TelephonyLine, 'is_protected' | 'phone_number'>): boolean {
  return Boolean(line.is_protected || line.phone_number === '+79585382099')
}

function mapMangoWarning(warning: string): string {
  if (warning.includes('MANGO_WEBHOOK_SECRET')) {
    return 'Проверка входящего вебхука не настроена. Задайте MANGO_WEBHOOK_SECRET перед боевой маршрутизацией входящих звонков.'
  }
  if (warning.includes('auto-discovered Mango extension')) {
    return 'Для исходящих звонков будет использован автоматически найденный внутренний номер Mango. Для предсказуемого боевого исходящего вызова всё ещё лучше явно задать MANGO_FROM_EXT.'
  }
  if (warning.includes('MANGO_FROM_EXT')) {
    return 'Исходящие звонки не настроены. Задайте MANGO_FROM_EXT перед боевым исходящим вызовом.'
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
    return 'Mango не настроен. Задайте MANGO_API_KEY и MANGO_API_SALT в окружении backend.'
  }
  if (code === 'mango_api_unavailable') {
    if (getNestedHttpStatus(err) === 429) {
      return 'Mango временно ограничил API внутренних номеров по лимиту запросов. Привязка линии остаётся доступной, повторите попытку позже.'
    }
    return 'Mango API временно недоступен. Проверьте доступность аккаунта и попробуйте ещё раз.'
  }
  if (code === 'mango_sync_failed') {
    return 'Не удалось синхронизировать линии Mango. Проверьте ответ API и повторите синхронизацию.'
  }
  if (code === 'telephony_line_not_found') {
    return 'Выбранная линия Mango не найдена. Сначала обновите инвентарь и выберите существующую линию.'
  }
  if (code === 'telephony_line_inactive') {
    return 'Выбранная линия Mango неактивна. Сохранение заблокировано, пока вы не выберете активную линию.'
  }
  if (code === 'telephony_line_protected') {
    return 'Линия +79585382099 защищена. Её нельзя назначать агентам или менять в настройках.'
  }
  if (code === 'invalid_voice_provider') {
    return 'Выбран неподдерживаемый голосовой провайдер. Используйте Gemini или ElevenLabs.'
  }

  return err.message || fallback
}

export default function AgentEditorPage() {
  const { token } = useAuth()
  const { agentId } = useParams()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
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
  const currentBindingRef = useRef<{ hasLine: boolean; remoteLineId: string | null }>({
    hasLine: false,
    remoteLineId: null,
  })

  useEffect(() => {
    currentBindingRef.current = {
      hasLine: Boolean(settings?.telephony_line),
      remoteLineId: settings?.telephony_remote_line_id ?? null,
    }
  }, [settings])

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
      setKnowledgeError(err instanceof ApiError ? err.message : 'Не удалось загрузить базу знаний.')
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
      const [linesResult, extensionsResult, readinessResult] = await Promise.allSettled([
        apiFetch<TelephonyLineListResponse>('/v1/telephony/mango/lines', {}, token),
        apiFetch<TelephonyExtensionListResponse>('/v1/telephony/mango/extensions', {}, token),
        apiFetch<MangoReadiness>('/v1/telephony/mango/readiness', {}, token),
      ])

      const nextReadiness =
        readinessResult.status === 'fulfilled'
          ? readinessResult.value
          : null
      const nextLines =
        linesResult.status === 'fulfilled'
          ? linesResult.value.items
          : []

      setMangoReadiness(nextReadiness)
      setTelephonyLines(nextLines)

      if (extensionsResult.status === 'fulfilled') {
        setTelephonyExtensions(extensionsResult.value.items)
      } else if (extensionsResult.reason instanceof ApiError) {
        setTelephonyNotice(
          mapTelephonyApiError(
            extensionsResult.reason,
            'Внутренние номера Mango временно недоступны. Привязка линии остаётся доступной.',
          ),
        )
        setTelephonyExtensions([])
      } else {
        setTelephonyExtensions([])
      }

      if (linesResult.status === 'rejected') {
        const nextError = mapTelephonyApiError(linesResult.reason, 'Не удалось загрузить список номеров Mango.')
        const shouldSuppressHardConfigError =
          nextReadiness?.api_configured
          || hasMangoInventory(nextLines)
          || currentBindingRef.current.hasLine
          || Boolean(currentBindingRef.current.remoteLineId)

        if (!(shouldSuppressHardConfigError && nextError.includes('Mango не настроен'))) {
          setTelephonyError(nextError)
        }
      }
    } catch (err) {
      setTelephonyError(mapTelephonyApiError(err, 'Не удалось загрузить список номеров Mango.'))
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

  // Auto-select AI-recommended line when agent has no binding and inventory is loaded.
  // Runs once after both agent settings and telephony lines are ready.
  useEffect(() => {
    if (isCreateMode || loading || telephonyLoading) return
    if (form.telephonyRemoteLineId) return  // already bound — don't override

    // Priority: backend suggestion → is_recommended_for_ai flag → schema_name → ID fallback
    const suggestionFromApi = settings?.suggested_telephony_remote_line_id ?? null
    const byFlag = telephonyLines.find((l) => l.is_active && l.is_recommended_for_ai)
    const bySchema = telephonyLines.find(
      (l) => l.is_active && !isProtectedTelephonyLine(l) && (l.schema_name || '').trim() === 'ДЛЯ ИИ менеджера',
    )
    const byId = telephonyLines.find((l) => l.is_active && !isProtectedTelephonyLine(l) && l.remote_line_id === '405622036')
    const candidateId =
      (suggestionFromApi && telephonyLines.find((l) => l.remote_line_id === suggestionFromApi && !isProtectedTelephonyLine(l))
        ? suggestionFromApi
        : null)
      ?? byFlag?.remote_line_id
      ?? bySchema?.remote_line_id
      ?? byId?.remote_line_id
      ?? null

    if (!candidateId) return
    const candidateLine = telephonyLines.find((l) => l.remote_line_id === candidateId)
    setForm((prev) => ({
      ...prev,
      telephonyRemoteLineId: candidateId,
      telephonyExtension: prev.telephonyExtension || candidateLine?.extension || '',
    }))
  // form.telephonyRemoteLineId intentionally excluded from deps — effect runs once after data loads
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isCreateMode, loading, telephonyLoading, settings, telephonyLines])

  const previewText = useMemo(() => {
    if (settings) {
      return settings.assembled_prompt_preview
    }
    return 'Предпросмотр появится после первого сохранения. Backend централизованно собирает рантайм-промпт и отдельно подключает базу знаний.'
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
      (line) => line.is_active && !isProtectedTelephonyLine(line) && ((line.schema_name || '').trim() === 'ДЛЯ ИИ менеджера'),
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
      if (isProtectedTelephonyLine(left) !== isProtectedTelephonyLine(right)) {
        return isProtectedTelephonyLine(left) ? 1 : -1
      }
      return formatTelephonyLineLabel(left).localeCompare(formatTelephonyLineLabel(right), 'ru')
    })
  }, [suggestedLineId, telephonyLines])

  const mangoWarningMessages = useMemo(
    () => (mangoReadiness?.warnings || []).map(mapMangoWarning),
    [mangoReadiness],
  )

  const focusedRemoteLineId = searchParams.get('mango_line') || ''
  const cameFromProviders = searchParams.get('from') === 'providers'

  const providersDeepLink = useMemo(() => {
    const targetLineId = focusedRemoteLineId || form.telephonyRemoteLineId || selectedTelephonyLine?.remote_line_id || ''
    if (!targetLineId) {
      return '/providers'
    }
    return `/providers?line=${encodeURIComponent(targetLineId)}`
  }, [focusedRemoteLineId, form.telephonyRemoteLineId, selectedTelephonyLine])

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
      setTelephonySuccess(`Синхронизация Mango завершена: ${response.synced_count} линий обновлено, ${response.deactivated_count} деактивировано.`)
      try {
        const extensions = await apiFetch<TelephonyExtensionListResponse>('/v1/telephony/mango/extensions', {}, token)
        setTelephonyExtensions(extensions.items)
      } catch (err) {
        setTelephonyNotice(mapTelephonyApiError(
          err,
          'Номера обновились, но внутренние номера загрузить не удалось.',
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

    const parsedSettings = buildAgentUserSettings(form)

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
      setTelephonySuccess('Настройки агента сохранены. Привязка Mango и голосовые поля рантайма обновлены.')
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
            Здесь собираются реальные настройки рантайма: голосовой провайдер, промпт и правила, база знаний и привязка к Mango
            номеру. Браузерная песочница остаётся отдельным путём и не ломается, а PSTN-привязка сохраняется на уровне
            конкретного агента.
          </p>
        </div>
        <div className="button-row">
          <Link to="/agents" className="ghost-link-button">
            Назад к списку
          </Link>
          {(cameFromProviders || focusedRemoteLineId || form.telephonyRemoteLineId) ? (
            <Link to={providersDeepLink} className="ghost-link-button">
              Назад к Mango для этой линии
            </Link>
          ) : null}
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
                  <p className="eyebrow">Телефония / Mango</p>
                  <h4>Привязка номера</h4>
                  <p className="compact-copy">Здесь агент получает конкретный номер Mango для основы входящей и исходящей маршрутизации.</p>
                </div>
                <button type="button" className="ghost-link-button" onClick={() => void handleSyncNumbers()} disabled={syncingTelephony}>
                  {syncingTelephony ? 'Синхронизация…' : 'Синхронизировать номера из Mango'}
                </button>
              </div>

              <div className="saas-summary-grid telephony-summary-grid">
                <div className="saas-summary-card">
                  <span>Провайдер телефонии</span>
                  <strong>Mango</strong>
                </div>
                <div className="saas-summary-card">
                  <span>Доступно номеров</span>
                  <strong>{telephonyLoading ? '…' : telephonyLines.length}</strong>
                </div>
                <div className="saas-summary-card">
                  <span>Внутренние номера</span>
                  <strong>{telephonyLoading ? '…' : telephonyExtensions.length}</strong>
                </div>
                <div className="saas-summary-card">
                  <span>Статус привязки</span>
                  <strong>{form.telephonyRemoteLineId ? 'Назначен агенту' : 'Не назначен'}</strong>
                </div>
              </div>

              {selectedTelephonyLine ? (
                <div className="binding-cta-card">
                  <div>
                    <p className="binding-cta-title">Назначено агенту</p>
                    <p className="binding-cta-copy">{formatTelephonyLineLabel(selectedTelephonyLine)}</p>
                    <div className="table-secondary">remote_line_id: {selectedTelephonyLine.remote_line_id}</div>
                  </div>
                  <span
                    className={`status-pill${
                      isProtectedTelephonyLine(selectedTelephonyLine)
                        ? ' error'
                        : selectedTelephonyLine.is_recommended_for_ai
                          ? ' live'
                          : ''
                    }`}
                  >
                    {getTelephonyLineBadge(selectedTelephonyLine) || 'Назначено'}
                  </span>
                </div>
              ) : null}

              {telephonyLines.some((line) => isProtectedTelephonyLine(line)) ? (
                <div className="warning-banner">
                  <strong>Защищённая линия:</strong> номер <strong>+79585382099</strong> зарезервирован. Его нельзя назначать агентам или менять через эту форму.
                </div>
              ) : null}

              {!selectedTelephonyLine && focusedRemoteLineId ? (
                <div className="info-banner">
                  Страница Mango передала фокус на линию <strong>{focusedRemoteLineId}</strong>. Синхронизируйте инвентарь или выберите линию заново, если её пока нет в списке.
                </div>
              ) : null}

              {!selectedTelephonyLine && telephonyLines.length > 0 ? (
                <div className="info-banner">
                  <strong>Назначьте номер агенту, чтобы включить звонки.</strong> Выберите линию ниже и сохраните настройки агента.
                </div>
              ) : null}

              <label>
                Выберите номер Mango
                <select
                  value={form.telephonyRemoteLineId}
                  onChange={(event) => {
                    const remoteLineId = event.target.value
                    const matched = telephonyLines.find((line) => line.remote_line_id === remoteLineId)
                    if (matched && isProtectedTelephonyLine(matched)) {
                      setTelephonyError('Линия +79585382099 защищена. Её нельзя назначать агентам или менять в настройках.')
                      return
                    }
                    updateField('telephonyRemoteLineId', remoteLineId)
                    if (matched && !form.telephonyExtension) {
                      updateField('telephonyExtension', matched.extension || '')
                    }
                  }}
                  disabled={isCreateMode || telephonyLoading}
                >
                  <option value="">Не привязывать номер</option>
                  {orderedTelephonyLines.map((line) => (
                  <option key={line.remote_line_id} value={line.remote_line_id} disabled={!line.is_active || isProtectedTelephonyLine(line)}>
                      {formatTelephonyLineLabel(line)}
                      {getTelephonyLineBadge(line) ? ` — ${getTelephonyLineBadge(line)}` : suggestedLineId === line.remote_line_id ? ' — рекомендовано' : ''}
                      {!line.is_active ? ' — неактивна' : ''}
                    </option>
                  ))}
                </select>
              </label>

              {suggestedLineId ? (
                <div className="info-banner">
                  <strong>Рекомендовано для AI:</strong> найдена линия “ДЛЯ ИИ менеджера”.{' '}
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
                  В этом кабинете Mango не настроены внутренние номера. Привязка линии всё равно доступна и без выбора внутреннего номера.
                </div>
              ) : null}

              <label>
                Внутренний номер / сотрудник
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
                  <p className="eyebrow">Голос</p>
                  <h4>Голосовой провайдер</h4>
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
                    <span className="voice-toggle-title">Голос Gemini</span>
                    <span className="voice-toggle-desc">Нативное аудио Gemini / `gemini_primary`</span>
                  </button>
                  <button
                    type="button"
                    className={`voice-toggle-btn${form.voiceProvider === 'elevenlabs' ? ' active' : ''}`}
                    onClick={() => updateField('voiceProvider', 'elevenlabs')}
                  >
                    <span className="voice-toggle-icon">🎙️</span>
                    <span className="voice-toggle-title">Голос ElevenLabs</span>
                    <span className="voice-toggle-desc">Текст Gemini + ElevenLabs / `tts_primary`</span>
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
                    Для ElevenLabs используется глобальная настройка голоса из раздела «Провайдеры». На агенте фиксируется только сам голосовой путь.
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
                  <p className="eyebrow">Пользовательские настройки</p>
                  <h4>Понятные настройки рантайма</h4>
                </div>
              </div>
              <label>
                Язык общения
                <select
                  value={form.userLocale}
                  onChange={(event) => updateField('userLocale', event.target.value)}
                >
                  <option value="ru-RU">Русский</option>
                  <option value="en-US">English</option>
                </select>
              </label>
              <details className="advanced-settings">
                <summary>Расширенные настройки для техподдержки</summary>
                <p className="compact-copy table-secondary">
                  Обычному пользователю этот JSON не нужен. Он оставлен только для точечной отладки и редких служебных правок.
                </p>
                <label>
                  Служебные параметры рантайма
                  <textarea
                    value={form.userSettingsText}
                    onChange={(event) => updateField('userSettingsText', event.target.value)}
                    rows={8}
                    className="mono-textarea"
                  />
                </label>
              </details>
            </section>

            <section className="panel-card form-section">
              <div className="panel-header">
                <div>
                  <p className="eyebrow">База знаний</p>
                  <h4>Контролируемый контекст агента</h4>
                </div>
              </div>

              {knowledgeLoading ? (
                <div className="empty-state">Загружаем доступные документы…</div>
              ) : knowledgeDocuments.length === 0 ? (
                <div className="empty-state">В базе знаний пока нет активных документов.</div>
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
                <div className="empty-state">Для этого агента пока не выбраны документы базы знаний.</div>
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
                  <span>провайдер</span>
                  <strong>{form.telephonyRemoteLineId ? 'mango' : '—'}</strong>
                </div>
                <div className="debug-row">
                  <span>выбранная линия</span>
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
                  <span>внутренний номер</span>
                  <strong>{form.telephonyExtension || '—'}</strong>
                </div>
                <div className="debug-row">
                  <span>входящие</span>
                  <strong>{selectedTelephonyLine ? (selectedTelephonyLine.is_inbound_enabled ? 'да' : 'нет') : '—'}</strong>
                </div>
                <div className="debug-row">
                  <span>исходящие</span>
                  <strong>{selectedTelephonyLine ? (selectedTelephonyLine.is_outbound_enabled ? 'да' : 'нет') : '—'}</strong>
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
                  <span>путь голоса</span>
                  <strong>{voiceStrategyFromProvider(form.voiceProvider)}</strong>
                </div>
                <div className="debug-row">
                  <span>голосовой провайдер</span>
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
