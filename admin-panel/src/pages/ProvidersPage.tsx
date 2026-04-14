import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

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
  warnings: string[]
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
    description: 'Хранит Mango credentials как settings layer. Не включает маршрутизацию AI и не трогает номера amoCRM.',
    configFields: [
      {
        key: 'from_ext',
        label: 'Default extension',
        kind: 'text',
        help: 'Сохраняется как reference setting. Не включает исходящий маршрут автоматически.',
      },
      {
        key: 'webhook_ip_allowlist',
        label: 'Webhook IP allowlist',
        kind: 'textarea',
        help: 'CIDR/IP список для будущей webhook-конфигурации.',
      },
    ],
    secretFields: [
      { key: 'api_key', label: 'API key', kind: 'text' },
      { key: 'api_salt', label: 'API salt', kind: 'text' },
      { key: 'webhook_secret', label: 'Webhook signature secret', kind: 'text' },
      { key: 'webhook_shared_secret', label: 'Webhook shared secret', kind: 'text' },
    ],
  },
  {
    provider: 'gemini',
    description: 'Gemini model/API settings для Direct runtime и Browser Call. Сохранение не переключает runtime автоматически.',
    configFields: [
      { key: 'model_id', label: 'Model ID', kind: 'text' },
      { key: 'api_version', label: 'API version', kind: 'text' },
    ],
    secretFields: [{ key: 'api_key', label: 'API key', kind: 'text' }],
  },
  {
    provider: 'elevenlabs',
    description: 'TTS provider settings. Сохранение не делает ElevenLabs активным голосовым путём само по себе.',
    configFields: [
      { key: 'voice_id', label: 'Voice ID', kind: 'text' },
      { key: 'enabled', label: 'Provider enabled', kind: 'boolean', help: 'Флаг хранится отдельно как operator intent.' },
    ],
    secretFields: [{ key: 'api_key', label: 'API key', kind: 'text' }],
  },
  {
    provider: 'vapi',
    description: 'Optional route provider settings. Сохранение не меняет активный route и не затрагивает browser/direct runtime без явной wiring.',
    configFields: [
      { key: 'assistant_id', label: 'Assistant ID', kind: 'text' },
      { key: 'phone_number_id', label: 'Phone Number ID', kind: 'text' },
      { key: 'base_url', label: 'Base URL', kind: 'text' },
      { key: 'server_url', label: 'Server URL', kind: 'text' },
    ],
    secretFields: [
      { key: 'api_key', label: 'API key', kind: 'text' },
      { key: 'webhook_secret', label: 'Webhook secret', kind: 'text' },
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
    return 'Ещё не проверялось'
  }
  return new Date(value).toLocaleString()
}

function formatMangoLineLabel(line: Pick<TelephonyLine, 'schema_name' | 'label' | 'display_name' | 'phone_number'>) {
  const primary = line.schema_name || line.label || line.display_name || line.phone_number
  return primary === line.phone_number ? line.phone_number : `${primary} (${line.phone_number})`
}

function mapMangoReadinessWarning(warning: string) {
  if (warning.includes('MANGO_WEBHOOK_SECRET')) {
    return 'Inbound webhook verification not configured. Входящий webhook-path ещё не защищён.'
  }
  if (warning.includes('auto-discovered Mango extension')) {
    return 'Outbound source extension будет auto-discovered. Для фиксированного originate лучше явно задать MANGO_FROM_EXT.'
  }
  if (warning.includes('MANGO_FROM_EXT')) {
    return 'Outbound calling not ready: не задан MANGO_FROM_EXT и нет auto-discovery fallback.'
  }
  if (warning.includes('MANGO_API_KEY') || warning.includes('MANGO_API_SALT')) {
    return 'Mango API credentials missing. Inventory sync и live routing недоступны.'
  }
  return warning
}

export default function ProvidersPage() {
  const { token } = useAuth()
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
  const [inventoryFilter, setInventoryFilter] = useState<InventoryFilter>('all')

  const providerCards = useMemo(
    () => PROVIDER_DEFINITIONS.map((definition) => ({ definition, setting: settingsByProvider[definition.provider] })),
    [settingsByProvider],
  )

  const mangoWarningMessages = useMemo(
    () => (mangoReadiness?.warnings || []).map(mapMangoReadinessWarning),
    [mangoReadiness],
  )

  const latestMangoSyncAt = useMemo(() => {
    const values = mangoLines
      .map((line) => line.synced_at || null)
      .filter((value): value is string => Boolean(value))
      .sort()
    return values.length ? values[values.length - 1] : null
  }, [mangoLines])

  const recommendedMangoRemoteLineId = useMemo(() => {
    const match = mangoLines.find((line) => (line.schema_name || '').trim() === 'ДЛЯ ИИ менеджера')
    return match?.remote_line_id || null
  }, [mangoLines])

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
        [provider]: `${validation.message}${validation.remote_checked ? '' : ' Удалённое подключение намеренно не проверялось.'}`,
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
      setMangoSyncMessage(`Sync completed: ${response.synced_count} lines updated, ${response.deactivated_count} deactivated.`)
      await loadMangoOverview()
    } catch (err) {
      setMangoInventoryError(err instanceof ApiError ? err.message : 'Не удалось синхронизировать Mango inventory.')
    } finally {
      setMangoSyncing(false)
    }
  }

  return (
    <section className="stack-page providers-page">
      <article className="hero-card split-card providers-hero">
        <div>
          <p className="eyebrow">Настройки провайдеров</p>
          <h3>Учётные данные и статус провайдеров</h3>
          <p>
            Это безопасный settings layer. Сохранение credentials не включает боевой маршрут автоматически и не
            переводит shared Mango account на AI routing.
          </p>
        </div>
        <div className="status-strip">
          <span className="status-pill">Только настройки</span>
          <span className="status-pill">Без авторутинга</span>
          <span className="status-pill">Секреты скрыты</span>
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
                  {setting.status}
                </span>
                <span className={`status-pill${setting.activation_status === 'active' ? ' live' : ''}`}>
                  {setting.activation_status}
                </span>
              </div>
            </div>

            <div className="provider-note">{setting.safe_mode_note}</div>
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
                <div className="provider-note">
                  <strong>This page stores credentials and shows Mango inventory.</strong> Line binding is saved in the agent editor.
                  {' '}
                  <Link to="/agents" className="inline-link">Go to Agent settings to bind a number</Link>
                </div>

                <div className="button-row">
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => void handleSyncMangoInventory()}
                    disabled={mangoSyncing}
                  >
                    {mangoSyncing ? 'Syncing…' : 'Sync numbers from Mango'}
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => void loadMangoOverview()}
                    disabled={mangoInventoryLoading}
                  >
                    {mangoInventoryLoading ? 'Refreshing…' : 'Refresh inventory'}
                  </button>
                </div>

                <div className="button-row">
                  <button
                    type="button"
                    className={`ghost-button${inventoryFilter === 'all' ? ' active-filter' : ''}`}
                    onClick={() => setInventoryFilter('all')}
                  >
                    All lines
                  </button>
                  <button
                    type="button"
                    className={`ghost-button${inventoryFilter === 'recommended_only' ? ' active-filter' : ''}`}
                    onClick={() => setInventoryFilter('recommended_only')}
                  >
                    AI recommended
                  </button>
                  <button
                    type="button"
                    className={`ghost-button${inventoryFilter === 'unbound_only' ? ' active-filter' : ''}`}
                    onClick={() => setInventoryFilter('unbound_only')}
                  >
                    Unbound only
                  </button>
                </div>

                <div className="debug-list compact-debug">
                  <div className="debug-row">
                    <span>readiness</span>
                    <strong>{mangoReadiness?.api_configured ? 'configured' : 'not configured'}</strong>
                  </div>
                  <div className="debug-row">
                    <span>numbers found</span>
                    <strong>{mangoInventoryLoading ? 'loading…' : mangoLines.length}</strong>
                  </div>
                  <div className="debug-row">
                    <span>bound lines</span>
                    <strong>{mangoRoutingMap.filter((item) => item.agent_id).length}</strong>
                  </div>
                  <div className="debug-row">
                    <span>last sync</span>
                    <strong>{latestMangoSyncAt ? formatTimestamp(latestMangoSyncAt) : 'not synced yet'}</strong>
                  </div>
                </div>

                <div className="provider-note">
                  <strong>Last sync status</strong>
                  <div className="table-secondary">
                    {mangoSyncMessage
                      ? mangoSyncMessage
                      : latestMangoSyncAt
                        ? `Inventory already synced. Latest known sync: ${formatTimestamp(latestMangoSyncAt)}.`
                        : 'Sync has not been run from this page yet.'}
                  </div>
                </div>

                {mangoInventoryError ? <div className="error-banner">{mangoInventoryError}</div> : null}
                {mangoSyncMessage ? <div className="success-banner">{mangoSyncMessage}</div> : null}
                {mangoWarningMessages.map((warning) => (
                  <div key={warning} className="warning-banner">{warning}</div>
                ))}

                <section className="provider-subpanel">
                  <div className="panel-header">
                    <div>
                      <p className="eyebrow">Mango inventory</p>
                      <h4>Синхронизированные линии</h4>
                    </div>
                  </div>
                  {filteredMangoLines.length === 0 ? (
                    <div className="info-banner">No numbers synced yet. Run “Sync numbers from Mango” to pull the current tenant inventory.</div>
                  ) : (
                    <div className="inventory-list">
                      {filteredMangoLines.map((line) => (
                        <article key={line.remote_line_id} className="inventory-item">
                          <div className="inventory-item-main">
                            <strong>{formatMangoLineLabel(line)}</strong>
                            <div className="table-secondary">line_id: {line.remote_line_id}</div>
                          </div>
                          <div className="status-strip">
                            <span className={`status-pill${line.is_active ? ' live' : ' error'}`}>{line.is_active ? 'active' : 'inactive'}</span>
                            {recommendedMangoRemoteLineId === line.remote_line_id ? (
                              <span className="status-pill live">AI recommended</span>
                            ) : null}
                          </div>
                        </article>
                      ))}
                    </div>
                  )}
                </section>

                <section className="provider-subpanel">
                  <div className="panel-header">
                    <div>
                      <p className="eyebrow">Routing map</p>
                      <h4>Какая линия привязана к какому агенту</h4>
                    </div>
                  </div>
                  {filteredRoutingMap.length === 0 ? (
                    <div className="info-banner">Routing map is empty. Sync inventory first, then assign a line in Agent settings.</div>
                  ) : (
                    <div className="inventory-list">
                      {filteredRoutingMap.map((item) => (
                        <article key={item.remote_line_id} className="inventory-item">
                          <div className="inventory-item-main">
                            <strong>{formatMangoLineLabel(item)}</strong>
                            <div className="table-secondary">remote_line_id: {item.remote_line_id}</div>
                          </div>
                          <div className="inventory-binding-copy">
                            {item.agent_id ? (
                              <>
                                <strong>Bound agent:</strong> {item.agent_name || item.agent_id}
                                <div>
                                  <Link to={`/agents/${item.agent_id}`} className="inline-link">Open bound agent</Link>
                                </div>
                              </>
                            ) : (
                              <>Bound agent: not linked</>
                            )}
                          </div>
                        </article>
                      ))}
                    </div>
                  )}
                </section>
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
