import { fetchAPI } from './client'

type QueryValue = string | number | boolean | null | undefined

function withQuery(path: string, params: Record<string, QueryValue>): string {
  const q = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v === undefined || v === null) return
    const sv = String(v).trim()
    if (!sv) return
    q.set(k, sv)
  })
  const s = q.toString()
  return s ? `${path}?${s}` : path
}

export const insightApi = {
  quote: <T>(symbol: string, market: string) =>
    fetchAPI<T>(`/quotes/${encodeURIComponent(symbol)}?market=${encodeURIComponent(market)}`),

  klineSummary: <T>(symbol: string, market: string) =>
    fetchAPI<T>(`/klines/${encodeURIComponent(symbol)}/summary?market=${encodeURIComponent(market)}`),

  klines: <T>(symbol: string, params: { market: string; days?: number; interval?: string }) =>
    fetchAPI<T>(
      withQuery(`/klines/${encodeURIComponent(symbol)}`, {
        market: params.market,
        days: params.days,
        interval: params.interval,
      })
    ),

  suggestions: <T>(
    symbol: string,
    params: { market?: string; limit?: number; include_expired?: boolean }
  ) =>
    fetchAPI<T>(
      withQuery(`/suggestions/${encodeURIComponent(symbol)}`, {
        market: params.market,
        limit: params.limit,
        include_expired: params.include_expired,
      })
    ),

  news: <T>(params: Record<string, QueryValue>) => fetchAPI<T>(withQuery('/news', params)),

  history: <T>(params: Record<string, QueryValue>) => fetchAPI<T>(withQuery('/history', params)),

  portfolioSummary: <T>(params?: { include_quotes?: boolean }) =>
    fetchAPI<T>(
      withQuery('/portfolio/summary', {
        include_quotes: params?.include_quotes,
      })
    ),

  addPositionEval: (params: AddPositionEvalParams) =>
    fetchAPI<AddPositionEvalResult>('/insights/add-position-eval', {
      method: 'POST',
      body: JSON.stringify(params),
      timeoutMs: 60000, // AI 评估较慢,放宽超时
    }),

  announcementEval: (params: { symbol: string; market: string; model_id?: number }) =>
    fetchAPI<AnnouncementEvalResult>('/insights/announcement-eval', {
      method: 'POST',
      body: JSON.stringify(params),
      timeoutMs: 40000,
    }),

  filings: (params: { symbol: string; market: string; limit?: number; forms?: string }) =>
    fetchAPI<FilingsResponse>(
      withQuery('/insights/filings', {
        symbol: params.symbol,
        market: params.market,
        limit: params.limit,
        forms: params.forms,
      })
    ),

  holders: (params: { symbol: string; market: string }) =>
    fetchAPI<HoldersResponse>(
      withQuery('/insights/holders', {
        symbol: params.symbol,
        market: params.market,
      })
    ),
}

// One row from GET /insights/filings. `filed_at` is an ISO timestamp over the wire.
export interface FilingItem {
  source: string
  external_id: string
  symbol: string
  form_type: string
  title: string
  filed_at: string
  url: string
  description?: string
  report_date?: string
}

export interface FilingsResponse {
  symbol: string
  market: string
  items: FilingItem[]
  // Set for Canadian symbols: SEDAR+ has no free API, so press releases are shown instead.
  note?: string
}

export type HolderKind = 'breakdown' | 'institution' | 'insider_tx'

// One row from GET /insights/holders; `kind` decides which table it belongs to.
export interface HolderItem {
  symbol: string
  kind: HolderKind
  holder: string
  date?: string
  shares?: number | null
  value?: number | null
  pct_out?: number | null
  change_pct?: number | null
  transaction?: string
  position?: string
  ownership?: string
  url?: string
  source?: string
}

export interface HoldersResponse {
  breakdown: HolderItem[]
  institutions: HolderItem[]
  insider_transactions: HolderItem[]
  source?: string
}

export interface AnnouncementToneItem {
  title: string
  time: string
  tone: string // 利好 / 利空 / 中性
  summary: string
}

export interface AnnouncementEvalResult {
  symbol: string
  market: string
  items: AnnouncementToneItem[]
}

export interface AddPositionEvalParams {
  symbol: string
  market: string
  current_quantity: number
  current_cost: number
  add_quantity: number
  add_price: number
  model_id?: number
}

export interface AddPositionEvalResult {
  symbol: string
  market: string
  action: string // 加仓 / 建仓
  new_cost: number
  dilute_abs: number
  dilute_pct: number
  total_quantity: number
  total_invested: number
  verdict: string // Suitable / Caution / Not Suitable / Unknown
  content: string // markdown 结论
}
