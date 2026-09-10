import { Navigate } from 'react-router-dom'
import { useCapabilities } from '@/lib/capabilities'

const routeFallback = (
  <div className="flex min-h-screen items-center justify-center bg-background">
    <span className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-foreground" />
  </div>
)

/**
 * Gates one `<Route element>` on a capability string from rooms.ts
 * (`capabilityForPath`).
 *
 * Direct navigation -- a bookmark, a `LEGACY_REDIRECTS` target, or typing the
 * URL -- must be coherent: a route must never become reachable-but-broken (a
 * capability-gated page renders anyway, and its own fetches fail against a
 * deployment that does not implement that surface) nor unreachable-but-linked
 * (the nav hides it, but the path 404s or dead-ends instead of resolving
 * somewhere sane).
 *
 * So this WAITS rather than guessing: while `capabilities.loaded` is false
 * it renders the same kind of spinner `RequireAuth` shows for 'checking' --
 * deciding nothing yet, so a visitor who genuinely holds the capability is
 * never bounced just because `/auth/me` had not answered. Once loaded, it
 * renders `children` when the capability is granted and redirects to
 * `/today` (always visible, ungated) otherwise. No `capability` (undefined,
 * e.g. Today or the analysis detail page) always renders immediately.
 */
export default function RequireCapability(
  { capability, children }: { capability?: string; children: JSX.Element },
): JSX.Element {
  const capabilities = useCapabilities()

  if (!capability) return children
  if (!capabilities.loaded) return routeFallback
  if (!capabilities.has(capability)) return <Navigate to="/today" replace />
  return children
}
