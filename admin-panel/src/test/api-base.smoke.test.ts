import { afterEach, describe, expect, it, vi } from 'vitest'

describe('API base URL resolution', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('defaults to an empty base URL when VITE_API_BASE_URL is not set', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    const mod = await import('../lib/api')
    expect(mod.API_BASE_URL).toBe('')
  })

  it('uses VITE_API_BASE_URL when explicitly configured', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://voice.example.com')
    const mod = await import('../lib/api')
    expect(mod.API_BASE_URL).toBe('https://voice.example.com')
  })
})
