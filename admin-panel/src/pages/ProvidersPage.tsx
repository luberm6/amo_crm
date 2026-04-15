import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { ApiError, apiFetch } from '../lib/api'

type ProviderName = 'mango' | 'gemini' | 'elevenlabs' | 'vapi'

type ProviderSecretState = {
  is_set: boolean
  masked_value?: string | null
}

type ProviderSetting = {
  provider: ProviderName
  display_name: string
  is_enabled: boolean
  activation_status: 'active' | 'inactive'
  status: 'configured' | 'invalid' | 'not_tested'
  safe_mode_note: string
  config: Record<string, unknown>
  secrets: Record<string, ProviderSecretState>
  last_validated_at?: string | null
  last_validation_message?: string | null
  last_validation_remote_checked: boolean
}

type ProviderSettingsResponse = {
  items: ProviderSetting[]
}

type ProviderValidationResponse = {
  provider: ProviderName
  status: 'configured' | 'invalid' | 'not_tested'
  message: string
  remote_checked: boolean
  checked_at: string
}

type ProviderFormState = {
  is_enabled: boolean
  config: Record<string, unknown>
  secrets: Record<string, string>
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

type MangoReadiness = {
  api_configured: boolean
  webhook_secret_configured: boolean
  from_ext_configured: boolean
  from_ext_auto_discoverable?: boolean
  telephony_runtime_provider: string
  telephony_runtime_real: boolean
  backend_url: string
  webhook_url: string
  webhook_url_public: boolean
  inbound_webhook_smoke_ready: boolean
  outbound_originate_smoke_ready: boolean
  inbound_ai_runtime_ready: boolean
  missing_requirements: string[]
  warnings: string[]
  route_readiness?: Record<string, {
    key: 'inbound_webhook' | 'outbound_originate' | 'inbound_ai_runtime'
    ready: boolean
    status: 'ready' | 'blocked'
    summary: string
    blockers: string[]
  }>
  render_summary?: {
    ready_count: number
    blocked_count: number
    overall_status: 'ready' | 'partial' | 'blocked'
    operator_summary: string
  }
  actionable_next_step?: {
    key: string
    title: string
    description: string
    cta_label: string
    scope: 'global' | 'inbound_webhook' | 'outbound_originate' | 'inbound_ai_runtime'
  }
}

type MangoRoutingMapItem = {
  line_id: string
  provider_resource_id: string
  remote_line_id: string
  phone_number: string
  schema_name?: string | null
  display_name?: string | null
  label: string
  is_active: boolean
  is_inbound_enabled: boolean
  agent_id?: string | null
  agent_name?: string | null
  agent_is_active?: boolean | null
}

type MangoRoutingMapResponse = {
  items: MangoRoutingMapItem[]
  total: number
}

type InventoryFilter = 'all' | 'recommended_only' | 'unbound_only'

type ProviderField = {
  key: string
  label: string
  kind: 'text' | 'textarea' | 'boolean'
  help?: string
}

type ProviderDefinition = {
  provider: ProviderName
  description: string
  configFields: ProviderField[]
  secretFields: ProviderField[]
}

const PROVIDER_DEFINITIONS: ProviderDefinition[] = [
  {
    provider: 'mango',
    description: 'Хранит учётные данные Mango как слой настроек. Не включает AI-маршрутизацию и не трогает номера amoCRM.',
    configFields: [
      {
        key: 'from_ext',
        label: 'Исходный внутренний номер',
        kind: 'text',
        help: 'Сохраняется как опорная настройка. Сам по себе не включает исходящий маршрут.',
      },
      {
        key: 'webhook_ip_allowlist',
        label: 'Список IP для вебхука',
        kind: 'textarea',
        help: 'Список CIDR/IP для будущей конфигурации вебхука.',
      },
    ],
    secretFields: [
      { key: 'api_key', label: 'API-ключ', kind: 'text' },
      { key: 'api_salt', label: 'Соль API-подписи', kind: 'text' },
      { key: 'webhook_secret', label: 'Секрет подписи вебхука', kind: 'text' },
      { key: 'webhook_shared_secret', label: 'Общий секрет вебхука', kind: 'text' },
    ],
  },
  {
    provider: 'gemini',
    description: 'Настройки модели и API Gemini для direct-runtime контура и браузерного звонка. Сохранение не переключает рантайм автоматически.',
    configFields: [
      { key: 'model_id', label: 'Идентификатор модели', kind: 'text' },
      { key: 'api_version', label: 'Версия API', kind: 'text' },
    ],
    secretFields: [{ key: 'api_key', label: 'API-ключ', kind: 'text' }],
  },
  {
    provider: 'elevenlabs',
    description: 'Настройки TTS-провайдера. Сохранение само по себе не делает ElevenLabs активным голосовым путём.',
    configFields: [
      { key: 'voice_id', label: 'Идентификатор голоса', kind: 'text' },
      { key: 'enabled', label: 'Провайдер включён', kind: 'boolean', help: 'Флаг хранится отдельно как операторское намерение.' },
    ],
    secretFields: [{ key: 'api_key', label: 'API-ключ', kind: 'text' }],
  },
  {
    provider: 'vapi',
    description: 'Дополнительные настройки маршрутного провайдера. Сохранение не меняет активный маршрут и не затрагивает браузерный или direct-runtime контур без явной связки.',
    configFields: [
      { key: 'assistant_id', label: 'Идентификатор ассистента', kind: 'text' },
      { key: 'phone_number_id', label: 'Идентификатор номера', kind: 'text' },
      { key: 'base_url', label: 'Базовый URL', kind: 'text' },
      { key: 'server_url', label: 'Серверный URL', kind: 'text' },
    ],
    secretFields: [
      { key: 'api_key', label: 'API-ключ', kind: 'text' },
      { key: 'webhook_secret', label: 'Секрет вебхука', kind: 'text' },
    ],
  },
]

function buildInitialForm(setting: ProviderSetting): ProviderFormState {
  const secretDrafts = Object.keys(setting.secrets).reduce<Record<string, string>>((acc, key) => {
    acc[key] = ''
    return acc
  }, {})
  return {
    is_enabled: setting.is_enabled,
    config: { ...setting.config },
    secrets: secretDrafts,
  }
}

function formatTimestamp(value?: string | null) {
  if (!value) {
    return 'Проверка ещё не запускалась'
  }
  return new Date(value).toLocaleString()
}

function formatMangoLineLabel(line: Pick<TelephonyLine, 'schema_name' | 'label' | 'display_name' | 'phone_number'>) {
  const primary = line.schema_name || line.label || line.display_name || line.phone_number
  return primary === line.phone_number ? line.phone_number : `${primary} (${line.phone_number})`
}

function summarizeMangoIssue(readiness: MangoReadiness | null, linesCount: number, extensionsCount = 0) {
  if (!readiness?.api_configured) {
    return 'Не заданы API-учётные данные'
  }
  if (linesCount === 0) {
    return 'Линии ещё не синхронизированы'
  }
  if (!readiness.webhook_secret_configured) {
    return 'Не настроен вебхук'
  }
  if (!readiness.from_ext_configured && !readiness.from_ext_auto_discoverable) {
    return 'Не задан исходный внутренний номер'
  }
  if (extensionsCount === 0) {
    return 'Нет внутренних номеров'
  }
  return 'Критичных блокеров не найдено'
}

function formatIssueLabel(issue: string) {
  switch (issue) {
    case 'API credentials missing':
      return 'Не заданы API-учётные данные'
    case 'No synced numbers':
      return 'Номера не синхронизированы'
    case 'Webhook missing':
      return 'Не настроен вебхук'
    case 'FROM_EXT missing':
      return 'Не задан исходный внутренний номер'
    case 'FROM_EXT auto-discovered':
      return 'Исходный номер найден автоматически'
    case 'No agent binding yet':
      return 'Номер ещё не назначен агенту'
    default:
      return issue
  }
}

function mapMangoRequirementLabel(requirement: string) {
  switch (requirement) {
    case 'mango_api_credentials_missing':
      return 'Не заданы Mango API-учётные данные.'
    case 'mango_webhook_secret_missing':
      return 'Не задан секрет вебхука, поэтому входящий вебхук не защищён.'
    case 'backend_url_not_public':
      return 'BACKEND_URL не является публичным, поэтому Mango не сможет достучаться до этого backend на Render.'
    case 'mango_from_ext_missing':
      return 'Не задан MANGO_FROM_EXT, и стабильный fallback не найден.'
    case 'telephony_runtime_not_real':
      return 'Рантайм телефонии ещё не переключён на реальный маршрут Mango.'
    case 'media_gateway_disabled':
      return 'MEDIA_GATEWAY_ENABLED=false, поэтому входящий AI-маршрут заблокирован.'
    case 'media_gateway_provider_not_freeswitch':
      return 'Для входящего AI-маршрута ожидается MEDIA_GATEWAY_PROVIDER=freeswitch.'
    case 'media_gateway_mode_not_supported':
      return 'Для входящего AI-маршрута ожидается MEDIA_GATEWAY_MODE=mock или esl_rtp.'
    default:
      return requirement
  }
}

function collectReadinessBlockers(readiness: MangoReadiness | null, scope: 'webhook' | 'outbound' | 'inbound_ai') {
  if (!readiness) {
    return ['Данные о готовности недоступны.']
  }
  const scopeKey = scope === 'webhook' ? 'inbound_webhook' : scope === 'outbound' ? 'outbound_originate' : 'inbound_ai_runtime'
  const routeScope = readiness.route_readiness?.[scopeKey]
  if (routeScope?.blockers?.length) {
    return routeScope.blockers
  }
  const requirements = new Set(readiness.missing_requirements || [])
  if (scope === 'webhook') {
    return [
      requirements.has('mango_api_credentials_missing') ? mapMangoRequirementLabel('mango_api_credentials_missing') : null,
      requirements.has('mango_webhook_secret_missing') ? mapMangoRequirementLabel('mango_webhook_secret_missing') : null,
      requirements.has('backend_url_not_public') ? mapMangoRequirementLabel('backend_url_not_public') : null,
    ].filter(Boolean) as string[]
  }
  if (scope === 'outbound') {
    return [
      requirements.has('mango_api_credentials_missing') ? mapMangoRequirementLabel('mango_api_credentials_missing') : null,
      requirements.has('mango_from_ext_missing') ? mapMangoRequirementLabel('mango_from_ext_missing') : null,
      requirements.has('telephony_runtime_not_real') ? mapMangoRequirementLabel('telephony_runtime_not_real') : null,
    ].filter(Boolean) as string[]
  }
  return [
    requirements.has('mango_api_credentials_missing') ? mapMangoRequirementLabel('mango_api_credentials_missing') : null,
    requirements.has('mango_webhook_secret_missing') ? mapMangoRequirementLabel('mango_webhook_secret_missing') : null,
    requirements.has('backend_url_not_public') ? mapMangoRequirementLabel('backend_url_not_public') : null,
    requirements.has('media_gateway_disabled') ? mapMangoRequirementLabel('media_gateway_disabled') : null,
    requirements.has('media_gateway_provider_not_freeswitch') ? mapMangoRequirementLabel('media_gateway_provider_not_freeswitch') : null,
    requirements.has('media_gateway_mode_not_supported') ? mapMangoRequirementLabel('media_gateway_mode_not_supported') : null,
  ].filter(Boolean) as string[]
}

function mapMangoReadinessWarning(warning: string) {
  if (warning.includes('MANGO_WEBHOOK_SECRET')) {
    return 'Проверка входящего вебхука не настроена. Входящий путь вебхука пока не защищён.'
  }
  if (warning.includes('BACKEND_URL')) {
    return 'BACKEND_URL не является публичным. Mango не сможет доставить боевой вебхук в этот backend, пока URL не станет внешне доступным.'
  }
  if (warning.includes('auto-discovered Mango extension')) {
    return 'Исходный внутренний номер будет найден автоматически. Для предсказуемого исходящего вызова всё равно лучше явно задать MANGO_FROM_EXT.'
  }
  if (warning.includes('MANGO_FROM_EXT')) {
    return 'Исходящие звонки не готовы: не задан MANGO_FROM_EXT и нет автоматического fallback-подбора.'
  }
  if (warning.includes('telephony provider')) {
    return 'Direct-runtime контур сейчас не смотрит в реальный маршрут телефонии. Smoke-проверка PSTN пока упрётся не в Mango, а в тестовый провайдер.'
  }
  if (warning.includes('MEDIA_GATEWAY_ENABLED=false')) {
    return 'Входящий AI-рантайм заблокирован: MEDIA_GATEWAY_ENABLED=false.'
  }
  if (warning.includes('MEDIA_GATEWAY_PROVIDER=freeswitch')) {
    return 'Входящий AI-рантайм ожидает MEDIA_GATEWAY_PROVIDER=freeswitch.'
  }
  if (warning.includes('MEDIA_GATEWAY_MODE=mock or esl_rtp')) {
    return 'Входящий AI-рантайм ожидает MEDIA_GATEWAY_MODE=mock или esl_rtp.'
  }
  if (warning.includes('MANGO_API_KEY') || warning.includes('MANGO_API_SALT')) {
    return 'Не заданы учётные данные Mango API. Синхронизация инвентаря и боевая маршрутизация недоступны.'
  }
  return warning
}

function formatProviderStatus(status: ProviderSetting['status']) {
  switch (status) {
    case 'configured':
      return 'настроен'
    case 'invalid':
      return 'ошибка'
    case 'not_tested':
      return 'не проверен'
    default:
      return status
  }
}

function formatActivationStatus(status: ProviderSetting['activation_status']) {
  return status === 'active' ? 'активен' : 'неактивен'
}

function formatSafeModeNote(note: string) {
  switch (note) {
    case 'Saving Mango credentials does not activate AI routing.':
      return 'Сохранение учётных данных Mango само по себе не включает AI-маршрутизацию.'
    case 'Saving Gemini settings does not switch runtime automatically.':
      return 'Сохранение настроек Gemini не переключает рантайм автоматически.'
    case 'Saving ElevenLabs settings does not make it the active voice path automatically.':
      return 'Сохранение настроек ElevenLabs не делает его активным голосовым путём автоматически.'
    case 'Saving Vapi settings does not activate the route automatically.':
      return 'Сохранение настроек Vapi не активирует этот маршрут автоматически.'
    default:
      return note
  }
}

function formatSetupStageTitle(title: SetupStage['title']) {
  switch (title) {
    case 'Connected':
      return 'Подключено'
    case 'Synced':
      return 'Синхронизировано'
    case 'Bound':
      return 'Назначено'
    case 'Live-ready':
      return 'Готово к боевой проверке'
    default:
      return title
  }
}

type SetupStage = {
  key: 'connected' | 'synced' | 'bound' | 'live_ready'
  title: string
  description: string
  ready: boolean
}

type LocalNextStep = {
  title: string
  description: string
  ctaLabel: string
  href?: string
  buttonAction?: 'sync'
}

export default function ProvidersPage() {
  const { token } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const [settingsByProvider, setSettingsByProvider] = useState<Record<string, ProviderSetting>>({})
  const [formsByProvider, setFormsByProvider] = useState<Record<string, ProviderFormState>>({})
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState<string | null>(null)
  const [savingProvider, setSavingProvider] = useState<string | null>(null)
  const [validatingProvider, setValidatingProvider] = useState<string | null>(null)
  const [providerMessages, setProviderMessages] = useState<Record<string, string | null>>({})
  const [providerErrors, setProviderErrors] = useState<Record<string, string | null>>({})
  const [mangoLines, setMangoLines] = useState<TelephonyLine[]>([])
  const [mangoRoutingMap, setMangoRoutingMap] = useState<MangoRoutingMapItem[]>([])
  const [mangoReadiness, setMangoReadiness] = useState<MangoReadiness | null>(null)
  const [mangoInventoryLoading, setMangoInventoryLoading] = useState(false)
  const [mangoInventoryError, setMangoInventoryError] = useState<string | null>(null)
  const [mangoSyncing, setMangoSyncing] = useState(false)
  const [mangoSyncMessage, setMangoSyncMessage] = useState<string | null>(null)
  const [lastSyncResponse, setLastSyncResponse] = useState<TelephonyLineSyncResponse | null>(null)
  const [inventoryFilter, setInventoryFilter] = useState<InventoryFilter>('all')

  const focusedRemoteLineId = searchParams.get('line') || ''

  const providerCards = useMemo(
    () => PROVIDER_DEFINITIONS.map((definition) => ({ definition, setting: settingsByProvider[definition.provider] })),
    [settingsByProvider],
  )

  const mangoWarningMessages = useMemo(
    () => (mangoReadiness?.warnings || []).map(mapMangoReadinessWarning),
    [mangoReadiness],
  )

  const boundRoutingItems = useMemo(
    () => mangoRoutingMap.filter((item) => item.agent_id),
    [mangoRoutingMap],
  )

  const activeLineCount = useMemo(
    () => mangoLines.filter((line) => line.is_active).length,
    [mangoLines],
  )

  const inactiveLineCount = useMemo(
    () => mangoLines.filter((line) => !line.is_active).length,
    [mangoLines],
  )

  const boundLineCount = boundRoutingItems.length
  const unboundLineCount = Math.max(mangoLines.length - boundLineCount, 0)

  const latestMangoSyncAt = useMemo(() => {
    const values = mangoLines
      .map((line) => line.synced_at || null)
      .filter((value): value is string => Boolean(value))
      .sort()
    return values.length ? values[values.length - 1] : null
  }, [mangoLines])

  const recommendedMangoRemoteLineId = useMemo(() => {
    // Priority: is_recommended_for_ai flag from API → schema_name → canonical ID fallback
    const byFlag = mangoLines.find((line) => line.is_recommended_for_ai)
    if (byFlag) return byFlag.remote_line_id
    const byName = mangoLines.find((line) => (line.schema_name || '').trim() === 'ДЛЯ ИИ менеджера')
    if (byName) return byName.remote_line_id
    const byId = mangoLines.find((line) => line.remote_line_id === '405622036')
    return byId?.remote_line_id || null
  }, [mangoLines])

  const recommendedLine = useMemo(
    () => mangoLines.find((line) => line.remote_line_id === recommendedMangoRemoteLineId) || null,
    [mangoLines, recommendedMangoRemoteLineId],
  )

  const mangoIssues = useMemo(() => {
    const issues: string[] = []
    if (!mangoReadiness?.api_configured) {
      issues.push('API credentials missing')
    }
    if (mangoLines.length === 0) {
      issues.push('No synced numbers')
    }
    if (mangoReadiness && !mangoReadiness.webhook_secret_configured) {
      issues.push('Webhook missing')
    }
    if (mangoReadiness && !mangoReadiness.from_ext_configured && !mangoReadiness.from_ext_auto_discoverable) {
      issues.push('FROM_EXT missing')
    }
    if (mangoReadiness && mangoReadiness.api_configured && !mangoReadiness.from_ext_configured && mangoReadiness.from_ext_auto_discoverable) {
      issues.push('FROM_EXT auto-discovered')
    }
    if (mangoReadiness?.api_configured && mangoLines.length > 0 && !boundLineCount) {
      issues.push('No agent binding yet')
    }
    return issues
  }, [boundLineCount, mangoLines.length, mangoReadiness])

  const focusedLine = useMemo(
    () => mangoLines.find((line) => line.remote_line_id === focusedRemoteLineId) || null,
    [focusedRemoteLineId, mangoLines],
  )

  const controlPlaneInboundReady = Boolean(
    mangoReadiness?.api_configured && mangoLines.length > 0 && boundLineCount > 0,
  )

  const inboundWebhookSmokeReady = Boolean(
    (mangoReadiness?.route_readiness?.inbound_webhook?.ready ?? mangoReadiness?.inbound_webhook_smoke_ready) && mangoLines.length > 0 && boundLineCount > 0,
  )

  const controlPlaneOutboundReady = Boolean(
    mangoReadiness?.api_configured
      && mangoLines.length > 0
      && (mangoReadiness?.from_ext_configured || mangoReadiness?.from_ext_auto_discoverable),
  )

  const outboundOriginateSmokeReady = Boolean(
    (mangoReadiness?.route_readiness?.outbound_originate?.ready ?? mangoReadiness?.outbound_originate_smoke_ready) && mangoLines.length > 0,
  )

  const inboundAiRuntimeReady = Boolean(
    (mangoReadiness?.route_readiness?.inbound_ai_runtime?.ready ?? mangoReadiness?.inbound_ai_runtime_ready) && boundLineCount > 0,
  )

  const webhookBlockers = useMemo(
    () => collectReadinessBlockers(mangoReadiness, 'webhook'),
    [mangoReadiness],
  )
  const outboundBlockers = useMemo(
    () => collectReadinessBlockers(mangoReadiness, 'outbound'),
    [mangoReadiness],
  )
  const inboundAiBlockers = useMemo(
    () => collectReadinessBlockers(mangoReadiness, 'inbound_ai'),
    [mangoReadiness],
  )
  const inboundWebhookSummary = mangoReadiness?.route_readiness?.inbound_webhook?.summary
    || 'Webhook Mango в backend на Render'
  const outboundOriginateSummary = mangoReadiness?.route_readiness?.outbound_originate?.summary
    || 'Линия Mango, назначенная агенту, для исходящего smoke'
  const inboundAiSummary = mangoReadiness?.route_readiness?.inbound_ai_runtime?.summary
    || 'Вебхук -> назначенный агент -> AI-рантайм'
  const nextStep = mangoReadiness?.actionable_next_step || null
  const setupStages = useMemo<SetupStage[]>(() => [
    {
      key: 'connected',
      title: 'Подключено',
      description: 'Учётные данные Mango сохранены, API можно использовать.',
      ready: Boolean(mangoReadiness?.api_configured),
    },
    {
      key: 'synced',
      title: 'Синхронизировано',
      description: 'Номера Mango уже видны в инвентаре.',
      ready: mangoLines.length > 0,
    },
    {
      key: 'bound',
      title: 'Назначено',
      description: 'Хотя бы одна линия уже назначена агенту.',
      ready: boundLineCount > 0,
    },
    {
      key: 'live_ready',
      title: 'Готово к live',
      description: 'Путь входящего вебхука и исходящего вызова на Render готов к честной smoke-проверке.',
      ready: mangoReadiness?.render_summary?.overall_status === 'ready',
    },
  ], [boundLineCount, mangoLines.length, mangoReadiness?.api_configured, mangoReadiness?.render_summary?.overall_status])

  const inventoryNextStep = useMemo<LocalNextStep>(() => {
    if (!mangoReadiness?.api_configured) {
      return {
        title: 'Сначала сохраните учётные данные Mango',
        description: 'Пока подключение не настроено, синхронизация не сможет подтянуть номера из Mango.',
        ctaLabel: 'Сохранить учётные данные ниже',
      }
    }
    if (mangoLines.length === 0) {
      return {
        title: 'Синхронизируйте номера из Mango',
        description: 'Подтяните актуальный инвентарь аккаунта, чтобы операторы увидели реальные номера и могли их назначать.',
        ctaLabel: 'Синхронизировать номера из Mango',
        buttonAction: 'sync',
      }
    }
    return {
      title: 'Инвентарь уже виден',
      description: 'Номера уже синхронизированы. Обновляйте список после изменений в Mango или после правок провайдера.',
      ctaLabel: 'Обновить инвентарь',
      buttonAction: 'sync',
    }
  }, [mangoLines.length, mangoReadiness?.api_configured])

  const bindingNextStep = useMemo<LocalNextStep>(() => {
    if (mangoLines.length === 0) {
      return {
        title: 'Сначала синхронизируйте номера',
        description: 'Пока инвентарь Mango не виден на этой странице, назначить номер агенту нельзя.',
        ctaLabel: 'Сначала синхронизировать номера',
        buttonAction: 'sync',
      }
    }
    if (boundLineCount === 0 && recommendedLine) {
      return {
        title: 'Назначьте рекомендованную AI-линию',
        description: `Откройте настройки агента и привяжите ${formatMangoLineLabel(recommendedLine)}, чтобы входящий и исходящий маршруты смотрели в реальную линию.`,
        ctaLabel: 'Назначить рекомендованную линию',
        href: `/agents?source=mango&line=${recommendedLine.remote_line_id}`,
      }
    }
    if (boundLineCount === 0) {
      return {
        title: 'Назначьте синхронизированную линию агенту',
        description: 'Номера уже видны, но ни один агент пока не привязан к линии. Выберите линию и назначьте её.',
        ctaLabel: 'Назначить линию агенту',
        href: '/agents?source=mango',
      }
    }
    return {
      title: 'Проверьте текущее назначение агента',
      description: 'Хотя бы одна линия уже назначена. Откройте привязанного агента и проверьте телефонию, голос и промпт.',
      ctaLabel: 'Открыть привязанного агента',
      href: boundRoutingItems[0]?.agent_id ? `/agents/${boundRoutingItems[0].agent_id}?mango_line=${boundRoutingItems[0].remote_line_id}&from=providers` : '/agents',
    }
  }, [boundLineCount, boundRoutingItems, mangoLines.length, recommendedLine])

  const unboundRemoteLineIds = useMemo(() => {
    const boundIds = new Set(
      mangoRoutingMap.filter((item) => item.agent_id).map((item) => item.remote_line_id),
    )
    return new Set(mangoLines.filter((line) => !boundIds.has(line.remote_line_id)).map((line) => line.remote_line_id))
  }, [mangoLines, mangoRoutingMap])

  const filteredMangoLines = useMemo(() => {
    if (inventoryFilter === 'recommended_only') {
      return mangoLines.filter((line) => line.remote_line_id === recommendedMangoRemoteLineId)
    }
    if (inventoryFilter === 'unbound_only') {
      return mangoLines.filter((line) => unboundRemoteLineIds.has(line.remote_line_id))
    }
    return mangoLines
  }, [inventoryFilter, mangoLines, recommendedMangoRemoteLineId, unboundRemoteLineIds])

  const filteredRoutingMap = useMemo(() => {
    if (inventoryFilter === 'recommended_only') {
      return mangoRoutingMap.filter((item) => item.remote_line_id === recommendedMangoRemoteLineId)
    }
    if (inventoryFilter === 'unbound_only') {
      return mangoRoutingMap.filter((item) => !item.agent_id)
    }
    return mangoRoutingMap
  }, [inventoryFilter, mangoRoutingMap, recommendedMangoRemoteLineId])

  useEffect(() => {
    const filter = searchParams.get('filter')
    if (filter === 'all' || filter === 'recommended_only' || filter === 'unbound_only') {
      setInventoryFilter(filter)
    }
  }, [searchParams])

  const loadProviders = useCallback(async () => {
    if (!token) {
      return
    }
    setLoading(true)
    setPageError(null)
    try {
      const response = await apiFetch<ProviderSettingsResponse>('/v1/providers/settings', {}, token)
      const nextSettings = response.items.reduce<Record<string, ProviderSetting>>((acc, item) => {
        acc[item.provider] = item
        return acc
      }, {})
      const nextForms = response.items.reduce<Record<string, ProviderFormState>>((acc, item) => {
        acc[item.provider] = buildInitialForm(item)
        return acc
      }, {})
      setSettingsByProvider(nextSettings)
      setFormsByProvider(nextForms)
    } catch (err) {
      setPageError(err instanceof ApiError ? err.message : 'Не удалось загрузить настройки провайдеров.')
    } finally {
      setLoading(false)
    }
  }, [token])

  const loadMangoOverview = useCallback(async () => {
    if (!token) {
      return
    }
    setMangoInventoryLoading(true)
    setMangoInventoryError(null)
    try {
      const [linesResponse, readinessResponse, routingResponse] = await Promise.all([
        apiFetch<TelephonyLineListResponse>('/v1/telephony/mango/lines', {}, token),
        apiFetch<MangoReadiness>('/v1/telephony/mango/readiness', {}, token).catch(() => null),
        apiFetch<MangoRoutingMapResponse>('/v1/telephony/mango/routing-map', {}, token).catch(() => ({ items: [], total: 0 })),
      ])
      setMangoLines(linesResponse.items)
      setMangoReadiness(readinessResponse)
      setMangoRoutingMap(routingResponse.items)
    } catch (err) {
      setMangoInventoryError(err instanceof ApiError ? err.message : 'Не удалось загрузить Mango inventory.')
    } finally {
      setMangoInventoryLoading(false)
    }
  }, [token])

  useEffect(() => {
    void loadProviders()
    void loadMangoOverview()
  }, [loadMangoOverview, loadProviders])

  function updateConfig(provider: ProviderName, key: string, value: unknown) {
    setFormsByProvider((current) => ({
      ...current,
      [provider]: {
        ...(current[provider] || { is_enabled: false, config: {}, secrets: {} }),
        config: {
          ...(current[provider]?.config || {}),
          [key]: value,
        },
        secrets: { ...(current[provider]?.secrets || {}) },
      },
    }))
  }

  function updateSecret(provider: ProviderName, key: string, value: string) {
    setFormsByProvider((current) => ({
      ...current,
      [provider]: {
        ...(current[provider] || { is_enabled: false, config: {}, secrets: {} }),
        config: { ...(current[provider]?.config || {}) },
        secrets: {
          ...(current[provider]?.secrets || {}),
          [key]: value,
        },
      },
    }))
  }

  function updateEnabled(provider: ProviderName, value: boolean) {
    setFormsByProvider((current) => ({
      ...current,
      [provider]: {
        ...(current[provider] || { is_enabled: false, config: {}, secrets: {} }),
        is_enabled: value,
        config: { ...(current[provider]?.config || {}) },
        secrets: { ...(current[provider]?.secrets || {}) },
      },
    }))
  }

  async function handleSave(event: FormEvent<HTMLFormElement>, provider: ProviderName) {
    event.preventDefault()
    if (!token) {
      return
    }
    const form = formsByProvider[provider]
    if (!form) {
      return
    }

    setSavingProvider(provider)
    setProviderErrors((current) => ({ ...current, [provider]: null }))
    setProviderMessages((current) => ({ ...current, [provider]: null }))

    const secretPayload = Object.entries(form.secrets).reduce<Record<string, string>>((acc, [key, value]) => {
      const cleaned = value.trim()
      if (cleaned) {
        acc[key] = cleaned
      }
      return acc
    }, {})

    try {
      const response = await apiFetch<ProviderSetting>(
        `/v1/providers/settings/${provider}`,
        {
          method: 'PATCH',
          body: JSON.stringify({
            is_enabled: form.is_enabled,
            config: form.config,
            secrets: secretPayload,
          }),
        },
        token,
      )
      setSettingsByProvider((current) => ({ ...current, [provider]: response }))
      setFormsByProvider((current) => ({
        ...current,
        [provider]: {
          is_enabled: response.is_enabled,
          config: { ...response.config },
          secrets: Object.keys(response.secrets).reduce<Record<string, string>>((acc, key) => {
            acc[key] = ''
            return acc
          }, {}),
        },
      }))
      setProviderMessages((current) => ({
        ...current,
        [provider]: 'Настройки сохранены. Секреты остаются замаскированными и не отображаются в открытом виде.',
      }))
    } catch (err) {
      setProviderErrors((current) => ({
        ...current,
        [provider]: err instanceof ApiError ? err.message : 'Не удалось сохранить настройки провайдера.',
      }))
    } finally {
      setSavingProvider(null)
    }
  }

  async function handleValidate(provider: ProviderName) {
    if (!token) {
      return
    }
    setValidatingProvider(provider)
    setProviderErrors((current) => ({ ...current, [provider]: null }))
    setProviderMessages((current) => ({ ...current, [provider]: null }))
    try {
      const validation = await apiFetch<ProviderValidationResponse>(
        `/v1/providers/settings/${provider}/validate`,
        { method: 'POST' },
        token,
      )
      await loadProviders()
      if (provider === 'mango') {
        await loadMangoOverview()
      }
      setProviderMessages((current) => ({
        ...current,
        [provider]: `${validation.message}${validation.remote_checked ? '' : ' Удалённая проверка намеренно не выполнялась.'}`,
      }))
    } catch (err) {
      setProviderErrors((current) => ({
        ...current,
        [provider]: err instanceof ApiError ? err.message : 'Не удалось проверить настройки провайдера.',
      }))
      await loadProviders()
    } finally {
      setValidatingProvider(null)
    }
  }

  async function handleSyncMangoInventory() {
    if (!token) {
      return
    }
    setMangoSyncing(true)
    setMangoInventoryError(null)
    setMangoSyncMessage(null)
    try {
      const response = await apiFetch<TelephonyLineSyncResponse>(
        '/v1/telephony/mango/sync-lines',
        { method: 'POST' },
        token,
      )
      setMangoLines(response.items)
      setLastSyncResponse(response)
      setMangoSyncMessage(`Синхронизация завершена: обновлено ${response.synced_count} линий, деактивировано ${response.deactivated_count}.`)
      await loadMangoOverview()
    } catch (err) {
      setMangoInventoryError(err instanceof ApiError ? err.message : 'Не удалось синхронизировать Mango inventory.')
    } finally {
      setMangoSyncing(false)
    }
  }

  function selectInventoryFilter(next: InventoryFilter) {
    setInventoryFilter(next)
    const nextParams = new URLSearchParams(searchParams)
    nextParams.set('filter', next)
    setSearchParams(nextParams, { replace: true })
  }

  function clearFocusedLine() {
    const nextParams = new URLSearchParams(searchParams)
    nextParams.delete('line')
    setSearchParams(nextParams, { replace: true })
  }

  function setFocusedLine(remoteLineId: string) {
    const nextParams = new URLSearchParams(searchParams)
    nextParams.set('line', remoteLineId)
    setSearchParams(nextParams, { replace: true })
  }

  return (
    <section className="stack-page providers-page">
      <article className="hero-card split-card providers-hero">
        <div>
          <p className="eyebrow">Настройки провайдеров</p>
          <h3>Mango, который понятен с первого взгляда</h3>
          <p>
            Здесь должно быть видно без догадок: подключён ли Mango, какие номера уже подтянуты, какой номер
            рекомендован для AI и где именно этот номер назначается агенту.
          </p>
        </div>
        <div className="status-strip">
          <span className={`status-pill${mangoReadiness?.api_configured ? ' live' : ''}`}>
            {mangoReadiness?.api_configured ? 'Mango подключён' : 'Mango не подключён'}
          </span>
          <span className="status-pill">Номера видны</span>
          <span className="status-pill">Назначение агенту отдельно</span>
        </div>
      </article>

      {pageError ? <div className="error-banner">{pageError}</div> : null}
      {loading ? <div className="route-state">Загрузка настроек провайдеров…</div> : null}

      {!loading && providerCards.map(({ definition, setting }) => {
        if (!setting) {
          return null
        }
        const form = formsByProvider[definition.provider] || buildInitialForm(setting)
        return (
          <article key={definition.provider} className="panel-card provider-card">
            <div className="panel-header">
              <div>
                <p className="eyebrow">{setting.provider}</p>
                <h4>{setting.display_name}</h4>
                <p className="compact-copy">{definition.description}</p>
              </div>
              <div className="status-strip provider-status-strip">
                <span className={`status-pill${setting.status === 'configured' ? ' live' : setting.status === 'invalid' ? ' error' : ''}`}>
                  {formatProviderStatus(setting.status)}
                </span>
                <span className={`status-pill${setting.activation_status === 'active' ? ' live' : ''}`}>
                  {formatActivationStatus(setting.activation_status)}
                </span>
              </div>
            </div>

            <div className="provider-note">{formatSafeModeNote(setting.safe_mode_note)}</div>
            {setting.last_validation_message ? (
              <div className="provider-validation-copy">
                <strong>Последняя проверка:</strong> {setting.last_validation_message}
                <div className="table-secondary">{formatTimestamp(setting.last_validated_at)}</div>
              </div>
            ) : null}
            {providerMessages[definition.provider] ? <div className="status-banner">{providerMessages[definition.provider]}</div> : null}
            {providerErrors[definition.provider] ? <div className="error-banner">{providerErrors[definition.provider]}</div> : null}

            {definition.provider === 'mango' ? (
              <section className="provider-telephony-overview">
                <section className="provider-subpanel">
                  <div className="panel-header">
                    <div>
                      <p className="eyebrow">1. Подключение</p>
                      <h4>Статус Mango</h4>
                    </div>
                  </div>
                  <div className="saas-summary-grid">
                    <div className="saas-summary-card">
                      <span>Подключение</span>
                      <strong>{mangoReadiness?.api_configured ? 'Да' : 'Нет'}</strong>
                    </div>
                    <div className="saas-summary-card">
                      <span>Статус API-ключа</span>
                      <strong>{setting.secrets.api_key?.is_set ? 'Сохранён' : 'Отсутствует'}</strong>
                    </div>
                    <div className="saas-summary-card">
                      <span>Готовность</span>
                      <strong>{mangoReadiness?.api_configured ? 'Готово к синхронизации' : 'Сначала настройте подключение'}</strong>
                    </div>
                    <div className="saas-summary-card">
                      <span>Найдено линий</span>
                      <strong>{mangoInventoryLoading ? '…' : mangoLines.length}</strong>
                    </div>
                    <div className="saas-summary-card">
                      <span>Последняя синхронизация</span>
                      <strong>{latestMangoSyncAt ? formatTimestamp(latestMangoSyncAt) : 'Ещё не синхронизировано'}</strong>
                    </div>
                    <div className="saas-summary-card">
                      <span>Проблемы</span>
                      <strong>{summarizeMangoIssue(mangoReadiness, mangoLines.length)}</strong>
                    </div>
                  </div>
                  <div className="setup-progress-card">
                    <div className="panel-header">
                      <div>
                        <p className="eyebrow">Прогресс настройки</p>
                        <h4>Подключено → Синхронизировано → Назначено → Готово к live</h4>
                      </div>
                    </div>
                    <div className="setup-progress-grid">
                      {setupStages.map((stage) => (
                        <div key={stage.key} className={`setup-progress-step${stage.ready ? ' ready' : ''}`}>
                          <div className="setup-progress-badge">{stage.ready ? '✓' : stage.key === 'live_ready' ? '4' : stage.key === 'bound' ? '3' : stage.key === 'synced' ? '2' : '1'}</div>
                          <div>
                            <strong>{formatSetupStageTitle(stage.title)}</strong>
                            <div className="table-secondary">{stage.description}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                  {mangoIssues.length > 0 ? (
                    <div className="issue-chip-row">
                      {mangoIssues.map((issue) => (
                        <span key={issue} className="issue-chip">
                          {formatIssueLabel(issue)}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  <div className="info-banner">
                    <strong>Что происходит на этой странице:</strong> сначала вы подключаете Mango и синхронизируете номера здесь,
                    потом назначаете конкретный номер в карточке агента.
                  </div>
                  <div className="journey-card">
                    <div className="journey-step">
                      <span className="journey-step-index">1</span>
                      <div>
                        <strong>Сохраните учётные данные</strong>
                        <div className="table-secondary">API-ключ, salt и статусы готовности остаются на этой странице.</div>
                      </div>
                    </div>
                    <div className="journey-step">
                      <span className="journey-step-index">2</span>
                      <div>
                        <strong>Синхронизируйте номера из Mango</strong>
                        <div className="table-secondary">После синхронизации ниже появляется список линий и рекомендованный AI-номер.</div>
                      </div>
                    </div>
                    <div className="journey-step">
                      <span className="journey-step-index">3</span>
                      <div>
                        <strong>Назначьте номер в карточке агента</strong>
                        <div className="table-secondary">Привязка номера сохраняется на уровне конкретного агента.</div>
                      </div>
                    </div>
                  </div>
                </section>

                <div className="provider-note">
                  <strong>Эта страница хранит учётные данные и показывает инвентарь Mango.</strong> Номера назначаются не здесь, а в карточке агента.
                  {' '}
                  <Link to="/agents" className="inline-link">Перейти в настройки агента и назначить номер</Link>
                </div>

                {focusedLine ? (
                  <div className="info-banner">
                    <strong>Линия в фокусе:</strong> {formatMangoLineLabel(focusedLine)}
                    <div className="table-secondary">remote_line_id: {focusedLine.remote_line_id}</div>
                    <div className="button-row">
                      <button type="button" className="ghost-button" onClick={clearFocusedLine}>
                        Сбросить фокус линии
                      </button>
                    </div>
                  </div>
                ) : null}

                <div className="button-row">
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => void handleSyncMangoInventory()}
                    disabled={mangoSyncing}
                  >
                    {mangoSyncing ? 'Синхронизация…' : 'Синхронизировать номера из Mango'}
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => void loadMangoOverview()}
                    disabled={mangoInventoryLoading}
                  >
                    {mangoInventoryLoading ? 'Обновление…' : 'Обновить инвентарь'}
                  </button>
                </div>

                {mangoIssues.length > 0 ? (
                  <div className="warning-banner">
                    <strong>Проблемы:</strong> {mangoIssues.join(' • ')}
                  </div>
                ) : null}

                {mangoLines.length > 0 ? (
                  <div className="info-banner">
                    <strong>Номера уже синхронизированы из Mango.</strong> Теперь перейдите в карточку агента и назначьте номер,
                    чтобы включить основу входящей и исходящей маршрутизации.
                  </div>
                  ) : (
                    <div className="info-banner">
                      <strong>Номера ещё не синхронизированы.</strong> Нажмите <strong>Синхронизировать номера из Mango</strong>, чтобы подтянуть номера кабинета.
                    </div>
                  )}

                <div className="inventory-toolbar">
                  <div className="table-secondary">
                    Сначала синхронизируйте инвентарь здесь. Затем откройте агента и назначьте ему конкретную линию.
                  </div>
                  <div className="button-row">
                    <button
                      type="button"
                      className={`ghost-button${inventoryFilter === 'all' ? ' active-filter' : ''}`}
                      onClick={() => selectInventoryFilter('all')}
                    >
                      Все линии
                    </button>
                    <button
                      type="button"
                      className={`ghost-button${inventoryFilter === 'recommended_only' ? ' active-filter' : ''}`}
                      onClick={() => selectInventoryFilter('recommended_only')}
                    >
                      Рекомендовано для AI
                    </button>
                    <button
                      type="button"
                      className={`ghost-button${inventoryFilter === 'unbound_only' ? ' active-filter' : ''}`}
                      onClick={() => selectInventoryFilter('unbound_only')}
                    >
                      Только неназначенные
                    </button>
                  </div>
                </div>

                <div className="debug-list compact-debug">
                  <div className="debug-row">
                    <span>готовность</span>
                    <strong>{mangoReadiness?.api_configured ? 'настроено' : 'не настроено'}</strong>
                  </div>
                  <div className="debug-row">
                    <span>найдено номеров</span>
                    <strong>{mangoInventoryLoading ? 'загрузка…' : mangoLines.length}</strong>
                  </div>
                  <div className="debug-row">
                    <span>назначено линий</span>
                    <strong>{mangoRoutingMap.filter((item) => item.agent_id).length}</strong>
                  </div>
                  <div className="debug-row">
                    <span>последняя синхронизация</span>
                    <strong>{latestMangoSyncAt ? formatTimestamp(latestMangoSyncAt) : 'ещё не синхронизировано'}</strong>
                  </div>
                </div>

                <section className="provider-subpanel">
                  <div className="panel-header">
                    <div>
                      <p className="eyebrow">2. Номера</p>
                      <h4>Линии Mango</h4>
                    </div>
                  </div>
                  <div className="binding-cta-card">
                    <div>
                      <p className="binding-cta-title">{inventoryNextStep.title}</p>
                      <p className="binding-cta-copy">{inventoryNextStep.description}</p>
                    </div>
                    {inventoryNextStep.href ? (
                      <Link to={inventoryNextStep.href} className="primary-link-button">
                        {inventoryNextStep.ctaLabel}
                      </Link>
                    ) : (
                      <button
                        type="button"
                        className="primary-button"
                        onClick={() => void handleSyncMangoInventory()}
                        disabled={mangoSyncing}
                      >
                        {mangoSyncing && inventoryNextStep.buttonAction === 'sync' ? 'Синхронизация…' : inventoryNextStep.ctaLabel}
                      </button>
                    )}
                  </div>
                  <div className="provider-note">
                    <strong>Статус последней синхронизации</strong>
                    <div className="table-secondary">
                      {mangoSyncMessage
                        ? mangoSyncMessage
                        : latestMangoSyncAt
                          ? `Инвентарь уже синхронизирован. Последняя известная синхронизация: ${formatTimestamp(latestMangoSyncAt)}.`
                          : 'С этой страницы синхронизация ещё не запускалась.'}
                    </div>
                    <div className="sync-summary-grid">
                      <div className="sync-summary-item">
                        <span >активные</span>
                        <strong>{activeLineCount}</strong>
                      </div>
                      <div className="sync-summary-item">
                        <span >неактивные</span>
                        <strong>{inactiveLineCount}</strong>
                      </div>
                      <div className="sync-summary-item">
                        <span >назначенные</span>
                        <strong>{boundLineCount}</strong>
                      </div>
                      <div className="sync-summary-item">
                        <span >свободные</span>
                        <strong>{unboundLineCount}</strong>
                      </div>
                    </div>
                  </div>

                  {filteredMangoLines.length === 0 ? (
                    <div className="empty-state-card">
                      <strong>Номера ещё не синхронизированы</strong>
                      <p>Подтяните номера из Mango и система сразу покажет, какие линии можно назначать агентам.</p>
                      <button
                        type="button"
                        className="primary-button"
                        onClick={() => void handleSyncMangoInventory()}
                        disabled={mangoSyncing}
                      >
                        {mangoSyncing ? 'Синхронизация…' : 'Синхронизировать номера из Mango'}
                      </button>
                    </div>
                  ) : (
                    <div className="inventory-card-grid">
                      {filteredMangoLines.map((line) => {
                        const boundAgent = mangoRoutingMap.find((item) => item.remote_line_id === line.remote_line_id && item.agent_id)
                        return (
                          <article
                            key={line.remote_line_id}
                            className={`inventory-card${focusedRemoteLineId === line.remote_line_id ? ' focused' : ''}`}
                          >
                            <div className="inventory-card-header">
                              <div>
                                <p className="inventory-card-title">{line.schema_name || line.label || line.phone_number}</p>
                                <p className="inventory-card-phone">{line.phone_number}</p>
                              </div>
                              <div className="status-strip">
                                <span className={`status-pill${line.is_active ? ' live' : ' error'}`}>{line.is_active ? 'Активна' : 'Неактивна'}</span>
                                {recommendedMangoRemoteLineId === line.remote_line_id ? (
                                  <span className="status-pill live" >Рекомендуется для AI</span>
                                ) : null}
                              </div>
                            </div>
                            <div className="inventory-card-meta">
                              <div><span>ID линии</span><strong>{line.remote_line_id}</strong></div>
                              <div><span >Провайдер</span><strong>{line.provider}</strong></div>
                              <div><span >Назначение</span><strong>{boundAgent ? boundAgent.agent_name || 'Назначено' : 'Не назначено'}</strong></div>
                            </div>
                            <div className="inventory-card-actions">
                              {boundAgent?.agent_id ? (
                                <Link to={`/agents/${boundAgent.agent_id}?mango_line=${line.remote_line_id}&from=providers`} className="primary-link-button">
                                  Открыть привязанного агента
                                </Link>
                              ) : (
                                <Link to={`/agents?source=mango&line=${line.remote_line_id}`} className="primary-link-button">
                                  Назначить эту линию сейчас
                                </Link>
                              )}
                              {focusedRemoteLineId !== line.remote_line_id ? (
                                <button type="button" className="ghost-button" onClick={() => setFocusedLine(line.remote_line_id)}>
                                  Показать эту линию
                                </button>
                              ) : null}
                            </div>
                          </article>
                        )
                      })}
                    </div>
                  )}
                </section>

                <section className="provider-subpanel">
                  <div className="panel-header">
                    <div>
                      <p className="eyebrow">3. Боевая готовность</p>
                      <h4>Готовность маршрутизации на Render</h4>
                    </div>
                  </div>
                  <div className="info-banner">
                    <strong>Честный статус боевого контура.</strong> Этот блок показывает не общую “готовность интеграции”, а можно ли
                    уже идти в боевую smoke-проверку вебхука и исходящего вызова на Render.
                  </div>
                  {mangoReadiness?.render_summary ? (
                    <div className="provider-note">
                      <strong>
                        {mangoReadiness.render_summary.overall_status === 'ready'
                          ? 'Маршрутизация на Render готова'
                          : mangoReadiness.render_summary.overall_status === 'partial'
                            ? 'Маршрутизация на Render частично готова'
                            : 'Маршрутизация на Render заблокирована'}
                      </strong>
                      <div className="table-secondary">{mangoReadiness.render_summary.operator_summary}</div>
                      <div className="table-secondary">
                        готово: {mangoReadiness.render_summary.ready_count} / заблокировано: {mangoReadiness.render_summary.blocked_count}
                      </div>
                    </div>
                  ) : null}
                  {nextStep ? (
                    <div className={`next-step-banner${mangoReadiness?.render_summary?.overall_status === 'ready' ? ' success' : ''}`}>
                      <div>
                        <p className="eyebrow" >Главный следующий шаг</p>
                        <strong>{nextStep.title}</strong>
                        <div className="table-secondary">{nextStep.description}</div>
                      </div>
                      <span className="next-step-cta">{nextStep.cta_label}</span>
                    </div>
                  ) : null}
                  <div className="readiness-card-grid">
                    <article className={`readiness-card${inboundWebhookSmokeReady ? ' ready' : ' blocked'}`}>
                      <div className="readiness-card-header">
                        <div>
                          <p className="readiness-card-title" >Входящий вебхук</p>
                          <p className="readiness-card-copy">{inboundWebhookSummary}</p>
                        </div>
                        <span className={`status-pill${inboundWebhookSmokeReady ? ' live' : ' error'}`}>
                          {inboundWebhookSmokeReady ? 'Готово' : 'Заблокировано'}
                        </span>
                      </div>
                      <ul className="readiness-list">
                        {(webhookBlockers.length > 0 ? webhookBlockers : ['Путь вебхука готов к smoke-проверке доставки.']).map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </article>

                    <article className={`readiness-card${outboundOriginateSmokeReady ? ' ready' : ' blocked'}`}>
                      <div className="readiness-card-header">
                        <div>
                          <p className="readiness-card-title" >Исходящий вызов</p>
                          <p className="readiness-card-copy">{outboundOriginateSummary}</p>
                        </div>
                        <span className={`status-pill${outboundOriginateSmokeReady ? ' live' : ' error'}`}>
                          {outboundOriginateSmokeReady ? 'Готово' : 'Заблокировано'}
                        </span>
                      </div>
                      <ul className="readiness-list">
                        {(outboundBlockers.length > 0 ? outboundBlockers : ['Путь исходящей smoke-проверки уже готов в текущей конфигурации Render.']).map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </article>

                    <article className={`readiness-card${inboundAiRuntimeReady ? ' ready' : ' blocked'}`}>
                      <div className="readiness-card-header">
                        <div>
                          <p className="readiness-card-title" >Входящий AI-рантайм</p>
                          <p className="readiness-card-copy">{inboundAiSummary}</p>
                        </div>
                        <span className={`status-pill${inboundAiRuntimeReady ? ' live' : ' error'}`}>
                          {inboundAiRuntimeReady ? 'Готово' : 'Заблокировано'}
                        </span>
                      </div>
                      <ul className="readiness-list">
                        {(inboundAiBlockers.length > 0 ? inboundAiBlockers : ['Путь входящего AI-рантайма готов к боевой smoke-проверке через вебхук.']).map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </article>
                  </div>
                  <div className="provider-note">
                    <strong>Используемые Render URL</strong>
                    <div className="table-secondary">backend_url: {mangoReadiness?.backend_url || 'н/д'}</div>
                    <div className="table-secondary">URL вебхука: {mangoReadiness?.webhook_url || 'н/д'}</div>
                  </div>
                </section>

                <section className="provider-subpanel">
                  <div className="panel-header">
                    <div>
                      <p className="eyebrow">4. Привязка агента</p>
                      <h4>Где назначается номер</h4>
                    </div>
                  </div>
                  <div className="binding-cta-card">
                    <div>
                      <p className="binding-cta-title">{bindingNextStep.title}</p>
                      <p className="binding-cta-copy">{bindingNextStep.description}</p>
                    </div>
                    {bindingNextStep.href ? (
                      <Link to={bindingNextStep.href} className="primary-link-button">
                        {bindingNextStep.ctaLabel}
                      </Link>
                    ) : (
                      <button
                        type="button"
                        className="primary-button"
                        onClick={() => void handleSyncMangoInventory()}
                        disabled={mangoSyncing}
                      >
                        {mangoSyncing && bindingNextStep.buttonAction === 'sync' ? 'Синхронизация…' : bindingNextStep.ctaLabel}
                      </button>
                    )}
                  </div>
                  <div className="info-banner">
                    <strong>Назначьте номер агенту, чтобы включить звонки.</strong> Откройте карточку агента, выберите номер Mango в блоке
                    <strong> Telephony</strong> и сохраните настройки.
                  </div>
                  <div className="journey-card">
                    <div className="journey-step">
                      <span className="journey-step-index">A</span>
                      <div>
                        <strong>Откройте агента</strong>
                        <div className="table-secondary">Можно перейти прямо из карточки линии или из routing map ниже.</div>
                      </div>
                    </div>
                    <div className="journey-step">
                      <span className="journey-step-index">B</span>
                      <div>
                        <strong>Выберите номер Mango</strong>
                        <div className="table-secondary">Интерфейс покажет понятную подпись вроде “ДЛЯ ИИ менеджера (+79300350609)”.</div>
                      </div>
                    </div>
                    <div className="journey-step">
                      <span className="journey-step-index">C</span>
                      <div>
                        <strong>Сохраните и проверьте привязку</strong>
                        <div className="table-secondary">После перезагрузки привязка читается обратно и попадает в карту маршрутизации.</div>
                      </div>
                    </div>
                  </div>
                  {recommendedLine ? (
                    <div className="binding-cta-card">
                      <div>
                        <p className="binding-cta-title">Рекомендуемая AI-линия</p>
                        <p className="binding-cta-copy">{formatMangoLineLabel(recommendedLine)}</p>
                      </div>
                      <Link to={`/agents?source=mango&line=${recommendedLine.remote_line_id}`} className="primary-link-button">
                        Открыть агентов и назначить
                      </Link>
                    </div>
                  ) : null}
                </section>

                <section className="provider-subpanel">
                  <div className="panel-header">
                    <div>
                      <p className="eyebrow" >Карта маршрутизации</p>
                      <h4>Какая линия привязана к какому агенту</h4>
                    </div>
                  </div>
                  {filteredRoutingMap.length === 0 ? (
                    <div className="info-banner">Карта маршрутизации пока пуста. Сначала синхронизируйте инвентарь, потом назначьте линию в настройках агента.</div>
                  ) : (
                    <div className="inventory-list">
                      {filteredRoutingMap.map((item) => (
                        <article
                          key={item.remote_line_id}
                          className={`inventory-item${focusedRemoteLineId === item.remote_line_id ? ' focused' : ''}`}
                        >
                          <div className="inventory-item-main">
                            <strong>{formatMangoLineLabel(item)}</strong>
                            <div className="table-secondary">remote_line_id: {item.remote_line_id}</div>
                          </div>
                          <div className="inventory-binding-copy">
                            {item.agent_id ? (
                              <>
                                <strong>Привязанный агент:</strong> {item.agent_name || item.agent_id}
                                <div>
                                  <Link
                                    to={`/agents/${item.agent_id}?mango_line=${item.remote_line_id}&from=providers`}
                                    className="inline-link"
                                  >
                                    Открыть привязанного агента
                                  </Link>
                                </div>
                              </>
                            ) : (
                              <>Привязанный агент: не назначен</>
                            )}
                          </div>
                        </article>
                      ))}
                    </div>
                  )}
                </section>

                <section className="provider-subpanel">
                  <div className="panel-header">
                    <div>
                      <p className="eyebrow" >Отладка</p>
                      <h4>Операционные детали</h4>
                    </div>
                  </div>
                  <div className="table-secondary">
                    Этот блок нужен для быстрой диагностики. Он специально вынесен вниз, чтобы не мешать основному пользовательскому сценарию.
                  </div>
                  <div className="debug-list compact-debug">
                    <div className="debug-row">
                      <span >число сырых линий</span>
                      <strong>{mangoLines.length}</strong>
                    </div>
                    <div className="debug-row">
                      <span >последний ответ синхронизации</span>
                      <strong>{lastSyncResponse ? `${lastSyncResponse.synced_count} синхронизировано / ${lastSyncResponse.deactivated_count} деактивировано` : 'пока нет'}</strong>
                    </div>
                    <div className="debug-row">
                      <span >флаги готовности</span>
                      <strong>
                        {[
                          mangoReadiness?.api_configured ? 'api' : null,
                          mangoReadiness?.webhook_secret_configured ? 'webhook' : null,
                          mangoReadiness?.from_ext_configured ? 'from_ext' : mangoReadiness?.from_ext_auto_discoverable ? 'auto_from_ext' : null,
                        ].filter(Boolean).join(', ') || 'нет'}
                      </strong>
                    </div>
                  </div>
                </section>

                {mangoInventoryError ? <div className="error-banner">{mangoInventoryError}</div> : null}
                {mangoSyncMessage ? <div className="success-banner">{mangoSyncMessage}</div> : null}
                {mangoWarningMessages.map((warning) => (
                  <div key={warning} className="warning-banner">{warning}</div>
                ))}
              </section>
            ) : null}

            <form className="provider-form" onSubmit={(event) => void handleSave(event, definition.provider)}>
              <section className="form-section">
                <label className="boxed-toggle toggle-row">
                  <input
                    type="checkbox"
                    checked={form.is_enabled}
                    onChange={(event) => updateEnabled(definition.provider, event.target.checked)}
                  />
                  <span>Провайдер включён</span>
                </label>
              </section>

              <section className="form-section two-column-fields">
                {definition.configFields.map((field) => (
                  field.kind === 'boolean' ? (
                    <div key={field.key} className="boolean-field">
                      <label className="boxed-toggle toggle-row">
                        <input
                          type="checkbox"
                          checked={Boolean(form.config[field.key])}
                          onChange={(event) => updateConfig(definition.provider, field.key, event.target.checked)}
                        />
                        <span>{field.label}</span>
                      </label>
                      {field.help ? <small className="table-secondary">{field.help}</small> : null}
                    </div>
                  ) : (
                    <label key={field.key}>
                      <span>{field.label}</span>
                      {field.kind === 'textarea' ? (
                        <textarea
                          value={String(form.config[field.key] ?? '')}
                          onChange={(event) => updateConfig(definition.provider, field.key, event.target.value)}
                        />
                      ) : (
                        <input
                          value={String(form.config[field.key] ?? '')}
                          onChange={(event) => updateConfig(definition.provider, field.key, event.target.value)}
                        />
                      )}
                      {field.help ? <small className="table-secondary">{field.help}</small> : null}
                    </label>
                  )
                ))}
              </section>

              <section className="form-section two-column-fields">
                {definition.secretFields.map((field) => {
                  const secretState = setting.secrets[field.key]
                  return (
                    <label key={field.key}>
                      <span>{field.label}</span>
                      <input
                        type="password"
                        autoComplete="new-password"
                        value={form.secrets[field.key] || ''}
                        onChange={(event) => updateSecret(definition.provider, field.key, event.target.value)}
                        placeholder={secretState?.is_set ? 'Оставьте пустым, чтобы сохранить текущий секрет' : 'Введите секрет'}
                      />
                      <small className="table-secondary">
                        {secretState?.is_set ? `Сохранён: ${secretState.masked_value}` : 'Не сохранён'}
                      </small>
                    </label>
                  )
                })}
              </section>

              <div className="button-row">
                <button type="submit" className="primary-button" disabled={savingProvider === definition.provider}>
                  {savingProvider === definition.provider ? 'Сохранение…' : 'Сохранить настройки'}
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => void handleValidate(definition.provider)}
                  disabled={validatingProvider === definition.provider}
                >
                  {validatingProvider === definition.provider ? 'Проверка…' : 'Проверить подключение'}
                </button>
              </div>
            </form>
          </article>
        )
      })}
    </section>
  )
}
