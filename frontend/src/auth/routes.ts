/**
 * tickerkeep-core auth seam: which paths belong to the sign-in UI.
 *
 * Core is the self-hosted, single-user edition: one username/password login
 * page at `/login`, a bearer token kept in localStorage, and `/api/auth/*` on
 * the backend. That is the implementation in this directory.
 *
 * Overlay contract: cloud builds core at a pinned commit and copies its own
 * `routes.ts`, `index.tsx` and `session.tsx` over this directory, the same
 * way it replaces `src/marketing/` and `src/enterprise/`. src/App.tsx only
 * ever imports the three names below from `@/auth/routes`, so an overlay
 * that keeps these exports (with these types) is a complete replacement.
 * The cloud overlay speaks a different backend surface (`/auth/*`, session
 * cookie, no `/api` prefix, no response envelope) and brings its own client;
 * nothing in core's `@tickerkeep/api` auth helpers is part of this contract.
 *
 * - AUTH_PATHS: every pathname App.tsx must hand to <AuthRoutes/> instead of
 *   the signed-in shell (login, plus whatever registration, verification or
 *   password-reset pages an overlay ships).
 * - LOGIN_PATH: where an unauthenticated visitor is redirected.
 * - isAuthPath(): the predicate App.tsx calls; must be true for exactly the
 *   pathnames an overlay's <AuthRoutes/> knows how to render.
 */
export const AUTH_PATHS: readonly string[] = ['/login']

/** Where <RequireAuth/> sends a visitor with no session. */
export const LOGIN_PATH: string = '/login'

/** True when `pathname` is rendered by <AuthRoutes/> (./index) rather than the app shell. */
export function isAuthPath(pathname: string): boolean {
  return AUTH_PATHS.includes(pathname)
}
