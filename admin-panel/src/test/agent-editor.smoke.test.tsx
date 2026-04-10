import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { AuthProvider } from '../auth/AuthContext'
import ProtectedRoute from '../components/ProtectedRoute'
import AdminLayout from '../layout/AdminLayout'
import AgentEditorPage from '../pages/AgentEditorPage'

describe('agent editor knowledge smoke', () => {
  beforeEach(() => {
    window.localStorage.setItem('amo_admin_token', 'token-123')
    vi.restoreAllMocks()
  })

  it('renders bound knowledge documents for an existing agent', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const path = typeof input === 'string' ? input : input.toString()
      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/agents/agent-1/knowledge')) {
        return new Response(JSON.stringify({
          items: [
            {
              id: 'binding-1',
              agent_profile_id: 'agent-1',
              knowledge_document_id: 'doc-1',
              created_at: '2026-04-05T00:00:00Z',
              knowledge_document: {
                id: 'doc-1',
                title: 'Refund policy',
                category: 'company_policy',
                content: 'Refunds require approval.',
                is_active: true,
                metadata: {},
                created_at: '2026-04-05T00:00:00Z',
                updated_at: '2026-04-05T01:00:00Z',
              },
            },
          ],
          total: 1,
        }), {
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
      if (path.includes('/v1/agents/agent-1')) {
        return new Response(JSON.stringify({
          id: 'agent-1',
          name: 'Sales Alpha',
          is_active: true,
          system_prompt: 'Base prompt',
          voice_strategy: 'tts_primary',
          config: {},
          version: 2,
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
      expect(screen.getAllByText(/Refund policy/i).length).toBeGreaterThan(0)
    })
    expect(screen.getByText(/Controlled context for this agent/i)).toBeInTheDocument()
  })
})
