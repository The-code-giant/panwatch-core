import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Plus, Trash2, Pencil, Search, X, Bot, Play, RefreshCw, Building2, ChevronDown, ChevronRight, Cpu, Bell, Clock, Newspaper, ExternalLink, BarChart3, Brain, Eye } from 'lucide-react'
import { fetchAPI, stocksApi, type AIService, type NotifyChannel } from '@tickerkeep/api'
import { useLocalStorage } from '@/lib/utils'
import { mergePortfolioQuotes } from '@/lib/portfolio-valuation'
import { ALL_MARKETS, DEFAULT_MARKET, EQUITY_MARKETS, MARKET_LABEL, MARKET_SHORT, MARKET_SYMBOL_HINT, isMarket, marketLabel } from '@/lib/markets'
import { marketBadgeClass } from '@/lib/market'
import { SuggestionBadge, type SuggestionInfo, type KlineSummary } from '@tickerkeep/biz-ui/components/suggestion-badge'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { KlineSummaryDialog } from '@tickerkeep/biz-ui/components/kline-summary-dialog'
import { Button } from '@tickerkeep/base-ui/components/ui/button'
import { Input } from '@tickerkeep/base-ui/components/ui/input'
import { Label } from '@tickerkeep/base-ui/components/ui/label'
import { Switch } from '@tickerkeep/base-ui/components/ui/switch'
import { Badge } from '@tickerkeep/base-ui/components/ui/badge'
import { Skeleton } from '@tickerkeep/base-ui/components/ui/skeleton'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@tickerkeep/base-ui/components/ui/dialog'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectLabel, SelectItem } from '@tickerkeep/base-ui/components/ui/select'
import { useToast } from '@tickerkeep/base-ui/components/ui/toast'
import { Card, StatCell } from '@tickerkeep/base-ui/components/ui/card'
import { TableWrap, Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@tickerkeep/base-ui/components/ui/table'
import { InfoTip } from '@tickerkeep/base-ui/components/ui/tooltip'
import { EmptyState } from '@tickerkeep/base-ui/components/ui/empty-state'
import StockInsightModal from '@tickerkeep/biz-ui/components/stock-insight-modal'
import { DeepAnalysisModal } from '@tickerkeep/biz-ui/components/deep-analysis-modal'
import StockPriceAlertPanel from '@tickerkeep/biz-ui/components/stock-price-alert-panel'

interface AgentResult {
  success?: boolean
  message?: string
  title: string
  content: string
  should_alert: boolean
  notified: boolean
  skipped?: boolean
}

interface StockAgentInfo {
  agent_name: string
  schedule: string
  ai_model_id: number | null
  notify_channel_ids: number[]
}

interface Stock {
  id: number
  symbol: string
  name: string
  market: string
  sort_order?: number
  agents: StockAgentInfo[]
}

interface Account {
  id: number
  name: string
  available_funds: number
  enabled: boolean
}

// A position's valuation coverage classification — see
// frontend/src/lib/portfolio-valuation.ts for the full contract.
type ValuationStatus = 'unsupported' | 'unavailable' | 'priced'
// Freshness of a position's price: the server emits 'fresh' | 'unavailable';
// 'last_known' is client-only (a prior price kept when a quote round carried
// no entry for it).
type PriceStatus = 'fresh' | 'last_known' | 'unavailable'
// Provenance of the FX rate applied to a position (server emits 'known' |
// 'unknown'; 'last_known' is declared provenance, never fresh).
type FxStatus = 'known' | 'last_known' | 'unknown'

interface Position {
  id: number
  stock_id: number
  sort_order?: number
  symbol: string
  name: string
  market: string
  cost_price: number
  quantity: number
  invested_amount: number | null
  trading_style: string  // short: 短线, swing: 波段, long: 长线
  current_price: number | null
  current_price_cny: number | null  // price in USD (field name is historical; CA converts at CAD to USD)
  change_pct: number | null
  // Canonical daily-P&L input (contract 2, round 7): the prior session's
  // close. Null/absent/non-finite/<=0 means unusable — daily_pnl is then
  // null. Never reconstructed from change_pct — see portfolio-valuation.ts.
  prev_close?: number | null
  market_value: number | null
  market_value_cny: number | null  // market value in USD (field name is historical)
  pnl: number | null
  pnl_pct: number | null
  daily_pnl: number | null
  daily_pnl_pct: number | null
  exchange_rate: number | null  // rate to USD (CA only)
  // Valuation coverage (round 4 — see portfolio-valuation.ts). Optional
  // because a pre-merge payload from the backend may not carry them yet;
  // mergePortfolioQuotes always fills them in.
  priced?: boolean
  valuation_status?: ValuationStatus
  price_status?: PriceStatus
  fx_status?: FxStatus
  cost?: number | null  // native-currency cost (cost_price * quantity); null only for non-finite inputs
  cost_usd?: number | null  // null when the FX rate for this market is unknown
  cost_cny?: number | null  // identical to cost_usd; kept for field-name parity (historical "_cny" = USD)
}

// Valuation coverage fields shared by an account summary and the grand
// total. total_pnl/total_pnl_pct/total_assets are null exactly when nothing
// in the group is priced — never an invented 0.
interface ValuationCoverage {
  total_positions?: number
  priced_positions?: number
  unpriced_positions?: number
  unsupported_positions?: number
  unavailable_positions?: number
  fresh_positions?: number
  last_known_positions?: number
  fx_unknown_positions?: number
  daily_pnl_positions?: number
  daily_pnl_complete?: boolean
  unpriced_cost?: number | null
  // True only when every position is priced from a fresh quote with a known
  // FX rate — a last-known price never makes a valuation complete.
  valuation_complete?: boolean
  pnl_basis?: 'complete' | 'priced_subset' | 'last_known'
  priced_cost_basis?: number
  cost_basis_complete?: boolean
  total_assets_complete?: boolean
}

interface AccountSummary extends ValuationCoverage {
  id: number
  name: string
  available_funds: number
  total_market_value: number
  total_cost: number
  total_pnl: number | null
  total_pnl_pct: number | null
  total_daily_pnl: number | null  // null when holdings exist but none has a daily P&L
  total_assets: number | null
  positions: Position[]
}

interface PortfolioSummary {
  accounts: AccountSummary[]
  total: ValuationCoverage & {
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
  /** Rates to the USD base: USD per one unit of the market currency. */
  exchange_rates?: {
    CAD_USD?: number | null
  }
  /** Provenance of each rate; the merge resolves it (may become 'last_known'). */
  fx_status?: {
    CAD_USD?: FxStatus
  }
  quotes?: Record<string, { current_price: number | null; change_pct: number | null; prev_close?: number | null }>
}

interface AgentConfig {
  name: string
  display_name: string
  description: string
  enabled: boolean
  schedule: string
  execution_mode: string  // batch: 批量分析, single: 逐只分析
}

interface SchedulePreview {
  schedule: string
  timezone: string
  next_runs: string[]
}

interface SearchResult {
  symbol: string
  name: string
  market: string
}

interface QuoteRequestItem {
  symbol: string
  market: string
}

interface QuoteResponse {
  symbol: string
  market: string
  current_price: number | null
  change_pct: number | null
  // Canonical daily-P&L input (contract 2) — null/absent when unusable;
  // never reconstructed from change_pct.
  prev_close?: number | null
  supported?: boolean
  unsupported_reason?: string | null
}

interface StockForm {
  symbol: string
  name: string
  market: string
}

interface AccountForm {
  name: string
  available_funds: string
}

interface PositionForm {
  account_id: number
  stock_id: number
  cost_price: string
  quantity: string
  invested_amount: string
  trading_style: string
  // 搜索选中的股票信息（新增持仓时用）
  stock_symbol: string
  stock_name: string
  stock_market: string
}

// 股票建议信息（来自盘中监控 API）
interface StockSuggestionData {
  symbol: string
  suggestion: SuggestionInfo | null
  kline: KlineSummary | null
}

// 建议池中的建议（包含来源和时间信息）
interface PoolSuggestion {
  id: number
  stock_symbol: string
  stock_market?: string
  stock_name: string
  action: string
  action_label: string
  signal: string
  reason: string
  agent_name: string
  agent_label: string
  created_at: string
  expires_at: string | null
  is_expired: boolean
  prompt_context: string
  ai_response: string
  meta?: Record<string, any>
  should_alert?: boolean
}

interface MarketStatus {
  code: string
  name: string
  status: string
  status_text: string
  is_trading: boolean
  sessions: string[]
  local_time: string
}

interface NewsItem {
  source: string
  source_label: string
  external_id: string
  title: string
  content: string
  publish_time: string
  symbols: string[]
  importance: number
  url: string
}

interface PriceAlertRuleSummary {
  stock_symbol: string
  market: string
  enabled: boolean
}

const emptyStockForm: StockForm = { symbol: '', name: '', market: DEFAULT_MARKET }
const emptyAccountForm: AccountForm = { name: '', available_funds: '0' }

interface StocksPageProps {
  // The room shell now owns the Positions / Watchlist tab bar; this page
  // renders only the view it's told to render.
  view?: 'positions' | 'watchlist'
}

export default function StocksPage({ view = 'positions' }: StocksPageProps = {}) {
  const [stocks, setStocks] = useState<Stock[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [agents, setAgents] = useState<AgentConfig[]>([])
  const [services, setServices] = useState<AIService[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [loading, setLoading] = useState(true)

  // Portfolio
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null)
  const [portfolioRaw, setPortfolioRaw] = useState<PortfolioSummary | null>(null)
  const [portfolioLoading, setPortfolioLoading] = useState(false)
  const [expandedAccounts, setExpandedAccounts] = useState<Set<number>>(new Set())

  // Quotes for all stocks (used in stock list)
  const [quotes, setQuotes] = useState<Record<string, { current_price: number | null; change_pct: number | null; prev_close?: number | null }>>({})
  const [quotesLoading, setQuotesLoading] = useState(false)
  // Keyed by `${market}:${symbol}` to avoid cross-market symbol collisions
  const [klineSummaries, setKlineSummaries] = useState<Record<string, KlineSummary>>({})

  // Auto-refresh (持久化到 localStorage)
  const [autoRefresh, setAutoRefresh] = useLocalStorage('tickerkeep_stocks_autoRefresh', false)
  const [refreshInterval, setRefreshInterval] = useLocalStorage('tickerkeep_stocks_refreshInterval', 30)
  const [lastRefreshTime, setLastRefreshTime] = useState<Date | null>(null)
  const refreshTimerRef = useRef<ReturnType<typeof setInterval>>()

  // Alerts / Scanning
  const [scanning, setScanning] = useState(false)

  // 股票 AI 建议（来自盘中监控 API）
  const [suggestions] = useState<Record<string, StockSuggestionData>>({})
  // 建议池建议（来自 /suggestions API）
  const [poolSuggestions, setPoolSuggestions] = useState<Record<string, PoolSuggestion>>({})
  const [poolSuggestionsLoading, setPoolSuggestionsLoading] = useState(false)
  const [priceAlertSummaryMap, setPriceAlertSummaryMap] = useState<Record<string, { total: number; enabled: number }>>({})

  // News Dialog
  const [newsDialogOpen, setNewsDialogOpen] = useState(false)
  const [newsDialogSymbol, setNewsDialogSymbol] = useState<string>('')  // 空=全部, 否则=指定股票
  const [news, setNews] = useState<NewsItem[]>([])
  const [newsLoading, setNewsLoading] = useState(false)

  // Kline Dialog
  const [klineDialogOpen, setKlineDialogOpen] = useState(false)
  const [klineDialogSymbol, setKlineDialogSymbol] = useState('')
  const [klineDialogMarket, setKlineDialogMarket] = useState<string>(DEFAULT_MARKET)
  const [klineDialogName, setKlineDialogName] = useState<string | undefined>(undefined)
  const [klineDialogHasPosition, setKlineDialogHasPosition] = useState<boolean>(false)
  const [klineDialogInitialSummary, setKlineDialogInitialSummary] = useState<KlineSummary | null>(null)
  const [insightOpen, setInsightOpen] = useState(false)
  const [insightSymbol, setInsightSymbol] = useState('')
  const [insightMarket, setInsightMarket] = useState<string>(DEFAULT_MARKET)
  const [insightName, setInsightName] = useState<string | undefined>(undefined)
  const [insightHasPosition, setInsightHasPosition] = useState(false)

  // Market status
  const [marketStatus, setMarketStatus] = useState<MarketStatus[]>([])
  // Guard to prevent overlapping K线刷新任务导致实际并发超限
  const klineRefreshInFlight = useRef<Promise<void> | null>(null)

  // Stock form
  const [showStockForm, setShowStockForm] = useState(false)
  const [stockForm, setStockForm] = useState<StockForm>(emptyStockForm)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchMarket, setSearchMarket] = useState('')  // 搜索市场筛选
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [searching, setSearching] = useState(false)
  const [refreshingStockList, setRefreshingStockList] = useState(false)

  // Account form
  const [accountDialogOpen, setAccountDialogOpen] = useState(false)
  const [accountForm, setAccountForm] = useState<AccountForm>(emptyAccountForm)
  const [editAccountId, setEditAccountId] = useState<number | null>(null)

  // Position form
  const [positionDialogOpen, setPositionDialogOpen] = useState(false)
  const [positionForm, setPositionForm] = useState<PositionForm>({ account_id: 0, stock_id: 0, cost_price: '', quantity: '', invested_amount: '', trading_style: '', stock_symbol: '', stock_name: '', stock_market: DEFAULT_MARKET })
  const [editPositionId, setEditPositionId] = useState<number | null>(null)
  const [positionDialogAccountId, setPositionDialogAccountId] = useState<number | null>(null)
  const [positionSearchQuery, setPositionSearchQuery] = useState('')
  const [positionSearchMarket, setPositionSearchMarket] = useState('')  // 搜索市场筛选
  const [positionSearchResults, setPositionSearchResults] = useState<SearchResult[]>([])
  const [positionSearching, setPositionSearching] = useState(false)
  const [showPositionDropdown, setShowPositionDropdown] = useState(false)
  const positionSearchTimer = useRef<ReturnType<typeof setTimeout>>()
  const positionDropdownRef = useRef<HTMLDivElement>(null)

  // Agent dialog
  const [agentDialogStock, setAgentDialogStock] = useState<Stock | null>(null)

  // 深度分析(TradingAgents)弹窗
  const [deepAnalysisTarget, setDeepAnalysisTarget] = useState<{
    stockId: number
    symbol: string
    name: string
  } | null>(null)
  const openDeepAnalysis = useCallback((stockId: number, symbol: string, name: string) => {
    setDeepAnalysisTarget({ stockId, symbol, name })
  }, [])
  const [triggeringAgent, setTriggeringAgent] = useState<string | null>(null)
  const [schedulePreviewCache, setSchedulePreviewCache] = useState<Record<string, SchedulePreview | { error: string }>>({})
  const [schedulePreviewLoading, setSchedulePreviewLoading] = useState<Record<string, boolean>>({})
  // 运行中的单只股票 Agent（按股票标记具体 Agent 名称）
  const [runningAgents, setRunningAgents] = useState<Record<number, string | null>>({})
  const [agentResultDialog, setAgentResultDialog] = useState<{ title: string; content: string; should_alert: boolean; notified: boolean } | null>(null)

  // Stock list filter
  const [stockListFilter, setStockListFilter] = useState('')  // '' = all, else a market code from ALL_MARKETS
  const [watchlistOnlyAlerts, setWatchlistOnlyAlerts] = useLocalStorage<boolean>('tickerkeep_watchlist_only_alerts', false)

  // Remove watchlist modal
  const [removeWatchStock, setRemoveWatchStock] = useState<Stock | null>(null)
  const [removingWatchStock, setRemovingWatchStock] = useState(false)
  const [draggingWatchStockId, setDraggingWatchStockId] = useState<number | null>(null)
  const [draggingPositionId, setDraggingPositionId] = useState<number | null>(null)
  const [draggingPositionAccountId, setDraggingPositionAccountId] = useState<number | null>(null)
  const watchDragSnapshotRef = useRef<Stock[] | null>(null)
  const positionDragSnapshotRef = useRef<PortfolioSummary | null>(null)

  const { toast } = useToast()

  const moveById = <T extends { id: number }>(list: T[], fromId: number, toId: number): T[] => {
    const fromIdx = list.findIndex(x => x.id === fromId)
    const toIdx = list.findIndex(x => x.id === toId)
    if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return list
    const next = [...list]
    const [moved] = next.splice(fromIdx, 1)
    next.splice(toIdx, 0, moved)
    return next
  }

  const persistWatchlistOrder = useCallback(async (ordered: Stock[]) => {
    const payload = ordered.map((s, idx) => ({ id: s.id, sort_order: idx + 1 }))
    await fetchAPI('/stocks/reorder', {
      method: 'PUT',
      body: JSON.stringify({ items: payload }),
    })
  }, [])

  const previewWatchlistReorder = useCallback((fromId: number, toId: number) => {
    if (fromId === toId) return
    setStocks(prev => {
      const ordered = [...prev].sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0) || a.id - b.id)
      const moved = moveById(ordered, fromId, toId)
      return moved.map((s, idx) => ({ ...s, sort_order: idx + 1 }))
    })
  }, [])

  const commitWatchlistReorder = useCallback(async () => {
    const current = stocks
    if (!current || current.length === 0) return
    try {
      await persistWatchlistOrder(current)
    } catch (e) {
      if (watchDragSnapshotRef.current) setStocks(watchDragSnapshotRef.current)
      toast(e instanceof Error ? e.message : 'Failed to save watchlist order', 'error')
    }
  }, [persistWatchlistOrder, stocks, toast])

  const persistPositionOrder = useCallback(async (ordered: Position[]) => {
    const payload = ordered.map((p, idx) => ({ id: p.id, sort_order: idx + 1 }))
    await fetchAPI('/positions/reorder/batch', {
      method: 'PUT',
      body: JSON.stringify({ items: payload }),
    })
  }, [])

  const previewPositionReorder = useCallback((accountId: number, fromId: number, toId: number) => {
    if (fromId === toId) return
    setPortfolioRaw(prev => {
      if (!prev) return prev
      const accountsNext = prev.accounts.map(acc => {
        if (acc.id !== accountId) return acc
        const moved = moveById(acc.positions || [], fromId, toId).map((p, idx) => ({ ...p, sort_order: idx + 1 }))
        return { ...acc, positions: moved }
      })
      return { ...prev, accounts: accountsNext }
    })
  }, [])

  const commitPositionReorder = useCallback(async (accountId: number) => {
    const acc = portfolioRaw?.accounts?.find(a => a.id === accountId)
    const ordered = acc?.positions || []
    if (!ordered.length) return
    try {
      await persistPositionOrder(ordered)
    } catch (e) {
      if (positionDragSnapshotRef.current) setPortfolioRaw(positionDragSnapshotRef.current)
      toast(e instanceof Error ? e.message : 'Failed to save position order', 'error')
    }
  }, [persistPositionOrder, portfolioRaw, toast])

  const isSuppressCardClick = () => {
    try {
      const until = (window as any).__tickerkeep_suppress_card_click_until
      return typeof until === 'number' && Date.now() < until
    } catch {
      return false
    }
  }
  const searchTimer = useRef<ReturnType<typeof setTimeout>>()
  const dropdownRef = useRef<HTMLDivElement>(null)

  // 非核心数据后台加载（不阻塞 UI）
  const loadConfigAsync = async () => {
    try {
      const [agentData, servicesData, channelsData] = await Promise.all([
        fetchAPI<AgentConfig[]>('/agents'),
        fetchAPI<AIService[]>('/providers/services'),
        fetchAPI<NotifyChannel[]>('/channels'),
      ])
      setAgents(agentData)
      setServices(servicesData)
      setChannels(channelsData)
    } catch (e) {
      console.warn('Failed to load config data:', e)
    }
  }

  const load = async () => {
    try {
      // 核心数据（立即需要）
      const [stockData, accountData] = await Promise.all([
        fetchAPI<Stock[]>('/stocks'),
        fetchAPI<Account[]>('/accounts'),
      ])
      setStocks(stockData)
      setAccounts(accountData)
      // 默认展开所有账户
      setExpandedAccounts(new Set(accountData.map((a: Account) => a.id)))
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)  // 提前解除阻塞
    }

    // 非核心数据（后台加载，不阻塞 UI）
    loadConfigAsync()

    // 市场状态（非核心，失败不影响页面）
    try {
      const marketStatusData = await fetchAPI<MarketStatus[]>('/stocks/markets/status')
      setMarketStatus(marketStatusData)
    } catch (e) {
      console.warn('Failed to fetch market status:', e)
    }
  }

  const loadPortfolio = async () => {
    setPortfolioLoading(true)
    try {
      // 核心数据：仅本地账户/持仓
      const portfolioData = await fetchAPI<PortfolioSummary>('/portfolio/summary?include_quotes=false')
      setPortfolioRaw(portfolioData)
      setPortfolio(mergePortfolioQuotes(portfolioData, quotes))

      // 市场状态（非核心，失败不影响页面）
      try {
        const marketStatusData = await fetchAPI<MarketStatus[]>('/stocks/markets/status')
        setMarketStatus(marketStatusData)
      } catch (e) {
        console.warn('Failed to fetch market status:', e)
      }
    } catch (e) {
      console.error(e)
    } finally {
      setPortfolioLoading(false)
    }
  }

  // The server's enabled-market scope (round 5 — see portfolio-valuation.ts).
  // When the payload carries `total.market_scope` it is the authority on which
  // markets are supported; the local ALL_MARKETS list is only a fallback for
  // an older payload that doesn't send it.
  const serverMarketScope = useMemo(() => {
    const scope = portfolioRaw?.total.market_scope
    return Array.isArray(scope) ? new Set(scope.map(m => String(m).toUpperCase())) : null
  }, [portfolioRaw])
  const isSupportedMarket = useCallback(
    (market: string) =>
      serverMarketScope ? serverMarketScope.has(String(market || '').toUpperCase()) : isMarket(market),
    [serverMarketScope],
  )

  const buildQuoteItems = useCallback((): QuoteRequestItem[] => {
    const items: QuoteRequestItem[] = []
    const seen = new Set<string>()

    for (const stock of stocks) {
      if (!isSupportedMarket(stock.market)) continue
      const key = `${stock.market}:${stock.symbol}`
      if (seen.has(key)) continue
      seen.add(key)
      items.push({ symbol: stock.symbol, market: stock.market })
    }

    for (const account of portfolioRaw?.accounts || []) {
      for (const pos of account.positions) {
        // The server's own per-position status is honoured even when no
        // scope list is present.
        if (pos.valuation_status === 'unsupported' || !isSupportedMarket(pos.market)) continue
        const key = `${pos.market}:${pos.symbol}`
        if (seen.has(key)) continue
        seen.add(key)
        items.push({ symbol: pos.symbol, market: pos.market })
      }
    }

    return items
  }, [stocks, portfolioRaw, isSupportedMarket])

  const refreshQuotes = useCallback(async () => {
    const items = buildQuoteItems()
    if (items.length === 0) return

    setQuotesLoading(true)
    try {
      const data = await fetchAPI<QuoteResponse[]>('/quotes/batch', {
        method: 'POST',
        body: JSON.stringify({ items }),
      })
      const map: Record<string, { current_price: number | null; change_pct: number | null; prev_close?: number | null }> = {}
      for (const item of data) {
        map[`${item.market}:${item.symbol}`] = {
          current_price: item.current_price ?? null,
          change_pct: item.change_pct ?? null,
          prev_close: item.prev_close ?? null,
        }
      }
      setQuotes(map)
      setLastRefreshTime(new Date())
    } catch (e) {
      console.warn('Failed to refresh quotes:', e)
    } finally {
      setQuotesLoading(false)
    }
  }, [buildQuoteItems])

  useEffect(() => {
    if (!portfolioRaw) return
    setPortfolio(mergePortfolioQuotes(portfolioRaw, quotes))
  }, [portfolioRaw, quotes])

  useEffect(() => {
    if (stocks.length === 0 && (!portfolioRaw || portfolioRaw.accounts.length === 0)) return
    refreshQuotes()
    // 刷新 K 线摘要（用于常驻评分徽章）
    ;(async () => {
      try { await refreshKlines() } catch {}
    })()
  }, [stocks, portfolioRaw, refreshQuotes])

  // 刷新 K 线摘要（并发受限的单个请求，避免批量接口慢）；并防止重入
  const refreshKlines = useCallback(async () => {
    if (klineRefreshInFlight.current) return klineRefreshInFlight.current
    const run = (async () => {
      const items = buildQuoteItems()
      if (items.length === 0) return
      const limit = 5
      const map: Record<string, KlineSummary> = {}
      let idx = 0
      const worker = async () => {
        while (idx < items.length) {
          const i = idx++
          const it = items[i]
          try {
            const res = await fetchAPI<{ symbol: string; market: string; summary: KlineSummary }>(`/klines/${encodeURIComponent(it.symbol)}/summary?market=${encodeURIComponent(it.market)}`)
            if (res && (res as any).summary) {
              map[`${it.market}:${it.symbol}`] = (res as any).summary as KlineSummary
            }
          } catch {
            // ignore single failure
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()))
      // 增量合并：本轮单只失败时保留旧值，避免技术徽章闪断/消失
      setKlineSummaries(prev => ({ ...prev, ...map }))
    })()
    klineRefreshInFlight.current = run
    try { await run } finally { klineRefreshInFlight.current = null }
  }, [buildQuoteItems])

  // 从建议池加载建议（包含历史建议和多来源建议）
  const loadPoolSuggestions = useCallback(async () => {
    setPoolSuggestionsLoading(true)
    try {
      const data = await fetchAPI<Record<string, PoolSuggestion>>('/suggestions?include_expired=true')
      setPoolSuggestions(data)
    } catch (e) {
      console.warn('Failed to load suggestion pool:', e)
    } finally {
      setPoolSuggestionsLoading(false)
    }
  }, [])

  const loadPriceAlertSummaries = useCallback(async () => {
    try {
      const rows = await fetchAPI<PriceAlertRuleSummary[]>('/price-alerts')
      const map: Record<string, { total: number; enabled: number }> = {}
      for (const r of rows || []) {
        const key = `${String(r.market || DEFAULT_MARKET).toUpperCase()}:${String(r.stock_symbol || '').toUpperCase()}`
        if (!map[key]) map[key] = { total: 0, enabled: 0 }
        map[key].total += 1
        if (r.enabled) map[key].enabled += 1
      }
      setPriceAlertSummaryMap(map)
    } catch (e) {
      console.warn('Failed to load alert summary:', e)
    }
  }, [])

  // Load news for specific stock or all watchlist
  const loadNews = useCallback(async (stockName?: string) => {
    setNewsLoading(true)
    try {
      const params = new URLSearchParams({ hours: '168', limit: '50' })  // 7天
      if (stockName) {
        // 直接传递股票名称，比代码更稳定
        params.set('names', stockName)
      }
      const newsData = await fetchAPI<NewsItem[]>(`/news?${params}`)
      setNews(newsData)
    } catch (e) {
      console.error('Failed to load news:', e)
    } finally {
      setNewsLoading(false)
    }
  }, [])

  const openKlineDialog = useCallback((symbol: string, market: string, name?: string, hasPosition?: boolean) => {
    setKlineDialogSymbol(symbol)
    setKlineDialogMarket(market || DEFAULT_MARKET)
    setKlineDialogName(name)
    setKlineDialogHasPosition(!!hasPosition)
    const m = market || DEFAULT_MARKET
    setKlineDialogInitialSummary(klineSummaries[`${m}:${symbol}`] || null)
    setKlineDialogOpen(true)
  }, [klineSummaries])

  // Open news dialog - pass stock name for more stable search
  const openNewsDialog = useCallback((stockName?: string) => {
    setNewsDialogSymbol(stockName || '')  // 存储名称用于 UI 显示
    setNewsDialogOpen(true)
    loadNews(stockName)
  }, [loadNews])

  const openStockDetail = useCallback((stockSymbol: string, stockMarket: string, stockName?: string, hasPosition?: boolean) => {
    setInsightSymbol(stockSymbol)
    setInsightMarket(stockMarket || DEFAULT_MARKET)
    setInsightName(stockName)
    setInsightHasPosition(!!hasPosition)
    setInsightOpen(true)
  }, [])

  const formatPreviewTime = (iso: string, tz?: string): string => {
    try {
      const d = new Date(iso)
      if (isNaN(d.getTime())) return iso
      return d.toLocaleString('zh-CN', {
        timeZone: tz || undefined,
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    } catch {
      return iso
    }
  }

  const effectiveSchedule = (agent: AgentConfig, stockAgent?: StockAgentInfo | null): string => {
    const local = (stockAgent?.schedule || '').trim()
    if (local) return local
    return (agent.schedule || '').trim()
  }

  // Refresh quotes only (decoupled from portfolio and scans)
  const handleRefresh = useCallback(async () => {
    await Promise.all([
      refreshQuotes(),
      loadPoolSuggestions(),
      refreshKlines(),
    ])
  }, [refreshQuotes, loadPoolSuggestions, refreshKlines])

  useEffect(() => { load(); loadPortfolio(); loadPoolSuggestions(); loadPriceAlertSummaries(); refreshKlines() }, [])

  // 仅关注列表场景（无持仓）也要在列表加载后预取 K 线摘要，保证技术指标徽章可见
  const watchlistKlineInitDone = useRef(false)
  const klineMissingRetryRef = useRef<Record<string, number>>({})
  useEffect(() => {
    if (watchlistKlineInitDone.current) return
    if (!stocks || stocks.length === 0) return
    watchlistKlineInitDone.current = true
    refreshKlines()
  }, [stocks, refreshKlines])

  // 关注列表变更后，自动补齐缺失的 K 线摘要（避免未配置 agent 时没有技术指标徽章）
  useEffect(() => {
    if (!stocks || stocks.length === 0) return
    const now = Date.now()
    const retryGapMs = 2 * 60 * 1000
    const missing = stocks.filter(s => {
      const key = `${s.market || DEFAULT_MARKET}:${s.symbol}`
      if (klineSummaries[key]) return false
      const lastTry = klineMissingRetryRef.current[key] || 0
      return (now - lastTry) > retryGapMs
    })
    if (missing.length === 0) return
    for (const s of missing) {
      const key = `${s.market || DEFAULT_MARKET}:${s.symbol}`
      klineMissingRetryRef.current[key] = now
    }
    refreshKlines()
  }, [stocks, klineSummaries, refreshKlines])

  // Agent 配置弹窗：预览未来触发时间（用于自检工作日/周末语义）
  useEffect(() => {
    if (!agentDialogStock) return
    if (!agents || agents.length === 0) return

    const stockAgentMap = new Map((agentDialogStock.agents || []).map(a => [a.agent_name, a]))
    const schedules = new Set<string>()
    for (const agent of agents) {
      if (agent.execution_mode === 'batch') continue
      const sa = stockAgentMap.get(agent.name)
      if (!sa) continue
      const eff = effectiveSchedule(agent, sa)
      if (eff) schedules.add(eff)
    }

    const toFetch = Array.from(schedules).filter(s => !schedulePreviewCache[s] && !schedulePreviewLoading[s])
    if (toFetch.length === 0) return

    let cancelled = false
    ;(async () => {
      // Mark loading
      setSchedulePreviewLoading(prev => {
        const next = { ...prev }
        for (const s of toFetch) next[s] = true
        return next
      })
      try {
        const pairs = await Promise.all(toFetch.map(async s => {
          try {
            const p = await fetchAPI<SchedulePreview>(`/agents/schedule/preview?schedule=${encodeURIComponent(s)}&count=5`)
            return [s, p] as const
          } catch (e) {
            const msg = e instanceof Error ? e.message : 'Preview failed'
            return [s, { error: msg }] as const
          }
        }))
        if (cancelled) return
        setSchedulePreviewCache(prev => ({ ...prev, ...Object.fromEntries(pairs) }))
      } finally {
        if (cancelled) return
        setSchedulePreviewLoading(prev => {
          const next = { ...prev }
          for (const s of toFetch) next[s] = false
          return next
        })
      }
    })()

    return () => { cancelled = true }
  }, [agentDialogStock, agents, schedulePreviewCache, schedulePreviewLoading])

  // 触发扫描：调用盘中监控扫描，并刷新建议池
  const scanAndReload = useCallback(async () => {
    setScanning(true)
    try {
      const url = '/agents/intraday/scan?analyze=true'
      await fetchAPI(url, { method: 'POST' })
      await loadPoolSuggestions()
      await refreshKlines()
      setLastRefreshTime(new Date())
    } catch (e) {
      console.error('Scan failed:', e)
      toast(e instanceof Error ? e.message : 'Scan failed', 'error')
    } finally {
      setScanning(false)
    }
  }, [loadPoolSuggestions, refreshKlines, toast])

  // 首次加载后，按需刷新 K 线摘要与建议池
  const initialKlineDone = useRef(false)
  useEffect(() => {
    if (portfolio && portfolio.accounts.length > 0 && !initialKlineDone.current) {
      initialKlineDone.current = true
      refreshKlines()
      loadPoolSuggestions()
    }
  }, [portfolio, refreshKlines, loadPoolSuggestions])

  // Auto-refresh timer
  useEffect(() => {
    if (autoRefresh) {
      refreshQuotes()
      refreshKlines()
      loadPoolSuggestions()
      refreshTimerRef.current = setInterval(() => {
        refreshQuotes()
        refreshKlines()
        loadPoolSuggestions()
      }, refreshInterval * 1000)
    } else {
      // Clear interval when disabled
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current)
        refreshTimerRef.current = undefined
      }
    }

    return () => {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current)
      }
    }
  }, [autoRefresh, refreshInterval, refreshQuotes, refreshKlines])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
      if (positionDropdownRef.current && !positionDropdownRef.current.contains(e.target as Node)) {
        setShowPositionDropdown(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // ========== Stock handlers ==========
  const doSearch = async (q: string, market: string = searchMarket) => {
    if (q.length < 1) { setSearchResults([]); setShowDropdown(false); return }
    setSearching(true)
    try {
      const marketParam = market ? `&market=${market}` : ''
      const results = await fetchAPI<SearchResult[]>(`/stocks/search?q=${encodeURIComponent(q)}${marketParam}`)
      setSearchResults(results)
      setShowDropdown(true)
    } catch { setSearchResults([]) }
    finally { setSearching(false) }
  }

  const handleSearchInput = (value: string) => {
    setSearchQuery(value)
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => doSearch(value), 500)
  }

  const handleSearchMarketChange = (market: string) => {
    setSearchMarket(market)
    if (searchQuery) {
      doSearch(searchQuery, market)
    }
  }

  const refreshStockListCache = async () => {
    setRefreshingStockList(true)
    try {
      const result = await fetchAPI<{ count: number }>('/stocks/refresh-list', { method: 'POST' })
      toast(`Stock list refreshed, ${result.count} total`, 'success')
      if (searchQuery) {
        doSearch(searchQuery)
      }
    } catch (e) {
      toast('Refresh failed', 'error')
    } finally {
      setRefreshingStockList(false)
    }
  }

  const selectStock = (item: SearchResult) => {
    setStockForm({ symbol: item.symbol, name: item.name, market: item.market })
    setSearchQuery(`${item.symbol} ${item.name}`)
    setShowDropdown(false)
  }

  const handleStockSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      await stocksApi.create(stockForm)
      setStockForm(emptyStockForm)
      setSearchQuery('')
      setShowStockForm(false)
      load()
      toast('Stock added', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to add stock', 'error')
    }
  }

  const hasAnyPositionForStockId = (id: number): boolean => {
    return (portfolio?.accounts || []).some(acc => (acc.positions || []).some(p => p.stock_id === id))
  }

  const removeFromWatchlist = async (stock: Stock) => {
    if (hasAnyPositionForStockId(stock.id)) {
      toast('This stock has open positions - remove the positions before deleting the stock', 'error')
      return
    }

    setRemovingWatchStock(true)
    try {
      await stocksApi.remove(stock.id)
      toast('Stock deleted', 'success')
      setRemoveWatchStock(null)
      load()
      // Price alerts/linked config are removed along with the stock; refresh once to avoid stale UI state.
      loadPortfolio()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Delete failed', 'error')
    } finally {
      setRemovingWatchStock(false)
    }
  }

  // ========== Account handlers ==========
  const openAccountDialog = (account?: Account) => {
    if (account) {
      setAccountForm({ name: account.name, available_funds: account.available_funds.toString() })
      setEditAccountId(account.id)
    } else {
      setAccountForm(emptyAccountForm)
      setEditAccountId(null)
    }
    setAccountDialogOpen(true)
  }

  const handleAccountSubmit = async () => {
    try {
      const payload = {
        name: accountForm.name,
        available_funds: parseFloat(accountForm.available_funds) || 0,
      }
      if (editAccountId) {
        await fetchAPI(`/accounts/${editAccountId}`, { method: 'PUT', body: JSON.stringify(payload) })
      } else {
        await fetchAPI('/accounts', { method: 'POST', body: JSON.stringify(payload) })
      }
      setAccountDialogOpen(false)
      load()
      loadPortfolio()
      toast(editAccountId ? 'Account updated' : 'Account created', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to save account', 'error')
    }
  }

  const handleDeleteAccount = async (id: number) => {
    if (!confirm('Delete this account? This will also delete all positions in this account.')) return
    try {
      await fetchAPI(`/accounts/${id}`, { method: 'DELETE' })
      load()
      loadPortfolio()
      toast('Account deleted', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to delete account', 'error')
    }
  }

  // ========== Position handlers ==========
  const openPositionDialog = (accountId: number, position?: Position) => {
    setPositionDialogAccountId(accountId)
    setPositionSearchQuery('')
    setPositionSearchResults([])
    setShowPositionDropdown(false)
    if (position) {
      setPositionForm({
        account_id: accountId,
        stock_id: position.stock_id,
        cost_price: position.cost_price.toString(),
        quantity: position.quantity.toString(),
        invested_amount: position.invested_amount?.toString() || '',
        trading_style: position.trading_style || '',
        stock_symbol: position.symbol,
        stock_name: position.name,
        stock_market: position.market,
      })
      setEditPositionId(position.id)
    } else {
      setPositionForm({
        account_id: accountId,
        stock_id: 0,
        cost_price: '',
        quantity: '',
        invested_amount: '',
        trading_style: '',
        stock_symbol: '',
        stock_name: '',
        stock_market: DEFAULT_MARKET,
      })
      setEditPositionId(null)
    }
    setPositionDialogOpen(true)
  }

  const doPositionSearch = async (q: string, market: string = positionSearchMarket) => {
    if (q.length < 1) { setPositionSearchResults([]); setShowPositionDropdown(false); return }
    setPositionSearching(true)
    try {
      const marketParam = market ? `&market=${market}` : ''
      const results = await fetchAPI<SearchResult[]>(`/stocks/search?q=${encodeURIComponent(q)}${marketParam}`)
      setPositionSearchResults(results)
      setShowPositionDropdown(results.length > 0)
    } catch { setPositionSearchResults([]) }
    finally { setPositionSearching(false) }
  }

  const handlePositionSearchInput = (value: string) => {
    setPositionSearchQuery(value)
    clearTimeout(positionSearchTimer.current)
    positionSearchTimer.current = setTimeout(() => doPositionSearch(value), 500)
  }

  const handlePositionSearchMarketChange = (market: string) => {
    setPositionSearchMarket(market)
    if (positionSearchQuery) {
      doPositionSearch(positionSearchQuery, market)
    }
  }

  const selectPositionStock = (item: SearchResult) => {
    // Check whether this stock already exists
    const existing = stocks.find(s => s.symbol === item.symbol && s.market === item.market)
    setPositionForm({
      ...positionForm,
      stock_id: existing?.id || 0,
      stock_symbol: item.symbol,
      stock_name: item.name,
      stock_market: item.market,
    })
    setPositionSearchQuery(`${item.symbol} ${item.name}`)
    setShowPositionDropdown(false)
  }

  const handlePositionSubmit = async () => {
    try {
      let stockId = positionForm.stock_id

      // If this is a new position and the stock isn't on the watchlist yet, add it first
      if (!editPositionId && !stockId && positionForm.stock_symbol) {
        try {
          const newStock = await fetchAPI<Stock>('/stocks', {
            method: 'POST',
            body: JSON.stringify({
              symbol: positionForm.stock_symbol,
              name: positionForm.stock_name,
              market: positionForm.stock_market,
            })
          })
          stockId = newStock.id
          load() // Refresh the stock list
        } catch {
          // The stock may already exist — try fetching it instead (handles concurrent creation/historical data).
          try {
            const existingStocks = await fetchAPI<Stock[]>('/stocks')
            const existing = existingStocks.find(s => s.symbol === positionForm.stock_symbol && s.market === positionForm.stock_market)
            if (existing) {
              stockId = existing.id
            } else {
              toast('Failed to add stock', 'error')
              return
            }
          } catch (e) {
            toast(e instanceof Error ? e.message : 'Failed to add stock', 'error')
            return
          }
        }
      }

      const payload = {
        account_id: positionForm.account_id,
        stock_id: stockId,
        cost_price: parseFloat(positionForm.cost_price),
        quantity: parseInt(positionForm.quantity),
        invested_amount: positionForm.invested_amount ? parseFloat(positionForm.invested_amount) : null,
        trading_style: positionForm.trading_style,  // empty string means clear it
      }
      if (editPositionId) {
        await fetchAPI(`/positions/${editPositionId}`, { method: 'PUT', body: JSON.stringify(payload) })
      } else {
        await fetchAPI('/positions', { method: 'POST', body: JSON.stringify(payload) })
      }
      setPositionDialogOpen(false)
      loadPortfolio()
      toast(editPositionId ? 'Position updated' : 'Position added', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to save position', 'error')
    }
  }

  const handleDeletePosition = async (id: number) => {
    if (!confirm('Delete this position?')) return
    try {
      await fetchAPI(`/positions/${id}`, { method: 'DELETE' })
      loadPortfolio()
      toast('Position deleted', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to delete position', 'error')
    }
  }

  // ========== Agent handlers ==========
  const toggleAgent = async (stock: Stock, agentName: string) => {
    try {
      const current = stock.agents || []
      const isAssigned = current.some(a => a.agent_name === agentName)
      const newAgents = isAssigned
        ? current.filter(a => a.agent_name !== agentName)
        : [...current, { agent_name: agentName, schedule: '', ai_model_id: null, notify_channel_ids: [] }]
      await fetchAPI(`/stocks/${stock.id}/agents`, { method: 'PUT', body: JSON.stringify({ agents: newAgents }) })
      load()
      setAgentDialogStock(prev => prev ? { ...prev, agents: newAgents } : null)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to update Agent binding', 'error')
    }
  }

  const triggerStockAgent = async (stockId: number, agentName: string) => {
    setTriggeringAgent(agentName)
    setRunningAgents(prev => ({ ...prev, [stockId]: agentName }))
    // Close the config dialog immediately after triggering to avoid stacked-dialog interference
    setAgentDialogStock(null)
    try {
      // Manual triggers bypass throttling, to make testing easier
      const resp = await fetchAPI<{ result: AgentResult; success?: boolean; message?: string }>(
        `/stocks/${stockId}/agents/${agentName}/trigger?bypass_throttle=true`,
        { method: 'POST' }
      )
      const result = resp?.result
      if (result) {
        // Show a toast only, no result dialog, to avoid interruption
        if (result.success === false) {
          toast(result.message || result.content || 'Execution failed', 'info')
          return
        }
        const isSkipped = !!result.skipped || /execution skipped|outside trading hours|已跳过执行|非交易时段/.test(result.content || '')
        if (isSkipped) {
          toast(result.content || 'Currently outside trading hours; execution skipped', 'info')
        } else {
          toast(result.should_alert ? 'AI recommends attention' : 'AI judged no attention needed', result.should_alert ? 'success' : 'info')
        }
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Trigger failed'
      if (/execution skipped|outside trading hours|非交易时段|跳过执行/.test(msg)) {
        toast(msg, 'info')
      } else {
        toast(msg, 'error')
      }
    } finally {
      setTriggeringAgent(null)
      setRunningAgents(prev => ({ ...prev, [stockId]: null }))
    }
  }

  const updateStockAgentModel = async (stock: Stock, agentName: string, modelId: number | null) => {
    try {
      const newAgents = (stock.agents || []).map(a =>
        a.agent_name === agentName ? { ...a, ai_model_id: modelId } : a
      )
      await fetchAPI(`/stocks/${stock.id}/agents`, { method: 'PUT', body: JSON.stringify({ agents: newAgents }) })
      load()
      setAgentDialogStock(prev => prev ? { ...prev, agents: newAgents } : null)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to update Agent model', 'error')
    }
  }

  const toggleStockAgentChannel = async (stock: Stock, agentName: string, channelId: number) => {
    try {
      const newAgents = (stock.agents || []).map(a => {
        if (a.agent_name !== agentName) return a
        const current = a.notify_channel_ids || []
        const newIds = current.includes(channelId)
          ? current.filter(id => id !== channelId)
          : [...current, channelId]
        return { ...a, notify_channel_ids: newIds }
      })
      await fetchAPI(`/stocks/${stock.id}/agents`, { method: 'PUT', body: JSON.stringify({ agents: newAgents }) })
      load()
      setAgentDialogStock(prev => prev ? { ...prev, agents: newAgents } : null)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to update Agent notification config', 'error')
    }
  }

  const updateStockAgentSchedule = async (stock: Stock, agentName: string, schedule: string) => {
    try {
      const newAgents = (stock.agents || []).map(a =>
        a.agent_name === agentName ? { ...a, schedule } : a
      )
      await fetchAPI(`/stocks/${stock.id}/agents`, { method: 'PUT', body: JSON.stringify({ agents: newAgents }) })
      load()
      setAgentDialogStock(prev => prev ? { ...prev, agents: newAgents } : null)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to update Agent schedule', 'error')
    }
  }

  // ========== Helpers ==========
  const formatMoney = (value: number) => {
    if (Math.abs(value) >= 10000) {
      return `${(value / 1000).toFixed(1)}k`
    }
    return value.toFixed(2)
  }

  // A valuation total can now be genuinely unknown (nothing priced) — render
  // an explicit "unknown" dash rather than inventing 0.00.
  const formatMoneyOrDash = (value: number | null) => (value != null ? formatMoney(value) : '--')

  // Market badge style and short label — identity only, never good/bad/up/down.
  const marketBadge = (m: string) => {
    const code = String(m || '').toUpperCase()
    return {
      style: `${marketBadgeClass(code)} text-mkt-ink`,
      label: isMarket(code) ? MARKET_SHORT[code] : code.slice(0, 2) || '--',
    }
  }

  // 保留原始精度显示价格（不强制截断小数位）
  const formatPrice = (value: number) => {
    // 最多显示4位小数，去除末尾的0
    const formatted = value.toFixed(4).replace(/\.?0+$/, '')
    return formatted
  }

  // 获取股票的行情信息
  const getStockQuote = (quoteKey: string) => {
    return quotes[quoteKey] || null
  }

  const getPriceAlertSummary = (symbol: string, market: string) => {
    const key = `${String(market || DEFAULT_MARKET).toUpperCase()}:${String(symbol || '').toUpperCase()}`
    return priceAlertSummaryMap[key] || { total: 0, enabled: 0 }
  }

  // 获取股票的建议信息（优先使用建议池，包含来源和时间信息）
  const getSuggestionForStock = (symbol: string, market: string, hasPosition?: boolean): { suggestion: SuggestionInfo | null; kline: KlineSummary | null } => {
    const key = `${market || DEFAULT_MARKET}:${symbol}`
    // 优先使用建议池的建议（包含来源和时间信息）
    const poolSug =
      poolSuggestions[key] ||
      (() => {
        const fallback = poolSuggestions[symbol]
        if (!fallback) return null
        const fm = String(fallback.stock_market || '').toUpperCase()
        return fm && fm !== String(market || DEFAULT_MARKET).toUpperCase() ? null : fallback
      })()
    if (poolSug) {
      const preloadedKline = klineSummaries[key] || (suggestions[symbol]?.kline as any) || null
      return {
        suggestion: {
          id: poolSug.id,
          action: poolSug.action,
          action_label: poolSug.action_label,
          signal: poolSug.signal,
          reason: poolSug.reason,
          should_alert: poolSug.should_alert ?? (['alert', 'avoid', 'sell', 'reduce'].includes(poolSug.action)),
          agent_name: poolSug.agent_name,
          agent_label: poolSug.agent_label,
          created_at: poolSug.created_at,
          is_expired: poolSug.is_expired,
          prompt_context: poolSug.prompt_context,
          ai_response: poolSug.ai_response,
          meta: poolSug.meta,
        },
        // 优先使用本页并发预取的 kline 摘要，确保徽章与弹窗一致且免加载
        kline: preloadedKline,
      }
    }

    // 无池建议时，使用 K 线评分构建轻量建议（仅用于徽章展示）
    const ks = klineSummaries[key]
    if (ks) {
      const scored = buildKlineSuggestion(ks as any, hasPosition)
      return {
        suggestion: {
          action: scored.action,
          action_label: scored.action_label,
          signal: scored.signal,
          reason: '',
          should_alert: false,
          agent_label: 'Technical Indicators',
        },
        kline: ks,
      }
    }

    return { suggestion: null, kline: null }
  }

  const positionRatio = useMemo(() => {
    if (!portfolio) return null
    // total_assets is null exactly when nothing in the portfolio is priced
    // (see portfolio-valuation.ts) — the ratio is unknown then, never 100%.
    const assets = portfolio.total.total_assets
    if (assets == null) return null
    // A ratio computed from a partial valuation would read as a complete one
    // (e.g. a flat 100%), so it is unknown whenever any holding is unpriced.
    if (portfolio.total.valuation_complete === false) return null
    const mv = portfolio.total.total_market_value || 0
    const pct = assets > 0 ? (mv / assets * 100) : 0
    return { mv, assets, pct }
  }, [portfolio])

  const toggleAccountExpanded = (id: number) => {
    setExpandedAccounts(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // 骨架屏：初始加载时显示
  if (loading) {
    return (
      <div>
        {/* Header Skeleton */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <Skeleton className="h-6 w-16 mb-2" />
            <Skeleton className="h-4 w-32" />
          </div>
          <div className="hidden md:flex items-center gap-3">
            <Skeleton className="h-9 w-24" />
            <Skeleton className="h-9 w-24" />
            <Skeleton className="h-9 w-24" />
          </div>
        </div>
        {/* Summary Cards Skeleton */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="card p-4">
              <Skeleton className="h-4 w-16 mb-2" />
              <Skeleton className="h-6 w-24" />
            </div>
          ))}
        </div>
        {/* Account List Skeleton */}
        <div className="space-y-4">
          {[...Array(2)].map((_, i) => (
            <div key={i} className="card">
              <div className="px-4 py-3 border-b border-border/50">
                <Skeleton className="h-5 w-32" />
              </div>
              <div className="divide-y divide-border/50">
                {[...Array(3)].map((_, j) => (
                  <div key={j} className="px-4 py-3 flex items-center gap-4">
                    <Skeleton className="h-4 w-16" />
                    <Skeleton className="h-4 w-24" />
                    <Skeleton className="h-4 w-16 ml-auto" />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div>
      {/* Control strip: the room shell owns the title and the Positions/Watchlist
          tabs now, so this page starts at its first real panel — this toolbar. */}
      <div className="card p-3 md:p-4 flex flex-col gap-2 md:gap-3 mb-5 md:mb-6">
        <div className="flex items-center justify-end gap-2">
          {/* Desktop buttons + controls */}
          <div className="hidden md:flex items-center gap-3">
            {/* Controls */}
            <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-accent/30">
              <div className="flex items-center gap-1.5">
                <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} className="scale-90" />
                <span className="text-[11px] text-muted-foreground">Auto-refresh</span>
                {autoRefresh && (
                  <Select value={refreshInterval.toString()} onValueChange={v => setRefreshInterval(parseInt(v))}>
                    <SelectTrigger className="h-6 w-14 text-[10px] px-1.5">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="10">10s</SelectItem>
                      <SelectItem value="30">30s</SelectItem>
                      <SelectItem value="60">1 min</SelectItem>
                      <SelectItem value="120">2 min</SelectItem>
                    </SelectContent>
                  </Select>
                )}
              </div>
              {(poolSuggestionsLoading || Object.keys(poolSuggestions).length > 0) && (
                <>
                  <div className="w-px h-4 bg-border" />
                  <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    {poolSuggestionsLoading && (
                      <span className="w-3 h-3 border-2 border-border border-t-muted-foreground rounded-full animate-spin" />
                    )}
                    {!poolSuggestionsLoading && Object.keys(poolSuggestions).length > 0 && (
                      <span className="text-[10px] text-foreground">
                        {Object.keys(poolSuggestions).length}
                      </span>
                    )}
                  </div>
                </>
              )}
              {lastRefreshTime && (
                <>
                  <div className="w-px h-4 bg-border" />
                  <span className="text-[10px] text-muted-foreground/60">
                    {lastRefreshTime.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                  </span>
                </>
              )}
            </div>
            {/* Buttons */}
            <Button variant="secondary" onClick={handleRefresh} disabled={quotesLoading}>
              <RefreshCw className={`w-4 h-4 ${quotesLoading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
            <Button variant="secondary" onClick={scanAndReload} disabled={scanning}>
              <Bot className="w-4 h-4" /> Scan
            </Button>
            <Button variant="secondary" onClick={() => openAccountDialog()}>
              <Building2 className="w-4 h-4" /> Add Account
            </Button>
            <Button onClick={() => { setStockForm(emptyStockForm); setSearchQuery(''); setShowStockForm(true) }}>
              <Plus className="w-4 h-4" /> Add Stock
            </Button>
          </div>
          {/* Mobile buttons */}
          <div className="flex md:hidden items-center gap-1.5">
            <Button variant="secondary" size="sm" className="h-8 w-8 p-0" onClick={handleRefresh} disabled={quotesLoading}>
              <RefreshCw className={`w-4 h-4 ${quotesLoading ? 'animate-spin' : ''}`} />
            </Button>
            <Button variant="secondary" size="sm" className="h-8 w-8 p-0" onClick={scanAndReload} disabled={scanning}>
              <Bot className="w-4 h-4" />
            </Button>
            <Button variant="secondary" size="sm" className="h-8 w-8 p-0" onClick={() => openAccountDialog()}>
              <Building2 className="w-4 h-4" />
            </Button>
            <Button size="sm" className="h-8 w-8 p-0" onClick={() => { setStockForm(emptyStockForm); setSearchQuery(''); setShowStockForm(true) }}>
              <Plus className="w-4 h-4" />
            </Button>
          </div>
        </div>

        {/* Mobile row 2: market status + auto-refresh + timestamp merged into one row, horizontal scroll to avoid wrapping; desktop only shows market pills (auto-refresh already shown at the top on desktop) */}
        <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-none -mx-1 px-1 md:flex-wrap md:overflow-visible">
          {marketStatus.map(m => {
            return (
              <div
                key={m.code}
                className="shrink-0 flex items-center gap-1 md:gap-1.5"
                title={`${m.sessions.join(', ')} (${m.local_time}) · ${m.status_text}`}
              >
                <span className={`w-1.5 h-1.5 rounded-full ${m.is_trading ? 'bg-success' : 'bg-muted-foreground/30'}`} />
                <span className="text-[11px] text-muted-foreground">{m.name}</span>
                <span className={`text-[10px] ${m.is_trading ? 'text-success' : 'text-muted-foreground/60'} hidden sm:inline`}>
                  {m.status_text}
                </span>
              </div>
            )
          })}
          {/* Compact mobile auto-refresh control */}
          <div className="flex md:hidden shrink-0 items-center gap-1 px-2 py-0.5 rounded-full bg-accent/30 ml-1">
            <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} className="scale-75" />
            {autoRefresh ? (
              <Select value={refreshInterval.toString()} onValueChange={v => setRefreshInterval(parseInt(v))}>
                <SelectTrigger className="h-5 w-12 text-[10px] px-1 border-0 bg-transparent">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="10">10s</SelectItem>
                  <SelectItem value="30">30s</SelectItem>
                  <SelectItem value="60">1 min</SelectItem>
                  <SelectItem value="120">2 min</SelectItem>
                </SelectContent>
              </Select>
            ) : (
              <span className="text-[10px] text-muted-foreground">Auto-refresh</span>
            )}
            {poolSuggestionsLoading && (
              <span className="w-2.5 h-2.5 border-2 border-border border-t-muted-foreground rounded-full animate-spin" />
            )}
          </div>
          {lastRefreshTime && (
            <span className="md:hidden shrink-0 text-[10px] text-muted-foreground/60 font-mono ml-1">
              {lastRefreshTime.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
        </div>
      </div>

      {/* Portfolio Total Summary — one instrument, not six separate cards */}
      {portfolioLoading && !portfolio ? (
        <Card variant="strip" className="mb-6 grid-cols-1 min-[420px]:grid-cols-2 md:grid-cols-3 lg:grid-cols-6 lg:divide-y-0">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="p-4">
              <Skeleton className="h-3 w-16 mb-2" />
              <Skeleton className="h-6 w-20" />
            </div>
          ))}
        </Card>
      ) : portfolio ? (() => {
        const dayPnl = portfolio.total.total_daily_pnl
        const totalMv = portfolio.total.total_market_value
        const prevMv = dayPnl != null ? totalMv - dayPnl : null
        const dayPct = dayPnl != null && prevMv != null && prevMv > 0 ? (dayPnl / prevMv * 100) : null
        const dayUp = dayPnl != null && dayPnl >= 0
        const totalPnl = portfolio.total.total_pnl
        const totalPnlPct = portfolio.total.total_pnl_pct
        const totalUp = totalPnl != null && totalPnl >= 0
        // Coverage fields are always filled in by mergePortfolioQuotes; the
        // fallbacks below only guard a payload that hasn't been through it
        // yet, and they fail towards "incomplete" — a missing flag never
        // asserts a full valuation.
        const valuationComplete = portfolio.total.valuation_complete === true
        const unpricedCount = portfolio.total.unpriced_positions ?? 0
        const lastKnownCount = portfolio.total.last_known_positions ?? 0
        const fxUnknownCount = portfolio.total.fx_unknown_positions ?? 0
        const totalAssetsComplete = portfolio.total.total_assets_complete === true
        const dailyComplete = portfolio.total.daily_pnl_complete === true
        const dailyCount = portfolio.total.daily_pnl_positions ?? 0
        const totalCount = portfolio.total.total_positions ?? 0
        // One suffix + one explanation shared by every figure computed over
        // the priced subset (market value, P&L, assets): "priced only" when
        // something is unpriced, "last known" when everything is priced but
        // some quote or FX rate is not fresh.
        const coverageSuffix = valuationComplete ? null : unpricedCount > 0 ? 'priced only' : 'last known'
        const coverageTip = valuationComplete
          ? null
          : [
              unpricedCount > 0
                ? `${unpricedCount} position(s) have no current price${fxUnknownCount > 0 ? ` (${fxUnknownCount} with no FX rate to USD)` : ''} and are excluded from this figure.`
                : null,
              lastKnownCount > 0
                ? `${lastKnownCount} position(s) use a last-known price or FX rate rather than a fresh quote.`
                : null,
            ].filter(Boolean).join(' ')
        return (
          <Card variant="strip" className="mb-6 grid-cols-1 min-[420px]:grid-cols-2 md:grid-cols-3 lg:grid-cols-6 lg:divide-y-0">
            <StatCell
              label={
                <span className="inline-flex items-center gap-1">
                  Total Market Value
                  {coverageSuffix && <span className="text-muted-foreground/70">· {coverageSuffix}</span>}
                  {coverageTip && <InfoTip label={coverageTip} />}
                </span>
              }
              value={formatMoney(portfolio.total.total_market_value)}
            />
            <StatCell
              label={
                <span className="inline-flex items-center gap-1">
                  Total P&L
                  {coverageSuffix && <span className="text-muted-foreground/70">· {coverageSuffix}</span>}
                  <InfoTip label={coverageTip ?? 'Unrealized P&L across every position.'} />
                </span>
              }
              tone={totalPnl == null ? 'muted' : totalUp ? 'up' : 'down'}
              value={totalPnl == null ? '--' : <>{totalUp ? '+' : ''}{formatMoney(totalPnl)}</>}
              aside={
                <span className="flex flex-col items-end gap-0.5">
                  {totalPnlPct != null && (
                    <span className={`text-[12px] opacity-80 ${totalUp ? 'text-stock-up' : 'text-stock-down'}`}>
                      ({totalPnlPct >= 0 ? '+' : ''}{totalPnlPct.toFixed(2)}%)
                    </span>
                  )}
                  {!valuationComplete && (
                    <span className="text-[9.5px] text-muted-foreground/70 whitespace-nowrap">
                      {unpricedCount > 0 ? `${unpricedCount} unpriced` : `${lastKnownCount} last known`}
                    </span>
                  )}
                </span>
              }
            />
            <StatCell
              label={
                <span className="inline-flex items-center gap-1">
                  Today's P&L
                  {!dailyComplete && <span className="text-muted-foreground/70">· partial</span>}
                  {!dailyComplete && (
                    <InfoTip
                      label={
                        dayPnl == null
                          ? 'No held position has a day change available yet.'
                          : `${dailyCount} of ${totalCount} position(s) have a day change; the rest are excluded from this figure.`
                      }
                    />
                  )}
                </span>
              }
              tone={dayPnl == null ? 'muted' : dayUp ? 'up' : 'down'}
              value={dayPnl == null ? '--' : <>{dayUp ? '+' : ''}{formatMoney(dayPnl)}</>}
              aside={
                dayPct != null ? (
                  <span className={`text-[12px] opacity-80 ${dayUp ? 'text-stock-up' : 'text-stock-down'}`}>
                    ({dayPct >= 0 ? '+' : ''}{dayPct.toFixed(2)}%)
                  </span>
                ) : undefined
              }
            />
            <StatCell label="Available Funds" value={formatMoney(portfolio.total.available_funds)} />
            <StatCell
              label={
                <span className="inline-flex items-center gap-1">
                  Total Assets
                  {!totalAssetsComplete && <span className="text-muted-foreground/70">· partial</span>}
                  {!totalAssetsComplete && coverageTip && <InfoTip label={coverageTip} />}
                </span>
              }
              value={formatMoneyOrDash(portfolio.total.total_assets)}
            />
            <StatCell
              label={
                <span className="inline-flex items-center gap-1">
                  Position Ratio
                  <InfoTip label="Total market value as a share of total assets (market value ÷ total assets). Shows unknown while any priced-holdings coverage is incomplete." />
                </span>
              }
              value={positionRatio ? `${positionRatio.pct.toFixed(1)}%` : '--'}
              aside={positionRatio ? (
                <span className="min-w-0 truncate text-[10.5px] text-muted-foreground/80">
                  {formatMoney(positionRatio.mv)} / {formatMoney(positionRatio.assets)}
                </span>
              ) : undefined}
            />
          </Card>
        )
      })() : null}

      {/* Add Stock Dialog */}
      <Dialog open={showStockForm} onOpenChange={(open) => { setShowStockForm(open); if (!open) { setSearchQuery(''); setSearchMarket('') } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Add Stock to Watchlist</DialogTitle>
            <DialogDescription>Search and add to your watchlist</DialogDescription>
          </DialogHeader>
          <form onSubmit={handleStockSubmit}>
            <div className="relative" ref={dropdownRef}>
              <div className="flex items-center gap-2 mb-2">
                <Label className="mb-0">Search Stock</Label>
                <div className="flex items-center gap-1">
                  {[
                    { value: '', label: 'All' },
                    ...ALL_MARKETS.map(m => ({ value: m, label: MARKET_LABEL[m] })),
                  ].map(opt => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => handleSearchMarketChange(opt.value)}
                      className={`text-[11px] px-2 py-0.5 rounded transition-colors ${
                        searchMarket === opt.value
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={refreshStockListCache}
                  disabled={refreshingStockList}
                  className="text-[10px] text-muted-foreground hover:text-foreground transition-colors ml-2"
                  title="Can't find it? Click to refresh the stock list"
                >
                  {refreshingStockList ? (
                    <span className="flex items-center gap-1">
                      <RefreshCw className="w-3 h-3 animate-spin" /> Refreshing...
                    </span>
                  ) : (
                    <span className="flex items-center gap-1">
                      <RefreshCw className="w-3 h-3" /> Refresh List
                    </span>
                  )}
                </button>
              </div>
              <div className="relative">
                <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground/50" />
                <Input
                  value={searchQuery}
                  onChange={e => handleSearchInput(e.target.value)}
                  onFocus={() => searchResults.length > 0 && setShowDropdown(true)}
                  placeholder={isMarket(searchMarket) ? MARKET_SYMBOL_HINT[searchMarket] : 'Ticker or name, e.g. AAPL, SHOP.TO (TSX) or BTC-USD'}
                  className="pl-10"
                  autoComplete="off"
                />
                {searching && <span className="absolute right-3.5 top-1/2 -translate-y-1/2 w-4 h-4 border-2 border-border border-t-foreground rounded-full animate-spin" />}
              </div>
              {showDropdown && (
                <div className="absolute z-50 w-full mt-2 max-h-64 overflow-auto scrollbar card">
                  {searchResults.length === 0 && !searching && (
                    <div className="px-4 py-3 text-[12px] text-muted-foreground">
                      No matches. Canadian listings need the exchange suffix, e.g. SHOP.TO or XYZ.V.
                    </div>
                  )}
                  {searchResults.map(item => (
                    <button
                      key={`${item.market}-${item.symbol}`}
                      type="button"
                      onClick={() => selectStock(item)}
                      className="w-full flex items-center gap-3 px-4 py-3 text-[13px] hover:bg-accent/50 text-left transition-colors"
                    >
                      <span className="font-mono text-muted-foreground text-[12px] w-14">{item.symbol}</span>
                      <span className="flex-1 font-medium text-foreground">{item.name}</span>
                      <Badge variant="secondary">{marketLabel(item.market)}</Badge>
                    </button>
                  ))}
                </div>
              )}
              {stockForm.symbol && (
                <div className="mt-2.5 flex items-center gap-2">
                  <Badge><span className="font-mono">{stockForm.symbol}</span> {stockForm.name}</Badge>
                  <Badge variant="secondary">{marketLabel(stockForm.market)}</Badge>
                </div>
              )}
            </div>
            <div className="mt-6 flex items-center gap-3 justify-end">
              <Button type="button" variant="ghost" onClick={() => { setShowStockForm(false); setSearchQuery('') }}>Cancel</Button>
              <Button type="submit" disabled={!stockForm.symbol}>Confirm Add</Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      {/* Accounts & Positions */}
      {view === 'positions' && (
        portfolio && portfolio.accounts.length === 0 ? (
          <div className="card">
            <EmptyState
              icon={Building2}
              title="No accounts yet"
              description="Add a trading account to start tracking positions, cost, and P&L."
              action={<Button onClick={() => openAccountDialog()}><Building2 className="w-4 h-4" /> Add Account</Button>}
              size="md"
            />
          </div>
        ) : (
          <div className="space-y-4">
            {portfolio?.accounts.map(account => (
              <div key={account.id} className="card overflow-hidden">
              {/* Account Header */}
              <div
                className="flex flex-col md:flex-row md:items-center justify-between p-3 md:p-4 gap-2 row-interactive"
                onClick={() => toggleAccountExpanded(account.id)}
              >
                <div className="flex items-center gap-2 md:gap-3">
                  {expandedAccounts.has(account.id) ? (
                    <ChevronDown className="w-4 h-4 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-muted-foreground" />
                  )}
                  <Building2 className="w-4 h-4 text-muted-foreground" />
                  <span className="text-[14px] md:text-[15px] font-semibold text-foreground">{account.name}</span>
                  <span className="text-[11px] md:text-[12px] text-muted-foreground">
                    {account.positions.length} position(s)
                    {!!account.unpriced_positions && (
                      <span className="text-muted-foreground/70"> · {account.unpriced_positions} unpriced</span>
                    )}
                    {!!account.last_known_positions && (
                      <span className="text-muted-foreground/70"> · {account.last_known_positions} last known</span>
                    )}
                  </span>
                </div>
                <div className="flex items-center justify-between md:justify-end gap-2 md:gap-6 pl-6 md:pl-0">
                  <div className="flex items-center gap-2.5 md:gap-6 min-w-0">
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">
                        Value{account.valuation_complete !== true && <span className="text-muted-foreground/60"> ({account.unpriced_positions ? 'priced' : 'last known'})</span>}
                      </div>
                      <div className="text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap">{formatMoney(account.total_market_value)}</div>
                    </div>
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">
                        P&L{account.valuation_complete !== true && <span className="text-muted-foreground/60"> ({account.unpriced_positions ? 'priced' : 'last known'})</span>}
                      </div>
                      {account.total_pnl == null ? (
                        <div className="text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap text-muted-foreground">--</div>
                      ) : (
                        <div className={`text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap ${account.total_pnl >= 0 ? 'text-stock-up' : 'text-stock-down'}`}>
                          {account.total_pnl >= 0 ? '+' : ''}{formatMoney(account.total_pnl)}
                          {account.total_pnl_pct != null && (
                            <span className="text-[10px] md:text-[11px] ml-1 hidden md:inline">({account.total_pnl_pct >= 0 ? '+' : ''}{account.total_pnl_pct.toFixed(2)}%)</span>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="text-left md:text-right">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">
                        Today{account.daily_pnl_complete !== true && <span className="text-muted-foreground/60"> (partial)</span>}
                      </div>
                      {account.total_daily_pnl == null ? (
                        <div className="text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap text-muted-foreground">--</div>
                      ) : (
                        <div className={`text-[12px] md:text-[13px] font-mono font-medium whitespace-nowrap ${account.total_daily_pnl >= 0 ? 'text-stock-up' : 'text-stock-down'}`}>
                          {account.total_daily_pnl >= 0 ? '+' : ''}{formatMoney(account.total_daily_pnl)}
                        </div>
                      )}
                    </div>
                    <div className="text-left md:text-right hidden sm:block">
                      <div className="text-[10px] md:text-[11px] text-muted-foreground">Available</div>
                      <div className="text-[12px] md:text-[13px] font-mono whitespace-nowrap">{formatMoney(account.available_funds)}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-0 md:gap-1 shrink-0" onClick={e => e.stopPropagation()}>
                    <Button variant="ghost" size="icon" className="h-7 w-7 md:h-8 md:w-8" onClick={() => openPositionDialog(account.id)}>
                      <Plus className="w-3 md:w-3.5 h-3 md:h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 md:h-8 md:w-8" onClick={() => openAccountDialog(accounts.find(a => a.id === account.id))}>
                      <Pencil className="w-3 md:w-3.5 h-3 md:h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 md:h-8 md:w-8 hover:text-destructive" onClick={() => handleDeleteAccount(account.id)}>
                      <Trash2 className="w-3 md:w-3.5 h-3 md:h-3.5" />
                    </Button>
                  </div>
                </div>
              </div>

              {/* Positions */}
              {expandedAccounts.has(account.id) && (
                <div className="border-t border-border/30">
                  {account.positions.length === 0 ? (
                    <EmptyState
                      icon={Plus}
                      title="No positions yet"
                      description="Add a position to start tracking cost, market value, and P&L."
                      action={<Button size="sm" onClick={() => openPositionDialog(account.id)}><Plus className="w-4 h-4" /> Add Position</Button>}
                      size="sm"
                    />
                  ) : (
                    <>
                      {/* Desktop Table */}
                      <TableWrap bordered={false} className="hidden md:block">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Stock</TableHead>
                              <TableHead numeric>Price</TableHead>
                              <TableHead numeric>Change</TableHead>
                              <TableHead numeric>Cost</TableHead>
                              <TableHead numeric>Qty</TableHead>
                              <TableHead numeric>Value</TableHead>
                              <TableHead numeric>
                                <span className="inline-flex items-center gap-1 justify-end">
                                  P&L
                                  <InfoTip label="Unrealized: current market value vs. cost basis, marked to market." />
                                </span>
                              </TableHead>
                              <TableHead numeric>Today</TableHead>
                              <TableHead className="text-center">Style</TableHead>
                              <TableHead>Agent</TableHead>
                              <TableHead className="text-center">Actions</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {account.positions.map((pos) => {
                              const stock = stocks.find(s => s.id === pos.stock_id)
                              const badge = marketBadge(pos.market)
                              const isForeign = pos.market === 'CA'
                              const changeColor = pos.change_pct != null
                                ? (pos.change_pct > 0 ? 'text-stock-up' : pos.change_pct < 0 ? 'text-stock-down' : 'text-muted-foreground')
                                : 'text-muted-foreground'
                              const pnlColor = pos.pnl != null
                                ? (pos.pnl > 0 ? 'text-stock-up' : pos.pnl < 0 ? 'text-stock-down' : 'text-muted-foreground')
                                : 'text-muted-foreground'
                              return (
                                <TableRow
                                  key={pos.id}
                                  hoverable
                                  draggable
                                  onDragStart={(e) => {
                                    positionDragSnapshotRef.current = portfolioRaw ? JSON.parse(JSON.stringify(portfolioRaw)) : null
                                    setDraggingPositionId(pos.id)
                                    setDraggingPositionAccountId(account.id)
                                    e.dataTransfer.effectAllowed = 'move'
                                  }}
                                  onDragOver={(e) => {
                                    e.preventDefault()
                                    e.dataTransfer.dropEffect = 'move'
                                    if (draggingPositionId != null && draggingPositionAccountId === account.id) {
                                      previewPositionReorder(account.id, draggingPositionId, pos.id)
                                    }
                                  }}
                                  onDrop={(e) => {
                                    e.preventDefault()
                                    if (draggingPositionId != null && draggingPositionAccountId === account.id) {
                                      commitPositionReorder(account.id)
                                    }
                                    setDraggingPositionId(null)
                                    setDraggingPositionAccountId(null)
                                    positionDragSnapshotRef.current = null
                                  }}
                                  onDragEnd={() => {
                                    setDraggingPositionId(null)
                                    setDraggingPositionAccountId(null)
                                    positionDragSnapshotRef.current = null
                                  }}
                                  className={draggingPositionId === pos.id ? 'opacity-60' : undefined}
                                >
                                  <TableCell>
                                    <span className={`text-[9px] px-1 py-0.5 rounded mr-1.5 ${badge.style}`}>{badge.label}</span>
                                    {pos.valuation_status === 'unsupported' && (
                                      <span
                                        className="chip-neutral text-[9px] mr-1.5"
                                        title="This market is not enabled on this server. The record is preserved; quotes and signals are not fetched for it."
                                      >
                                        Unsupported
                                      </span>
                                    )}
                                    {pos.valuation_status === 'unavailable' && (
                                      pos.current_price != null ? (
                                        <span
                                          className="text-[9px] text-muted-foreground/60 mr-1.5"
                                          title="No FX rate to USD is available for this market right now. The native price is shown; the position is excluded from USD totals until a rate is available."
                                        >
                                          No FX
                                        </span>
                                      ) : (
                                        <span
                                          className="text-[9px] text-muted-foreground/60 mr-1.5"
                                          title="Price temporarily unavailable. The position is preserved; it's excluded from P&L until a price is available again."
                                        >
                                          No price
                                        </span>
                                      )
                                    )}
                                    {pos.price_status === 'last_known' && pos.current_price != null && (
                                      <span
                                        className="text-[9px] text-muted-foreground/60 mr-1.5"
                                        title="The latest quote round returned no update for this position; its last-known price is shown. It counts toward priced totals but not toward a complete valuation."
                                      >
                                        Last known
                                      </span>
                                    )}
                                    {pos.fx_status === 'last_known' && pos.priced && (
                                      <span
                                        className="text-[9px] text-muted-foreground/60 mr-1.5"
                                        title="Converted with a last-known FX rate, not a fresh one."
                                      >
                                        FX last known
                                      </span>
                                    )}
                                    <span className="font-mono text-[12px] font-semibold text-foreground">
                                      {pos.symbol}
                                    </span>
                                    <button
                                      className="ml-1.5 text-[12px] text-muted-foreground hover:text-foreground"
                                      onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                    >
                                      {pos.name}
                                    </button>
                                    {(() => {
                                      const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market, true)
                                      return (suggestion || kline) ? (
                                        <span className="ml-2">
                                          <SuggestionBadge
                                            suggestion={suggestion}
                                            stockName={pos.name}
                                            stockSymbol={pos.symbol}
                                            kline={kline}
                                            market={pos.market}
                                            hasPosition={true}
                                          />
                                        </span>
                                      ) : null
                                    })()}
                                  </TableCell>
                                  <TableCell numeric className={`font-mono text-[12px] ${changeColor}`}>
                                    {pos.current_price != null ? <span>{pos.current_price.toFixed(2)}{isForeign ? ' CAD' : ''}</span> : '-'}
                                  </TableCell>
                                  <TableCell numeric className={`font-mono text-[12px] ${changeColor}`}>
                                    {pos.change_pct != null ? `${pos.change_pct >= 0 ? '+' : ''}${pos.change_pct.toFixed(2)}%` : '-'}
                                  </TableCell>
                                  <TableCell numeric className="font-mono text-[12px] text-muted-foreground">{formatPrice(pos.cost_price)}</TableCell>
                                  <TableCell numeric className="font-mono text-[12px] text-muted-foreground">{pos.quantity}</TableCell>
                                  <TableCell numeric className="font-mono text-[12px] text-muted-foreground">
                                    {pos.market_value != null ? (
                                      <div className="flex flex-col items-end">
                                        {isForeign ? (
                                          <>
                                            <span>{formatMoney(pos.market_value)} CAD</span>
                                            {pos.market_value_cny != null && <span className="text-[10px] text-muted-foreground/60">≈{formatMoney(pos.market_value_cny)} USD</span>}
                                          </>
                                        ) : <span>{formatMoney(pos.market_value)}</span>}
                                      </div>
                                    ) : '-'}
                                  </TableCell>
                                  <TableCell numeric className={`font-mono text-[12px] ${pnlColor}`}>
                                    {pos.pnl != null ? (
                                      <div className="flex flex-col items-end">
                                        <span>{pos.pnl >= 0 ? '+' : ''}{formatMoney(pos.pnl)}</span>
                                        <span className="text-[10px] opacity-70">{pos.pnl_pct != null ? `${pos.pnl_pct >= 0 ? '+' : ''}${pos.pnl_pct.toFixed(2)}%` : ''}{isForeign && ' USD'}</span>
                                      </div>
                                    ) : '-'}
                                  </TableCell>
                                  <TableCell numeric className={`font-mono text-[12px] ${pos.daily_pnl != null ? (pos.daily_pnl >= 0 ? 'text-stock-up' : 'text-stock-down') : ''}`}>
                                    {pos.daily_pnl != null ? (
                                      <div className="flex flex-col items-end">
                                        <span>{pos.daily_pnl >= 0 ? '+' : ''}{formatMoney(pos.daily_pnl)}</span>
                                        <span className="text-[10px] opacity-70">{pos.daily_pnl_pct != null ? `${pos.daily_pnl_pct >= 0 ? '+' : ''}${pos.daily_pnl_pct.toFixed(2)}%` : ''}</span>
                                      </div>
                                    ) : '-'}
                                  </TableCell>
                                  <TableCell className="text-center">
                                    {pos.trading_style ? (
                                      <span className="chip-neutral text-[10px]">
                                        {pos.trading_style === 'short' ? 'Short-term' : pos.trading_style === 'long' ? 'Long-term' : 'Swing'}
                                      </span>
                                    ) : (
                                      <span className="text-[10px] text-muted-foreground/50">-</span>
                                    )}
                                  </TableCell>
                                  <TableCell>
                                    {stock && (
                                      <button onClick={() => setAgentDialogStock(stock)} className="flex items-center gap-1.5 hover:opacity-70 transition-opacity">
                                        {stock.agents && stock.agents.length > 0 ? (
                                          <div className="flex items-center gap-1.5 flex-wrap">
                                            {stock.agents.map(sa => {
                                              const agent = agents.find(a => a.name === sa.agent_name)
                                              const isRunning = runningAgents[stock.id] === sa.agent_name
                                              return (
                                                <span key={sa.agent_name} className="inline-flex items-center gap-1">
                                                  <span className="chip-neutral text-[10px]">{agent?.display_name || sa.agent_name}</span>
                                                  {isRunning && (
                                                    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                                                      <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                                                      Running
                                                    </span>
                                                  )}
                                                </span>
                                              )
                                            })}
                                          </div>
                                        ) : (
                                          <span className="text-[11px] text-muted-foreground/50 flex items-center gap-1"><Bot className="w-3 h-3" /> Not configured</span>
                                        )}
                                      </button>
                                    )}
                                  </TableCell>
                                  <TableCell className="text-center">
                                    <div className="flex items-center justify-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                      {(() => { const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market, true); return (!suggestion && !kline) ? (
                                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openKlineDialog(pos.symbol, pos.market, pos.name, true)} title="Chart Indicators"><BarChart3 className="w-3 h-3" /></Button>
                                      ) : null })()}
                                      <StockPriceAlertPanel
                                        mode="icon"
                                        stockId={pos.stock_id}
                                        symbol={pos.symbol}
                                        market={pos.market}
                                        stockName={pos.name}
                                        initialTotal={getPriceAlertSummary(pos.symbol, pos.market).total}
                                        initialEnabled={getPriceAlertSummary(pos.symbol, pos.market).enabled}
                                        onChanged={loadPriceAlertSummaries}
                                      />
                                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openNewsDialog(pos.name)} title="Related News" aria-label="Related News"><Newspaper className="w-3 h-3" /></Button>
                                      <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-foreground" title="Deep Analysis (TradingAgents)" aria-label="Deep Analysis (TradingAgents)" onClick={() => openDeepAnalysis(pos.stock_id, pos.symbol, pos.name)}><Brain className="w-3 h-3" /></Button>
                                      <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openPositionDialog(account.id, pos)}><Pencil className="w-3 h-3" /></Button>
                                      <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => handleDeletePosition(pos.id)}><Trash2 className="w-3 h-3" /></Button>
                                    </div>
                                  </TableCell>
                                </TableRow>
                              )
                            })}
                          </TableBody>
                        </Table>
                      </TableWrap>

                      {/* Mobile Cards */}
                      <div className="md:hidden divide-y divide-border/30">
                        {account.positions.map(pos => {
                          const stock = stocks.find(s => s.id === pos.stock_id)
                          const badge = marketBadge(pos.market)
                          const changeColor = pos.change_pct != null
                            ? (pos.change_pct > 0 ? 'text-stock-up' : pos.change_pct < 0 ? 'text-stock-down' : 'text-muted-foreground')
                            : 'text-muted-foreground'
                          const pnlColor = pos.pnl != null
                            ? (pos.pnl > 0 ? 'text-stock-up' : pos.pnl < 0 ? 'text-stock-down' : 'text-muted-foreground')
                            : 'text-muted-foreground'
                          return (
                            <div
                              key={pos.id}
                              draggable
                              onDragStart={(e) => {
                                positionDragSnapshotRef.current = portfolioRaw ? JSON.parse(JSON.stringify(portfolioRaw)) : null
                                setDraggingPositionId(pos.id)
                                setDraggingPositionAccountId(account.id)
                                e.dataTransfer.effectAllowed = 'move'
                              }}
                              onDragOver={(e) => {
                                e.preventDefault()
                                e.dataTransfer.dropEffect = 'move'
                                if (draggingPositionId != null && draggingPositionAccountId === account.id) {
                                  previewPositionReorder(account.id, draggingPositionId, pos.id)
                                }
                              }}
                              onDrop={(e) => {
                                e.preventDefault()
                                if (draggingPositionId != null && draggingPositionAccountId === account.id) {
                                  commitPositionReorder(account.id)
                                }
                                setDraggingPositionId(null)
                                setDraggingPositionAccountId(null)
                                positionDragSnapshotRef.current = null
                              }}
                              onDragEnd={() => {
                                setDraggingPositionId(null)
                                setDraggingPositionAccountId(null)
                                positionDragSnapshotRef.current = null
                              }}
                              className={`p-3 row-hover ${draggingPositionId === pos.id ? 'opacity-60' : ''}`}
                            >
                              {/* Row 1: Stock info + Current price */}
                              <div className="flex items-center justify-between gap-2 mb-2">
                                <div className="flex items-center gap-1.5 min-w-0">
                                  <span className={`shrink-0 text-[9px] px-1 py-0.5 rounded ${badge.style}`}>{badge.label}</span>
                                  {pos.valuation_status === 'unsupported' && (
                                    <span
                                      className="shrink-0 chip-neutral text-[9px]"
                                      title="This market is not enabled on this server. The record is preserved; quotes and signals are not fetched for it."
                                    >
                                      Unsupported
                                    </span>
                                  )}
                                  {pos.valuation_status === 'unavailable' && (
                                    pos.current_price != null ? (
                                      <span
                                        className="shrink-0 text-[9px] text-muted-foreground/60"
                                        title="No FX rate to USD is available for this market right now. The native price is shown; the position is excluded from USD totals until a rate is available."
                                      >
                                        No FX
                                      </span>
                                    ) : (
                                      <span
                                        className="shrink-0 text-[9px] text-muted-foreground/60"
                                        title="Price temporarily unavailable. The position is preserved; it's excluded from P&L until a price is available again."
                                      >
                                        No price
                                      </span>
                                    )
                                  )}
                                  {pos.price_status === 'last_known' && pos.current_price != null && (
                                    <span
                                      className="shrink-0 text-[9px] text-muted-foreground/60"
                                      title="The latest quote round returned no update for this position; its last-known price is shown. It counts toward priced totals but not toward a complete valuation."
                                    >
                                      Last known
                                    </span>
                                  )}
                                  {pos.fx_status === 'last_known' && pos.priced && (
                                    <span
                                      className="shrink-0 text-[9px] text-muted-foreground/60"
                                      title="Converted with a last-known FX rate, not a fresh one."
                                    >
                                      FX last known
                                    </span>
                                  )}
                                  <span className="shrink-0 font-mono text-[12px] font-semibold text-foreground">
                                    {pos.symbol}
                                  </span>
                                  <button
                                    className="text-[12px] text-muted-foreground hover:text-foreground truncate"
                                    onClick={() => openStockDetail(pos.symbol, pos.market, pos.name, true)}
                                  >
                                    {pos.name}
                                  </button>
                                  {pos.trading_style && (
                                    <span className="shrink-0 chip-neutral text-[9px]">
                                      {pos.trading_style === 'short' ? 'S' : pos.trading_style === 'long' ? 'L' : 'Sw'}
                                    </span>
                                  )}
                                </div>
                                <div className={`font-mono text-[13px] font-medium whitespace-nowrap shrink-0 ${changeColor}`}>
                                  {pos.current_price != null ? pos.current_price.toFixed(2) : '-'}
                                  {/* Native currency must be named on mobile too: an unlabelled
                                      CAD price sitting beside USD rows reads as USD, and for a
                                      No-FX row there is no converted figure to disambiguate it. */}
                                  {pos.current_price != null && pos.market === 'CA' && <span className="text-[10px] ml-0.5 text-muted-foreground">CAD</span>}
                                  {pos.change_pct != null && <span className="text-[11px] ml-1">{pos.change_pct >= 0 ? '+' : ''}{pos.change_pct.toFixed(2)}%</span>}
                                </div>
                              </div>
                              {/* Row 2 (Suggestion badge, dedicated row to avoid wrapping mess) */}
                              {(() => {
                                const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market, true)
                                return (suggestion || kline) ? (
                                  <div className="mb-2">
                                    <SuggestionBadge
                                      suggestion={suggestion}
                                      stockName={pos.name}
                                      stockSymbol={pos.symbol}
                                      kline={kline}
                                      market={pos.market}
                                      hasPosition={true}
                                    />
                                  </div>
                                ) : null
                              })()}
                              {/* Row 3: Stats grid (4 cols, whitespace-nowrap to prevent "万" wrapping) */}
                              <div className="grid grid-cols-4 gap-2 text-[11px]">
                                <div className="min-w-0">
                                  <div className="text-[10px] text-muted-foreground">Cost</div>
                                  <div className="font-mono text-foreground truncate" title={String(pos.cost_price)}>{formatPrice(pos.cost_price)}</div>
                                </div>
                                <div className="min-w-0">
                                  <div className="text-[10px] text-muted-foreground">Qty</div>
                                  <div className="font-mono text-foreground truncate" title={String(pos.quantity)}>{pos.quantity}</div>
                                </div>
                                <div className="min-w-0">
                                  <div className="text-[10px] text-muted-foreground">P&L</div>
                                  <div className={`font-mono whitespace-nowrap ${pnlColor}`}>
                                    {pos.pnl != null ? `${pos.pnl >= 0 ? '+' : ''}${formatMoney(pos.pnl)}` : '-'}
                                  </div>
                                  {pos.pnl_pct != null && (
                                    <div className={`text-[10px] font-mono ${pnlColor} opacity-80`}>
                                      {pos.pnl_pct >= 0 ? '+' : ''}{pos.pnl_pct.toFixed(2)}%
                                    </div>
                                  )}
                                </div>
                                <div className="min-w-0">
                                  <div className="text-[10px] text-muted-foreground">Today</div>
                                  <div className={`font-mono whitespace-nowrap ${pos.daily_pnl != null ? (pos.daily_pnl >= 0 ? 'text-stock-up' : 'text-stock-down') : 'text-muted-foreground'}`}>
                                    {pos.daily_pnl != null ? `${pos.daily_pnl >= 0 ? '+' : ''}${formatMoney(pos.daily_pnl)}` : '-'}
                                  </div>
                                </div>
                              </div>
                              {/* Row 4: Actions */}
                              <div className="flex items-center justify-between mt-2 pt-2 border-t border-border/20">
                                <div>
                                  {stock && stock.agents && stock.agents.length > 0 ? (
                                    <button onClick={() => setAgentDialogStock(stock)} className="flex items-center gap-1">
                                      {stock.agents.slice(0, 2).map(sa => {
                                        const agent = agents.find(a => a.name === sa.agent_name)
                                        const isRunning = runningAgents[stock.id] === sa.agent_name
                                        return (
                                          <span key={sa.agent_name} className="inline-flex items-center gap-1">
                                            <span className="chip-neutral text-[9px]">{agent?.display_name || sa.agent_name}</span>
                                            {isRunning && (
                                              <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                                                <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                                                Running
                                              </span>
                                            )}
                                          </span>
                                        )
                                      })}
                                    </button>
                                  ) : (
                                    <button onClick={() => stock && setAgentDialogStock(stock)} className="text-[10px] text-muted-foreground/50 flex items-center gap-1">
                                      <Bot className="w-3 h-3" /> Agent
                                    </button>
                                  )}
                                </div>
                                <div className="flex items-center gap-1">
                                  {(() => { const { suggestion, kline } = getSuggestionForStock(pos.symbol, pos.market, true); return (!suggestion && !kline) ? (
                                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openKlineDialog(pos.symbol, pos.market, pos.name, true)} title="Chart Indicators"><BarChart3 className="w-3 h-3" /></Button>
                                  ) : null })()}
                                  <StockPriceAlertPanel
                                    mode="icon"
                                    stockId={pos.stock_id}
                                    symbol={pos.symbol}
                                    market={pos.market}
                                    stockName={pos.name}
                                    initialTotal={getPriceAlertSummary(pos.symbol, pos.market).total}
                                    initialEnabled={getPriceAlertSummary(pos.symbol, pos.market).enabled}
                                    onChanged={loadPriceAlertSummaries}
                                  />
                                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openNewsDialog(pos.name)}><Newspaper className="w-3 h-3" /></Button>
                                  <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-foreground" title="Deep Analysis (TradingAgents)" aria-label="Deep Analysis (TradingAgents)" onClick={() => openDeepAnalysis(pos.stock_id, pos.symbol, pos.name)}><Brain className="w-3 h-3" /></Button>
                                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openPositionDialog(account.id, pos)}><Pencil className="w-3 h-3" /></Button>
                                  <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => handleDeletePosition(pos.id)}><Trash2 className="w-3 h-3" /></Button>
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
        )
      )}

      {/* Watchlist */}
      {view === 'watchlist' && (() => {
        // Computed once so the desktop table and mobile row list (and the
        // empty vs. filtered-to-nothing check below) stay in sync.
        const filteredWatchStocks = stocks
          .filter(s => !stockListFilter || s.market === stockListFilter)
          .sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0) || a.id - b.id)
          .filter(stock => {
            if (!watchlistOnlyAlerts) return true
            const { suggestion } = getSuggestionForStock(stock.symbol, stock.market, false)
            return !!suggestion?.should_alert
          })
        const isFiltered = !!stockListFilter || watchlistOnlyAlerts
        return (
        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="section-title">Watchlist</h3>
            <div className="flex items-center gap-1">
              {[
                { value: '', label: 'All', count: stocks.length },
                ...ALL_MARKETS.map(m => ({ value: m, label: MARKET_LABEL[m], count: stocks.filter(s => s.market === m).length })),
              ].map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setStockListFilter(opt.value)}
                  className={`text-[11px] px-2 py-0.5 rounded transition-colors ${
                    stockListFilter === opt.value
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                  }`}
                >
                  {opt.label} ({opt.count})
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center justify-between mb-3">
            <div className="text-[11px] text-muted-foreground">Filter</div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setWatchlistOnlyAlerts(!watchlistOnlyAlerts)}
                className={`text-[11px] px-2.5 py-1 rounded-full border transition-colors duration-150 ${
                  watchlistOnlyAlerts
                    ? 'bg-primary border-primary text-primary-foreground font-semibold'
                    : 'bg-transparent border-border text-muted-foreground hover:bg-muted'
                }`}
                title="Only show stocks that need attention/have alerts"
              >
                Alerts only
              </button>
            </div>
          </div>
          {stocks.length === 0 ? (
            <EmptyState
              icon={Eye}
              title="No watchlist stocks yet"
              description="Add a stock to start tracking its price, technicals, and agent suggestions."
              action={<Button size="sm" onClick={() => { setStockForm(emptyStockForm); setSearchQuery(''); setShowStockForm(true) }}><Plus className="w-4 h-4" /> Add Stock</Button>}
              size="md"
            />
          ) : filteredWatchStocks.length === 0 ? (
            <EmptyState
              icon={Search}
              title="No matches"
              description="No watchlist stocks match the current filter."
              action={<Button size="sm" variant="secondary" onClick={() => { setStockListFilter(''); setWatchlistOnlyAlerts(false) }}>Clear filters</Button>}
              size="md"
            />
          ) : (
            <>
              {/* Desktop: dense table, same vocabulary as the Positions table above */}
              <TableWrap bordered={false} className="hidden md:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Stock</TableHead>
                      <TableHead numeric>Price</TableHead>
                      <TableHead numeric>Change</TableHead>
                      <TableHead>Agent</TableHead>
                      <TableHead className="text-center">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredWatchStocks.map((stock) => {
                      const quote = getStockQuote(`${stock.market}:${stock.symbol}`)
                      const changeColor = quote?.change_pct != null
                        ? (quote.change_pct > 0 ? 'text-stock-up' : quote.change_pct < 0 ? 'text-stock-down' : 'text-muted-foreground')
                        : 'text-muted-foreground'
                      const { suggestion, kline } = getSuggestionForStock(stock.symbol, stock.market, false)
                      return (
                        <TableRow
                          key={stock.id}
                          interactive
                          draggable={!isFiltered}
                          onDragStart={(e) => {
                            if (isFiltered) return
                            watchDragSnapshotRef.current = stocks
                            setDraggingWatchStockId(stock.id)
                            e.dataTransfer.effectAllowed = 'move'
                          }}
                          onDragOver={(e) => {
                            if (isFiltered) return
                            e.preventDefault()
                            e.dataTransfer.dropEffect = 'move'
                            if (draggingWatchStockId != null) {
                              previewWatchlistReorder(draggingWatchStockId, stock.id)
                            }
                          }}
                          onDrop={(e) => {
                            if (isFiltered) return
                            e.preventDefault()
                            if (draggingWatchStockId != null) commitWatchlistReorder()
                            setDraggingWatchStockId(null)
                            watchDragSnapshotRef.current = null
                          }}
                          onDragEnd={() => {
                            setDraggingWatchStockId(null)
                            watchDragSnapshotRef.current = null
                          }}
                          className={draggingWatchStockId === stock.id ? 'opacity-60' : undefined}
                          onClick={() => {
                            if (isSuppressCardClick()) return
                            setAgentDialogStock(stock)
                          }}
                        >
                          <TableCell>
                            <span className={`text-[9px] px-1 py-0.5 rounded mr-1.5 ${marketBadge(stock.market).style}`}>
                              {marketBadge(stock.market).label}
                            </span>
                            {!isSupportedMarket(stock.market) && (
                              <span
                                className="chip-neutral text-[9px] mr-1.5"
                                title="This market is not enabled on this server. The record is preserved; quotes and signals are not fetched for it."
                              >
                                Unsupported
                              </span>
                            )}
                            <button
                              className="font-mono text-[12px] font-semibold text-foreground hover:text-foreground"
                              onClick={(e) => { e.stopPropagation(); openStockDetail(stock.symbol, stock.market, stock.name, false) }}
                            >
                              {stock.symbol}
                            </button>
                            <button
                              className="ml-1.5 text-[12px] text-muted-foreground hover:text-foreground"
                              onClick={(e) => { e.stopPropagation(); openStockDetail(stock.symbol, stock.market, stock.name, false) }}
                            >
                              {stock.name}
                            </button>
                            {(suggestion || kline) && (
                              <span className="ml-2">
                                <SuggestionBadge
                                  suggestion={suggestion}
                                  stockName={stock.name}
                                  stockSymbol={stock.symbol}
                                  kline={kline}
                                  market={stock.market}
                                  hasPosition={false}
                                />
                              </span>
                            )}
                          </TableCell>
                          <TableCell numeric className={`font-mono text-[12px] ${changeColor}`}>
                            {quote?.current_price != null ? quote.current_price.toFixed(2) : '--'}
                          </TableCell>
                          <TableCell numeric className={`font-mono text-[12px] ${changeColor}`}>
                            {quote?.change_pct != null ? `${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%` : '--'}
                          </TableCell>
                          <TableCell>
                            {stock.agents && stock.agents.length > 0 ? (
                              <div className="flex items-center gap-1.5 flex-wrap">
                                <span className="chip-neutral text-[10px]">{stock.agents.length} Agent</span>
                                {runningAgents[stock.id] && (
                                  <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                                    <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                                    {agents.find(a => a.name === runningAgents[stock.id])?.display_name || runningAgents[stock.id]}
                                  </span>
                                )}
                              </div>
                            ) : (
                              <span className="text-[11px] text-muted-foreground/50 flex items-center gap-1"><Bot className="w-3 h-3" /> Not configured</span>
                            )}
                          </TableCell>
                          <TableCell className="text-center">
                            <div
                              className="flex items-center justify-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openKlineDialog(stock.symbol, stock.market, stock.name, false)} title="Chart Indicators"><BarChart3 className="w-3.5 h-3.5" /></Button>
                              <StockPriceAlertPanel
                                mode="icon"
                                stockId={stock.id}
                                symbol={stock.symbol}
                                market={stock.market}
                                stockName={stock.name}
                                initialTotal={getPriceAlertSummary(stock.symbol, stock.market).total}
                                initialEnabled={getPriceAlertSummary(stock.symbol, stock.market).enabled}
                                onChanged={loadPriceAlertSummaries}
                              />
                              <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openNewsDialog(stock.name)} title="Related News" aria-label="Related News"><Newspaper className="w-3.5 h-3.5" /></Button>
                              <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-foreground" title="Deep Analysis (TradingAgents)" aria-label="Deep Analysis (TradingAgents)" onClick={() => openDeepAnalysis(stock.id, stock.symbol, stock.name)}><Brain className="w-3.5 h-3.5" /></Button>
                              <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => setRemoveWatchStock(stock)} title="Delete Stock" aria-label="Delete Stock"><X className="w-3.5 h-3.5" /></Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </TableWrap>

              {/* Mobile: flat row list, same vocabulary as the Positions mobile list above (no per-item card) */}
              <div className="md:hidden divide-y divide-border/30 border border-border/30 rounded-lg">
                {filteredWatchStocks.map((stock) => {
                  const quote = getStockQuote(`${stock.market}:${stock.symbol}`)
                  const changeColor = quote?.change_pct != null
                    ? (quote.change_pct > 0 ? 'text-stock-up' : quote.change_pct < 0 ? 'text-stock-down' : 'text-muted-foreground')
                    : 'text-muted-foreground'
                  const { suggestion, kline } = getSuggestionForStock(stock.symbol, stock.market, false)
                  return (
                    <div
                      key={stock.id}
                      draggable={!isFiltered}
                      onDragStart={(e) => {
                        if (isFiltered) return
                        watchDragSnapshotRef.current = stocks
                        setDraggingWatchStockId(stock.id)
                        e.dataTransfer.effectAllowed = 'move'
                      }}
                      onDragOver={(e) => {
                        if (isFiltered) return
                        e.preventDefault()
                        e.dataTransfer.dropEffect = 'move'
                        if (draggingWatchStockId != null) {
                          previewWatchlistReorder(draggingWatchStockId, stock.id)
                        }
                      }}
                      onDrop={(e) => {
                        if (isFiltered) return
                        e.preventDefault()
                        if (draggingWatchStockId != null) commitWatchlistReorder()
                        setDraggingWatchStockId(null)
                        watchDragSnapshotRef.current = null
                      }}
                      onDragEnd={() => {
                        setDraggingWatchStockId(null)
                        watchDragSnapshotRef.current = null
                      }}
                      className={`p-3 row-interactive ${draggingWatchStockId === stock.id ? 'opacity-60' : ''}`}
                      onClick={() => {
                        if (isSuppressCardClick()) return
                        setAgentDialogStock(stock)
                      }}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span className={`shrink-0 text-[9px] px-1 py-0.5 rounded ${marketBadge(stock.market).style}`}>
                            {marketBadge(stock.market).label}
                          </span>
                          {!isSupportedMarket(stock.market) && (
                            <span
                              className="shrink-0 chip-neutral text-[9px]"
                              title="This market is not enabled on this server. The record is preserved; quotes and signals are not fetched for it."
                            >
                              Unsupported
                            </span>
                          )}
                          <span className="shrink-0 font-mono text-[12px] font-semibold text-foreground">{stock.symbol}</span>
                          <button
                            className="text-[12px] text-muted-foreground hover:text-foreground truncate"
                            onClick={(e) => { e.stopPropagation(); openStockDetail(stock.symbol, stock.market, stock.name, false) }}
                          >
                            {stock.name}
                          </button>
                        </div>
                        <div className={`font-mono text-[13px] font-medium whitespace-nowrap shrink-0 ${changeColor}`}>
                          {quote?.current_price != null ? quote.current_price.toFixed(2) : '--'}
                          {quote?.change_pct != null && <span className="text-[11px] ml-1">{quote.change_pct >= 0 ? '+' : ''}{quote.change_pct.toFixed(2)}%</span>}
                        </div>
                      </div>

                      {(suggestion || kline) && (
                        <div className="mt-2">
                          <SuggestionBadge
                            suggestion={suggestion}
                            stockName={stock.name}
                            stockSymbol={stock.symbol}
                            kline={kline}
                            market={stock.market}
                            hasPosition={false}
                          />
                        </div>
                      )}

                      <div className="mt-2 pt-2 border-t border-border/20 flex items-center justify-between gap-2">
                        <div className="flex items-center gap-1 flex-wrap min-w-0">
                          {stock.agents && stock.agents.length > 0 ? (
                            <span className="chip-neutral text-[10px]">{stock.agents.length} Agent</span>
                          ) : (
                            <span className="text-[10px] text-muted-foreground/60">No Agent configured</span>
                          )}
                          {runningAgents[stock.id] && (
                            <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                              <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                              {agents.find(a => a.name === runningAgents[stock.id])?.display_name || runningAgents[stock.id]}
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                          <StockPriceAlertPanel
                            mode="icon"
                            stockId={stock.id}
                            symbol={stock.symbol}
                            market={stock.market}
                            stockName={stock.name}
                            initialTotal={getPriceAlertSummary(stock.symbol, stock.market).total}
                            initialEnabled={getPriceAlertSummary(stock.symbol, stock.market).enabled}
                            onChanged={loadPriceAlertSummaries}
                          />
                          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openKlineDialog(stock.symbol, stock.market, stock.name, false)} title="Chart Indicators"><BarChart3 className="w-3.5 h-3.5" /></Button>
                          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openNewsDialog(stock.name)} title="Related News" aria-label="Related News"><Newspaper className="w-3.5 h-3.5" /></Button>
                          <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-foreground" title="Deep Analysis (TradingAgents)" aria-label="Deep Analysis (TradingAgents)" onClick={() => openDeepAnalysis(stock.id, stock.symbol, stock.name)}><Brain className="w-3.5 h-3.5" /></Button>
                          <Button variant="ghost" size="icon" className="h-7 w-7 hover:text-destructive" onClick={() => setRemoveWatchStock(stock)} title="Delete Stock" aria-label="Delete Stock"><X className="w-3.5 h-3.5" /></Button>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>
        )
      })()}

      {/* Kline Dialog */}
      <KlineSummaryDialog
        open={klineDialogOpen}
        onOpenChange={setKlineDialogOpen}
        symbol={klineDialogSymbol}
        market={klineDialogMarket}
        stockName={klineDialogName}
        hasPosition={klineDialogHasPosition}
        initialSummary={klineDialogInitialSummary as any}
      />

      <StockInsightModal
        open={insightOpen}
        onOpenChange={setInsightOpen}
        symbol={insightSymbol}
        market={insightMarket}
        stockName={insightName}
        hasPosition={insightHasPosition}
      />

      {/* TradingAgents deep analysis dialog */}
      {deepAnalysisTarget && (
        <DeepAnalysisModal
          open={!!deepAnalysisTarget}
          onOpenChange={(open) => { if (!open) setDeepAnalysisTarget(null) }}
          stockId={deepAnalysisTarget.stockId}
          stockSymbol={deepAnalysisTarget.symbol}
          stockName={deepAnalysisTarget.name}
        />
      )}

      {/* Remove Watchlist Dialog */}
      <Dialog open={!!removeWatchStock} onOpenChange={(open) => { if (!open) setRemoveWatchStock(null) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete Stock</DialogTitle>
            <DialogDescription>This will remove the stock and its watchlist config from the system</DialogDescription>
          </DialogHeader>
          {removeWatchStock && (
            <div className="space-y-4 mt-2">
              <div className="rounded-lg border border-border/40 bg-accent/20 p-3">
                <div className="text-[13px] font-semibold text-foreground">
                  {removeWatchStock.name}
                  <span className="ml-2 font-mono text-[12px] text-muted-foreground">{removeWatchStock.symbol}</span>
                </div>
                <div className="mt-1 text-[12px] text-muted-foreground">
                  {hasAnyPositionForStockId(removeWatchStock.id)
                    ? 'This stock has open positions and cannot be deleted directly. Delete the position records in the "Positions" tab first.'
                    : 'Once deleted, it will no longer appear in the watchlist, and its linked price alerts will be cleaned up.'}
                </div>
              </div>

              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setRemoveWatchStock(null)} disabled={removingWatchStock}>Cancel</Button>
                <Button
                  variant="destructive"
                  onClick={() => removeFromWatchlist(removeWatchStock)}
                  disabled={removingWatchStock || hasAnyPositionForStockId(removeWatchStock.id)}
                >
                  {hasAnyPositionForStockId(removeWatchStock.id) ? 'Delete positions first' : (removingWatchStock ? 'Processing…' : 'Delete Stock')}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Account Dialog */}
      <Dialog open={accountDialogOpen} onOpenChange={setAccountDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editAccountId ? 'Edit Account' : 'Add Account'}</DialogTitle>
            <DialogDescription>Set up trading account information</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>Account Name</Label>
              <Input
                value={accountForm.name}
                onChange={e => setAccountForm({ ...accountForm, name: e.target.value })}
                placeholder="e.g. Fidelity, Schwab"
              />
            </div>
            <div>
              <Label>Available Funds ($)</Label>
              <Input
                value={accountForm.available_funds}
                onChange={e => setAccountForm({ ...accountForm, available_funds: e.target.value })}
                placeholder="0"
                className="font-mono"
                inputMode="decimal"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setAccountDialogOpen(false)}>Cancel</Button>
              <Button onClick={handleAccountSubmit} disabled={!accountForm.name}>
                {editAccountId ? 'Save' : 'Create'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Position Dialog */}
      <Dialog
        open={positionDialogOpen}
        onOpenChange={(open) => {
          setPositionDialogOpen(open)
          if (!open) {
            setPositionSearchQuery('')
            setPositionSearchResults([])
            setShowPositionDropdown(false)
            setPositionSearchMarket('')
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editPositionId ? 'Edit Position' : 'Add Position'}</DialogTitle>
            <DialogDescription>
              Position in account: {accounts.find(a => a.id === positionDialogAccountId)?.name}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            {editPositionId ? (
              <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-accent/30">
                <span className={`text-[9px] px-1.5 py-0.5 rounded ${marketBadge(positionForm.stock_market).style}`}>
                  {marketBadge(positionForm.stock_market).label}
                </span>
                <span className="font-mono text-[12px] text-muted-foreground">{positionForm.stock_symbol}</span>
                <span className="text-[13px] text-foreground">{positionForm.stock_name}</span>
              </div>
            ) : (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Label className="mb-0">Search Stock</Label>
                  <div className="flex items-center gap-1">
                    {[
                      { value: '', label: 'All' },
                      ...EQUITY_MARKETS.map(m => ({ value: m, label: MARKET_LABEL[m] })),
                    ].map(opt => (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => handlePositionSearchMarketChange(opt.value)}
                        className={`text-[11px] px-2 py-0.5 rounded transition-colors ${
                          positionSearchMarket === opt.value
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="relative" ref={positionDropdownRef}>
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground/50" />
                  <Input
                    value={positionSearchQuery}
                    onChange={e => handlePositionSearchInput(e.target.value)}
                    onFocus={() => positionSearchResults.length > 0 && setShowPositionDropdown(true)}
                    placeholder={isMarket(positionSearchMarket) ? MARKET_SYMBOL_HINT[positionSearchMarket] : 'Ticker or name, e.g. AAPL or SHOP.TO (TSX)'}
                    className="pl-9"
                    autoComplete="off"
                  />
                  {positionSearching && <span className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 border-2 border-border border-t-foreground rounded-full animate-spin" />}
                  {showPositionDropdown && positionSearchResults.length > 0 && (
                    <div className="absolute z-50 w-full mt-1 max-h-48 overflow-auto scrollbar card">
                      {positionSearchResults.map(item => (
                        <button
                          key={`${item.market}-${item.symbol}`}
                          type="button"
                          onClick={() => selectPositionStock(item)}
                          className="w-full flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-accent/50 text-left transition-colors"
                        >
                          <span className={`text-[9px] px-1 py-0.5 rounded ${marketBadge(item.market).style}`}>
                            {marketBadge(item.market).label}
                          </span>
                          <span className="font-mono text-muted-foreground text-[12px]">{item.symbol}</span>
                          <span className="flex-1 text-foreground">{item.name}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                {positionForm.stock_symbol && (
                  <div className="mt-2 flex items-center gap-2">
                    <span className={`text-[9px] px-1.5 py-0.5 rounded ${marketBadge(positionForm.stock_market).style}`}>
                      {marketBadge(positionForm.stock_market).label}
                    </span>
                    <span className="font-mono text-[12px] text-muted-foreground">{positionForm.stock_symbol}</span>
                    <span className="text-[13px] text-foreground">{positionForm.stock_name}</span>
                    <button
                      type="button"
                      onClick={() => {
                        setPositionForm({ ...positionForm, stock_id: 0, stock_symbol: '', stock_name: '', stock_market: '' })
                        setPositionSearchQuery('')
                      }}
                      className="ml-1 text-muted-foreground hover:text-destructive"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}
              </div>
            )}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>Cost Price</Label>
                <Input
                  value={positionForm.cost_price}
                  onChange={e => setPositionForm({ ...positionForm, cost_price: e.target.value })}
                  placeholder="0.00"
                  className="font-mono"
                  inputMode="decimal"
                />
              </div>
              <div>
                <Label>Quantity</Label>
                <Input
                  value={positionForm.quantity}
                  onChange={e => setPositionForm({ ...positionForm, quantity: e.target.value })}
                  placeholder="0"
                  className="font-mono"
                  inputMode="numeric"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>Invested Amount <span className="text-muted-foreground/60 text-[11px]">(optional)</span></Label>
                <Input
                  value={positionForm.invested_amount}
                  onChange={e => setPositionForm({ ...positionForm, invested_amount: e.target.value })}
                  placeholder="Optional"
                  className="font-mono"
                  inputMode="decimal"
                />
              </div>
              <div>
                <Label>Trading Style <span className="text-muted-foreground font-normal">(optional)</span></Label>
                <Select
                  value={positionForm.trading_style}
                  onValueChange={val => setPositionForm({ ...positionForm, trading_style: val === '__none__' ? '' : val })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Not set" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">Not set</SelectItem>
                    <SelectItem value="short">Short-term (1-5 days)</SelectItem>
                    <SelectItem value="swing">Swing (1-4 weeks)</SelectItem>
                    <SelectItem value="long">Long-term (months)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setPositionDialogOpen(false)}>Cancel</Button>
              <Button
                onClick={handlePositionSubmit}
                disabled={!positionForm.cost_price || !positionForm.quantity || (!editPositionId && !positionForm.stock_id && !positionForm.stock_symbol)}
              >
                {editPositionId ? 'Save' : 'Add'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Agent Assignment Dialog */}
      <Dialog open={!!agentDialogStock} onOpenChange={open => !open && setAgentDialogStock(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Configure Monitoring Agents</DialogTitle>
            <DialogDescription>
              Choose which Agents to monitor {agentDialogStock?.name} ({agentDialogStock?.symbol}) with
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            {agents.length === 0 ? (
              <EmptyState
                icon={Bot}
                title="No agents available"
                description="Agents are configured on the Agents page."
                size="sm"
              />
            ) : (
              agents.map(agent => {
                const stockAgent = agentDialogStock?.agents?.find(a => a.agent_name === agent.name)
                const isAssigned = !!stockAgent
                const isBatchMode = agent.execution_mode === 'batch'
                return (
                  <div key={agent.name} className="rounded-xl bg-accent/30 hover:bg-accent/50 transition-colors overflow-hidden">
                    <div className="flex items-center justify-between p-3.5">
                      <div className="flex items-center gap-3">
                        <div className={`w-2 h-2 rounded-full ${agent.enabled ? 'bg-success' : 'bg-border'}`} />
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="text-[13px] font-medium text-foreground">{agent.display_name}</span>
                            <Badge variant="secondary" className="text-[9px]">
                              {isBatchMode ? 'Batch' : 'Per-stock'}
                            </Badge>
                          </div>
                          <p className="text-[11px] text-muted-foreground mt-0.5">{agent.description}</p>
                        </div>
                      </div>
                      <Switch
                        checked={isAssigned}
                        onCheckedChange={() => agentDialogStock && toggleAgent(agentDialogStock, agent.name)}
                        disabled={!agent.enabled}
                      />
                    </div>
                    {isAssigned && isBatchMode && (
                      <div className="px-3.5 pb-3.5 pt-0">
                        <p className="text-[11px] text-muted-foreground">
                          Schedule, AI model, and notification channels are configured on the <a href="/agents" className="text-foreground underline underline-offset-4 hover:no-underline">Agent Config</a> page
                        </p>
                      </div>
                    )}
                    {isAssigned && !isBatchMode && (
                      <div className="px-3.5 pb-3.5 pt-0 space-y-2.5">
                        {/* Schedule/Interval Select */}
                        <div className="flex items-center gap-2">
                          <Clock className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                          <Select
                            value={stockAgent?.schedule || '__default__'}
                            onValueChange={val => agentDialogStock && updateStockAgentSchedule(agentDialogStock, agent.name, val === '__default__' ? '' : val)}
                          >
                            <SelectTrigger className="h-7 text-[11px] w-auto min-w-[140px] px-2.5 bg-accent/50 border-border/50">
                              <SelectValue placeholder="Execution interval" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__default__">Follow global</SelectItem>
                              <SelectItem value="*/1 9-15 * * 1-5">Every 1 min</SelectItem>
                              <SelectItem value="*/3 9-15 * * 1-5">Every 3 min</SelectItem>
                              <SelectItem value="*/5 9-15 * * 1-5">Every 5 min</SelectItem>
                              <SelectItem value="*/10 9-15 * * 1-5">Every 10 min</SelectItem>
                              <SelectItem value="*/15 9-15 * * 1-5">Every 15 min</SelectItem>
                              <SelectItem value="*/30 9-15 * * 1-5">Every 30 min</SelectItem>
                            </SelectContent>
                          </Select>
                          <span className="text-[10px] text-muted-foreground">Trading hours</span>
                        </div>

                        {/* Schedule Preview */}
                        {(() => {
                          const eff = effectiveSchedule(agent, stockAgent)
                          const isFollowingGlobal = !(stockAgent?.schedule || '').trim() && !!(agent.schedule || '').trim()
                          const preview = eff ? schedulePreviewCache[eff] : null
                          const isLoading = eff ? !!schedulePreviewLoading[eff] : false
                          if (!eff) return null
                          return (
                            <div className="ml-[22px] rounded-lg border border-border/40 bg-background/30 px-2.5 py-2">
                              <div className="flex items-center justify-between">
                                <div className="text-[11px] text-muted-foreground">
                                  Upcoming trigger time preview{isFollowingGlobal ? <span className="ml-1 opacity-70">(following global)</span> : null}
                                </div>
                                {isLoading && (
                                  <span className="w-3 h-3 border-2 border-border border-t-muted-foreground rounded-full animate-spin" />
                                )}
                              </div>
                              {'error' in (preview || {}) ? (
                                <div className="mt-1 text-[11px] text-muted-foreground">{(preview as any).error}</div>
                              ) : (preview as SchedulePreview | undefined)?.next_runs?.length ? (
                                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                                  {(preview as SchedulePreview).next_runs.map((t, i) => (
                                    <span key={i} className="px-1.5 py-0.5 rounded border border-border/60 bg-accent/20 font-mono" title={t}>
                                      {formatPreviewTime(t, (preview as SchedulePreview).timezone)}
                                    </span>
                                  ))}
                                  {(preview as SchedulePreview).timezone ? (
                                    <span className="opacity-60">({(preview as SchedulePreview).timezone})</span>
                                  ) : null}
                                </div>
                              ) : (
                                <div className="mt-1 text-[11px] text-muted-foreground">-</div>
                              )}
                              <div className="mt-1 text-[10px] text-muted-foreground/70 font-mono">schedule: {eff}</div>
                            </div>
                          )
                        })()}

                        {/* AI Model Select */}
                        <div className="flex items-center gap-2">
                          <Cpu className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                          <Select
                            value={stockAgent?.ai_model_id?.toString() ?? '__default__'}
                            onValueChange={val => agentDialogStock && updateStockAgentModel(agentDialogStock, agent.name, val === '__default__' ? null : parseInt(val))}
                          >
                            <SelectTrigger className="h-7 text-[11px] w-auto min-w-[140px] px-2.5 bg-accent/50 border-border/50">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__default__">System default</SelectItem>
                              {services.map(svc => (
                                <SelectGroup key={svc.id}>
                                  <SelectLabel>{svc.name}</SelectLabel>
                                  {svc.models.map(m => (
                                    <SelectItem key={m.id} value={m.id.toString()}>
                                      {m.name}{m.name !== m.model ? ` (${m.model})` : ''}
                                    </SelectItem>
                                  ))}
                                </SelectGroup>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        {/* Notification Channels */}
                        {channels.length > 0 && (
                          <div className="flex items-center gap-2 flex-wrap">
                            <Bell className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                            {channels.map(ch => {
                              const isSelected = (stockAgent?.notify_channel_ids || []).includes(ch.id)
                              return (
                                <button
                                  key={ch.id}
                                  onClick={() => agentDialogStock && toggleStockAgentChannel(agentDialogStock, agent.name, ch.id)}
                                  className={`text-[10px] px-2 py-0.5 rounded-md border transition-colors ${
                                    isSelected
                                      ? 'bg-primary border-primary text-primary-foreground font-medium'
                                      : 'bg-accent/30 border-border/50 text-muted-foreground hover:border-primary/30'
                                  }`}
                                >
                                  {ch.name}
                                </button>
                              )
                            })}
                            {(stockAgent?.notify_channel_ids || []).length === 0 && (
                              <span className="text-[10px] text-muted-foreground">System default</span>
                            )}
                          </div>
                        )}
                        {/* Trigger Button */}
                        <div className="flex items-center gap-2 pt-1">
                          <Button
                            variant="secondary" size="sm" className="h-7 text-[11px] px-2.5"
                            disabled={triggeringAgent === agent.name}
                            onClick={() => agentDialogStock && triggerStockAgent(agentDialogStock.id, agent.name)}
                          >
                            {triggeringAgent === agent.name ? (
                              <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                            ) : (
                              <Play className="w-3 h-3" />
                            )}
                            Analyze Now
                          </Button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Agent analysis result dialog */}
      <Dialog open={!!agentResultDialog} onOpenChange={open => !open && setAgentResultDialog(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-base">{agentResultDialog?.title}</DialogTitle>
            <DialogDescription className="flex items-center gap-2 pt-1">
              {agentResultDialog?.should_alert ? (
                <Badge variant="default" className="text-[10px]">Recommends attention</Badge>
              ) : (
                <Badge variant="secondary" className="text-[10px]">No attention needed</Badge>
              )}
              {agentResultDialog?.notified && (
                <Badge variant="outline" className="text-[10px]">Notification sent</Badge>
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="mt-2 p-3 bg-accent/30 rounded-lg">
            <pre className="text-[13px] whitespace-pre-wrap font-sans leading-relaxed">
              {agentResultDialog?.content}
            </pre>
          </div>
          <div className="flex justify-end mt-2">
            <Button variant="outline" size="sm" onClick={() => setAgentResultDialog(null)}>
              Close
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Related News dialog */}
      <Dialog open={newsDialogOpen} onOpenChange={setNewsDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Newspaper className="w-5 h-5 text-muted-foreground" />
              Related News
            </DialogTitle>
            <DialogDescription>
              {newsDialogSymbol
                ? `News and announcements related to ${newsDialogSymbol}`
                : 'News and announcements for your watchlist (last 7 days)'
              }
            </DialogDescription>
          </DialogHeader>

          {/* Stock filter */}
          <div className="flex items-center gap-2 flex-wrap py-2 border-b">
            <span className="text-[12px] text-muted-foreground">Filter:</span>
            <button
              onClick={() => { setNewsDialogSymbol(''); loadNews() }}
              className={`text-[11px] px-2.5 py-1 rounded-md transition-colors ${
                !newsDialogSymbol
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-accent/50 text-muted-foreground hover:bg-accent'
              }`}
            >
              All
            </button>
            {stocks.slice(0, 10).map(stock => (
              <button
                key={stock.symbol}
                onClick={() => { setNewsDialogSymbol(stock.name); loadNews(stock.name) }}
                className={`text-[11px] px-2.5 py-1 rounded-md transition-colors ${
                  newsDialogSymbol === stock.name
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                }`}
              >
                {stock.name}
              </button>
            ))}
            {stocks.length > 10 && (
              <span className="text-[10px] text-muted-foreground">+{stocks.length - 10}</span>
            )}
          </div>

          {/* News list */}
          <div className="flex-1 overflow-y-auto min-h-0 py-2">
            {newsLoading ? (
              <div className="flex items-center justify-center py-12">
                <span className="w-5 h-5 border-2 border-border border-t-foreground rounded-full animate-spin" />
                <span className="ml-2 text-[13px] text-muted-foreground">Loading...</span>
              </div>
            ) : news.length === 0 ? (
              <EmptyState
                icon={Newspaper}
                title={newsDialogSymbol ? 'No news for this symbol' : 'No related news'}
                description={newsDialogSymbol ? 'Try "All" or a different symbol filter.' : 'Check back later, or try a symbol filter above.'}
                action={newsDialogSymbol ? <Button size="sm" variant="secondary" onClick={() => { setNewsDialogSymbol(''); loadNews() }}>Clear filter</Button> : undefined}
                size="sm"
              />
            ) : (
              <div className="space-y-2">
                {news.map((item, idx) => (
                  <div
                    key={`${item.source}-${item.external_id}-${idx}`}
                    className="p-3 rounded-lg bg-accent/30 hover:bg-accent/50 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className="chip-neutral text-[10px]">
                            {item.source_label}
                          </span>
                          {item.importance >= 2 && (
                            <span className="chip-neutral text-[10px] font-bold uppercase tracking-[0.04em]">
                              Important
                            </span>
                          )}
                          <span className="text-[10px] text-muted-foreground">
                            {item.publish_time}
                          </span>
                        </div>
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[13px] font-medium text-foreground hover:text-foreground transition-colors block"
                        >
                          {item.title}
                        </a>
                        {item.symbols.length > 0 && (
                          <div className="flex items-center gap-1.5 mt-2">
                            {item.symbols.slice(0, 5).map(sym => {
                              const stockInfo = stocks.find(s => s.symbol === sym)
                              const stockName = stockInfo?.name || sym
                              return (
                                <button
                                  key={sym}
                                  onClick={() => { setNewsDialogSymbol(stockName); loadNews(stockName) }}
                                  className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-foreground font-mono hover:bg-accent transition-colors"
                                >
                                  {stockName}
                                </button>
                              )
                            })}
                            {item.symbols.length > 5 && (
                              <span className="text-[10px] text-muted-foreground">+{item.symbols.length - 5}</span>
                            )}
                          </div>
                        )}
                      </div>
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex-shrink-0 p-1.5 rounded-md hover:bg-accent transition-colors"
                        title="View original"
                      >
                        <ExternalLink className="w-4 h-4 text-muted-foreground" />
                      </a>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Bottom refresh button */}
          <div className="flex items-center justify-between pt-2 border-t">
            <span className="text-[11px] text-muted-foreground">
              {news.length} news item(s)
            </span>
            <Button variant="secondary" size="sm" onClick={() => loadNews(newsDialogSymbol || undefined)} disabled={newsLoading}>
              <RefreshCw className={`w-3 h-3 ${newsLoading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
