import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { AuthProvider } from '../auth/AuthContext'
import ProtectedRoute from '../components/ProtectedRoute'
import AdminLayout from '../layout/AdminLayout'
import ProvidersPage from '../pages/ProvidersPage'

describe('providers page smoke', () => {
  beforeEach(() => {
    window.localStorage.setItem('amo_admin_token', 'token-123')
    vi.restoreAllMocks()
  })

  it('renders SaaS-style Mango status, inventory, and routing hints', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const path = typeof input === 'string' ? input : input.toString()
      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/providers/settings')) {
        return new Response(JSON.stringify({
          items: [
            {
              provider: 'mango',
              display_name: 'Mango',
              is_enabled: false,
              activation_status: 'inactive',
              status: 'not_tested',
              safe_mode_note: 'Saving Mango credentials does not activate AI routing.',
              config: { from_ext: '101', webhook_ip_allowlist: '' },
              secrets: {
                api_key: { is_set: true, masked_value: 'ma***ey' },
                api_salt: { is_set: true, masked_value: 'ma***lt' },
                webhook_secret: { is_set: false, masked_value: null },
                webhook_shared_secret: { is_set: false, masked_value: null },
              },
              secrets_accessible: true,
              storage_warning: null,
              last_validated_at: null,
              last_validation_message: null,
              last_validation_remote_checked: false,
            },
            {
              provider: 'gemini',
              display_name: 'Gemini',
              is_enabled: true,
              activation_status: 'active',
              status: 'configured',
              safe_mode_note: 'Gemini settings are stored independently.',
              config: { model_id: 'gemini-2.5-flash-native-audio-preview-12-2025', api_version: 'v1beta' },
              secrets: { api_key: { is_set: true, masked_value: 'ge***ey' } },
              secrets_accessible: true,
              storage_warning: null,
              last_validated_at: '2030-01-01T00:00:00Z',
              last_validation_message: 'Gemini model settings responded successfully.',
              last_validation_remote_checked: true,
            },
            {
              provider: 'elevenlabs',
              display_name: 'ElevenLabs',
              is_enabled: false,
              activation_status: 'inactive',
              status: 'not_tested',
              safe_mode_note: 'Saving ElevenLabs settings does not switch runtime automatically.',
              config: { voice_id: 'voice-1', enabled: true },
              secrets: { api_key: { is_set: false, masked_value: null } },
              secrets_accessible: true,
              storage_warning: null,
              last_validated_at: null,
              last_validation_message: null,
              last_validation_remote_checked: false,
            },
            {
              provider: 'vapi',
              display_name: 'Vapi',
              is_enabled: false,
              activation_status: 'inactive',
              status: 'not_tested',
              safe_mode_note: 'Vapi settings are stored only as config.',
              config: { assistant_id: '', phone_number_id: '', base_url: 'https://api.vapi.ai', server_url: '' },
              secrets: {
                api_key: { is_set: false, masked_value: null },
                webhook_secret: { is_set: false, masked_value: null },
              },
              secrets_accessible: true,
              storage_warning: null,
              last_validated_at: null,
              last_validation_message: null,
              last_validation_remote_checked: false,
            },
          ],
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/telephony/mango/readiness')) {
        return new Response(JSON.stringify({
          api_configured: true,
          webhook_secret_configured: false,
          from_ext_configured: false,
          from_ext_auto_discoverable: true,
          telephony_runtime_provider: 'mango',
          telephony_runtime_real: true,
          backend_url: 'http://127.0.0.1:8000',
          webhook_url: 'http://127.0.0.1:8000/v1/webhooks/mango',
          webhook_url_public: false,
          inbound_webhook_smoke_ready: false,
          outbound_originate_smoke_ready: true,
          inbound_ai_runtime_ready: false,
          missing_requirements: ['mango_webhook_secret_missing', 'backend_url_not_public', 'media_gateway_disabled'],
          warnings: [
            'Inbound webhook verification is not configured (MANGO_WEBHOOK_SECRET is empty).',
            'BACKEND_URL is not publicly reachable. Mango cannot deliver a live webhook to this backend yet.',
            'Outbound calling will use an auto-discovered Mango extension because MANGO_FROM_EXT is empty.',
            'Inbound AI runtime is blocked because MEDIA_GATEWAY_ENABLED=false.',
          ],
          route_readiness: {
            inbound_webhook: {
              key: 'inbound_webhook',
              ready: false,
              status: 'blocked',
              summary: 'Render webhook delivery is not ready yet.',
              blockers: ['Webhook secret is missing.', 'BACKEND_URL is not public.'],
            },
            outbound_originate: {
              key: 'outbound_originate',
              ready: true,
              status: 'ready',
              summary: 'Agent-bound Mango lines can run an outbound originate smoke.',
              blockers: [],
            },
            inbound_ai_runtime: {
              key: 'inbound_ai_runtime',
              ready: false,
              status: 'blocked',
              summary: 'Inbound AI runtime is still blocked.',
              blockers: ['Webhook secret is missing.', 'BACKEND_URL is not public.', 'MEDIA_GATEWAY_ENABLED=false.'],
            },
          },
          render_summary: {
            ready_count: 1,
            blocked_count: 2,
            overall_status: 'partial',
            operator_summary: 'Render-side Mango routing is partially ready. Check the blocked cards before live smoke.',
          },
          actionable_next_step: {
            key: 'make_backend_url_public',
            title: 'Make BACKEND_URL public',
            description: 'Mango cannot deliver a webhook to a local or private BACKEND_URL. Point it to the public Render backend URL.',
            cta_label: 'Set a public BACKEND_URL',
            scope: 'inbound_webhook',
          },
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/telephony/mango/lines')) {
        return new Response(JSON.stringify({
          items: [
            {
              id: 'line-ai',
              provider: 'mango',
              provider_resource_id: '405622036',
              remote_line_id: '405622036',
              phone_number: '+79300350609',
              schema_name: 'ДЛЯ ИИ менеджера',
              display_name: 'ДЛЯ ИИ менеджера',
              label: 'ДЛЯ ИИ менеджера',
              extension: null,
              is_active: true,
              is_inbound_enabled: true,
              is_outbound_enabled: false,
              synced_at: '2030-01-02T00:00:00Z',
            },
            {
              id: 'line-free',
              provider: 'mango',
              provider_resource_id: '405519147',
              remote_line_id: '405519147',
              phone_number: '+79585382099',
              schema_name: 'По умолчанию',
              display_name: 'По умолчанию',
              label: 'По умолчанию',
              extension: null,
              is_active: true,
              is_inbound_enabled: true,
              is_outbound_enabled: false,
              is_protected: true,
              synced_at: '2030-01-02T00:00:00Z',
            },
          ],
          total: 2,
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/telephony/mango/routing-map')) {
        return new Response(JSON.stringify({
          items: [
            {
              line_id: 'line-ai',
              provider_resource_id: '405622036',
              remote_line_id: '405622036',
              phone_number: '+79300350609',
              schema_name: 'ДЛЯ ИИ менеджера',
              display_name: 'ДЛЯ ИИ менеджера',
              label: 'ДЛЯ ИИ менеджера',
              is_active: true,
              is_inbound_enabled: true,
              agent_id: 'agent-1',
              agent_name: 'Sales Alpha',
              agent_is_active: true,
            },
            {
              line_id: 'line-free',
              provider_resource_id: '405519147',
              remote_line_id: '405519147',
              phone_number: '+79585382099',
              schema_name: 'По умолчанию',
              display_name: 'По умолчанию',
              label: 'По умолчанию',
              is_active: true,
              is_inbound_enabled: true,
              is_protected: true,
              agent_id: null,
              agent_name: null,
              agent_is_active: null,
            },
          ],
          total: 2,
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      throw new Error(`Unexpected fetch path: ${path}`)
    })

    render(
      <MemoryRouter initialEntries={['/providers?line=405622036']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route element={<AdminLayout />}>
                <Route path="/providers" element={<ProvidersPage />} />
              </Route>
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /Сохранить настройки/i }).length).toBeGreaterThan(0)
    })
    const user = userEvent.setup()
    expect(screen.getAllByRole('button', { name: /Проверить подключение/i }).length).toBeGreaterThan(0)
    expect(screen.getByText(/ma\*\*\*ey/i)).toBeInTheDocument()
    expect(screen.getByText(/Эта страница хранит учётные данные и показывает инвентарь Mango/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Синхронизировать номера из Mango/i })).toBeInTheDocument()
    expect(screen.getAllByText(/ДЛЯ ИИ менеджера \(\+79300350609\)/i).length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText(/Рекомендуется для AI/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/Привязанный агент:/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/Статус Mango/i)).toBeInTheDocument()
    expect(screen.getByText(/Подключено → Синхронизировано → Назначено → Готово к live/i)).toBeInTheDocument()
    expect(screen.getAllByText(/^Подключено$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^Синхронизировано$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^Назначено$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^Готово к live$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/^Статус API-ключа$/i)).toBeInTheDocument()
    expect(screen.getAllByText(/^Готовность$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByRole('button', { name: /Сохранить настройки/i }).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Синхронизировать номера из Mango/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/Назначьте номер агенту, чтобы включить звонки/i)).toBeInTheDocument()
    expect(screen.queryByText(/Номера ещё не синхронизированы/i)).not.toBeInTheDocument()
    expect(screen.getAllByText('Sales Alpha').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/Исходный внутренний номер будет найден автоматически/i)).toBeInTheDocument()
    expect(screen.getAllByText(/BACKEND_URL не является публичным/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('link', { name: /Перейти в настройки агента и назначить номер/i })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /Открыть привязанного агента/i })[0]).toHaveAttribute('href', '/agents/agent-1?mango_line=405622036&from=providers')
    expect(screen.getByText(/Статус последней синхронизации/i)).toBeInTheDocument()
    expect(screen.getByText(/Инвентарь уже виден/i)).toBeInTheDocument()
    expect(screen.getByText(/Номера уже синхронизированы из Mango/i)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Обновить инвентарь/i }).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/Линия в фокусе:/i)).toBeInTheDocument()
    expect(screen.getByText(/Готовность маршрутизации на Render/i)).toBeInTheDocument()
    expect(screen.getByText(/Маршрутизация на Render частично готова/i)).toBeInTheDocument()
    expect(screen.getByText(/Главный следующий шаг/i)).toBeInTheDocument()
    expect(screen.getByText(/Сделайте BACKEND_URL публичным/i)).toBeInTheDocument()
    expect(screen.getByText(/Mango не сможет доставить вебхук на локальный или приватный BACKEND_URL/i)).toBeInTheDocument()
    expect(screen.getByText(/Задать публичный BACKEND_URL/i)).toBeInTheDocument()
    expect(screen.getAllByText(/^Входящий вебхук$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^Исходящий вызов$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^Входящий AI-рантайм$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/входящий вебхук не защищён|Проверка входящего вебхука не настроена/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/BACKEND_URL не является публичным/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/MEDIA_GATEWAY_ENABLED=false/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/Операционные детали/i)).toBeInTheDocument()
    expect(screen.getAllByText(/^mango$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^активные$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^неактивные$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^назначенные$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^свободные$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^защищённые$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/число сырых линий/i)).toBeInTheDocument()
    expect(screen.getByText(/последний ответ синхронизации/i)).toBeInTheDocument()
    expect(screen.getByText(/флаги готовности/i)).toBeInTheDocument()
    expect(screen.getByText(/защищённая линия:/i)).toBeInTheDocument()
    expect(screen.getAllByText(/\+79585382099/i).length).toBeGreaterThanOrEqual(1)

    await user.click(screen.getByRole('button', { name: /Только неназначенные/i }))
    expect(screen.queryByText(/По умолчанию \(\+79585382099\)/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Sales Alpha')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Рекомендовано для AI/i }))
    expect(screen.getAllByText(/ДЛЯ ИИ менеджера \(\+79300350609\)/i).length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText(/По умолчанию \(\+79585382099\)/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Защищена/i)).not.toBeInTheDocument()
  })
})
