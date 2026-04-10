import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import { apiFetch } from '../lib/api'
import { clearToken, readToken, writeToken } from './storage'

type AdminUser = {
  email: string
  role: string
}

type LoginResponse = {
  access_token: string
  token_type: string
  expires_at: string
  user: AdminUser
}

type AuthContextValue = {
  user: AdminUser | null
  token: string | null
  isAuthenticated: boolean
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => readToken())
  const [user, setUser] = useState<AdminUser | null>(null)
  const [loading, setLoading] = useState(true)

  const logout = useCallback(() => {
    clearToken()
    setToken(null)
    setUser(null)
  }, [])

  const hydrateUser = useCallback(async (nextToken: string) => {
    const me = await apiFetch<AdminUser>('/v1/admin/auth/me', {}, nextToken)
    setUser(me)
  }, [])

  useEffect(() => {
    if (!token) {
      setLoading(false)
      return
    }

    let mounted = true
    setLoading(true)
    hydrateUser(token)
      .catch(() => {
        if (mounted) {
          logout()
        }
      })
      .finally(() => {
        if (mounted) {
          setLoading(false)
        }
      })

    return () => {
      mounted = false
    }
  }, [hydrateUser, logout, token])

  const login = useCallback(async (email: string, password: string) => {
    const response = await apiFetch<LoginResponse>('/v1/admin/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
    writeToken(response.access_token)
    setToken(response.access_token)
    setUser(response.user)
  }, [])

  const value = useMemo<AuthContextValue>(() => ({
    user,
    token,
    isAuthenticated: Boolean(token && user),
    loading,
    login,
    logout,
  }), [loading, login, logout, token, user])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}
