/**
 * The one source of truth for which markets this app knows about.
 *
 * Every page, panel and API type imports from here (the app re-exports it as
 * `@/lib/markets`). No page keeps its own
 * market literal or label map. Mirrors `src/models/market.py` on the backend
 * (`ENABLED_MARKETS` / `EQUITY_MARKETS` / `default_market()`).
 */

/** Markets that trade in sessions and can hold positions. */
export const EQUITY_MARKETS = ['US', 'CA'] as const

/** Every market a watchlist row may carry. Crypto and gold are watch-only extras. */
export const ALL_MARKETS = ['US', 'CA', 'CRYPTO', 'GOLD'] as const

export type EquityMarket = (typeof EQUITY_MARKETS)[number]
export type Market = (typeof ALL_MARKETS)[number]

/** An equity market plus the "every market" view that lists and filters offer. */
export type MarketView = 'ALL' | EquityMarket

export const DEFAULT_MARKET: EquityMarket = 'US'

export const MARKET_LABEL: Record<Market, string> = {
  US: 'US',
  CA: 'Canada',
  CRYPTO: 'Crypto',
  GOLD: 'Gold',
}

/** Two-letter mark for a square identity tag. */
export const MARKET_SHORT: Record<Market, string> = {
  US: 'US',
  CA: 'CA',
  CRYPTO: 'CR',
  GOLD: 'AU',
}

/** Trading currency of each market. Everything is reported in USD; CA converts. */
export const MARKET_CURRENCY: Record<Market, string> = {
  US: 'USD',
  CA: 'CAD',
  CRYPTO: 'USD',
  GOLD: 'USD',
}

/** One-line help for a symbol input, per market. */
export const MARKET_SYMBOL_HINT: Record<Market, string> = {
  US: 'Ticker, e.g. AAPL or BRK.B',
  CA: 'Ticker with the .TO (TSX) or .V (TSX Venture) suffix, e.g. SHOP.TO or RY.TO',
  CRYPTO: 'Pair, e.g. BTC-USD or ETH-USD',
  GOLD: 'XAUUSD',
}

export function isMarket(value: unknown): value is Market {
  return typeof value === 'string' && (ALL_MARKETS as readonly string[]).includes(value)
}

export function isEquityMarket(value: unknown): value is EquityMarket {
  return typeof value === 'string' && (EQUITY_MARKETS as readonly string[]).includes(value)
}

/**
 * Coerce any market-ish string (an API payload, a query param, a stored
 * preference from before the market list changed) to an enabled market.
 * Anything unknown, including the retired CN and HK codes, becomes the default.
 */
export function normalizeMarket(value: unknown): Market {
  const m = String(value ?? '').trim().toUpperCase()
  return isMarket(m) ? m : DEFAULT_MARKET
}

export function normalizeEquityMarket(value: unknown): EquityMarket {
  const m = String(value ?? '').trim().toUpperCase()
  return isEquityMarket(m) ? m : DEFAULT_MARKET
}

export function normalizeMarketView(value: unknown): MarketView {
  const m = String(value ?? '').trim().toUpperCase()
  return m === 'ALL' ? 'ALL' : normalizeEquityMarket(m)
}

/** Human label for any market code; unknown codes echo back unchanged. */
export function marketLabel(market: unknown): string {
  const m = String(market ?? '').trim().toUpperCase()
  return isMarket(m) ? MARKET_LABEL[m] : m
}

/** Infer the market from a symbol's shape: `.TO` / `.V` is Canada, else US. */
export function inferMarketFromSymbol(symbol: string): EquityMarket {
  return /\.(TO|V)$/i.test(String(symbol || '').trim()) ? 'CA' : 'US'
}
