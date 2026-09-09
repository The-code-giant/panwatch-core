import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { authApi } from '@tickerkeep/api'

/**
 * Capability client for the signed-in shell.
 *
 * Fetches `/auth/me` once when the authenticated app mounts and exposes the
 * flat capability list it returns. Names follow `<noun>:<verb>` (see
 * OPERATOR_CAPABILITIES in src/web/api/auth.py); core is single-operator, so
 * core's backend always answers with the complete list.
 *
 * Fail-closed and silent: any error, non-JSON body, or missing `capabilities`
 * key resolves to an empty list. It never throws, never redirects, and never
 * takes down the shell, because a deployment that does not serve core's
 * `/api/auth/me` must degrade to "no capabilities", not to a broken dashboard.
 */
export interface Capabilities {
  /** Capability names granted to the current session. Empty until loaded. */
  list: readonly string[]
  /** True once the fetch has settled, successfully or not. */
  loaded: boolean
  /** Exact-name check. Unknown names are false. */
  has: (name: string) => boolean
}

const NONE: readonly string[] = []

const CapabilityContext = createContext<Capabilities>({
  list: NONE,
  loaded: false,
  has: () => false,
})

function normalize(value: unknown): readonly string[] {
  if (!Array.isArray(value)) return NONE
  return value.filter((c): c is string => typeof c === 'string')
}

export function CapabilityProvider({ children }: { children: React.ReactNode }) {
  const [list, setList] = useState<readonly string[]>(NONE)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    authApi.me()
      .then(res => { if (!cancelled) setList(normalize(res?.capabilities)) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoaded(true) })
    return () => { cancelled = true }
  }, [])

  const value = useMemo<Capabilities>(() => {
    const set = new Set(list)
    return { list, loaded, has: (name: string) => set.has(name) }
  }, [list, loaded])

  return <CapabilityContext.Provider value={value}>{children}</CapabilityContext.Provider>
}

/**
 * The current session's capabilities, e.g. `useCapabilities().has('billing:manage')`.
 * Outside a provider: empty, never loaded.
 */
export function useCapabilities(): Capabilities {
  return useContext(CapabilityContext)
}
