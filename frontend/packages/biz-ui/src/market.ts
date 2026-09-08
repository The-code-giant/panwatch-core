import { MARKET_LABEL, isMarket } from '@tickerkeep/api/markets'

export interface MarketBadgeInfo {
  style: string
  label: string
}

/** Identity-only badge: filled market hue with the contrast-checked ink on it. */
const BADGE_STYLE: Record<string, string> = {
  US: 'bg-mkt-us-solid text-mkt-ink',
  CA: 'bg-mkt-ca-solid text-mkt-ink',
  CRYPTO: 'bg-mkt-crypto-solid text-mkt-ink',
  GOLD: 'bg-mkt-gold-solid text-mkt-ink',
}

export function getMarketBadge(market: string): MarketBadgeInfo {
  const m = String(market || '').trim().toUpperCase()
  if (isMarket(m)) return { style: BADGE_STYLE[m], label: MARKET_LABEL[m] }
  return { style: 'bg-muted text-muted-foreground', label: m || '--' }
}
