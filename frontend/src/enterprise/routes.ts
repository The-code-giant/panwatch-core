/**
 * tickerkeep-core stub. The enterprise pages (organization, members, SSO,
 * audit, seats, billing) are not part of the open-source core; they live in
 * the private tickerkeep-cloud repository. This module keeps the import
 * contract used by src/App.tsx while routing every path to the core app.
 *
 * Overlay contract: cloud builds core at a pinned commit and copies its own
 * `routes.ts`, `index.tsx` and `pages/**` over this directory. Nothing in core
 * imports from `./pages`, and core has no `enterprise/pages/` directory, so a
 * plain file-level copy is a complete replacement. The overlay supplies its own
 * data access as well: it talks to a different API surface (session cookie,
 * no `/api` prefix, no response envelope) than core's `fetchAPI`, so the core
 * capability client in src/lib/capabilities.tsx is core's, not the overlay's.
 */
export const ENTERPRISE_PATHS = [] as const

export type EnterprisePath = (typeof ENTERPRISE_PATHS)[number]

/** Always false in core: there are no enterprise paths. */
export function isEnterprisePath(_pathname: string): boolean {
  return false
}
