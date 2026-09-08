/**
 * Pure portfolio valuation and quote-merge logic, shared between the initial
 * `/portfolio/summary` load and the client-side quote-refresh path in
 * Stocks.tsx (`mergePortfolioQuotes` used to live inline there and repeat
 * the backend's arithmetic, which let a backend-only fix regress on refresh
 * — see docs/product/REVIEW-03.md; round 5 closes the scope/quote/FX parity
 * gaps in docs/product/REVIEW-04.md section B).
 *
 * IMPORTANT — this file must stay importable by
 * `node --experimental-strip-types` for the test suite. That means:
 *   - no path aliases (`@/...`, `@tickerkeep/...`) and no imports of any kind
 *     from a package Node can't resolve — this file has ZERO imports;
 *     every type it needs is declared locally below.
 *   - no `enum`, no parameter properties, no namespaces — nothing that
 *     *emits* JS from a type. Type-only syntax is fine; Node strips it.
 *   - `mergePortfolioQuotes` is a named export (what the test imports).
 *
 * THE SHARED VALUATION CONTRACT (mirrored in src/web/api/accounts.py):
 *   - THE SERVER IS THE SCOPE AUTHORITY. Whether a market is enabled is never
 *     decided here from a hardcoded list. It is read from the payload:
 *     `total.market_scope` (the server's enabled-market list) first, else
 *     each position's server-provided `valuation_status`. If neither is
 *     present the position fails CLOSED (unsupported) — an unknown market is
 *     never silently valued.
 *   - A quote entry that is PRESENT with `current_price: null` means the
 *     provider says "unavailable": the price is dropped
 *     (`price_status: 'unavailable'`), never retained. An ABSENT entry means
 *     "no update this round": a previously known price may be kept, but only
 *     as `price_status: 'last_known'` — never presented as fresh.
 *   - `priced` is true only when the position's USD value is known: a price
 *     is present AND the FX rate to USD is known. `valuation_status` is
 *     'unsupported' when the market is out of scope, else 'unavailable' when
 *     not priced (no price, or FX unknown), else 'priced'.
 *   - NO INVENTED FX, EVER. The CAD→USD rate comes only from the payload's
 *     `exchange_rates.CAD_USD`, and only when `fx_status.CAD_USD` is 'known'
 *     (or, for an older payload with no `fx_status` block at all, when an
 *     explicit finite rate is supplied). Anything else leaves the rate
 *     UNKNOWN: every USD-denominated field is null, the position never enters
 *     a USD aggregate, and its NATIVE cost/market value still survive. A rate
 *     the payload declares 'last_known' is used but carries that provenance
 *     (`fx_status: 'last_known'`) and never counts as fresh.
 *   - `valuation_complete` is true only when EVERY position is priced from a
 *     fresh quote with a known (not last-known) FX rate. A last-known price
 *     still contributes to the priced-subset numbers, but the aggregate is
 *     then labelled `pnl_basis: 'last_known'` (or 'priced_subset' if anything
 *     is unpriced), never 'complete'.
 *   - A missing price NEVER contributes cost to a P&L number. `total_pnl` /
 *     `total_pnl_pct` are computed over the PRICED subset only and are `null`
 *     (never 0) when nothing is priced. Likewise `total_daily_pnl` is null
 *     when no held position has a daily P&L.
 *   - Numbers are validated: a non-finite (NaN/Infinity) or negative price,
 *     and a non-finite or non-positive rate, is treated as unknown. A genuine
 *     `0` survives everywhere: every null below comes from an explicit
 *     `!= null` / `=== null` check, never a truthiness check.
 *   - DAILY P&L IS COMPUTED FROM `prev_close` ONLY (round 7 / contract 2 —
 *     mirrored in `src/web/api/accounts.py`). The server's serialized quote
 *     map and `/quotes/batch` response, and every position, carry `prev_close`
 *     authoritatively; this file never reconstructs a previous close from a
 *     ROUNDED `change_pct` (that reconstruction is undefined at -100% and
 *     loses precision generally). `daily_pnl` is `null` when `prev_close` is
 *     absent, non-finite, or `<= 0`. A genuine zero `current_price` is valid
 *     data: `current_price: 0` with `prev_close: 100` yields `daily_pnl:
 *     -100`, never null — never fabricate a zero and never discard a known
 *     daily P&L just to make two sides agree.
 */

export type ValuationStatus = 'unsupported' | 'unavailable' | 'priced'
/** Server emits only 'fresh' | 'unavailable'; 'last_known' is client-only. */
export type PriceStatus = 'fresh' | 'last_known' | 'unavailable'
/** Server emits only 'known' | 'unknown'; 'last_known' is accepted as declared provenance. */
export type FxStatus = 'known' | 'last_known' | 'unknown'
export type PnlBasis = 'complete' | 'priced_subset' | 'last_known'

/**
 * Which currency each market quotes in. This decides only WHICH FX rate
 * applies (1 for USD-denominated markets, the server's CAD_USD for CA) —
 * never WHETHER a market is enabled; that is the server's call via
 * `market_scope` / `valuation_status`. A market missing from this table has
 * an unknown currency, so even when the server enables it its FX is unknown
 * and it cannot be valued in USD (never a 1:1 guess).
 */
const MARKET_CURRENCY: { [market: string]: 'USD' | 'CAD' } = {
  US: 'USD',
  CRYPTO: 'USD',
  GOLD: 'USD',
  CA: 'CAD',
}

export interface QuoteEntry {
  current_price: number | null
  change_pct: number | null
  /** Canonical daily-P&L input (contract 2). Null/absent when unusable —
   * never reconstructed from `change_pct`. */
  prev_close?: number | null
}

export interface QuoteMap {
  [marketAndSymbol: string]: QuoteEntry
}

export interface RawPosition {
  id: number
  stock_id: number
  sort_order?: number
  symbol: string
  name: string
  market: string
  cost_price: number
  quantity: number
  invested_amount: number | null
  trading_style: string
  current_price: number | null
  current_price_cny: number | null
  change_pct: number | null
  /** Canonical daily-P&L input (contract 2): the prior session's close.
   * Null/absent/non-finite/<=0 means unusable — daily_pnl is then null. Never
   * reconstructed from `change_pct`. */
  prev_close?: number | null
  market_value: number | null
  market_value_cny: number | null
  pnl: number | null
  pnl_pct: number | null
  daily_pnl: number | null
  daily_pnl_pct: number | null
  exchange_rate: number | null
  // Valuation coverage fields — optional on input (an older payload, or a
  // position that hasn't been through this merge yet, may not carry them),
  // always populated on the merged output.
  priced?: boolean
  /** Server-provided on input; only its 'unsupported' bit is consulted, and
   * only when the payload carries no `total.market_scope`. */
  valuation_status?: ValuationStatus
  /** Freshness of `current_price`. */
  price_status?: PriceStatus
  /** Provenance of the FX rate applied to this position. */
  fx_status?: FxStatus
  /** Cost in the position's native currency (cost_price * quantity). Null
   * only when cost_price/quantity are not finite numbers. */
  cost?: number | null
  /** Cost converted to USD, or null when the FX rate for this market is
   * unknown. Never a 1:1 guess. */
  cost_usd?: number | null
  /** Identical to cost_usd — kept for field-name parity with
   * market_value_cny / current_price_cny (also historical names for USD
   * values) and with the backend's identical pair. Always equal to cost_usd. */
  cost_cny?: number | null
}

/**
 * Valuation coverage fields shared by an account summary and the grand
 * total. Optional on input (an older backend payload may not send them —
 * `mergePortfolioQuotes` always rederives them from the positions rather
 * than trusting these, so that case degrades gracefully instead of
 * fabricating `valuation_complete: true`); always populated on output.
 */
export interface ValuationCoverageFields {
  total_positions?: number
  /** Positions with a known USD value (price present AND FX known). */
  priced_positions?: number
  unpriced_positions?: number
  unsupported_positions?: number
  /** Supported but not priced: no price, or FX unknown. */
  unavailable_positions?: number
  /** Priced from a fresh quote with a known FX rate. */
  fresh_positions?: number
  /** Priced, but from a last-known price and/or a last-known FX rate. */
  last_known_positions?: number
  /** Supported positions holding a price whose FX rate is unknown. */
  fx_unknown_positions?: number
  /** Positions contributing to total_daily_pnl. */
  daily_pnl_positions?: number
  /** True only when every position contributes a daily P&L. */
  daily_pnl_complete?: boolean
  /** USD cost of unpriced positions, or null if any of them has unknown FX. */
  unpriced_cost?: number | null
  /** True only when every position is fresh-priced with known FX. */
  valuation_complete?: boolean
  pnl_basis?: PnlBasis
  /** USD cost of PRICED positions only — the denominator behind total_pnl_pct. */
  priced_cost_basis?: number
  /** False when any position's cost_usd is null. */
  cost_basis_complete?: boolean
  /** Mirrors valuation_complete. */
  total_assets_complete?: boolean
}

export interface RawAccountSummary extends ValuationCoverageFields {
  id: number
  name: string
  available_funds: number
  total_market_value: number
  total_cost: number
  total_pnl: number | null
  total_pnl_pct: number | null
  total_daily_pnl: number | null
  total_assets: number | null
  positions: RawPosition[]
}

export interface RawPortfolioTotal extends ValuationCoverageFields {
  total_market_value: number
  total_cost: number
  total_pnl: number | null
  total_pnl_pct: number | null
  total_daily_pnl: number | null
  available_funds: number
  total_assets: number | null
  /** The server's authoritative enabled-market list. */
  market_scope?: string[]
}

export interface RawPortfolioSummary {
  accounts: RawAccountSummary[]
  total: RawPortfolioTotal
  /** Rates to the USD base: USD per one unit of the market currency. */
  exchange_rates?: { CAD_USD?: number | null }
  /** Provenance of each rate. Server emits 'known' | 'unknown'; the merged
   * output carries the resolved status (which may be 'last_known'). */
  fx_status?: { CAD_USD?: FxStatus }
  /** Epoch seconds of the fetch that produced the rate; null/absent when
   * the rate itself is unknown. Same per-currency dict shape as fx_status. */
  fx_as_of?: { CAD_USD?: number | null }
  /** e.g. "Bank of Canada", "Yahoo CAD=X"; null/absent when unknown. */
  fx_source?: { CAD_USD?: string | null }
  quotes?: Record<string, QuoteEntry>
}

export const round2 = (value: number): number => Math.round(value * 100) / 100

/** A finite number, else null. Rejects NaN/Infinity/non-numbers; keeps 0. */
const toFiniteOrNull = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

/** A valid price: finite and non-negative. A genuine 0 is a valid price. */
const toPriceOrNull = (value: unknown): number | null => {
  const n = toFiniteOrNull(value)
  return n != null && n >= 0 ? n : null
}

/** A valid FX rate: finite and strictly positive. */
const toRateOrNull = (value: unknown): number | null => {
  const n = toFiniteOrNull(value)
  return n != null && n > 0 ? n : null
}

/** A valid `prev_close` for daily P&L (contract 2): finite and strictly
 * positive, identical validation to an FX rate — never reconstructed, only
 * ever accepted or rejected. */
const toPrevCloseOrNull = toRateOrNull

const hasOwn = (obj: object, key: string): boolean => Object.prototype.hasOwnProperty.call(obj, key)

const normalizeMarket = (market: unknown): string => String(market ?? '').toUpperCase()

/**
 * The server's enabled-market scope, or null when the payload doesn't carry
 * one (older backend) — callers then fall back to per-position status.
 */
function resolveMarketScope(portfolio: RawPortfolioSummary): Set<string> | null {
  const scope = portfolio.total?.market_scope
  if (!Array.isArray(scope)) return null
  return new Set(scope.filter((m): m is string => typeof m === 'string').map(normalizeMarket))
}

/**
 * Is this position's market enabled? Server scope first; else the server's
 * own per-position `valuation_status`; else fail closed.
 */
function isSupported(pos: RawPosition, scope: Set<string> | null): boolean {
  if (scope) return scope.has(normalizeMarket(pos.market))
  if (pos.valuation_status != null) return pos.valuation_status !== 'unsupported'
  return false
}

interface ResolvedRate {
  rate: number | null
  status: FxStatus
  /** Epoch seconds of the fetch that produced `rate`; null when unknown. */
  as_of: number | null
  /** e.g. "Bank of Canada", "Yahoo CAD=X"; null when unknown. */
  source: string | null
}

/**
 * Resolve the CAD→USD rate from the payload alone. A rate is only ever
 * *supplied*, never invented; its provenance is carried alongside it.
 */
function resolveCadUsd(portfolio: RawPortfolioSummary): ResolvedRate {
  const rate = toRateOrNull(portfolio.exchange_rates?.CAD_USD)
  if (rate == null) return { rate: null, status: 'unknown', as_of: null, source: null }

  // The server nests the block as `exchange_rates.fx_status` (see
  // `exchange_rates_payload` in src/web/api/accounts.py); a top-level
  // `fx_status` is also accepted so either shape works. `fx_as_of`/
  // `fx_source` follow the same dual-shape acceptance for consistency.
  type NestedFx = {
    fx_status?: { CAD_USD?: FxStatus }
    fx_as_of?: { CAD_USD?: number | null }
    fx_source?: { CAD_USD?: string | null }
  }
  const nested = portfolio.exchange_rates as NestedFx | undefined
  const declared = nested?.fx_status?.CAD_USD ?? portfolio.fx_status?.CAD_USD
  const asOf = toFiniteOrNull(nested?.fx_as_of?.CAD_USD ?? portfolio.fx_as_of?.CAD_USD)
  const source = (nested?.fx_source?.CAD_USD ?? portfolio.fx_source?.CAD_USD) || null

  if (declared === 'known') return { rate, status: 'known', as_of: asOf, source }
  if (declared === 'last_known') return { rate, status: 'last_known', as_of: asOf, source }
  // Fail closed. A payload that supplies a NUMBER but no provenance is exactly
  // how the server's constant fallback (0.73) used to be mistaken for a real
  // rate, so an absent or unrecognized status is NOT usable.
  return { rate: null, status: 'unknown', as_of: null, source: null }
}

interface ResolvedPrice {
  current_price: number | null
  change_pct: number | null
  /** Canonical daily-P&L input (contract 2) — validated finite/positive,
   * never derived from `change_pct`. */
  prev_close: number | null
  status: PriceStatus
}

/**
 * Distinguish a MISSING quote entry (no update this round — a prior price
 * may be kept as last-known) from an entry PRESENT with a null price (the
 * provider says unavailable — drop the price). `prev_close` is carried
 * alongside the price from whichever source supplied it (this round's fresh
 * quote, or the position's own previously-serialized value when nothing
 * fresh arrived) — it is never invented from `change_pct`.
 */
function resolvePrice(pos: RawPosition, quotes: QuoteMap): ResolvedPrice {
  const key = `${pos.market}:${pos.symbol}`
  if (hasOwn(quotes, key)) {
    const quote = quotes[key]
    const price = toPriceOrNull(quote?.current_price)
    if (price != null) {
      return {
        current_price: price,
        change_pct: toFiniteOrNull(quote.change_pct),
        prev_close: toPrevCloseOrNull(quote?.prev_close),
        status: 'fresh',
      }
    }
    return { current_price: null, change_pct: null, prev_close: null, status: 'unavailable' }
  }
  const prior = toPriceOrNull(pos.current_price)
  if (prior != null) {
    return {
      current_price: prior,
      change_pct: toFiniteOrNull(pos.change_pct),
      prev_close: toPrevCloseOrNull(pos.prev_close),
      status: 'last_known',
    }
  }
  return { current_price: null, change_pct: null, prev_close: null, status: 'unavailable' }
}

/**
 * Recompute every derived valuation field for one position from its raw
 * quantity/cost plus the freshest quote available, with scope and FX taken
 * from the payload (never from local policy).
 */
function computePosition(
  pos: RawPosition,
  quotes: QuoteMap,
  cadUsd: ResolvedRate,
  scope: Set<string> | null
): RawPosition {
  const supported = isSupported(pos, scope)
  const { current_price, change_pct, prev_close, status: price_status } = resolvePrice(pos, quotes)

  // FX rate to USD. 1 for USD-denominated markets; the server's supplied
  // CAD rate for CA; unknown for an unsupported market or one whose currency
  // we don't track. Never invented.
  const currency = supported ? MARKET_CURRENCY[normalizeMarket(pos.market)] : undefined
  let rate: number | null = null
  let fx_status: FxStatus = 'unknown'
  if (currency === 'USD') {
    rate = 1
    fx_status = 'known'
  } else if (currency === 'CAD') {
    rate = cadUsd.rate
    fx_status = cadUsd.status
  }

  const quantity = toFiniteOrNull(pos.quantity)
  const costPrice = toFiniteOrNull(pos.cost_price)

  // Native-currency figures never need FX, so they survive even when the
  // market's rate (or the market itself) is unknown.
  const cost = quantity != null && costPrice != null ? round2(costPrice * quantity) : null
  const market_value = current_price != null && quantity != null ? round2(current_price * quantity) : null

  const cost_usd = rate != null && cost != null ? round2(cost * rate) : null

  // "Priced" means the USD value is known: a price AND a rate.
  const priced = current_price != null && rate != null
  const valuation_status: ValuationStatus = !supported ? 'unsupported' : priced ? 'priced' : 'unavailable'

  let market_value_cny: number | null = null
  let current_price_cny: number | null = null
  let pnl: number | null = null
  let pnl_pct: number | null = null
  let daily_pnl: number | null = null
  let daily_pnl_pct: number | null = null

  if (priced && current_price != null && rate != null) {
    current_price_cny = round2(current_price * rate)
    if (market_value != null) {
      market_value_cny = round2(market_value * rate)
      if (cost_usd != null) {
        pnl = round2(market_value_cny - cost_usd)
        // cost_usd > 0, not pnl !== 0 — a genuine 0.00 P&L must still divide
        // out to 0, not be treated as "nothing to report".
        pnl_pct = cost_usd > 0 ? round2((pnl / cost_usd) * 100) : null
      }
    }

    // Daily P&L is computed from `prev_close` ONLY (contract 2) — never
    // reconstructed from `change_pct`, which is a rounded percentage and
    // undefined at -100%. `prev_close` is already validated finite/positive
    // by `resolvePrice`/`toPrevCloseOrNull`, so this division is always safe.
    // A genuine zero `current_price` still produces a real (negative)
    // daily_pnl here — it is never treated as "no data".
    if (prev_close != null && quantity != null) {
      daily_pnl = round2((current_price - prev_close) * quantity * rate)
      daily_pnl_pct = round2(((current_price - prev_close) / prev_close) * 100)
    }
  }

  return {
    ...pos,
    current_price,
    current_price_cny,
    change_pct,
    prev_close,
    market_value,
    market_value_cny,
    pnl,
    pnl_pct,
    daily_pnl,
    daily_pnl_pct,
    // Only a non-USD market carries an applied rate; USD markets report null
    // here exactly as the backend does.
    exchange_rate: currency === 'CAD' ? rate : null,
    priced,
    valuation_status,
    price_status,
    fx_status,
    cost,
    cost_usd,
    // cost_usd is the accurate name for this USD-converted cost; cost_cny is
    // set to the identical value (never independently derived) so it can
    // never drift from cost_usd after an FX change — kept only for field
    // parity with market_value_cny / current_price_cny (also historical names
    // for USD values) and with the backend's identical pair in accounts.py.
    cost_cny: cost_usd,
  }
}

/** A position whose USD value comes from a fresh quote and a known rate. */
const isFresh = (pos: RawPosition): boolean =>
  pos.priced === true && pos.price_status === 'fresh' && pos.fx_status === 'known'

interface Summary extends Required<ValuationCoverageFields> {
  total_market_value: number
  total_cost: number
  total_pnl: number | null
  total_pnl_pct: number | null
  total_daily_pnl: number | null
  total_assets: number | null
}

/**
 * Aggregate a set of already-computed positions into the shared totals
 * shape. Used for both a single account and the grand total, so the two are
 * never computed by two different formulas that could drift apart.
 */
function summarize(positions: RawPosition[], availableFunds: number): Summary {
  let marketValue = 0
  let pricedCostBasis = 0
  let totalCostKnown = 0
  let dailyPnl = 0
  let dailyPnlCount = 0
  let pricedCount = 0
  let freshCount = 0
  let lastKnownCount = 0
  let unsupportedCount = 0
  let unavailableCount = 0
  let fxUnknownCount = 0
  let unpricedCostKnownSum = 0
  let unpricedHasUnknownFx = false
  let costBasisComplete = true

  for (const pos of positions) {
    const priced = pos.priced === true
    if (priced) {
      pricedCount += 1
      if (isFresh(pos)) freshCount += 1
      else lastKnownCount += 1
    }
    if (pos.valuation_status === 'unsupported') unsupportedCount += 1
    if (pos.valuation_status === 'unavailable') {
      unavailableCount += 1
      if (pos.current_price != null && pos.fx_status !== 'known' && pos.fx_status !== 'last_known') fxUnknownCount += 1
    }

    if (pos.cost_usd == null) {
      costBasisComplete = false
    } else {
      totalCostKnown += pos.cost_usd
    }

    // Only a position with a known USD market value AND a known USD cost
    // ever contributes to a P&L number — this is what stops a missing price
    // from being booked as a total loss (its cost never lands here alone).
    if (priced && pos.market_value_cny != null && pos.cost_usd != null) {
      marketValue += pos.market_value_cny
      pricedCostBasis += pos.cost_usd
    }

    if (!priced) {
      if (pos.cost_usd == null) unpricedHasUnknownFx = true
      else unpricedCostKnownSum += pos.cost_usd
    }

    if (pos.daily_pnl != null) {
      dailyPnl += pos.daily_pnl
      dailyPnlCount += 1
    }
  }

  const totalPositions = positions.length
  const unpricedPositions = totalPositions - pricedCount
  // Complete only when every position is fresh-priced. A last-known price
  // still contributes to the priced subset but never makes the valuation
  // read as complete.
  const valuationComplete = freshCount === totalPositions
  const pnlBasis: PnlBasis = valuationComplete ? 'complete' : unpricedPositions > 0 ? 'priced_subset' : 'last_known'

  const totalPnl = pricedCount === 0 ? null : round2(marketValue - pricedCostBasis)
  const totalPnlPct =
    pricedCount === 0 ? null : pricedCostBasis > 0 ? round2(((totalPnl as number) / pricedCostBasis) * 100) : null
  // A zero-position group (real cash, no holdings) is fully known — it must
  // not render as "--" just because pricedCount is also 0 there. Only an
  // *unpriced holding* (totalPositions > 0 but nothing priced) makes assets
  // unknown; an empty group's assets are exactly its available funds. This
  // keeps total_assets_complete (== valuationComplete, which is true when
  // totalPositions === 0) from ever coexisting with a null total_assets.
  const totalAssets =
    pricedCount === 0 && totalPositions > 0 ? null : round2(marketValue + availableFunds)
  // Daily P&L is unknown (never an invented 0) when holdings exist but none
  // of them has one; an empty group has a genuine 0.
  const totalDailyPnl = dailyPnlCount === 0 && totalPositions > 0 ? null : round2(dailyPnl)

  return {
    total_market_value: round2(marketValue),
    total_cost: round2(totalCostKnown),
    total_pnl: totalPnl,
    total_pnl_pct: totalPnlPct,
    total_daily_pnl: totalDailyPnl,
    total_assets: totalAssets,
    total_positions: totalPositions,
    priced_positions: pricedCount,
    unpriced_positions: unpricedPositions,
    unsupported_positions: unsupportedCount,
    unavailable_positions: unavailableCount,
    fresh_positions: freshCount,
    last_known_positions: lastKnownCount,
    fx_unknown_positions: fxUnknownCount,
    daily_pnl_positions: dailyPnlCount,
    daily_pnl_complete: dailyPnlCount === totalPositions,
    unpriced_cost: unpricedHasUnknownFx ? null : round2(unpricedCostKnownSum),
    valuation_complete: valuationComplete,
    pnl_basis: pnlBasis,
    priced_cost_basis: round2(pricedCostBasis),
    cost_basis_complete: costBasisComplete,
    total_assets_complete: valuationComplete,
  }
}

/**
 * Merge fresh quotes into a portfolio snapshot, recomputing every derived
 * valuation field — and every coverage/aggregate field — from scratch off
 * the raw positions. Scope and FX are consumed from the payload as DATA
 * (`total.market_scope`, `exchange_rates`, `fx_status`, per-position
 * `valuation_status`). This function never trusts a `valuation_complete` or
 * coverage field the caller might have sent; it always rederives them, so an
 * older backend payload that doesn't carry the new fields yet degrades to
 * "derive it ourselves" automatically instead of crashing or fabricating
 * `valuation_complete: true`.
 */
export function mergePortfolioQuotes(
  portfolio: RawPortfolioSummary | null,
  quotes: QuoteMap
): RawPortfolioSummary | null {
  if (!portfolio) return null

  const scope = resolveMarketScope(portfolio)
  const cadUsd = resolveCadUsd(portfolio)
  const quoteMap: QuoteMap = quotes ?? {}

  let grandAvailable = 0
  const allComputedPositions: RawPosition[] = []

  const accounts = portfolio.accounts.map(account => {
    const positions = account.positions.map(pos => computePosition(pos, quoteMap, cadUsd, scope))
    allComputedPositions.push(...positions)
    grandAvailable += account.available_funds

    const summary = summarize(positions, account.available_funds)
    return {
      ...account,
      ...summary,
      positions,
    }
  })

  const grandSummary = summarize(allComputedPositions, grandAvailable)

  return {
    ...portfolio,
    accounts,
    total: {
      // Carry the server's own total fields (notably market_scope) through;
      // every derived/coverage field is overridden by the fresh summary.
      ...portfolio.total,
      ...grandSummary,
      available_funds: round2(grandAvailable),
    },
    exchange_rates: { ...(portfolio.exchange_rates ?? {}), CAD_USD: cadUsd.rate },
    fx_status: { ...(portfolio.fx_status ?? {}), CAD_USD: cadUsd.status },
    fx_as_of: { ...(portfolio.fx_as_of ?? {}), CAD_USD: cadUsd.as_of },
    fx_source: { ...(portfolio.fx_source ?? {}), CAD_USD: cadUsd.source },
  }
}
