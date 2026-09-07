import { fetchAPI } from './client'
import type { EquityMarket } from './markets'

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

export interface HotStockItem {
  symbol: string
  market: string
  name: string
  price: number | null
  change_pct: number | null
  turnover: number | null
  volume?: number | null
}

export interface HotBoardItem {
  code: string
  name: string
  change_pct: number | null
  turnover: number | null
}

export const discoveryApi = {
  listHotStocks: (params?: {
    market?: EquityMarket
    mode?: 'turnover' | 'gainers' | 'losers' | 'for_you'
    limit?: number
  }) =>
    fetchAPI<HotStockItem[]>(
      withQuery('/discovery/stocks', {
        market: params?.market,
        mode: params?.mode,
        limit: params?.limit,
      })
    ),

  listHotBoards: (params?: {
    market?: EquityMarket
    mode?: 'gainers' | 'losers' | 'turnover' | 'hot'
    limit?: number
  }) =>
    fetchAPI<HotBoardItem[]>(
      withQuery('/discovery/boards', {
        market: params?.market,
        mode: params?.mode,
        limit: params?.limit,
      })
    ),

  listBoardStocks: (
    boardCode: string,
    params?: {
      mode?: 'gainers' | 'losers' | 'turnover' | 'hot'
      limit?: number
    }
  ) =>
    fetchAPI<HotStockItem[]>(
      withQuery(`/discovery/boards/${encodeURIComponent(boardCode)}/stocks`, {
        mode: params?.mode,
        limit: params?.limit,
      })
    ),
}

