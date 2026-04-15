import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { vi } from 'vitest'

import { AuthProvider } from '../auth/AuthContext'
import ProtectedRoute from '../components/ProtectedRoute'
import AdminLayout from '../layout/AdminLayout'
import LoginPage from '../pages/LoginPage'

function renderWithProviders(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<ProtectedRoute />}>
            <Route element={<AdminLayout />}>
              <Route path="/" element={<div>Dashboard content</div>} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('admin panel smoke', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders login page', () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: { message: 'unauthorized' } }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    }))

    renderWithProviders('/login')
    expect(screen.getByRole('heading', { name: /Панель управления AMO CRM Voice/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Войти/i })).toBeInTheDocument()
  })

  it('redirects protected route to login without token', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: { message: 'unauthorized' } }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    }))

    renderWithProviders('/')
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Панель управления AMO CRM Voice/i })).toBeInTheDocument()
    })
  })

  it('logs in and renders layout', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const path = typeof input === 'string' ? input : input.toString()
      if (path.includes('/v1/admin/auth/login')) {
        return new Response(JSON.stringify({
          access_token: 'token-123',
          token_type: 'bearer',
          expires_at: '2030-01-01T00:00:00Z',
          user: { email: 'admin@example.com', role: 'admin' },
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      throw new Error(`Unexpected fetch path: ${path}`)
    })

    renderWithProviders('/login')

    await user.type(screen.getByLabelText(/Электронная почта/i), 'admin@example.com')
    await user.type(screen.getByLabelText(/Пароль/i), 'password123')
    await user.click(screen.getByRole('button', { name: /Войти/i }))

    await waitFor(() => {
      expect(screen.getByText(/Dashboard content/i)).toBeInTheDocument()
    })
    expect(screen.getByRole('navigation', { name: /Навигация администратора/i })).toBeInTheDocument()
    expect(screen.getByText(/admin@example.com/i)).toBeInTheDocument()
  })
})
