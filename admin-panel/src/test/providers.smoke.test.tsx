import { render, screen, waitFor } from '@testing-library/react'
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

  it('renders provider cards and action buttons', async () => {
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
      throw new Error(`Unexpected fetch path: ${path}`)
    })

    render(
      <MemoryRouter initialEntries={['/providers']}>
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
    expect(screen.getAllByRole('button', { name: /Проверить подключение/i }).length).toBeGreaterThan(0)
    expect(screen.getByText(/Без авторутинга/i)).toBeInTheDocument()
    expect(screen.getByText(/ma\*\*\*ey/i)).toBeInTheDocument()
  })
})
