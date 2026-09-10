/**
 * tickerkeep-core auth seam: the sign-in UI for the paths in ./routes.
 *
 * Core renders its single username/password page (src/pages/Login.tsx) at
 * `/login`. The page is left where it is and only referenced from here, so
 * when an overlay replaces this file it simply drops out of the bundle.
 *
 * Overlay contract: cloud builds core at a pinned commit and copies its own
 * `routes.ts`, `index.tsx` and `session.tsx` over this directory. App.tsx
 * mounts the default export via `lazy(() => import('@/auth'))` inside a
 * <Suspense> and returns it *instead of* the signed-in shell (no rail, no top
 * bar, no <RequireAuth/>) whenever isAuthPath(location.pathname) is true, so
 * the component owns the whole viewport and must render every path listed in
 * AUTH_PATHS. The cloud overlay speaks a different backend surface (`/auth/*`,
 * session cookie, email/password, verification) and brings its own client and
 * pages; none of core's Login page or `@tickerkeep/api` auth helpers is part
 * of the contract.
 */
import { lazy, Suspense } from 'react'
import { Routes, Route } from 'react-router-dom'

const LoginPage = lazy(() => import('@/pages/Login'))

const routeFallback = (
  <div className="flex min-h-screen items-center justify-center bg-background">
    <span className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-foreground" />
  </div>
)

/** Login stands outside the room shell: one centred form, no navigation. */
export default function AuthRoutes(): JSX.Element {
  return (
    <div className="min-h-screen bg-background">
      <Suspense fallback={routeFallback}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
        </Routes>
      </Suspense>
    </div>
  )
}
