import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { AuthProvider } from '../auth/AuthContext'
import ProtectedRoute from '../components/ProtectedRoute'
import AdminLayout from '../layout/AdminLayout'
import KnowledgeBasePage from '../pages/KnowledgeBasePage'

describe('knowledge base page smoke', () => {
  beforeEach(() => {
    window.localStorage.setItem('amo_admin_token', 'token-123')
    vi.restoreAllMocks()
  })

  it('renders company profile and knowledge documents', async () => {
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const path = typeof input === 'string' ? input : input.toString()
      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/company-profile')) {
        return new Response(JSON.stringify({
          id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
          name: 'AMO Voice',
          is_active: true,
          config: { locale: 'ru-RU' },
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/knowledge-documents?')) {
        return new Response(JSON.stringify({
          items: [
            {
              id: '11111111-1111-1111-1111-111111111111',
              title: 'Pricing sheet',
              category: 'pricing',
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
      if (path.endsWith('/v1/knowledge-documents')) {
        return new Response(JSON.stringify({
          items: [
            {
              id: '11111111-1111-1111-1111-111111111111',
              title: 'Pricing sheet',
              category: 'pricing',
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
      throw new Error(`Unexpected fetch path: ${path}`)
    })

    render(
      <MemoryRouter initialEntries={['/knowledge-base']}>
        <AuthProvider>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route element={<AdminLayout />}>
                <Route path="/knowledge-base" element={<KnowledgeBasePage />} />
              </Route>
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText(/Pricing sheet/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/AMO Voice/i)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Создать документ/i }).length).toBeGreaterThan(0)
  })
})
