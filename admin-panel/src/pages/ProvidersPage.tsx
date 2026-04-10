import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'

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
    return 'Not validated yet'
  }
  return new Date(value).toLocaleString()
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

  const providerCards = useMemo(
    () => PROVIDER_DEFINITIONS.map((definition) => ({ definition, setting: settingsByProvider[definition.provider] })),
    [settingsByProvider],
  )

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
      setPageError(err instanceof ApiError ? err.message : 'Failed to load provider settings.')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => {
    void loadProviders()
  }, [loadProviders])

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
        [provider]: 'Settings saved. Stored secrets remain masked and are not shown back in raw form.',
      }))
    } catch (err) {
      setProviderErrors((current) => ({
        ...current,
        [provider]: err instanceof ApiError ? err.message : 'Failed to save provider settings.',
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
      setProviderMessages((current) => ({
        ...current,
        [provider]: `${validation.message}${validation.remote_checked ? '' : ' Remote connection was intentionally not touched.'}`,
      }))
    } catch (err) {
      setProviderErrors((current) => ({
        ...current,
        [provider]: err instanceof ApiError ? err.message : 'Failed to validate provider settings.',
      }))
      await loadProviders()
    } finally {
      setValidatingProvider(null)
    }
  }

  return (
    <section className="stack-page providers-page">
      <article className="hero-card split-card providers-hero">
        <div>
          <p className="eyebrow">Providers Settings</p>
          <h3>Provider credentials and status</h3>
          <p>
            Это безопасный settings layer. Сохранение credentials не включает боевой маршрут автоматически и не
            переводит shared Mango account на AI routing.
          </p>
        </div>
        <div className="status-strip">
          <span className="status-pill">Settings only</span>
          <span className="status-pill">No auto-routing</span>
          <span className="status-pill">Secrets masked</span>
        </div>
      </article>

      {pageError ? <div className="error-banner">{pageError}</div> : null}
      {loading ? <div className="route-state">Loading provider settings…</div> : null}

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
                <strong>Last validation:</strong> {setting.last_validation_message}
                <div className="table-secondary">{formatTimestamp(setting.last_validated_at)}</div>
              </div>
            ) : null}
            {providerMessages[definition.provider] ? <div className="status-banner">{providerMessages[definition.provider]}</div> : null}
            {providerErrors[definition.provider] ? <div className="error-banner">{providerErrors[definition.provider]}</div> : null}

            <form className="editor-form" onSubmit={(event) => void handleSave(event, definition.provider)}>
              <section className="form-section">
                <label className="boxed-toggle toggle-row">
                  <input
                    type="checkbox"
                    checked={form.is_enabled}
                    onChange={(event) => updateEnabled(definition.provider, event.target.checked)}
                  />
                  <span>Mark settings active for future use</span>
                </label>
              </section>

              <section className="form-section two-column-fields">
                {definition.configFields.map((field) => (
                  <label key={field.key}>
                    <span>{field.label}</span>
                    {field.kind === 'textarea' ? (
                      <textarea
                        value={String(form.config[field.key] ?? '')}
                        onChange={(event) => updateConfig(definition.provider, field.key, event.target.value)}
                      />
                    ) : field.kind === 'boolean' ? (
                      <label className="boxed-toggle toggle-row">
                        <input
                          type="checkbox"
                          checked={Boolean(form.config[field.key])}
                          onChange={(event) => updateConfig(definition.provider, field.key, event.target.checked)}
                        />
                        <span>{field.help || field.label}</span>
                      </label>
                    ) : (
                      <input
                        value={String(form.config[field.key] ?? '')}
                        onChange={(event) => updateConfig(definition.provider, field.key, event.target.value)}
                      />
                    )}
                    {field.help && field.kind !== 'boolean' ? <small className="table-secondary">{field.help}</small> : null}
                  </label>
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
                        placeholder={secretState?.is_set ? 'Leave blank to keep stored secret' : 'Enter secret'}
                      />
                      <small className="table-secondary">
                        {secretState?.is_set ? `Stored: ${secretState.masked_value}` : 'Not stored'}
                      </small>
                    </label>
                  )
                })}
              </section>

              <div className="button-row">
                <button type="submit" className="primary-button" disabled={savingProvider === definition.provider}>
                  {savingProvider === definition.provider ? 'Saving…' : 'Save settings'}
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => void handleValidate(definition.provider)}
                  disabled={validatingProvider === definition.provider}
                >
                  {validatingProvider === definition.provider ? 'Validating…' : 'Check connection'}
                </button>
              </div>
            </form>
          </article>
        )
      })}
    </section>
  )
}
