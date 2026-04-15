import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { AuthProvider } from '../auth/AuthContext'
import ProtectedRoute from '../components/ProtectedRoute'
import AdminLayout from '../layout/AdminLayout'
import AgentEditorPage from '../pages/AgentEditorPage'

function renderEditor(initialEntry = '/agents/agent-1') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
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
}

function getBoundLineCard() {
  const match = screen.getAllByText(/Назначено агенту/i).find((node) => node.closest('.binding-cta-card'))
  return match?.closest('.binding-cta-card') ?? null
}

describe('agent editor telephony smoke', () => {
  beforeEach(() => {
    window.localStorage.setItem('amo_admin_token', 'token-123')
    vi.restoreAllMocks()
  })

  it('syncs Mango numbers, saves the AI line binding, and reopens with persisted state', async () => {
    const patchBodies: unknown[] = []
    let currentSettings = {
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
      telephony_remote_line_id: null,
      telephony_extension: null,
      telephony_line: null,
      user_settings: { locale: 'ru-RU' },
      knowledge_document_ids: [],
      version: 1,
      created_at: '2026-04-05T00:00:00Z',
      updated_at: '2026-04-05T01:00:00Z',
      assembled_prompt_preview: 'System Prompt:\nBase prompt',
    }

    const aiLine = {
      id: 'line-local-ai',
      provider: 'mango',
      provider_resource_id: '405622036',
      remote_line_id: '405622036',
      phone_number: '+79300350609',
      schema_name: 'ДЛЯ ИИ менеджера',
      display_name: null,
      label: 'ДЛЯ ИИ менеджера',
      extension: null,
      is_active: true,
      is_inbound_enabled: true,
      is_outbound_enabled: false,
      synced_at: '2026-04-13T10:00:00Z',
    }

    const backupLine = {
      id: 'line-local-default',
      provider: 'mango',
      provider_resource_id: '405519147',
      remote_line_id: '405519147',
      phone_number: '+79585382099',
      schema_name: 'По умолчанию',
      display_name: null,
      label: 'По умолчанию',
      extension: null,
      is_active: true,
      is_inbound_enabled: true,
      is_outbound_enabled: false,
      synced_at: '2026-04-13T10:00:00Z',
    }

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
          items: [aiLine, backupLine],
          total: 2,
          synced_count: 2,
          deactivated_count: 0,
          source: 'mango_api',
          synced_at: '2026-04-13T10:00:00Z',
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
          warnings: [
            'Inbound webhook verification is not configured (MANGO_WEBHOOK_SECRET is empty).',
            'Outbound calling is not configured (MANGO_FROM_EXT is empty).',
          ],
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/lines')) {
        return new Response(JSON.stringify({
          items: [aiLine, backupLine],
          total: 2,
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/extensions')) {
        return new Response(JSON.stringify({
          items: [],
          total: 0,
          source: 'mango_api',
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/agent-profiles/agent-1/settings') && method === 'PATCH') {
        const body = JSON.parse(String(init?.body || '{}'))
        patchBodies.push(body)
        currentSettings = {
          ...currentSettings,
          ...body,
          telephony_provider: 'mango',
          telephony_line_id: aiLine.id,
          telephony_remote_line_id: aiLine.remote_line_id,
          telephony_extension: null,
          telephony_line: aiLine,
          knowledge_document_ids: ['doc-1'],
          version: 2,
          updated_at: '2026-04-13T10:00:00Z',
        }
        return new Response(JSON.stringify(currentSettings), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/agent-profiles/agent-1/settings')) {
        return new Response(JSON.stringify(currentSettings), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      throw new Error(`Unexpected fetch path: ${path} (${method})`)
    })

    const view = renderEditor()

    await waitFor(() => {
      expect(screen.getByText(/Синхронизировать номера из Mango/i)).toBeInTheDocument()
    })

    const user = userEvent.setup()

    expect(screen.getByText(/Проверка входящего вебхука не настроена/i)).toBeInTheDocument()
    expect(screen.getByText(/Исходящие звонки не настроены/i)).toBeInTheDocument()
    expect(screen.getByText(/В этом tenant Mango не настроены внутренние номера/i)).toBeInTheDocument()

    await user.click(screen.getByText(/Синхронизировать номера из Mango/i))

    await waitFor(() => {
      expect(screen.getByText(/Mango sync завершён/i)).toBeInTheDocument()
    })

    const lineSelect = screen.getByLabelText(/Выберите номер Mango/i)
    expect(screen.getByRole('option', { name: /ДЛЯ ИИ менеджера \(\+79300350609\)/i })).toBeInTheDocument()

    await user.selectOptions(lineSelect, '405622036')
    await user.click(screen.getByLabelText(/Refund policy/i))
    await user.click(screen.getByRole('button', { name: /Сохранить настройки агента/i }))

    await waitFor(() => {
      expect(patchBodies).toHaveLength(1)
    })

    expect(patchBodies[0]).toMatchObject({
      telephony_provider: 'mango',
      telephony_remote_line_id: '405622036',
      knowledge_document_ids: ['doc-1'],
      voice_provider: 'elevenlabs',
    })

    expect(getBoundLineCard()).toHaveTextContent('ДЛЯ ИИ менеджера (+79300350609)')
    expect(screen.getByText('405622036')).toBeInTheDocument()

    view.unmount()
    renderEditor()

    await waitFor(() => {
      expect(screen.getByLabelText(/Выберите номер Mango/i)).toHaveValue('405622036')
    })
    expect(getBoundLineCard()).toHaveTextContent('ДЛЯ ИИ менеджера (+79300350609)')
    expect(getBoundLineCard()).toHaveTextContent('Назначено агенту')
    expect(screen.queryByText(/Назначьте номер агенту, чтобы включить звонки/i)).not.toBeInTheDocument()
  })

  it('shows deep-link back to providers for the focused Mango line', async () => {
    const aiLine = {
      id: 'line-local-ai',
      provider: 'mango',
      provider_resource_id: '405622036',
      remote_line_id: '405622036',
      phone_number: '+79300350609',
      schema_name: 'ДЛЯ ИИ менеджера',
      display_name: null,
      label: 'ДЛЯ ИИ менеджера',
      extension: null,
      is_active: true,
      is_inbound_enabled: true,
      is_outbound_enabled: false,
      synced_at: '2026-04-13T10:00:00Z',
    }

    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const path = typeof input === 'string' ? input : input.toString()

      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/knowledge-documents?active_only=true')) {
        return new Response(JSON.stringify({ items: [], total: 0 }), {
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
          ],
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/lines')) {
        return new Response(JSON.stringify({
          items: [aiLine],
          total: 1,
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/extensions')) {
        return new Response(JSON.stringify({
          items: [],
          total: 0,
          source: 'mango_api',
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
          telephony_provider: 'mango',
          telephony_line_id: aiLine.id,
          telephony_remote_line_id: aiLine.remote_line_id,
          telephony_extension: null,
          telephony_line: aiLine,
          user_settings: { locale: 'ru-RU' },
          knowledge_document_ids: [],
          version: 2,
          created_at: '2026-04-05T00:00:00Z',
          updated_at: '2026-04-13T10:00:00Z',
          assembled_prompt_preview: 'System Prompt:\\nBase prompt',
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      throw new Error(`Unexpected fetch path: ${path}`)
    })

    renderEditor('/agents/agent-1?mango_line=405622036&from=providers')

    await waitFor(() => {
      expect(screen.getByRole('link', { name: /Назад к Mango для этой линии/i })).toBeInTheDocument()
    })

    expect(screen.getByRole('link', { name: /Назад к Mango для этой линии/i })).toHaveAttribute('href', '/providers?line=405622036')
    expect(getBoundLineCard()).toHaveTextContent('ДЛЯ ИИ менеджера (+79300350609)')
  })

  it('shows readiness warnings without blocking line selection', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const path = typeof input === 'string' ? input : input.toString()

      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/knowledge-documents?active_only=true')) {
        return new Response(JSON.stringify({ items: [], total: 0 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/readiness')) {
        return new Response(JSON.stringify({
          api_configured: true,
          webhook_secret_configured: false,
          from_ext_configured: false,
          warnings: [
            'Inbound webhook verification is not configured (MANGO_WEBHOOK_SECRET is empty).',
            'Outbound calling is not configured (MANGO_FROM_EXT is empty).',
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
              id: 'line-local-ai',
              provider: 'mango',
              provider_resource_id: '405622036',
              remote_line_id: '405622036',
              phone_number: '+79300350609',
              schema_name: 'ДЛЯ ИИ менеджера',
              display_name: null,
              label: 'ДЛЯ ИИ менеджера',
              extension: null,
              is_active: true,
              is_inbound_enabled: true,
              is_outbound_enabled: false,
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
        return new Response(JSON.stringify({ items: [], total: 0, source: 'mango_api' }), {
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
          telephony_remote_line_id: null,
          telephony_extension: null,
          telephony_line: null,
          user_settings: {},
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

      throw new Error(`Unexpected fetch path: ${path}`)
    })

    renderEditor()

    await waitFor(() => {
      expect(screen.getByText(/Проверка входящего вебхука не настроена/i)).toBeInTheDocument()
    })

    expect(screen.getByText(/Исходящие звонки не настроены/i)).toBeInTheDocument()
    expect(screen.getByText(/В этом tenant Mango не настроены внутренние номера/i)).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /ДЛЯ ИИ менеджера \(\+79300350609\)/i })).toBeInTheDocument()
  })

  it('shows non-blocking rate-limit warning when Mango extensions endpoint is temporarily unavailable', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const path = typeof input === 'string' ? input : input.toString()

      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/knowledge-documents?active_only=true')) {
        return new Response(JSON.stringify({ items: [], total: 0 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/readiness')) {
        return new Response(JSON.stringify({
          api_configured: true,
          webhook_secret_configured: false,
          from_ext_configured: false,
          warnings: [],
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/lines')) {
        return new Response(JSON.stringify({
          items: [
            {
              id: 'line-local-ai',
              provider: 'mango',
              provider_resource_id: '405622036',
              remote_line_id: '405622036',
              phone_number: '+79300350609',
              schema_name: 'ДЛЯ ИИ менеджера',
              display_name: null,
              label: 'ДЛЯ ИИ менеджера',
              extension: null,
              is_active: true,
              is_inbound_enabled: true,
              is_outbound_enabled: false,
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
          detail: {
            error: 'mango_api_unavailable',
            message: 'Failed to load Mango extensions.',
            detail: {
              http_status: 429,
            },
          },
        }), {
          status: 502,
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
          telephony_remote_line_id: null,
          telephony_extension: null,
          telephony_line: null,
          user_settings: {},
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

      throw new Error(`Unexpected fetch path: ${path}`)
    })

    renderEditor()

    await waitFor(() => {
      expect(screen.getByText(/Mango временно ограничил API внутренних номеров по лимиту запросов/i)).toBeInTheDocument()
    })
    expect(screen.getByRole('option', { name: /ДЛЯ ИИ менеджера \(\+79300350609\)/i })).toBeInTheDocument()
  })

  it('shows an auto-discovery notice instead of a hard outbound blocker when Mango can resolve from_ext automatically', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const path = typeof input === 'string' ? input : input.toString()

      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/knowledge-documents?active_only=true')) {
        return new Response(JSON.stringify({ items: [], total: 0 }), {
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
              id: 'line-local-ai',
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
        return new Response(JSON.stringify({ items: [], total: 0, source: 'mango_api' }), {
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
          telephony_remote_line_id: null,
          telephony_extension: null,
          telephony_line: null,
          user_settings: {},
          knowledge_document_ids: [],
          version: 1,
          created_at: '2026-04-05T00:00:00Z',
          updated_at: '2026-04-05T01:00:00Z',
          assembled_prompt_preview: 'System Prompt:\\nBase prompt',
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      throw new Error(`Unexpected fetch path: ${path}`)
    })

    renderEditor()

    await waitFor(() => {
      expect(screen.getByText(/автоматически найденный внутренний номер Mango/i)).toBeInTheDocument()
    })

    expect(screen.queryByText(/Исходящие звонки не настроены/i)).not.toBeInTheDocument()
  })

  it('shows a friendly inactive-line error instead of raw backend codes', async () => {
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
        return new Response(JSON.stringify({ items: [], total: 0 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/readiness')) {
        return new Response(JSON.stringify({
          api_configured: true,
          webhook_secret_configured: false,
          from_ext_configured: false,
          warnings: [],
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/telephony/mango/lines')) {
        return new Response(JSON.stringify({
          items: [
            {
              id: 'line-local-inactive',
              provider: 'mango',
              provider_resource_id: '405622036',
              remote_line_id: '405622036',
              phone_number: '+79300350609',
              schema_name: 'ДЛЯ ИИ менеджера',
              display_name: null,
              label: 'ДЛЯ ИИ менеджера',
              extension: null,
              is_active: true,
              is_inbound_enabled: true,
              is_outbound_enabled: false,
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
        return new Response(JSON.stringify({ items: [], total: 0, source: 'mango_api' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      if (path.includes('/v1/agent-profiles/agent-1/settings') && method === 'PATCH') {
        return new Response(JSON.stringify({
          detail: {
            error: 'telephony_line_inactive',
            message: 'Telephony line is inactive.',
          },
        }), {
          status: 409,
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
          telephony_remote_line_id: null,
          telephony_extension: null,
          telephony_line: null,
          user_settings: {},
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

    renderEditor()

    await waitFor(() => {
      expect(screen.getByLabelText(/Выберите номер Mango/i)).toBeInTheDocument()
    })

    const user = userEvent.setup()
    await user.selectOptions(screen.getByLabelText(/Выберите номер Mango/i), '405622036')
    await user.click(screen.getByRole('button', { name: /Сохранить настройки агента/i }))

    await waitFor(() => {
      expect(screen.getByText(/Выбранная линия Mango неактивна/i)).toBeInTheDocument()
    })
    expect(screen.queryByText(/telephony_line_inactive/i)).not.toBeInTheDocument()
  })
})
