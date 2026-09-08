/**
 * tickerkeep-core stub. The public marketing site is not part of the open-source
 * core; it lives in the private tickerkeep-cloud repository. This module keeps the
 * import contract used by src/main.tsx and src/App.tsx while routing every path
 * to the application.
 */
export const MARKETING_PATHS = [] as const

export type MarketingPath = (typeof MARKETING_PATHS)[number]

/** Always false in core: there are no public marketing paths. */
export function isMarketingPath(_pathname: string): boolean {
  return false
}
