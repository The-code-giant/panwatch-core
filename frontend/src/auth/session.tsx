/**
 * tickerkeep-core auth seam: is there a signed-in session, and how to end it.
 *
 * Core's session is a bearer token in localStorage (`@tickerkeep/api`
 * client.ts): `isAuthenticated()` is synchronous and `logout()` clears the
 * token and reloads at `/login`. This module wraps those two calls with the
 * exact semantics App.tsx has always had: status starts as 'checking' and is
 * resolved in an effect on mount, never during render.
 *
 * Overlay contract: cloud builds core at a pinned commit and copies its own
 * `routes.ts`, `index.tsx` and `session.tsx` over this directory. App.tsx
 * imports exactly `useAuthSession` and `signOut` (and the `AuthStatus` type)
 * from `@/auth/session`:
 *
 * - useAuthSession() returns `{ status }`. 'checking' must be the initial
 *   value; the shell shows a spinner for it, redirects to LOGIN_PATH on
 *   'unauthenticated', and only fires its own `/api/*` bootstrap calls once
 *   the status is 'authenticated'. The hook may be mounted more than once at
 *   the same time (App() and <RequireAuth/> both call it), so an overlay whose
 *   check is a network round-trip should share one in-flight result rather
 *   than issue one request per mount.
 * - signOut() ends the session; it may be async. Core's just calls logout().
 *
 * The cloud overlay speaks a different backend surface (`/auth/*`, session
 * cookie, no `/api` prefix) and brings its own client; nothing in core's
 * `@tickerkeep/api` auth helpers is part of the contract.
 */
import { useEffect, useState } from 'react'
import { isAuthenticated, logout } from '@tickerkeep/api'

export type AuthStatus = 'checking' | 'authenticated' | 'unauthenticated'

/** Core: resolves synchronously on mount from the localStorage bearer token. */
export function useAuthSession(): { status: AuthStatus } {
  const [status, setStatus] = useState<AuthStatus>('checking')

  useEffect(() => {
    setStatus(isAuthenticated() ? 'authenticated' : 'unauthenticated')
  }, [])

  return { status }
}

/** Core: drop the bearer token and reload at /login. */
export function signOut(): void | Promise<void> {
  logout()
}
