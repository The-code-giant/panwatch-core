import { fetchAPI } from './client'

export interface AuthStatus {
  initialized: boolean
}

export interface AuthTokenPayload {
  token: string
  expires_at: string
}

export interface LoginPayload {
  username: string
  password: string
}

/**
 * `GET /auth/me`. `user` is the legacy identity string ("user", or "guest"
 * before a password is set). `capabilities` is a flat list of `<noun>:<verb>`
 * names (see OPERATOR_CAPABILITIES in src/web/api/auth.py); core's
 * single-operator backend always returns the full list.
 */
export interface AuthMe {
  user: string
  capabilities: string[]
}

export const authApi = {
  status: () => fetchAPI<AuthStatus>('/auth/status'),
  me: () => fetchAPI<AuthMe>('/auth/me'),
  login: (payload: LoginPayload) =>
    fetchAPI<AuthTokenPayload>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  setup: (payload: LoginPayload) =>
    fetchAPI<AuthTokenPayload>('/auth/setup', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
