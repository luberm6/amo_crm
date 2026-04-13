import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { AuthProvider } from '../auth/AuthContext'
import ProtectedRoute from '../components/ProtectedRoute'
import AdminLayout from '../layout/AdminLayout'
import AgentEditorPage from '../pages/AgentEditorPage'

describe('agent editor telephony smoke', () => {
  beforeEach(() => {
    window.localStorage.setItem('amo_admin_token', 'token-123')
    vi.restoreAllMocks()
  })

  it('syncs Mango numbers and saves agent telephony settings', async () => {
    const patchBodies: unknown[] = []

    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const path = typeof input === 'string' ? input : input.toString()
      const method = init?.method || 'GET'

      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/knowledge-documents?active_only=true')) {
        return new Response(JSON.stringify({
          items: [
            {
              id: 'doc-1',
              title: 'Refund policy',
              category: 'company_policy',
              is_active: true,
              created_at: '2026-04-05T00:00:00Z',
              updated_at: '2026-04-05T01:00:00Z',
            },
          ],
          total: 1,
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/sync-lines') && method === 'POST') {
        return new Response(JSON.stringify({
          items: [
            {
              id: 'line-1',
              provider: 'mango',
              provider_resource_id: 'line-resource-1',
              phone_number: '+74951234567',
              display_name: 'Main line',
              extension: '101',
              is_active: true,
              is_inbound_enabled: true,
              is_outbound_enabled: true,
              synced_at: '2026-04-13T10:00:00Z',
            },
          ],
          total: 1,
          synced_count: 1,
          deactivated_count: 0,
          source: 'mango_api',
          synced_at: '2026-04-13T10:00:00Z',
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/lines')) {
        return new Response(JSON.stringify({
          items: [
            {
              id: 'line-1',
              provider: 'mango',
              provider_resource_id: 'line-resource-1',
              phone_number: '+74951234567',
              display_name: 'Main line',
              extension: '101',
              is_active: true,
              is_inbound_enabled: true,
              is_outbound_enabled: true,
              synced_at: '2026-04-13T10:00:00Z',
            },
          ],
          total: 1,
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/extensions')) {
        return new Response(JSON.stringify({
          items: [
            {
              provider_resource_id: 'user-101',
              extension: '101',
              display_name: 'Alice',
              line_provider_resource_id: 'line-resource-1',
              line_phone_number: '+74951234567',
            },
          ],
          total: 1,
          source: 'mango_api',
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/agent-profiles/agent-1/settings') && method === 'PATCH') {
        patchBodies.push(JSON.parse(String(init?.body || '{}')))
        return new Response(JSON.stringify({
          agent_profile_id: 'agent-1',
          name: 'Sales Alpha',
          is_active: true,
          system_prompt: 'Base prompt',
          tone_rules: '',
          business_rules: '',
          sales_objectives: '',
          greeting_text: '',
          transfer_rules: '',
          prohibited_promises: '',
          voice_strategy: 'tts_primary',
          voice_provider: 'elevenlabs',
          telephony_provider: 'mango',
          telephony_line_id: 'line-1',
          telephony_extension: '101',
          telephony_line: {
            id: 'line-1',
            provider: 'mango',
            provider_resource_id: 'line-resource-1',
            phone_number: '+74951234567',
            display_name: 'Main line',
            extension: '101',
            is_active: true,
            is_inbound_enabled: true,
            is_outbound_enabled: true,
            synced_at: '2026-04-13T10:00:00Z',
          },
          user_settings: { locale: 'ru-RU' },
          knowledge_document_ids: ['doc-1'],
          version: 2,
          created_at: '2026-04-05T00:00:00Z',
          updated_at: '2026-04-13T10:00:00Z',
          assembled_prompt_preview: 'System Prompt:\nBase prompt',
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/agent-profiles/agent-1/settings')) {
        return new Response(JSON.stringify({
          agent_profile_id: 'agent-1',
          name: 'Sales Alpha',
          is_active: true,
          system_prompt: 'Base prompt',
          tone_rules: '',
          business_rules: '',
          sales_objectives: '',
          greeting_text: '',
          transfer_rules: '',
          prohibited_promises: '',
          voice_strategy: 'tts_primary',
          voice_provider: 'elevenlabs',
          telephony_provider: null,
          telephony_line_id: null,
          telephony_extension: null,
          telephony_line: null,
          user_settings: { locale: 'ru-RU' },
          knowledge_document_ids: [],
          version: 1,
          created_at: '2026-04-05T00:00:00Z',
          updated_at: '2026-04-05T01:00:00Z',
          assembled_prompt_preview: 'System Prompt:\nBase prompt',
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      throw new Error(`Unexpected fetch path: ${path} (${method})`)
    })

    render(
      <MemoryRouter initialEntries={['/agents/agent-1']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route element={<AdminLayout />}>
                <Route path="/agents/:agentId" element={<AgentEditorPage />} />
              </Route>
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText(/Sync numbers from Mango/i)).toBeInTheDocument()
    })

    const user = userEvent.setup()
    await user.click(screen.getByText(/Sync numbers from Mango/i))

    await waitFor(() => {
      expect(screen.getByText(/Mango sync завершён/i)).toBeInTheDocument()
    })

    await user.selectOptions(screen.getByLabelText(/Номер Mango/i), 'line-1')
    await user.selectOptions(screen.getByLabelText(/Extension \/ сотрудник/i), '101')
    await user.click(screen.getByLabelText(/Refund policy/i))
    await user.click(screen.getByRole('button', { name: /Сохранить настройки агента/i }))

    await waitFor(() => {
      expect(patchBodies).toHaveLength(1)
    })

    expect(patchBodies[0]).toMatchObject({
      telephony_provider: 'mango',
      telephony_line_id: 'line-1',
      telephony_extension: '101',
      knowledge_document_ids: ['doc-1'],
      voice_provider: 'elevenlabs',
    })
    expect(screen.getByText(/linked/i)).toBeInTheDocument()
  })
})
