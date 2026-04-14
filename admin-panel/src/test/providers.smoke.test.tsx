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

  it('renders provider cards, Mango inventory, and routing hints', async () => {
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
              config: { model_id: 'gemini-2.0-flash-live-001', api_version: 'v1beta' },
              secrets: { api_key: { is_set: true, masked_value: 'ge***ey' } },
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
          warnings: [
            'Inbound webhook verification is not configured (MANGO_WEBHOOK_SECRET is empty).',
            'Outbound calling will use an auto-discovered Mango extension because MANGO_FROM_EXT is empty.',
          ],
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
    expect(screen.getByText(/Без авторутинга/i)).toBeInTheDocument()
    expect(screen.getByText(/ma\*\*\*ey/i)).toBeInTheDocument()
    expect(screen.getByText(/This page stores credentials and shows Mango inventory/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Sync numbers from Mango/i })).toBeInTheDocument()
    expect(screen.getAllByText(/ДЛЯ ИИ менеджера \(\+79300350609\)/i).length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText(/AI recommended/i).length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText(/Bound agent:/i).length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('Sales Alpha')).toBeInTheDocument()
    expect(screen.getByText(/Outbound source extension будет auto-discovered/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Go to Agent settings to bind a number/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open bound agent/i })).toHaveAttribute('href', '/agents/agent-1?mango_line=405622036&from=providers')
    expect(screen.getByText(/Last sync status/i)).toBeInTheDocument()
    expect(screen.getByText(/Focused line:/i)).toBeInTheDocument()
    expect(screen.getByText(/Честный статус Mango routing/i)).toBeInTheDocument()
    expect(screen.getByText(/ready for webhook rollout/i)).toBeInTheDocument()
    expect(screen.getByText(/ready for originate smoke/i)).toBeInTheDocument()
    expect(screen.getAllByText(/^active$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^inactive$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^bound$/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/^unbound$/i).length).toBeGreaterThanOrEqual(1)

    await user.click(screen.getByRole('button', { name: /Unbound only/i }))
    expect(screen.getAllByText(/По умолчанию \(\+79585382099\)/i).length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText('Sales Alpha')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /AI recommended/i }))
    expect(screen.getAllByText(/ДЛЯ ИИ менеджера \(\+79300350609\)/i).length).toBeGreaterThanOrEqual(2)
  })
})
