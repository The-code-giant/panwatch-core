import { fetchAPI } from './client'

/**
 * Provenance of a supplied FX rate. Mirrors `FxStatus` in
 * `frontend/src/lib/portfolio-valuation.ts` and the contract in
 * `src/web/api/accounts.py` (`FxSnapshot`): 'known' only when a genuine
 * fetch is fresh; 'last_known' when the cache holds a real but expired
 * rate; 'unknown' when no genuine rate has ever been obtained (never a
 * 0.73-style invented fallback).
 */
export type PortfolioFxStatus = 'known' | 'last_known' | 'unknown'

/**
 * Coverage of a portfolio-level analytics figure (diagnostics/benchmark/
 * attribution/AI review) over the underlying positions — every one of
 * these endpoints is computed from the same priced/unpriced position set,
 * so a partial result must say so rather than read as complete.
 *
 * Field names mirror the server's `_valuation_coverage()`
 * (`src/web/api/accounts.py`) EXACTLY, not the account/total-level
 * `ValuationCoverageFields` in portfolio-valuation.ts — the two blocks are
 * built by different code and use different key names
 * (`analysed_holdings`/`excluded_unpriced_holdings`/`basis` here, vs.
 * `priced_positions`/`unpriced_positions`/`pnl_basis` on the summary
 * endpoint). Do not rename these to match the other block; that would just
 * make every field read `undefined` again.
 */
export interface PortfolioValuationCoverage {
  /** Distinct held instruments that actually entered the analysis (priced). */
  analysed_holdings?: number
  /** Held instruments excluded for lacking a current price or FX rate — the
   * server does not split this further, so don't invent a price-vs-FX split
   * that isn't in the data. */
  excluded_unpriced_holdings?: number
  /** True only when nothing was excluded AND no analysed holding used a
   * stale (last-known) FX rate. */
  valuation_complete?: boolean
  basis?: 'complete' | 'priced_subset' | 'last_known'
  /** Present ONLY when at least one analysed holding was converted at a
   * last-known (real but expired) CAD→USD rate — never confuse this with
   * `excluded_unpriced_holdings`, which is a different failure (no price/FX
   * at all, not a stale one). */
  fx_status?: 'last_known'
  /** Ready-made English sentence from the server explaining exclusions
   * and/or the stale-FX caveat — prefer this over deriving your own wording
   * when it's present, since it already gets the price-vs-FX distinction
   * right. */
  note?: string
}

/**
 * The FX block shared by every portfolio analytics response: the rate
 * itself (null when unknown — never an invented constant), its per-
 * currency provenance, and — when a rate is available — when it was
 * fetched and where from. Same per-currency dict shape as
 * `RawPortfolioSummary` in portfolio-valuation.ts.
 */
export interface PortfolioFxBlock {
  exchange_rates?: { CAD_USD?: number | null }
  fx_status?: { CAD_USD?: PortfolioFxStatus }
  /** Epoch seconds of the fetch that produced the rate; null/absent when unknown. */
  fx_as_of?: { CAD_USD?: number | null }
  /** e.g. "Bank of Canada", "Yahoo CAD=X"; null/absent when unknown. */
  fx_source?: { CAD_USD?: string | null }
}

export interface PortfolioDiagnostics extends PortfolioFxBlock {
  position_count: number
  total_market_value: number
  hhi: number
  max_weight: number
  by_market: Record<string, number>
  by_strategy: Record<string, number>
  total_unrealized_pnl: number
  alerts: string[]
  /** Coverage of the figures above over the position set. */
  valuation?: PortfolioValuationCoverage
}

export interface BenchmarkCurvePoint {
  date: string
  portfolio: number
  benchmark: number
}

export interface PortfolioBenchmark extends PortfolioFxBlock {
  empty?: boolean
  reason?: string
  portfolio_return?: number
  benchmark_return?: number
  excess_return?: number
  information_ratio?: number
  relative_drawdown?: number
  days?: number
  benchmark_code?: string
  benchmark_label?: string
  curve?: BenchmarkCurvePoint[]
  /** Coverage of the curve/return figures above over the position set. */
  valuation?: PortfolioValuationCoverage
}

export interface PortfolioAttribution extends PortfolioFxBlock {
  items: AttributionItem[]
  /** Coverage of the contribution figures over the position set. */
  valuation?: PortfolioValuationCoverage
}

export const portfolioApi = {
  /** 真实持仓组合诊断(集中度/分布/风险提示)。 */
  diagnostics: () => fetchAPI<PortfolioDiagnostics>('/portfolio/diagnostics'),

  /** 组合 vs 基准(超额/信息比率/相对回撤 + 归一化曲线)。 */
  benchmark: (params?: { days?: number; benchmark?: string }) => {
    // No client-side default: an unspecified benchmark lets the backend's
    // own DEFAULT_BENCHMARK ('GSPC', src/core/portfolio_benchmark.py) apply,
    // instead of this client silently pinning everyone to '000300'.
    const query = new URLSearchParams({ days: String(params?.days ?? 60) })
    if (params?.benchmark) query.set('benchmark', params.benchmark)
    return fetchAPI<PortfolioBenchmark>(`/portfolio/benchmark?${query.toString()}`, { timeoutMs: 60000 })
  },

  /** 个股对组合收益的贡献(谁拖累/贡献)。 */
  attribution: (days = 60) =>
    fetchAPI<PortfolioAttribution>(`/portfolio/attribution?days=${days}`, { timeoutMs: 60000 }),

  /** 组合 AI 体检(叙述结论 + 调仓建议)。 */
  aiReview: () => fetchAPI<PortfolioAiReview>('/portfolio/ai-review', { method: 'POST', timeoutMs: 60000 }),
}

export interface AttributionItem {
  symbol: string
  name: string
  market: string
  return_pct: number
  weight_pct: number
  contribution_pct: number
}

export interface PortfolioAiReview extends PortfolioFxBlock {
  empty?: boolean
  reason?: string
  content?: string
  top?: AttributionItem[]
  worst?: AttributionItem[]
  /** Coverage of the review's underlying figures over the position set. */
  valuation?: PortfolioValuationCoverage
}
