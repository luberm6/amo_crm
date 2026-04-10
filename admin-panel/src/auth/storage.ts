const TOKEN_KEY = 'amo_admin_token'

export function readToken(): string | null {
  return window.localStorage.getItem(TOKEN_KEY)
}

export function writeToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY)
}
