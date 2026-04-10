import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { vi } from 'vitest'

import { AuthProvider } from '../auth/AuthContext'
import ProtectedRoute from '../components/ProtectedRoute'
import AdminLayout from '../layout/AdminLayout'
import AgentsPage from '../pages/AgentsPage'

describe('agents page smoke', () => {
  beforeEach(() => {
    window.localStorage.setItem('amo_admin_token', 'token-123')
    vi.restoreAllMocks()
  })

  it('renders agent list from backend', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const path = typeof input === 'string' ? input : input.toString()
      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/agents')) {
        return new Response(JSON.stringify({
          items: [
            {
              id: '11111111-1111-1111-1111-111111111111',
              name: 'Sales Alpha',
              is_active: true,
              voice_strategy: 'tts_primary',
              version: 3,
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
      throw new Error(`Unexpected fetch path: ${path}`)
    })

    render(
      <MemoryRouter initialEntries={['/agents']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route element={<AdminLayout />}>
                <Route path="/agents" element={<AgentsPage />} />
              </Route>
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText(/Sales Alpha/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/tts_primary/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Create agent/i })).toBeInTheDocument()
  })
})
