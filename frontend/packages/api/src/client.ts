const API_BASE = '/api'
const DEFAULT_TIMEOUT_MS = 20000
const CSRF_HEADER = 'X-CSRF-Token'
const SAFE_METHODS = new Set(['GET', 'HEAD'])

/**
 * A function a deployment registers with `setCsrfTokenProvider` to hand
 * `fetchAPI` a CSRF double-submit token for the request about to go out.
 * `path` is exactly the argument `fetchAPI` was called with (root-relative,
 * e.g. `/stocks/42`); `method` is the resolved HTTP method, upper-cased.
 * Returning `null`/`undefined` (or a resolved promise of one) attaches no
 * header for that request, same as no provider at all.
 */
export type CsrfTokenProvider = (
  path: string,
  method: string,
) => string | null | undefined | Promise<string | null | undefined>

let csrfTokenProvider: CsrfTokenProvider | null = null

/**
 * Register (or, passing `null`, clear) the CSRF-token provider `fetchAPI`
 * consults for every non-GET/HEAD `/api/*` request.
 *
 * WHY THIS EXISTS: core's own backend only ever checks the `Authorization:
 * Bearer` header this module already sends, so core registers nothing and
 * `fetchAPI` behaves exactly as it always has. A deployment that fronts core
 * with a browser boundary that also demands a CSRF double-submit header
 * (a cookie-session overlay, for instance) registers its own token source
 * here; this module does not know or care what produces the token, what a
 * "session" is, or what cookie it might live in -- it only guarantees to ask
 * for one, once something has registered, on every request that is not
 * GET/HEAD.
 *
 * DEFAULT IS INERT: with no provider registered (core's default, and the
 * state before anything calls this function), `fetchAPI` attaches no such
 * header -- today's bearer-only shape, byte for byte.
 *
 * SAME-ORIGIN ONLY, STRUCTURALLY: `fetchAPI` only ever requests
 * `${API_BASE}${path}` (see below), a root-relative URL, so a request built
 * by this module is same-origin by construction -- there is no branch here
 * that could send this header cross-origin, because there is no code path
 * that builds a cross-origin request in the first place.
 */
export function setCsrfTokenProvider(provider: CsrfTokenProvider | null): void {
  csrfTokenProvider = provider
}

interface ApiResponse<T> {
  code: number
  success?: boolean
  data: T
  message: string
}

export function getToken(): string | null {
  return localStorage.getItem('token')
}

export function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('token_expires')
  window.location.href = '/login'
}

export function isAuthenticated(): boolean {
  const token = getToken()
  if (!token) return false

  const expires = localStorage.getItem('token_expires')
  if (expires && new Date(expires) < new Date()) {
    logout()
    return false
  }
  return true
}

export interface ApiRequestOptions extends RequestInit {
  timeoutMs?: number
}

export async function fetchAPI<T>(path: string, options?: ApiRequestOptions): Promise<T> {
  const headers: Record<string, string> = {}

  const token = getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const method = (options?.method || 'GET').toUpperCase()
  if (csrfTokenProvider && !SAFE_METHODS.has(method)) {
    const csrfToken = await csrfTokenProvider(path, method)
    if (csrfToken) {
      headers[CSRF_HEADER] = csrfToken
    }
  }

  if (options?.body) {
    headers['Content-Type'] = 'application/json'
  }

  const timeoutController = options?.signal ? null : new AbortController()
  const timeoutMs = typeof options?.timeoutMs === 'number' && options.timeoutMs > 0
    ? options.timeoutMs
    : DEFAULT_TIMEOUT_MS
  const timeoutId = timeoutController
    ? window.setTimeout(() => timeoutController.abort(), timeoutMs)
    : null

  let res: Response
  try {
    const { timeoutMs: _timeoutMs, ...requestOptions } = options || {}
    res = await fetch(`${API_BASE}${path}`, {
      ...requestOptions,
      headers: {
        ...headers,
        ...(requestOptions.headers as Record<string, string> | undefined),
      },
      signal: requestOptions.signal || timeoutController?.signal,
    })
  } catch (error: any) {
    if (error?.name === 'AbortError') {
      throw new Error('Request timed out, please try again later')
    }
    throw error
  } finally {
    if (timeoutId !== null) {
      window.clearTimeout(timeoutId)
    }
  }

  if (res.status === 401) {
    logout()
    throw new Error('Session expired')
  }

  const body: ApiResponse<T> = await res.json().catch(() => ({
    code: res.status,
    data: null as T,
    message: `HTTP ${res.status}`,
  }))
  if (body.code !== 0 || body.success === false) {
    throw new Error(body.message || `HTTP ${res.status}`)
  }
  return body.data
}

export const apiClient = {
  request: fetchAPI,
}
