import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { AuthProvider } from '../auth/AuthContext'
import DashboardPage from '../pages/DashboardPage'

function renderDashboardPage() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('dashboard dial panel smoke', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.localStorage.setItem('amo_admin_token', 'token-123')
    vi.restoreAllMocks()
  })

  it('starts outbound call and shows runtime result', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const path = typeof input === 'string' ? input : input.toString()
      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/calls/11111111-1111-1111-1111-111111111111')) {
        return new Response(JSON.stringify({
          accepted: true,
          id: '11111111-1111-1111-1111-111111111111',
          call_id: '11111111-1111-1111-1111-111111111111',
          phone: '+17547365909',
          mode: 'DIRECT',
          status: 'QUEUED',
          error: null,
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/calls')) {
        const initBody = init?.body ? JSON.parse(String(init.body)) : null
        expect(initBody?.voice_strategy_override).toBe('tts_primary')
        return new Response(JSON.stringify({
          accepted: true,
          id: '11111111-1111-1111-1111-111111111111',
          call_id: '11111111-1111-1111-1111-111111111111',
          phone: '+17547365909',
          mode: 'DIRECT',
          status: 'QUEUED',
          error: null,
        }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      throw new Error(`Unexpected fetch path: ${path}`)
    })

    renderDashboardPage()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Быстрый прозвон через Mango/i })).toBeInTheDocument()
    })

    await user.clear(screen.getByLabelText(/Номер для звонка/i))
    await user.type(screen.getByLabelText(/Номер для звонка/i), '+17547365909')
    await user.click(screen.getByRole('button', { name: /^Call$/i }))

    await waitFor(() => {
      expect(screen.getByText(/accepted:/i)).toBeInTheDocument()
    })
    expect(screen.getAllByText(/QUEUED/i).length).toBeGreaterThan(0)
    expect(screen.getByText(/11111111-1111-1111-1111-111111111111/i)).toBeInTheDocument()
  })

  it('shows a clearer operator message for answer timeout while keeping debug payload', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const path = typeof input === 'string' ? input : input.toString()
      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/calls')) {
        return new Response(JSON.stringify({
          detail: {
            error: 'telephony_error',
            message: 'Timed out waiting for leg direct-timeout-123 to answer after 30.0s',
          },
        }), {
          status: 502,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      throw new Error(`Unexpected fetch path: ${path}`)
    })

    renderDashboardPage()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Быстрый прозвон через Mango/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: /^Call$/i }))

    await waitFor(() => {
      expect(screen.getByText(/провайдер не подтвердил ответ абонента/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/Timed out waiting for leg direct-timeout-123/i)).toBeInTheDocument()
  })

  it('allows switching the live direct voice strategy override', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const path = typeof input === 'string' ? input : input.toString()
      if (path.includes('/v1/admin/auth/me')) {
        return new Response(JSON.stringify({ email: 'admin@example.com', role: 'admin' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/calls/22222222-2222-2222-2222-222222222222')) {
        return new Response(JSON.stringify({
          accepted: true,
          id: '22222222-2222-2222-2222-222222222222',
          call_id: '22222222-2222-2222-2222-222222222222',
          phone: '+79252879076',
          mode: 'DIRECT',
          status: 'QUEUED',
          error: null,
        }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (path.includes('/v1/calls')) {
        const body = JSON.parse(String(init?.body || '{}'))
        expect(body.voice_strategy_override).toBe('gemini_primary')
        return new Response(JSON.stringify({
          accepted: true,
          id: '22222222-2222-2222-2222-222222222222',
          call_id: '22222222-2222-2222-2222-222222222222',
          phone: '+79252879076',
          mode: 'DIRECT',
          status: 'QUEUED',
          error: null,
        }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      throw new Error(`Unexpected fetch path: ${path}`)
    })

    renderDashboardPage()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Быстрый прозвон через Mango/i })).toBeInTheDocument()
    })

    await user.selectOptions(screen.getByLabelText(/Голосовой тракт/i), 'gemini_primary')
    await user.click(screen.getByRole('button', { name: /^Call$/i }))

    await waitFor(() => {
      expect(screen.getByText(/22222222-2222-2222-2222-222222222222/i)).toBeInTheDocument()
    })
  })
})
