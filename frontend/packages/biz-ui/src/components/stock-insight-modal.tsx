import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Copy, Download, ExternalLink, RefreshCw, Share2, Sparkles } from 'lucide-react'
import {
  insightApi,
  stocksApi,
  tradingAgentsApi,
  type DeepAnalysisResult,
  type FilingItem,
  type HistoryComparisonResponse,
  type HolderItem,
  type HoldersResponse,
} from '@panwatch/api'
import { getMarketBadge } from '@panwatch/biz-ui'
import { DEFAULT_MARKET } from '@panwatch/api/markets'
import { useLocalStorage } from '@/lib/utils'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { SuggestionBadge, type KlineSummary, type SuggestionInfo } from '@panwatch/biz-ui/components/suggestion-badge'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import InteractiveKline from '@panwatch/biz-ui/components/InteractiveKline'
import { KlineIndicators } from '@panwatch/biz-ui/components/kline-indicators'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import StockPriceAlertPanel from '@panwatch/biz-ui/components/stock-price-alert-panel'
import { TechnicalBadge } from '@panwatch/biz-ui/components/technical-badge'
import AddPositionCalculator from '@panwatch/biz-ui/components/add-position-calculator'

interface QuoteResponse {
  symbol: string
  market: string
  name: string | null
  current_price: number | null
  change_pct: number | null
  change_amount: number | null
  prev_close: number | null
  open_price: number | null
  high_price: number | null
  low_price: number | null
  volume: number | null
  turnover: number | null
  turnover_rate?: number | null
  pe_ratio?: number | null
  total_market_value?: number | null
  circulating_market_value?: number | null
}

interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: KlineSummary
}

interface MiniKlineResponse {
  symbol: string
  market: string
  klines: Array<{
    date: string
    open: number
    close: number
    high: number
    low: number
    volume: number
  }>
}

interface NewsItem {
  source: string
  source_label: string
  title: string
  content?: string
  publish_time: string
  url: string
  symbols?: string[]
}

interface HistoryRecord {
  id: number
  agent_name: string
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  suggestions?: Record<string, any> | null
  news?: Array<{
    source?: string
    title?: string
    publish_time?: string
    url?: string
  }> | null
  quality_overview?: Record<string, any> | null
  context_summary?: Record<string, any> | null
  context_payload?: Record<string, any> | null
  prompt_context?: string | null
  prompt_stats?: Record<string, any> | null
  news_debug?: Record<string, any> | null
  created_at: string
  updated_at?: string
}

interface PortfolioPosition {
  symbol: string
  market: string
  quantity: number
  cost_price: number
  market_value_cny: number | null
  pnl: number | null
}

interface PortfolioSummaryResponse {
  accounts: Array<{
    positions: PortfolioPosition[]
  }>
}

type InsightTab = 'overview' | 'kline' | 'suggestions' | 'news' | 'filings' | 'holders' | 'reports' | 'deep'

// Form-type chips on the Filings tab. `match` runs against FilingItem.form_type
// (SEC form codes, or "press_release" for Canadian newswire items).
const FILING_FORM_CHIPS: { id: string; label: string; match: (formType: string) => boolean }[] = [
  { id: 'all', label: 'All', match: () => true },
  { id: '8-K', label: '8-K', match: f => f === '8-K' || f.startsWith('8-K/') },
  { id: '10-Q', label: '10-Q', match: f => f === '10-Q' || f.startsWith('10-Q/') },
  { id: '10-K', label: '10-K', match: f => f === '10-K' || f.startsWith('10-K/') },
  { id: '4', label: 'Form 4', match: f => f === '4' || f.startsWith('4/') },
  { id: 'press_release', label: 'Press releases', match: f => f === 'press_release' },
]

const INSIGHT_SOURCE_LABELS: Record<string, string> = {
  sec_edgar: 'SEC EDGAR',
  yfinance: 'Yahoo Finance',
  yfinance_newswire: 'Newswire',
}

function insightSourceLabel(source?: string): string {
  if (!source) return ''
  return INSIGHT_SOURCE_LABELS[source] || source
}

function filingFormLabel(formType?: string): string {
  if (!formType) return 'Filing'
  return formType === 'press_release' ? 'Press release' : formType
}

// yfinance `major_holders` index keys -> display labels; anything else is shown as-is.
const HOLDER_BREAKDOWN_LABELS: Record<string, string> = {
  insidersPercentHeld: 'Insiders',
  institutionsPercentHeld: 'Institutions',
  institutionsFloatPercentHeld: 'Institutions (of float)',
  institutionsCount: 'Institution count',
}

function holderBreakdownLabel(holder: string): string {
  return HOLDER_BREAKDOWN_LABELS[holder] || holder
}

function holderBreakdownValue(row: HolderItem): string {
  if (row.pct_out != null) return `${Number(row.pct_out).toFixed(2)}%`
  if (row.shares != null) return Number(row.shares).toLocaleString()
  return '--'
}

function insiderTransactionClass(transaction?: string): string {
  const t = (transaction || '').toLowerCase()
  if (t.startsWith('purchase') || t.startsWith('buy')) return 'text-stock-up'
  if (t.startsWith('sale') || t.startsWith('sold') || t.startsWith('sell')) return 'text-stock-down'
  return ''
}

interface StockAgentInfo {
  agent_name: string
  schedule?: string
  ai_model_id?: number | null
  notify_channel_ids?: number[]
}

interface StockItem {
  id: number
  symbol: string
  name: string
  market: string
  agents?: StockAgentInfo[]
}

const AGENT_LABELS: Record<string, string> = {
  daily_report: 'Post-market Report',
  premarket_outlook: 'Pre-market Outlook',
  news_digest: 'News Digest',
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value == null) return '--'
  return value.toFixed(digits)
}

function formatCompactNumber(value: number | null | undefined): string {
  if (value == null) return '--'
  const n = Number(value)
  if (!isFinite(n)) return '--'
  const abs = Math.abs(n)
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${(n / 1e3).toFixed(2)}K`
  return n.toFixed(0)
}

function formatMarketCap(value: number | null | undefined): string {
  if (value == null) return '--'
  const n = Number(value)
  if (!isFinite(n)) return '--'
  const abs = Math.abs(n)

  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  return `${n.toFixed(0)}`
}

function formatTime(isoTime?: string): string {
  if (!isoTime) return ''
  const d = new Date(isoTime)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function parseToMs(input?: string): number | null {
  if (!input) return null
  const d = new Date(input)
  if (!isNaN(d.getTime())) return d.getTime()
  const m = input.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return null
  const dt = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 0, 0, 0)
  return isNaN(dt.getTime()) ? null : dt.getTime()
}

function parseSuggestionJson(raw: unknown): Record<string, any> | null {
  if (typeof raw !== 'string') return null
  const s = raw.trim()
  if (!s) return null
  const candidates: string[] = [s]
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fence?.[1]) candidates.unshift(fence[1].trim())
  if (/^json\s*[\r\n]/i.test(s)) candidates.unshift(s.replace(/^json\s*[\r\n]/i, '').trim())
  for (const c of candidates) {
    if (!c) continue
    const direct = c
    const sliceStart = c.indexOf('{')
    const sliceEnd = c.lastIndexOf('}')
    const sliced = sliceStart >= 0 && sliceEnd > sliceStart ? c.slice(sliceStart, sliceEnd + 1) : ''
    for (const text of [direct, sliced]) {
      if (!text || !text.startsWith('{') || !text.endsWith('}')) continue
      try {
        const obj = JSON.parse(text)
        if (obj && typeof obj === 'object') return obj as Record<string, any>
      } catch {
        // try next candidate
      }
    }
  }
  return null
}

function normalizeSuggestionAction(action?: string, actionLabel?: string): string {
  const a = String(action || '').trim().toLowerCase()
  const l = String(actionLabel || '').trim()
  if (a === 'buy/add' || a === 'add/buy') return /add|increase/i.test(l) ? 'add' : 'buy'
  if (a === 'sell/reduce' || a === 'reduce/sell') return /reduce|decrease/i.test(l) ? 'reduce' : 'sell'
  return a || 'watch'
}

function pickSuggestionText(raw: unknown, field: 'signal' | 'reason'): string {
  const plain = String(raw || '').trim()
  const obj = parseSuggestionJson(plain)
  if (obj) {
    const v = String(obj[field] || '').trim()
    if (v) return v
    if (field === 'reason') {
      const rv = String(obj['raw'] || '').trim()
      if (rv) return rv
    }
    return ''
  }
  return plain
}

function normalizeTextList(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.map(x => String(x || '').trim()).filter(Boolean)
  const s = String(raw || '').trim()
  if (!s) return []
  const bySep = s.split(/[；;、|]/).map(x => x.trim()).filter(Boolean)
  return bySep.length > 1 ? bySep : [s]
}

function markdownToPlainText(input?: string): string {
  const raw = String(input || '').trim()
  if (!raw) return ''
  return raw
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*>\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/\*\*|__|\*|_/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function firstNonEmptyText(...vals: unknown[]): string {
  for (const v of vals) {
    const s = String(v || '').trim()
    if (s) return s
  }
  return ''
}

function buildShareTechnicalRisks(kline: KlineSummary | null): string[] {
  if (!kline) return []
  const out: string[] = []
  const rsi = String(kline.rsi_status || '')
  const macd = `${kline.macd_cross || ''} ${kline.macd_status || ''}`
  const vol = String(kline.volume_trend || '')
  if (rsi.includes('Overbought')) out.push('Short-term overheating pullback risk')
  if (rsi.includes('Oversold')) out.push('Risk of continued weakness')
  if (macd.includes('Death Cross')) out.push('Risk of weakening trend')
  if (macd.includes('Top Divergence')) out.push('Momentum divergence risk')
  if (vol.includes('High Volume')) out.push('Risk of widening volatility')
  return out.slice(0, 3)
}

function TechnicalIndicatorStrip(props: {
  klineSummary: KlineSummary | null
  technicalSuggestion: SuggestionInfo | null
  stockName: string
  stockSymbol: string
  market: string
  hasPosition: boolean
  score?: number
  evidence?: Array<{ text: string; delta: number }>
}) {
  const { klineSummary, technicalSuggestion, stockName, stockSymbol, market, hasPosition, score, evidence = [] } = props
  if (!klineSummary) {
    return <div className="text-[12px] text-muted-foreground py-3">No technical indicators yet</div>
  }
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[12px] text-muted-foreground">Technical Indicator Recommendation</span>
        <SuggestionBadge
          suggestion={technicalSuggestion}
          stockName={stockName}
          stockSymbol={stockSymbol}
          market={market}
          kline={klineSummary}
          hasPosition={hasPosition}
        />
        <TechnicalBadge label={`Score ${Number(score ?? 0).toFixed(1)}`} tone="neutral" size="xs" className="text-foreground" />
      </div>
      {evidence.length > 0 && (
        <div className="flex flex-wrap gap-1.5 text-[10px]">
          {evidence.slice(0, 6).map((item, idx) => (
            <TechnicalBadge
              key={`${item.text}-${idx}`}
              label={`${item.text} ${item.delta > 0 ? `+${item.delta}` : item.delta}`}
              tone={item.delta > 0 ? 'bullish' : item.delta < 0 ? 'bearish' : 'neutral'}
              size="xs"
            />
          ))}
        </div>
      )}
      <KlineIndicators summary={klineSummary as any} />
    </div>
  )
}

export default function StockInsightModal(props: {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  stockName?: string
  hasPosition?: boolean
}) {
  const { toast } = useToast()
  const symbol = String(props.symbol || '').trim()
  const market = String(props.market || DEFAULT_MARKET).trim().toUpperCase()
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState<InsightTab>('overview')
  const [newsHours, setNewsHours] = useLocalStorage<string>('stock_insight_news_hours', '168')
  const [filingForm, setFilingForm] = useState<string>('all')
  const [includeExpiredSuggestions, setIncludeExpiredSuggestions] = useLocalStorage<boolean>(
    'stock_insight_include_expired_suggestions',
    true
  )
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useLocalStorage<boolean>(
    'stock_insight_auto_refresh_enabled',
    true
  )
  const [autoRefreshSec, setAutoRefreshSec] = useLocalStorage<number>(
    'stock_insight_auto_refresh_sec',
    20
  )
  const [quote, setQuote] = useState<QuoteResponse | null>(null)
  const [klineSummary, setKlineSummary] = useState<KlineSummary | null>(null)
  const [miniKlines, setMiniKlines] = useState<MiniKlineResponse['klines']>([])
  const [miniKlineLoading, setMiniKlineLoading] = useState(false)
  const [miniHoverIdx, setMiniHoverIdx] = useState<number | null>(null)
  const [suggestions, setSuggestions] = useState<SuggestionInfo[]>([])
  const [news, setNews] = useState<NewsItem[]>([])
  const [filings, setFilings] = useState<FilingItem[]>([])
  const [filingsNote, setFilingsNote] = useState<string>('')
  const [holders, setHolders] = useState<HoldersResponse | null>(null)
  const [holdersLoaded, setHoldersLoaded] = useState(false)
  const [reports, setReports] = useState<HistoryRecord[]>([])
  const [reportTab, setReportTab] = useState<'premarket_outlook' | 'daily_report' | 'news_digest'>('premarket_outlook')
  const [deepResult, setDeepResult] = useState<DeepAnalysisResult | null>(null)
  const [deepLoading, setDeepLoading] = useState(false)
  const [deepLoaded, setDeepLoaded] = useState(false)
  const [deepShowAnalyst, setDeepShowAnalyst] = useState(false)
  const [deepShowDebate, setDeepShowDebate] = useState(false)
  const [deepHistory, setDeepHistory] = useState<HistoryComparisonResponse | null>(null)
  const [deepHistoryLoading, setDeepHistoryLoading] = useState(false)
  const [klineInterval] = useState<'1d' | '1w' | '1m'>('1d')
  const [alerting, setAlerting] = useState(false)
  const [watchingStock, setWatchingStock] = useState<StockItem | null>(null)
  const [watchToggleLoading, setWatchToggleLoading] = useState(false)
  const [autoSuggesting, setAutoSuggesting] = useState(false)
  const [imageExporting, setImageExporting] = useState(false)
  const [holdingAgg, setHoldingAgg] = useState<{
    quantity: number
    cost: number
    unitCost: number
    marketValue: number
    pnl: number
  } | null>(null)
  const [holdingLoaded, setHoldingLoaded] = useState(false)
  const [holdingLoadError, setHoldingLoadError] = useState(false)
  const autoTriggeredRef = useRef<Record<string, number>>({})
  const stockCacheRef = useRef<Record<string, StockItem>>({})
  const resolvedName = useMemo(() => props.stockName || quote?.name || symbol, [props.stockName, quote?.name, symbol])

  const loadQuote = useCallback(async () => {
    if (!symbol) return
    const data = await insightApi.quote<QuoteResponse>(symbol, market)
    setQuote(data || null)
  }, [symbol, market])

  const loadKline = useCallback(async () => {
    if (!symbol) return
    const data = await insightApi.klineSummary<KlineSummaryResponse>(symbol, market)
    setKlineSummary(data?.summary || null)
  }, [symbol, market])

  const loadMiniKline = useCallback(async (opts?: { silent?: boolean }) => {
    if (!symbol) return
    const silent = !!opts?.silent
    if (!silent) setMiniKlineLoading(true)
    try {
      const data = await insightApi.klines<MiniKlineResponse>(symbol, {
        market,
        days: 36,
        interval: '1d',
      })
      setMiniKlines((data?.klines || []).slice(-30))
    } catch {
      setMiniKlines([])
    } finally {
      if (!silent) setMiniKlineLoading(false)
    }
  }, [symbol, market])

  const loadSuggestions = useCallback(async () => {
    if (!symbol) return
    const data = await insightApi.suggestions<any[]>(symbol, {
      market,
      limit: 20,
      include_expired: includeExpiredSuggestions,
    })
    const list = (data || []).map(item => ({
      id: item.id,
      action: normalizeSuggestionAction(item.action, item.action_label),
      action_label: item.action_label || '',
      signal: pickSuggestionText(item.signal, 'signal'),
      reason: pickSuggestionText(item.reason, 'reason'),
      should_alert: !!item.should_alert,
      agent_name: item.agent_name,
      agent_label: item.agent_label,
      created_at: item.created_at,
      is_expired: item.is_expired,
      prompt_context: item.prompt_context,
      ai_response: item.ai_response,
      raw: item.raw || '',
      meta: item.meta,
    })) as SuggestionInfo[]
    setSuggestions(list)
  }, [symbol, market, includeExpiredSuggestions])

  const loadNews = useCallback(async () => {
    if (!symbol) return
    const runQuery = async (opts: { useName: boolean; filterRelated: boolean }) => {
      const params = new URLSearchParams()
      params.set('hours', newsHours)
      params.set('limit', '50')
      if (!opts.filterRelated) params.set('filter_related', 'false')
      if (opts.useName && resolvedName && resolvedName !== symbol) params.set('names', resolvedName)
      else params.set('symbols', symbol)
      return insightApi.news<NewsItem[]>(Object.fromEntries(params.entries()))
    }

    try {
      let data: NewsItem[] = await runQuery({ useName: true, filterRelated: true })
      if ((data || []).length === 0 && resolvedName && resolvedName !== symbol) {
        data = await runQuery({ useName: false, filterRelated: true })
      }
      if ((data || []).length === 0) {
        data = await runQuery({ useName: true, filterRelated: false })
      }
      if ((data || []).length === 0) {
        data = await runQuery({ useName: false, filterRelated: false })
      }
      if ((data || []).length === 0) {
        const global = await insightApi.news<NewsItem[]>({
          hours: newsHours,
          limit: 80,
        }).catch(() => [])
        const upperSymbol = symbol.toUpperCase()
        const name = (resolvedName || '').trim()
        data = (global || []).filter((n) => {
          const text = `${n.title || ''} ${n.content || ''}`.toUpperCase()
          if (upperSymbol && text.includes(upperSymbol)) return true
          if (name && `${n.title || ''} ${n.content || ''}`.includes(name)) return true
          return (n.symbols || []).map(x => String(x).toUpperCase()).includes(upperSymbol)
        })
      }
      // 兜底：实时新闻为空时，回退到 news_digest 历史快照中的新闻列表
      if ((data || []).length === 0) {
        const bySymbol = await insightApi.history<HistoryRecord[]>({
          agent_name: 'news_digest',
          stock_symbol: symbol,
          limit: 1,
        }).catch(() => [])
        let rec: HistoryRecord | null = (bySymbol || [])[0] || null
        if (!rec) {
          const globals = await insightApi.history<HistoryRecord[]>({
            agent_name: 'news_digest',
            stock_symbol: '*',
            limit: 20,
          }).catch(() => [])
          const upperSymbol = symbol.toUpperCase()
          const name = (resolvedName || '').trim()
          rec = (globals || []).find((r) => {
            const sug = r?.suggestions || {}
            const keys = Object.keys(sug || {})
            if (keys.includes(symbol) || keys.map(k => k.toUpperCase()).includes(upperSymbol)) return true
            const text = `${r?.title || ''}\n${r?.content || ''}`.toUpperCase()
            if (upperSymbol && text.includes(upperSymbol)) return true
            if (name && `${r?.title || ''}\n${r?.content || ''}`.includes(name)) return true
            return false
          }) || null
        }
        if (rec?.news && Array.isArray(rec.news)) {
          data = rec.news
            .map((n) => ({
              source: n.source || 'news_digest',
              source_label: n.source || 'news_digest',
              title: n.title || '',
              publish_time: n.publish_time || rec?.analysis_date || '',
              url: n.url || '',
            }))
            .filter((n) => !!n.title)
        }
      }
      setNews(data || [])
    } catch {
      setNews([])
    }
  }, [symbol, newsHours, resolvedName])

  const loadFilings = useCallback(async () => {
    if (!symbol) return
    try {
      const data = await insightApi.filings({ symbol, market, limit: 50 })
      setFilings(data?.items || [])
      setFilingsNote(data?.note || '')
    } catch {
      setFilings([])
      setFilingsNote('')
    }
  }, [symbol, market])

  const loadHolders = useCallback(async () => {
    if (!symbol) return
    try {
      const data = await insightApi.holders({ symbol, market })
      setHolders(data || null)
    } catch {
      setHolders(null)
    } finally {
      setHoldersLoaded(true)
    }
  }, [symbol, market])

  const loadHoldingAgg = useCallback(async () => {
    if (!symbol) return
    setHoldingLoaded(false)
    setHoldingLoadError(false)
    try {
      const data = await insightApi.portfolioSummary<PortfolioSummaryResponse>({ include_quotes: true })
      let quantity = 0
      let cost = 0
      let marketValue = 0
      let pnl = 0
      for (const acc of data?.accounts || []) {
        for (const p of acc.positions || []) {
          if (p.symbol !== symbol || p.market !== market) continue
          quantity += Number(p.quantity || 0)
          cost += Number(p.cost_price || 0) * Number(p.quantity || 0)
          marketValue += Number(p.market_value_cny || 0)
          pnl += Number(p.pnl || 0)
        }
      }
      if (quantity > 0) setHoldingAgg({ quantity, cost, unitCost: cost / quantity, marketValue, pnl })
      else setHoldingAgg(null)
    } catch {
      setHoldingAgg(null)
      setHoldingLoadError(true)
    } finally {
      setHoldingLoaded(true)
    }
  }, [symbol, market])

  const loadReports = useCallback(async () => {
    if (!symbol) return
    try {
      const agents = ['premarket_outlook', 'daily_report', 'news_digest']
      const bySymbolResults = await Promise.all(
        agents.map(agent =>
          insightApi.history<HistoryRecord[]>({
            agent_name: agent,
            stock_symbol: symbol,
            limit: 1,
          }).catch(() => [])
        )
      )
      let merged = bySymbolResults
        .flatMap(items => items || [])
        .filter(Boolean)
      // 兼容全局记录（stock_symbol="*"）场景：从最近全局记录中筛选与当前股票相关的报告。
      if (merged.length === 0) {
        const globalResults = await Promise.all(
          agents.map(agent =>
            insightApi.history<HistoryRecord[]>({
              agent_name: agent,
              stock_symbol: '*',
              limit: 20,
            }).catch(() => [])
          )
        )
        const upperSymbol = symbol.toUpperCase()
        const name = (resolvedName || '').trim()
        merged = globalResults
          .map(items => {
            const rows = (items || []).filter(Boolean)
            const hit = rows.find((r) => {
              const sug = r?.suggestions || {}
              const keys = Object.keys(sug || {})
              if (keys.includes(symbol) || keys.map(k => k.toUpperCase()).includes(upperSymbol)) return true
              const text = `${r?.title || ''}\n${r?.content || ''}`.toUpperCase()
              if (upperSymbol && text.includes(upperSymbol)) return true
              if (name && `${r?.title || ''}\n${r?.content || ''}`.includes(name)) return true
              return false
            })
            return hit || null
          })
          .filter(Boolean) as HistoryRecord[]
      }
      merged = merged.sort((a, b) => {
        const am = parseToMs(a.updated_at || a.created_at || a.analysis_date) || 0
        const bm = parseToMs(b.updated_at || b.created_at || b.analysis_date) || 0
        return bm - am
      })
      setReports(merged)
    } catch {
      setReports([])
    }
  }, [symbol, resolvedName])

  const loadCore = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      await Promise.allSettled([loadQuote(), loadKline(), loadMiniKline(), loadHoldingAgg()])
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to load', 'error')
    } finally {
      setLoading(false)
    }
  }, [symbol, loadQuote, loadKline, loadMiniKline, loadHoldingAgg, toast])

  const handleRefreshAll = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      await Promise.allSettled([loadQuote(), loadKline(), loadMiniKline(), loadSuggestions(), loadNews(), loadFilings(), loadHolders(), loadHoldingAgg(), loadReports()])
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to load', 'error')
    } finally {
      setLoading(false)
    }
  }, [symbol, loadQuote, loadKline, loadMiniKline, loadSuggestions, loadNews, loadFilings, loadHolders, loadHoldingAgg, loadReports, toast])

  const refreshForAuto = useCallback(async () => {
    if (!symbol) return
    const tasks: Promise<any>[] = [loadQuote(), loadHoldingAgg()]
    if (tab === 'overview' || tab === 'kline') {
      tasks.push(loadKline(), loadMiniKline({ silent: true }))
    }
    if (tab === 'overview' || tab === 'suggestions') {
      tasks.push(loadSuggestions())
    }
    if (tab === 'overview' || tab === 'news') {
      tasks.push(loadNews())
    }
    if (tab === 'filings') {
      tasks.push(loadFilings())
    }
    if (tab === 'holders') {
      tasks.push(loadHolders())
    }
    if (tab === 'overview' || tab === 'reports') {
      tasks.push(loadReports())
    }
    await Promise.allSettled(tasks)
  }, [symbol, tab, loadQuote, loadHoldingAgg, loadKline, loadMiniKline, loadSuggestions, loadNews, loadFilings, loadHolders, loadReports])

  const loadDeepResult = useCallback(async () => {
    if (!symbol) return
    setDeepLoading(true)
    setDeepHistoryLoading(true)
    try {
      const [latest, history] = await Promise.allSettled([
        tradingAgentsApi.getLatestForStock(symbol),
        tradingAgentsApi.getHistoryComparison(symbol, market, 90),
      ])
      setDeepResult(latest.status === 'fulfilled' ? latest.value : null)
      setDeepHistory(history.status === 'fulfilled' ? history.value : null)
    } catch {
      setDeepResult(null)
      setDeepHistory(null)
    } finally {
      setDeepLoaded(true)
      setDeepLoading(false)
      setDeepHistoryLoading(false)
    }
  }, [symbol, market])

  useEffect(() => {
    if (!props.open || !symbol) return
    setTab('overview')
    setSuggestions([])
    setNews([])
    setFilings([])
    setFilingsNote('')
    setFilingForm('all')
    setHolders(null)
    setHoldersLoaded(false)
    setReports([])
    setMiniKlines([])
    setWatchingStock(null)
    setDeepResult(null)
    setDeepLoaded(false)
    setDeepHistory(null)
    loadCore()
  }, [props.open, symbol, market, loadCore])

  // 切到「深度」tab 时按需拉取(仅首次)
  useEffect(() => {
    if (!props.open || !symbol) return
    if (tab === 'deep' && !deepLoaded && !deepLoading) {
      loadDeepResult()
    }
  }, [tab, props.open, symbol, deepLoaded, deepLoading, loadDeepResult])

  useEffect(() => {
    if (!props.open || !symbol) return
    let cancelled = false
    ;(async () => {
      try {
        const key = `${market}:${symbol}`
        const stocks = await stocksApi.list()
        if (cancelled) return
        const found = (stocks || []).find(s => s.symbol === symbol && s.market === market) || null
        if (found) {
          stockCacheRef.current[key] = found
        } else {
          delete stockCacheRef.current[key]
        }
        setWatchingStock(found)
      } catch {
        if (!cancelled) setWatchingStock(null)
      }
    })()
    return () => { cancelled = true }
  }, [props.open, symbol, market])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadNews().catch(() => setNews([]))
  }, [props.open, symbol, newsHours, loadNews])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadFilings().catch(() => setFilings([]))
  }, [props.open, symbol, loadFilings])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadHolders().catch(() => setHolders(null))
  }, [props.open, symbol, loadHolders])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadSuggestions().catch(() => setSuggestions([]))
  }, [props.open, symbol, includeExpiredSuggestions, loadSuggestions])

  useEffect(() => {
    if (!props.open || !symbol) return
    loadReports().catch(() => setReports([]))
  }, [props.open, symbol, loadReports])

  useEffect(() => {
    if (!props.open || !symbol || !autoRefreshEnabled) return
    const sec = Number(autoRefreshSec) > 0 ? Number(autoRefreshSec) : 20
    const ms = Math.max(10, sec) * 1000
    const timer = setInterval(() => {
      refreshForAuto().catch(() => undefined)
    }, ms)
    return () => clearInterval(timer)
  }, [props.open, symbol, autoRefreshEnabled, autoRefreshSec, refreshForAuto])

  const hasHolding = !!props.hasPosition || !!holdingAgg
  const technicalScored = useMemo(() => {
    if (!klineSummary) return null
    return buildKlineSuggestion(klineSummary as any, hasHolding)
  }, [klineSummary, hasHolding])
  const technicalFallbackSuggestion = useMemo<SuggestionInfo | null>(() => {
    if (!klineSummary || !technicalScored) return null
    const topEvidence = (technicalScored.evidence || []).filter(e => e.delta !== 0).slice(0, 3).map(e => e.text)
    return {
      action: technicalScored.action,
      action_label: technicalScored.action_label,
      signal: technicalScored.signal || 'Neutral technicals',
      reason: topEvidence.length > 0 ? topEvidence.join('; ') : 'A basic recommendation auto-generated from candlestick technical indicators',
      should_alert: technicalScored.action === 'buy' || technicalScored.action === 'add' || technicalScored.action === 'sell' || technicalScored.action === 'reduce',
      agent_name: 'technical_fallback',
      agent_label: 'Technical Indicators',
      created_at: new Date().toISOString(),
      is_expired: false,
      meta: {
        fallback: true,
        score: technicalScored.score,
        evidence_count: technicalScored.evidence?.length || 0,
      },
    }
  }, [klineSummary, technicalScored])
  const buildPageContext = useCallback(() => {
    const parts: string[] = []
    if (quote) {
      const items = [`price ${quote.current_price}`, `change ${quote.change_pct}%`]
      if (quote.volume != null) items.push(`volume ${quote.volume}`)
      if (quote.turnover_rate != null) items.push(`turnover rate ${quote.turnover_rate}%`)
      if (quote.pe_ratio != null) items.push(`P/E ${quote.pe_ratio}`)
      if (quote.total_market_value != null) items.push(`market cap ${quote.total_market_value}`)
      parts.push(`Real-time quote: ${items.join(', ')}`)
    }
    if (klineSummary) {
      const k = klineSummary as any
      const items = []
      if (k.trend) items.push(`trend ${k.trend}`)
      if (k.macd_status) items.push(`MACD ${k.macd_status}`)
      if (k.rsi_status) items.push(`RSI ${k.rsi_status}${k.rsi6 != null ? `(${k.rsi6})` : ''}`)
      if (k.kdj_status) items.push(`KDJ ${k.kdj_status}`)
      if (k.boll_status) items.push(`Bollinger ${k.boll_status}`)
      if (k.volume_trend) items.push(`volume ${k.volume_trend}${k.volume_ratio != null ? `(${k.volume_ratio}x)` : ''}`)
      if (k.support != null) items.push(`support ${k.support}`)
      if (k.resistance != null) items.push(`resistance ${k.resistance}`)
      if (items.length) parts.push(`Technicals: ${items.join(', ')}`)
    }
    if (technicalScored) {
      parts.push(`Technical score: ${technicalScored.action_label}(score=${technicalScored.score}), signal: ${technicalScored.signal || 'Neutral'}`)
      const evidence = (technicalScored.evidence || []).filter((e: any) => e.delta !== 0)
      if (evidence.length) {
        parts.push(`Score basis: ${evidence.map((e: any) => `${e.text}(${e.delta > 0 ? '+' : ''}${e.delta})`).join('; ')}`)
      }
    }
    if (suggestions.length > 0) {
      const lines = suggestions.slice(0, 3).map(s => `- [${s.agent_label || s.agent_name}] ${s.action_label}: ${s.signal}`)
      parts.push(`Recent AI recommendations:\n${lines.join('\n')}`)
    }
    if (holdingAgg) {
      parts.push(`Position: ${holdingAgg.quantity} shares, cost ${holdingAgg.unitCost}, value ${holdingAgg.marketValue}, P&L ${holdingAgg.pnl}`)
    }
    return parts.join('\n')
  }, [quote, klineSummary, technicalScored, suggestions, holdingAgg])

  const quoteUp = (quote?.change_pct || 0) > 0
  const quoteDown = (quote?.change_pct || 0) < 0
  const changeColor = quoteUp ? 'text-stock-up' : quoteDown ? 'text-stock-down' : 'text-foreground'
  const priceColor = quoteUp ? 'text-stock-up' : quoteDown ? 'text-stock-down' : 'text-foreground'
  const levelColor = (value: number | null | undefined) => {
    if (value == null || quote?.prev_close == null) return 'text-foreground'
    if (value > quote.prev_close) return 'text-stock-up'
    if (value < quote.prev_close) return 'text-stock-down'
    return 'text-foreground'
  }
  const badge = getMarketBadge(market)
  const amplitudePct = useMemo(() => {
    const hi = quote?.high_price
    const lo = quote?.low_price
    const pre = quote?.prev_close
    if (hi == null || lo == null || pre == null || pre === 0) return null
    return ((hi - lo) / pre) * 100
  }, [quote?.high_price, quote?.low_price, quote?.prev_close])

  const reportMap = useMemo(() => {
    const out: Record<string, HistoryRecord | null> = {
      premarket_outlook: null,
      daily_report: null,
      news_digest: null,
    }
    for (const r of reports) {
      if (!out[r.agent_name]) out[r.agent_name] = r
    }
    return out
  }, [reports])
  const activeReport = reportMap[reportTab]
  const latestReport = reports[0] || null
  const latestShareSuggestion = suggestions[0] || technicalFallbackSuggestion
  const shareCardPayload = useMemo(() => {
    const jsonSources = [
      parseSuggestionJson((latestShareSuggestion as any)?.signal),
      parseSuggestionJson((latestShareSuggestion as any)?.reason),
      parseSuggestionJson((latestShareSuggestion as any)?.raw),
      parseSuggestionJson((latestShareSuggestion as any)?.ai_response),
      parseSuggestionJson((latestShareSuggestion as any)?.prompt_context),
      (latestShareSuggestion as any)?.meta && typeof (latestShareSuggestion as any).meta === 'object'
        ? ((latestShareSuggestion as any).meta as Record<string, any>)
        : null,
    ].filter(Boolean) as Array<Record<string, any>>
    const pickFromJson = (...keys: string[]) => {
      for (const obj of jsonSources) {
        for (const key of keys) {
          const s = String(obj?.[key] || '').trim()
          if (s) return s
        }
      }
      return ''
    }
    const pickListFromJson = (...keys: string[]) => {
      for (const obj of jsonSources) {
        for (const key of keys) {
          const list = normalizeTextList(obj?.[key])
          if (list.length > 0) return list
        }
      }
      return [] as string[]
    }
    const marketLabel = badge.label
    const price = quote?.current_price != null ? formatNumber(quote.current_price) : '--'
    const chg = quote?.change_pct != null ? `${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%` : '--'
    const action = latestShareSuggestion?.action_label || latestShareSuggestion?.action || 'None yet'
    const signal = firstNonEmptyText(
      latestShareSuggestion?.signal,
      pickFromJson('signal', 'summary', 'core_view'),
      technicalScored?.signal,
      'Neutral technicals'
    ) || '--'
    const reason = firstNonEmptyText(
      latestShareSuggestion?.reason,
      pickFromJson('reason', 'thesis', 'core_judgement', 'core_judgment', 'analysis'),
      technicalFallbackSuggestion?.reason,
      'None yet'
    ) || '--'
    const risksList = [
      ...normalizeTextList((latestShareSuggestion as any)?.meta?.risks),
      ...pickListFromJson('risks', 'risk', 'risk_points'),
      ...buildShareTechnicalRisks(klineSummary),
    ].filter(Boolean)
    const dedupRisks = Array.from(new Set(risksList))
    const risks = dedupRisks.length > 0 ? dedupRisks.slice(0, 2).join('; ') : 'Market volatility risk'
    const triggerList = pickListFromJson('triggers', 'trigger', 'signals')
    const invalidList = pickListFromJson('invalidations', 'invalidation', 'stop_conditions')
    const trigger = triggerList.length > 0 ? triggerList.slice(0, 2).join('; ') : '--'
    const invalidation = invalidList.length > 0 ? invalidList.slice(0, 2).join('; ') : '--'
    const technicalBrief = firstNonEmptyText(
      [klineSummary?.trend, klineSummary?.macd_status, klineSummary?.rsi_status].filter(Boolean).join(' / '),
      technicalScored?.signal
    ) || '--'
    const levelsBrief = (klineSummary?.support != null && klineSummary?.resistance != null)
      ? `Support ${formatNumber(klineSummary.support)} / Resistance ${formatNumber(klineSummary.resistance)}`
      : '--'
    const source = latestShareSuggestion?.agent_label || latestShareSuggestion?.agent_name || 'Technical Indicators'
    const ts = new Date().toLocaleString('en-US', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
    return { marketLabel, price, chg, action, signal, reason, risks, trigger, invalidation, technicalBrief, levelsBrief, source, ts }
  }, [badge.label, klineSummary, latestShareSuggestion, quote?.change_pct, quote?.current_price, technicalFallbackSuggestion?.reason, technicalScored?.signal])

  const shareText = useMemo(() => {
    const { marketLabel, price, chg, action, signal, reason, risks, trigger, invalidation, technicalBrief, levelsBrief, source, ts } = shareCardPayload
    const lines = [
      `[PanWatch Insight] ${resolvedName} (${symbol} · ${marketLabel})`,
      `Time: ${ts}`,
      `Price: ${price} (${chg})`,
      `Recommendation: ${action}`,
      `Signal: ${signal}`,
      `Reason: ${reason}`,
      `Risk: ${risks}`,
      `Technicals: ${technicalBrief}`,
      `Key levels: ${levelsBrief}`,
      `Source: ${source}`,
    ]
    if (trigger !== '--') lines.splice(7, 0, `Trigger: ${trigger}`)
    if (invalidation !== '--') lines.splice(8, 0, `Invalidation: ${invalidation}`)
    return lines.join('\n')
  }, [shareCardPayload, resolvedName, symbol])

  const handleExportShareImage = useCallback(async () => {
    const esc = (s: string) => String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;')
    const trim = (s: string, n = 42) => {
      const x = String(s || '')
      return x.length > n ? `${x.slice(0, n - 1)}…` : x
    }

    setImageExporting(true)
    try {
      const { marketLabel, price, chg, action, signal, reason, risks, technicalBrief, levelsBrief, source, ts } = shareCardPayload
      const up = (quote?.change_pct || 0) >= 0
      const changeColor = up ? '#12855C' : '#DC2B3A'
      const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0b1220"/>
      <stop offset="100%" stop-color="#111827"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="1200" height="630" fill="url(#bg)"/>
  <rect x="40" y="30" width="1120" height="570" rx="22" fill="#0f172a" stroke="#1f2937"/>
  <text x="76" y="104" fill="#93c5fd" font-size="26" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">PanWatch Insight</text>
  <text x="76" y="150" fill="#f8fafc" font-size="42" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(`${resolvedName} (${symbol} · ${marketLabel})`, 28))}</text>
  <text x="76" y="198" fill="#94a3b8" font-size="22" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(ts)}</text>

  <text x="76" y="284" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">Price</text>
  <text x="180" y="284" fill="#f8fafc" font-size="52" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(price)}</text>
  <text x="380" y="284" fill="${changeColor}" font-size="36" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(chg)}</text>

  <text x="76" y="352" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">Recommendation</text>
  <text x="180" y="352" fill="#22d3ee" font-size="34" font-weight="700" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(action, 20))}</text>

  <text x="76" y="412" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">Signal</text>
  <text x="180" y="412" fill="#e2e8f0" font-size="26" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(signal, 46))}</text>

  <text x="76" y="466" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">Reason</text>
  <text x="180" y="466" fill="#cbd5e1" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(reason, 52))}</text>

  <text x="76" y="520" fill="#94a3b8" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">Risk</text>
  <text x="180" y="520" fill="#cbd5e1" font-size="24" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(risks, 52))}</text>

  <text x="76" y="560" fill="#94a3b8" font-size="22" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">Technicals</text>
  <text x="180" y="560" fill="#cbd5e1" font-size="21" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(technicalBrief, 58))}</text>
  <text x="76" y="590" fill="#94a3b8" font-size="22" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">Key Levels</text>
  <text x="180" y="590" fill="#cbd5e1" font-size="21" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">${esc(trim(levelsBrief, 58))}</text>
  <text x="76" y="618" fill="#64748b" font-size="18" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Microsoft YaHei,sans-serif">Source: ${esc(source)} · For reference only, not investment advice</text>
</svg>`

      const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const img = await new Promise<HTMLImageElement>((resolve, reject) => {
        const el = new Image()
        el.onload = () => resolve(el)
        el.onerror = reject
        el.src = url
      })
      const canvas = document.createElement('canvas')
      canvas.width = 1200
      canvas.height = 630
      const ctx = canvas.getContext('2d')
      if (!ctx) throw new Error('Could not create canvas')
      ctx.drawImage(img, 0, 0)
      URL.revokeObjectURL(url)
      const png = canvas.toDataURL('image/png')
      const a = document.createElement('a')
      a.href = png
      a.download = `panwatch-${symbol}-${Date.now()}.png`
      a.click()
      toast('Share image generated and downloaded', 'success')
    } catch {
      toast('Failed to generate image, please try again later', 'error')
    } finally {
      setImageExporting(false)
    }
  }, [quote?.change_pct, resolvedName, shareCardPayload, symbol, toast])

  const copyTextWithFallback = useCallback(async (text: string): Promise<boolean> => {
    if (!text) return false

    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text)
        return true
      } catch {
        // Fallback to legacy copy below.
      }
    }

    if (typeof document !== 'undefined') {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      textarea.style.pointerEvents = 'none'
      textarea.style.left = '-9999px'
      document.body.appendChild(textarea)
      try {
        textarea.focus()
        textarea.select()
        textarea.setSelectionRange(0, textarea.value.length)
        return !!document.execCommand?.('copy')
      } catch {
        return false
      } finally {
        document.body.removeChild(textarea)
      }
    }
    return false
  }, [])

  const handleCopyShareText = useCallback(async () => {
    try {
      const copied = await copyTextWithFallback(shareText)
      if (copied) {
        toast('Insight content copied', 'success')
      } else {
        toast('Copy failed - try the "Image" share option instead', 'error')
      }
    } catch {
      toast('Copy failed - try the "Image" share option instead', 'error')
    }
  }, [copyTextWithFallback, shareText, toast])

  const handleShareInsight = useCallback(async () => {
    try {
      if (typeof navigator !== 'undefined' && (navigator as any).share) {
        await (navigator as any).share({
          title: `${resolvedName} Insight`,
          text: shareText,
        })
        return
      }
      const copied = await copyTextWithFallback(shareText)
      if (copied) {
        toast('System sharing not supported here - content copied automatically', 'success')
      } else {
        toast('System sharing not supported and copy failed - try "Image" share instead', 'error')
      }
    } catch (e: any) {
      if (e?.name === 'AbortError') return
      const copied = await copyTextWithFallback(shareText)
      if (copied) {
        toast('Share failed - content copied automatically', 'success')
      } else {
        toast('Share failed and copy failed - try "Image" share instead', 'error')
      }
    }
  }, [copyTextWithFallback, resolvedName, shareText, toast])

  const handleSetAlert = async () => {
    if (!symbol) return
    setAlerting(true)
    try {
      const stocks = await stocksApi.list()
      let stock = (stocks || []).find(s => s.symbol === symbol && s.market === market) || null
      if (!stock) {
        stock = await stocksApi.create({ symbol, name: resolvedName || symbol, market })
      }

      const existingAgents = (stock.agents || []).map(a => ({
        agent_name: a.agent_name,
        schedule: a.schedule || '',
        ai_model_id: a.ai_model_id ?? null,
        notify_channel_ids: a.notify_channel_ids || [],
      }))
      const hasIntraday = existingAgents.some(a => a.agent_name === 'intraday_monitor')
      const nextAgents = hasIntraday
        ? existingAgents
        : [...existingAgents, { agent_name: 'intraday_monitor', schedule: '', ai_model_id: null, notify_channel_ids: [] }]

      await stocksApi.updateAgents(stock.id, { agents: nextAgents })
      await stocksApi.triggerAgent(stock.id, 'intraday_monitor', {
        bypass_throttle: true,
        bypass_market_hours: true,
      })
      toast('Alert set, AI analysis submitted', 'success')
      // Poll while waiting for the recommendation to generate (up to 2 minutes, every 5 seconds)
      const before = Date.now()
      const poll = setInterval(async () => {
        if (Date.now() - before > 120_000) { clearInterval(poll); setAlerting(false); return }
        await loadSuggestions()
      }, 5_000)
      await loadSuggestions()
      // Delayed cleanup: interval stops automatically after 2 minutes
      setTimeout(() => clearInterval(poll), 125_000)
      return
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to set alert', 'error')
    } finally {
      setAlerting(false)
    }
  }

  const toggleWatch = useCallback(async () => {
    if (!symbol) return
    if (watchingStock && hasHolding) {
      toast('This stock has open positions - remove the positions before unwatching', 'error')
      return
    }

    setWatchToggleLoading(true)
    try {
      if (watchingStock) {
        await stocksApi.remove(watchingStock.id)
        setWatchingStock(null)
        delete stockCacheRef.current[`${market}:${symbol}`]
        toast('Unwatched', 'success')
      } else {
        const created = await stocksApi.create({ symbol, name: resolvedName || symbol, market })
        setWatchingStock(created)
        stockCacheRef.current[`${market}:${symbol}`] = created
        toast('Added to watchlist', 'success')
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Operation failed', 'error')
    } finally {
      setWatchToggleLoading(false)
    }
  }, [hasHolding, market, resolvedName, symbol, toast, watchingStock])

  const triggerAutoAiSuggestion = useCallback(async () => {
    // Auto-recommendation only applies to stocks confirmed as not held, and doesn't auto-create the stock or bind an agent.
    if (!symbol || !market || !holdingLoaded || holdingLoadError || hasHolding || autoSuggesting) return
    const key = `${market}:${symbol}`
    const lastTs = autoTriggeredRef.current[key] || 0
    if (Date.now() - lastTs < 5 * 60 * 1000) return
    autoTriggeredRef.current[key] = Date.now()
    setAutoSuggesting(true)
    try {
      // intraday_monitor is lighter and more stable than chart_analyst, with no dependency on the screenshot pipeline
      await stocksApi.triggerAgent(0, 'intraday_monitor', {
        allow_unbound: true,
        symbol,
        market,
        name: resolvedName || symbol,
        bypass_throttle: true,
        bypass_market_hours: true,
      })
      // Async mode: triggerAgent returns immediately, poll while waiting for the recommendation to generate
      const before = Date.now()
      const poll = setInterval(async () => {
        if (Date.now() - before > 120_000) { clearInterval(poll); setAutoSuggesting(false); return }
        await loadSuggestions()
      }, 5_000)
      await loadSuggestions()
      setTimeout(() => clearInterval(poll), 125_000)
      return
    } catch (e) {
      toast(
        e instanceof Error ? e.message : 'Auto AI recommendation trigger failed - click "Set Alert" to retry',
        'error'
      )
      setAutoSuggesting(false)
    }
  }, [symbol, market, resolvedName, holdingLoaded, holdingLoadError, hasHolding, autoSuggesting, loadSuggestions, toast])

  useEffect(() => {
    if (!props.open || !symbol) return
    const timer = setTimeout(() => {
      triggerAutoAiSuggestion().catch(() => undefined)
    }, 700)
    return () => clearTimeout(timer)
  }, [props.open, symbol, market, triggerAutoAiSuggestion])

  const miniKlineExtrema = useMemo(() => {
    if (!miniKlines.length) return null
    let low = Number.POSITIVE_INFINITY
    let high = Number.NEGATIVE_INFINITY
    for (const k of miniKlines) {
      low = Math.min(low, Number(k.low))
      high = Math.max(high, Number(k.high))
    }
    if (!isFinite(low) || !isFinite(high) || high <= low) return null
    return { low, high }
  }, [miniKlines])

  return (
    <>
      <Dialog open={props.open} onOpenChange={props.onOpenChange}>
        <DialogContent className="w-[92vw] max-w-6xl p-5 md:p-6 overflow-x-hidden">
          <DialogHeader className="mb-3">
            <div className="flex items-start justify-between gap-3 pr-10 md:pr-8">
              <div className="shrink-0">
                <DialogTitle className="flex items-center gap-2 flex-wrap">
                  <span className={`text-[10px] px-2 py-0.5 rounded ${badge.style}`}>{badge.label}</span>
                  <span className="break-all">{resolvedName}</span>
                  <span className="font-mono text-[12px] text-muted-foreground">({symbol})</span>
                </DialogTitle>
                <DialogDescription className="hidden md:block">Overview, chart, AI recommendations, news, and historical analysis all in one dialog</DialogDescription>
              </div>
              <div className="hidden md:flex items-center gap-2">
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleExportShareImage()} disabled={imageExporting}>
                  <Download className={`w-3.5 h-3.5 ${imageExporting ? 'animate-pulse' : ''}`} />
                  <span>{imageExporting ? 'Generating' : 'Image'}</span>
                </Button>
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleShareInsight()}>
                  <Share2 className="w-3.5 h-3.5" />
                  <span>Share</span>
                </Button>
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => handleCopyShareText()}>
                  <Copy className="w-3.5 h-3.5" />
                  <span>Copy</span>
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  className="h-8 px-2.5"
                  onClick={toggleWatch}
                  disabled={watchToggleLoading || (hasHolding && !!watchingStock)}
                  title={hasHolding && watchingStock ? 'Cannot unwatch a stock with open positions' : undefined}
                >
                  {watchToggleLoading ? 'Processing...' : (watchingStock ? (hasHolding ? 'Held' : 'Unwatch') : 'Quick Watch')}
                </Button>
                <StockPriceAlertPanel mode="inline" symbol={symbol} market={market} stockName={resolvedName} />
                <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={handleSetAlert} disabled={alerting}>
                  {alerting ? 'Setting...' : 'Set Alert'}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  className="h-8 px-2.5"
                  onClick={() => {
                    window.dispatchEvent(new CustomEvent('panwatch-open-chat', {
                      detail: { symbol, market, stockName: resolvedName, pageContext: buildPageContext() }
                    }))
                    props.onOpenChange(false)
                  }}
                >
                  <Sparkles className="w-3.5 h-3.5 mr-1" /> Ask AI
                </Button>
                <Button variant="outline" size="sm" className="h-8 px-2.5" aria-label="Refresh" onClick={() => handleRefreshAll()} disabled={loading}>
                  <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
                </Button>
              </div>
            </div>
            <div className="flex md:hidden items-center gap-2 mt-2 overflow-x-auto scrollbar-none pb-1 -mb-1">
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" aria-label="Export image" onClick={() => handleExportShareImage()} disabled={imageExporting}>
                <Download className={`w-3.5 h-3.5 ${imageExporting ? 'animate-pulse' : ''}`} />
              </Button>
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" aria-label="Share" onClick={() => handleShareInsight()}>
                <Share2 className="w-3.5 h-3.5" />
              </Button>
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" aria-label="Copy" onClick={() => handleCopyShareText()}>
                <Copy className="w-3.5 h-3.5" />
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-8 px-2.5 shrink-0"
                onClick={toggleWatch}
                disabled={watchToggleLoading || (hasHolding && !!watchingStock)}
              >
                {watchToggleLoading ? 'Processing...' : (watchingStock ? (hasHolding ? 'Held' : 'Unwatch') : 'Quick Watch')}
              </Button>
              <StockPriceAlertPanel mode="inline" symbol={symbol} market={market} stockName={resolvedName} />
              <Button variant="secondary" size="sm" className="h-8 px-2.5 shrink-0" onClick={handleSetAlert} disabled={alerting}>
                {alerting ? 'Setting...' : 'Set Alert'}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className="h-8 px-2.5 shrink-0"
                onClick={() => {
                  window.dispatchEvent(new CustomEvent('panwatch-open-chat', {
                    detail: { symbol, market, stockName: resolvedName, pageContext: buildPageContext() }
                  }))
                  props.onOpenChange(false)
                }}
              >
                <Sparkles className="w-3.5 h-3.5 mr-1" /> Ask AI
              </Button>
              <Button variant="outline" size="sm" className="h-8 px-2.5 shrink-0" aria-label="Refresh" onClick={() => handleRefreshAll()} disabled={loading}>
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              </Button>
            </div>
          </DialogHeader>

          <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
            <div className="flex items-center gap-1 flex-wrap">
              {[
                { id: 'overview', label: 'Overview' },
                { id: 'suggestions', label: `Recommendations (${suggestions.length})` },
                { id: 'reports', label: `Reports (${reports.length})` },
                { id: 'deep', label: deepResult ? 'Deep (1)' : 'Deep' },
                { id: 'kline', label: 'Chart' },
                { id: 'filings', label: `Filings (${filings.length})` },
                { id: 'holders', label: 'Holders' },
                { id: 'news', label: `News (${news.length})` },
              ].map(item => (
                <button
                  key={item.id}
                  onClick={() => setTab(item.id as InsightTab)}
                  className={`text-[11px] px-2.5 py-1 rounded transition-colors ${
                    tab === item.id ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">Auto-refresh</span>
              <Switch
                checked={autoRefreshEnabled}
                onCheckedChange={setAutoRefreshEnabled}
                aria-label="Auto-refresh"
              />
              <Select value={String(autoRefreshSec)} onValueChange={(v) => setAutoRefreshSec(Number(v))}>
                <SelectTrigger className="h-7 w-[84px] text-[11px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="10">10s</SelectItem>
                  <SelectItem value="20">20s</SelectItem>
                  <SelectItem value="30">30s</SelectItem>
                  <SelectItem value="60">60s</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="max-h-[68vh] overflow-y-auto overflow-x-hidden pr-1 scrollbar">
            {tab === 'overview' && (
              <div className="space-y-3">
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 items-stretch">
                  <div className="card p-4 h-full">
                    <div className="mt-1 flex items-end justify-between gap-3">
                      <div className={`text-[34px] leading-none font-bold font-mono ${priceColor}`}>
                        {quote?.current_price != null ? formatNumber(quote.current_price) : '--'}
                      </div>
                      <div className={`text-[16px] font-mono ${changeColor}`}>
                        {quote?.change_pct != null ? `${quote.change_pct >= 0 ? '+' : ''}${quote.change_pct.toFixed(2)}%` : '--'}
                      </div>
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-[12px]">
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">Open</div><div className={`font-mono ${levelColor(quote?.open_price)}`}>{formatNumber(quote?.open_price)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">High</div><div className={`font-mono ${levelColor(quote?.high_price)}`}>{formatNumber(quote?.high_price)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">Low</div><div className={`font-mono ${levelColor(quote?.low_price)}`}>{formatNumber(quote?.low_price)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">Volume</div><div className="font-mono">{formatCompactNumber(quote?.volume)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">Turnover</div><div className="font-mono">{formatCompactNumber(quote?.turnover)}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">Amplitude</div><div className="font-mono">{amplitudePct != null ? `${amplitudePct.toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">Turnover Rate</div><div className="font-mono">{quote?.turnover_rate != null ? `${Number(quote.turnover_rate).toFixed(2)}%` : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">P/E Ratio</div><div className="font-mono">{quote?.pe_ratio != null ? Number(quote.pe_ratio).toFixed(2) : '--'}</div></div>
                      <div className="rounded bg-accent/15 px-2 py-1.5"><div className="text-[10px] text-muted-foreground">Market Cap</div><div className="font-mono">{formatMarketCap(quote?.total_market_value)}</div></div>
                    </div>
                    <div className="mt-3 border-t border-border/50 pt-3">
                      <div className="text-[11px] text-muted-foreground mb-2">Position Info</div>
                      {holdingAgg ? (
                        <div className="grid grid-cols-2 gap-2 text-[12px]">
                          <div className="rounded bg-muted/50 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">Quantity Held</div>
                            <div className="font-mono">{holdingAgg.quantity}</div>
                          </div>
                          <div className="rounded bg-muted/50 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">Cost Basis (Unit)</div>
                            <div
                              className={`font-mono ${
                                quote?.current_price != null
                                  ? quote.current_price > holdingAgg.unitCost
                                    ? 'text-stock-up'
                                    : quote.current_price < holdingAgg.unitCost
                                      ? 'text-stock-down'
                                      : 'text-foreground'
                                  : 'text-foreground'
                              }`}
                            >
                              {formatNumber(holdingAgg.unitCost)}
                            </div>
                          </div>
                          <div className="rounded bg-muted/50 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">Position Value</div>
                            <div className="font-mono">{formatCompactNumber(holdingAgg.marketValue)}</div>
                          </div>
                          <div className="rounded bg-muted/50 px-2 py-1.5">
                            <div className="text-[10px] text-muted-foreground">Total P&L</div>
                            <div className={`font-mono ${holdingAgg.pnl >= 0 ? 'text-stock-up' : 'text-stock-down'}`}>
                              {holdingAgg.pnl >= 0 ? '+' : ''}{formatCompactNumber(holdingAgg.pnl)}
                            </div>
                          </div>
                        </div>
                      ) : (
                        <div className="text-[11px] text-muted-foreground">Not currently held</div>
                      )}
                      <AddPositionCalculator
                        symbol={symbol}
                        market={market}
                        currentQuantity={holdingAgg?.quantity ?? 0}
                        currentCost={holdingAgg?.unitCost ?? 0}
                        currentPrice={quote?.current_price ?? null}
                      />
                    </div>
                  </div>

                  <div className="card p-4 h-full">
                    <div className="text-[12px] text-muted-foreground mb-2">Mini Chart</div>
                    {!klineSummary ? (
                      <div className="text-[12px] text-muted-foreground py-8">No chart summary available</div>
                    ) : (
                      <>
                        {miniKlineLoading ? (
                          <div className="h-32 rounded bg-accent/30 animate-pulse" />
                        ) : miniKlines.length > 0 && miniKlineExtrema ? (
                          <svg
                            viewBox="0 0 320 120"
                            className="w-full h-32 cursor-pointer"
                            onClick={() => setTab('kline')}
                            onMouseLeave={() => setMiniHoverIdx(null)}
                            onMouseMove={(e) => {
                              const rect = e.currentTarget.getBoundingClientRect()
                              const x = e.clientX - rect.left
                              const ratio = rect.width > 0 ? x / rect.width : 0
                              const idx = Math.floor(ratio * miniKlines.length)
                              setMiniHoverIdx(Math.max(0, Math.min(miniKlines.length - 1, idx)))
                            }}
                          >
                            <title>Click to open interactive chart</title>
                            {miniKlines.map((k, idx) => {
                              const xStep = 320 / miniKlines.length
                              const x = xStep * idx + xStep / 2
                              const bodyW = Math.max(2, xStep * 0.5)
                              const toY = (v: number) => 114 - ((v - miniKlineExtrema.low) / (miniKlineExtrema.high - miniKlineExtrema.low)) * 100
                              const yOpen = toY(Number(k.open))
                              const yClose = toY(Number(k.close))
                              const yHigh = toY(Number(k.high))
                              const yLow = toY(Number(k.low))
                              const up = Number(k.close) >= Number(k.open)
                              const color = up ? '#12855C' : '#DC2B3A'
                              const bodyTop = Math.min(yOpen, yClose)
                              const bodyH = Math.max(1.4, Math.abs(yOpen - yClose))
                              const active = miniHoverIdx === idx
                              return (
                                <g key={`${k.date}-${idx}`}>
                                  {active && <rect x={x - xStep / 2} y={6} width={xStep} height={108} fill="rgba(59,130,246,0.10)" />}
                                  <line x1={x} y1={yHigh} x2={x} y2={yLow} stroke={color} strokeWidth="1" />
                                  <rect x={x - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} fill={color} rx="0.6" />
                                </g>
                              )
                            })}
                          </svg>
                        ) : (
                          <div className="h-32 text-[11px] text-muted-foreground flex items-center justify-center">No mini chart available</div>
                        )}
                        <div className="mt-2 rounded bg-accent/10 p-2.5">
                          <TechnicalIndicatorStrip
                            klineSummary={klineSummary}
                            technicalSuggestion={technicalFallbackSuggestion}
                            stockName={resolvedName}
                            stockSymbol={symbol}
                            market={market}
                            hasPosition={!!props.hasPosition}
                            score={Number(technicalScored?.score ?? 0)}
                            evidence={technicalScored?.evidence || []}
                          />
                        </div>
                      </>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 items-stretch">
                  <div className="card p-4 h-full flex flex-col">
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-[12px] text-muted-foreground">AI Recommendations</div>
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px] text-muted-foreground" onClick={() => setTab('suggestions')}>
                        More
                      </Button>
                      {autoSuggesting && suggestions.length > 0 && (
                        <div className="text-[10px] text-muted-foreground">Updating...</div>
                      )}
                    </div>
                    {suggestions.length > 0 ? (
                      <div className="space-y-2">
                        <SuggestionBadge
                          suggestion={suggestions[0]}
                          stockName={resolvedName}
                          stockSymbol={symbol}
                          market={market}
                          hasPosition={!!props.hasPosition}
                          showTechnicalCompanion={false}
                        />
                        <div className="rounded bg-accent/10 p-2 text-[11px]">
                          <div className="text-muted-foreground">Core Judgment</div>
                          <div className="mt-1 text-foreground line-clamp-2">{suggestions[0].signal || suggestions[0].reason || 'No description available'}</div>
                          <div className="mt-1 text-muted-foreground">Action: {suggestions[0].action_label || suggestions[0].action || '--'}</div>
                          <div className="mt-1 text-foreground line-clamp-2">Basis: {suggestions[0].reason || 'No additional basis available'}</div>
                          <div className="mt-1 text-muted-foreground">
                            Source: {suggestions[0].agent_label || suggestions[0].agent_name || 'AI'}{suggestions[0].created_at ? ` · ${formatTime(suggestions[0].created_at)}` : ''}
                          </div>
                        </div>
                        {suggestions.length > 1 && (
                          <div className="rounded bg-accent/10 p-2 text-[11px]">
                            <div className="text-muted-foreground mb-1">Recent Additional Recommendations</div>
                            {suggestions.slice(1, 3).map((item, idx) => (
                              <div key={`${item.created_at || 'extra'}-${idx}`} className="line-clamp-1 text-foreground">
                                {item.action_label || item.action} · {item.signal || item.reason || '--'}
                              </div>
                            ))}
                          </div>
                        )}
                        <div className="text-[10px] text-muted-foreground min-h-[14px]">{autoSuggesting && suggestions.length === 0 ? 'Auto-generating AI recommendation...' : ''}</div>
                      </div>
                    ) : (
                      <div className="text-[12px] text-muted-foreground py-6">
                        {autoSuggesting ? 'Auto-generating AI recommendation (usually 5-15s)...' : 'No AI recommendations yet'}
                      </div>
                    )}
                  </div>

                  <div className="card p-4 h-full flex flex-col">
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-[12px] text-muted-foreground">News</div>
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px] text-muted-foreground" onClick={() => setTab('news')}>
                        More
                      </Button>
                    </div>
                    <div className="flex-1 space-y-2">
                      {news.length === 0 ? (
                        <div className="text-[12px] text-muted-foreground py-6">No related news available</div>
                      ) : (
                        news.slice(0, 3).map((item, idx) => (
                          <a
                            key={`${item.publish_time || 'n'}-${idx}`}
                            href={item.url}
                            target="_blank"
                            rel="noreferrer"
                            className="block rounded-lg border border-border/30 bg-accent/10 p-2.5 hover:bg-accent/20 transition-colors"
                          >
                            <div className="text-[12px] text-foreground line-clamp-2">{item.title}</div>
                            <div className="mt-1 text-[10px] text-muted-foreground">{item.source_label || item.source} · {formatTime(item.publish_time)}</div>
                          </a>
                        ))
                      )}
                    </div>
                  </div>
                  <div className="card p-4 h-full flex flex-col">
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <div className="text-[12px] text-muted-foreground">AI Reports</div>
                      <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px] text-muted-foreground" onClick={() => setTab('reports')}>
                        More
                      </Button>
                    </div>
                    {!latestReport ? (
                      <div className="text-[12px] text-muted-foreground py-3">No reports yet</div>
                    ) : (
                      <div className="rounded-lg border border-border/30 bg-accent/10 p-2.5">
                        <div className="text-[11px] text-muted-foreground">
                          {AGENT_LABELS[latestReport.agent_name] || latestReport.agent_name} · {latestReport.analysis_date}
                        </div>
                        <div className="mt-1 text-[13px] font-medium line-clamp-1">{latestReport.title || 'Report Summary'}</div>
                        <div className="mt-1 text-[12px] text-foreground/90 line-clamp-3">
                          {markdownToPlainText(latestReport.content) || 'No report content available'}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {tab === 'kline' && (
              <div className="card p-4">
                <InteractiveKline
                  symbol={symbol}
                  market={market}
                  initialInterval={klineInterval}
                />
              </div>
            )}

            {tab === 'reports' && (
              <div className="space-y-3">
                <div className="card p-3">
                  <div className="flex items-center gap-1">
                    {([
                      { key: 'premarket_outlook', label: 'Pre-market' },
                      { key: 'daily_report', label: 'Post-market' },
                      { key: 'news_digest', label: 'News' },
                    ] as const).map(item => (
                      <button
                        key={item.key}
                        onClick={() => setReportTab(item.key)}
                        className={`text-[11px] px-2.5 py-1 rounded ${
                          reportTab === item.key ? 'bg-primary text-primary-foreground' : 'bg-accent/60 text-muted-foreground hover:bg-accent'
                        }`}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
                {!activeReport ? (
                  <div className="card p-6 text-[12px] text-muted-foreground text-center">No reports yet</div>
                ) : (
                  <div className="card p-4 space-y-3">
                    <div className="text-[11px] text-muted-foreground">
                      {AGENT_LABELS[activeReport.agent_name] || activeReport.agent_name} · {activeReport.analysis_date}
                    </div>
                    <div className="text-[15px] font-medium">{activeReport.title || 'Report Summary'}</div>
                    {activeReport.suggestions && (activeReport.suggestions as any)?.[symbol]?.action_label && (
                      <div className="text-[11px] inline-flex px-2 py-0.5 rounded bg-muted text-foreground">
                        {(activeReport.suggestions as any)[symbol].action_label}
                      </div>
                    )}
                    <div className="rounded-lg bg-accent/10 p-3">
                      <div className="prose prose-sm dark:prose-invert max-w-none text-foreground/90 break-words">
                        <ReactMarkdown>{activeReport.content || 'No report content available'}</ReactMarkdown>
                      </div>
                    </div>
                    {(activeReport.prompt_context || activeReport.context_payload || activeReport.news_debug) && (
                      <details className="rounded-lg border border-border/40 bg-accent/10 p-3">
                        <summary className="cursor-pointer text-[12px] text-muted-foreground select-none">View Analysis Context</summary>
                        {activeReport.prompt_stats ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">Prompt Stats</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto">{JSON.stringify(activeReport.prompt_stats, null, 2)}</pre>
                          </div>
                        ) : null}
                        {activeReport.news_debug ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">News Injection Details</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto">{JSON.stringify(activeReport.news_debug, null, 2)}</pre>
                          </div>
                        ) : null}
                        {activeReport.context_payload ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">Context Snapshot</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto max-h-[220px] overflow-y-auto">{JSON.stringify(activeReport.context_payload, null, 2)}</pre>
                          </div>
                        ) : null}
                        {activeReport.prompt_context ? (
                          <div className="mt-2">
                            <div className="text-[11px] text-muted-foreground mb-1">Raw Prompt</div>
                            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto max-h-[220px] overflow-y-auto">{activeReport.prompt_context}</pre>
                          </div>
                        ) : null}
                      </details>
                    )}
                  </div>
                )}
              </div>
            )}

            {tab === 'deep' && (
              <div className="space-y-3">
                {deepResult && (
                  <div className="flex justify-end">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 px-2.5"
                      onClick={() =>
                        window.open(
                          `/analysis/${symbol}/${deepResult.timestamp ? String(deepResult.timestamp).slice(0, 10) : new Date().toISOString().slice(0, 10)}`,
                          '_blank',
                        )
                      }
                    >
                      Open Details Page ↗
                    </Button>
                  </div>
                )}
                <DeepAnalysisSection
                  loading={deepLoading}
                  loaded={deepLoaded}
                  result={deepResult}
                  history={deepHistory}
                  historyLoading={deepHistoryLoading}
                  showAnalyst={deepShowAnalyst}
                  setShowAnalyst={setDeepShowAnalyst}
                  showDebate={deepShowDebate}
                  setShowDebate={setDeepShowDebate}
                  onRefresh={loadDeepResult}
                />
              </div>
            )}

            {tab === 'suggestions' && (
              <div className="space-y-3">
                <div className="card p-3 flex items-center justify-between gap-3">
                  <div className="text-[12px] text-muted-foreground">Show Expired Recommendations</div>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-muted-foreground">{includeExpiredSuggestions ? 'Include Expired' : 'Valid Only'}</span>
                    <Switch
                      checked={includeExpiredSuggestions}
                      onCheckedChange={setIncludeExpiredSuggestions}
                      aria-label="Show Expired Recommendations"
                    />
                  </div>
                </div>
                {suggestions.length === 0 ? (
                  technicalFallbackSuggestion ? (
                    <div className="card p-4">
                      <SuggestionBadge suggestion={technicalFallbackSuggestion} stockName={resolvedName} stockSymbol={symbol} kline={klineSummary} hasPosition={!!props.hasPosition} />
                      <div className="mt-2 text-[10px] text-muted-foreground">
                        {autoSuggesting ? 'Auto-generating AI recommendation (usually 5-15s)...' : 'Currently showing technical indicator based recommendation'}
                      </div>
                    </div>
                  ) : (
                    <div className="card p-6 text-[12px] text-muted-foreground text-center">
                      {autoSuggesting ? 'Auto-generating AI recommendation (usually 5-15s)...' : 'No recommendations yet'}
                    </div>
                  )
                ) : (
                  <div className="max-h-[56vh] overflow-y-auto pr-1 scrollbar space-y-3">
                    {suggestions.map((item, idx) => (
                      <div key={`${item.created_at || 's'}-${idx}`} className="card p-4">
                        <SuggestionBadge suggestion={item} stockName={resolvedName} stockSymbol={symbol} kline={klineSummary} hasPosition={!!props.hasPosition} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {tab === 'news' && (
              <div className="space-y-3">
                <div className="flex items-center justify-end">
                  <Select value={newsHours} onValueChange={setNewsHours}>
                    <SelectTrigger className="h-8 w-[110px] text-[12px]">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="6">Last 6 hours</SelectItem>
                      <SelectItem value="12">Last 12 hours</SelectItem>
                      <SelectItem value="24">Last 24 hours</SelectItem>
                      <SelectItem value="48">Last 48 hours</SelectItem>
                      <SelectItem value="168">Last 7 days</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {news.length === 0 ? (
                  <div className="card p-6 text-[12px] text-muted-foreground text-center">No related news available</div>
                ) : (
                  news.map((item, idx) => (
                    <a
                      key={`${item.publish_time || 'n'}-${idx}`}
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="card block p-4 hover:bg-accent/20 transition-colors"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-[13px] font-medium text-foreground line-clamp-2">{item.title}</div>
                        <ExternalLink className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                      </div>
                      <div className="mt-2 text-[11px] text-muted-foreground">{item.source_label || item.source} · {formatTime(item.publish_time)}</div>
                    </a>
                  ))
                )}
              </div>
            )}

            {tab === 'filings' && (() => {
              const chip = FILING_FORM_CHIPS.find(c => c.id === filingForm) || FILING_FORM_CHIPS[0]
              const visibleFilings = filings.filter(item => chip.match(String(item.form_type || '')))
              return (
                <div className="space-y-3">
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="flex items-center gap-1 flex-wrap">
                      {FILING_FORM_CHIPS.map(item => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => setFilingForm(item.id)}
                          className={`text-[11px] px-2.5 py-1 rounded transition-colors ${
                            filingForm === item.id ? 'bg-primary text-primary-foreground' : 'bg-accent/50 text-muted-foreground hover:bg-accent'
                          }`}
                        >
                          {item.label}
                        </button>
                      ))}
                    </div>
                    {filingsNote && (
                      <span className="text-[11px] text-muted-foreground">{filingsNote}</span>
                    )}
                  </div>
                  {visibleFilings.length === 0 ? (
                    <div className="card p-6 text-[12px] text-muted-foreground text-center">
                      <div>No filings found</div>
                      {filingsNote && <div className="mt-1">{filingsNote}</div>}
                    </div>
                  ) : (
                    visibleFilings.map((item, idx) => (
                      <a
                        key={`${item.external_id || item.filed_at || 'f'}-${idx}`}
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        className="card block p-4 hover:bg-accent/20 transition-colors"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-accent/50 text-muted-foreground shrink-0">{filingFormLabel(item.form_type)}</span>
                            <div className="text-[13px] font-medium text-foreground line-clamp-2">{item.title}</div>
                          </div>
                          <ExternalLink className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                        </div>
                        {item.description && (
                          <div className="mt-1 text-[12px] text-muted-foreground line-clamp-2">{item.description}</div>
                        )}
                        <div className="mt-2 text-[11px] text-muted-foreground">
                          {insightSourceLabel(item.source)} · Filed {String(item.filed_at || '').slice(0, 10)}
                          {item.report_date ? ` · Period ${item.report_date}` : ''}
                        </div>
                      </a>
                    ))
                  )}
                </div>
              )
            })()}

            {tab === 'holders' && (() => {
              const breakdown = holders?.breakdown || []
              const institutions = holders?.institutions || []
              const insiderTx = holders?.insider_transactions || []
              const empty = breakdown.length === 0 && institutions.length === 0 && insiderTx.length === 0
              return (
                <div className="space-y-3">
                  {empty ? (
                    <div className="card p-6 text-[12px] text-muted-foreground text-center">
                      {holdersLoaded ? 'No holder data available' : 'Loading holders...'}
                    </div>
                  ) : (
                    <>
                      {breakdown.length > 0 && (
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
                          {breakdown.map((row, i) => (
                            <div key={`${row.holder}-${i}`} className="rounded bg-accent/30 px-2 py-1.5">
                              <div className="text-muted-foreground">{holderBreakdownLabel(row.holder)}</div>
                              <div className="font-semibold">{holderBreakdownValue(row)}</div>
                            </div>
                          ))}
                        </div>
                      )}

                      <div className="card p-4">
                        <div className="text-[12px] font-medium mb-2">Institutional holders</div>
                        {institutions.length === 0 ? (
                          <div className="text-[12px] text-muted-foreground">No institutional holders reported</div>
                        ) : (
                          <div className="overflow-x-auto -mx-1">
                            <table className="w-full text-[11px]">
                              <thead className="text-muted-foreground">
                                <tr className="border-b border-border/40">
                                  <th className="text-left px-1 py-1 font-normal">Holder</th>
                                  <th className="text-right px-1 py-1 font-normal">% out</th>
                                  <th className="text-right px-1 py-1 font-normal">Shares</th>
                                  <th className="text-right px-1 py-1 font-normal">Change</th>
                                </tr>
                              </thead>
                              <tbody>
                                {institutions.map((row, i) => (
                                  <tr key={`${row.holder}-${i}`} className="border-b border-border/20 hover:bg-accent/10">
                                    <td className="px-1 py-1">
                                      <span className="text-foreground">{row.holder}</span>
                                      {row.date && <span className="text-muted-foreground text-[10px] ml-1">({row.date})</span>}
                                    </td>
                                    <td className="px-1 py-1 text-right tabular-nums">{row.pct_out != null ? `${Number(row.pct_out).toFixed(2)}%` : '-'}</td>
                                    <td className="px-1 py-1 text-right tabular-nums text-foreground/80">{row.shares != null ? Number(row.shares).toLocaleString() : '-'}</td>
                                    <td className={`px-1 py-1 text-right tabular-nums ${
                                      row.change_pct == null ? 'text-muted-foreground' : row.change_pct > 0 ? 'text-stock-up' : row.change_pct < 0 ? 'text-stock-down' : ''
                                    }`}>
                                      {row.change_pct != null ? `${row.change_pct > 0 ? '+' : ''}${Number(row.change_pct).toFixed(2)}%` : '-'}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>

                      <div className="card p-4">
                        <div className="text-[12px] font-medium mb-2">Insider transactions</div>
                        {insiderTx.length === 0 ? (
                          <div className="text-[12px] text-muted-foreground">No insider transactions reported</div>
                        ) : (
                          <div className="overflow-x-auto -mx-1">
                            <table className="w-full text-[11px]">
                              <thead className="text-muted-foreground">
                                <tr className="border-b border-border/40">
                                  <th className="text-left px-1 py-1 font-normal">Date</th>
                                  <th className="text-left px-1 py-1 font-normal">Insider</th>
                                  <th className="text-left px-1 py-1 font-normal">Position</th>
                                  <th className="text-left px-1 py-1 font-normal">Transaction</th>
                                  <th className="text-right px-1 py-1 font-normal">Shares</th>
                                  <th className="text-right px-1 py-1 font-normal">Value</th>
                                </tr>
                              </thead>
                              <tbody>
                                {insiderTx.map((row, i) => (
                                  <tr key={`${row.holder}-${row.date}-${i}`} className="border-b border-border/20 hover:bg-accent/10">
                                    <td className="px-1 py-1 text-muted-foreground whitespace-nowrap">{row.date || '-'}</td>
                                    <td className="px-1 py-1">
                                      {row.url ? (
                                        <a href={row.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-foreground hover:underline">
                                          {row.holder}
                                          <ExternalLink className="w-3 h-3 text-muted-foreground shrink-0" />
                                        </a>
                                      ) : (
                                        <span className="text-foreground">{row.holder}</span>
                                      )}
                                    </td>
                                    <td className="px-1 py-1 text-muted-foreground">{row.position || '-'}</td>
                                    <td className={`px-1 py-1 ${insiderTransactionClass(row.transaction)}`}>{row.transaction || '-'}</td>
                                    <td className="px-1 py-1 text-right tabular-nums text-foreground/80">{row.shares != null ? Number(row.shares).toLocaleString() : '-'}</td>
                                    <td className="px-1 py-1 text-right tabular-nums text-foreground/80">{row.value != null ? formatCompactNumber(row.value) : '-'}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>

                      {holders?.source && (
                        <div className="text-[11px] text-muted-foreground">Source: {insightSourceLabel(holders.source)}</div>
                      )}
                    </>
                  )}
                </div>
              )
            })()}


          </div>
        </DialogContent>
      </Dialog>

    </>
  )
}

const DEEP_DECISION_COLOR: Record<string, string> = {
  // Matches the Western direction convention used for every other recommendation
  // badge in this file: buy tracks "up" (green), sell tracks "down" (red).
  buy: 'text-stock-up',
  hold: 'text-muted-foreground',
  sell: 'text-stock-down',
}

const DEEP_STAGE_LABEL: Record<string, string> = {
  market: 'Technical Analyst',
  social: 'Sentiment Analyst',
  news: 'News Analyst',
  fundamentals: 'Fundamentals Analyst',
}

function DeepAnalysisSection({
  loading,
  loaded,
  result,
  history,
  historyLoading,
  showAnalyst,
  setShowAnalyst,
  showDebate,
  setShowDebate,
  onRefresh,
}: {
  loading: boolean
  loaded: boolean
  result: DeepAnalysisResult | null
  history: HistoryComparisonResponse | null
  historyLoading: boolean
  showAnalyst: boolean
  setShowAnalyst: (v: boolean) => void
  showDebate: boolean
  setShowDebate: (v: boolean) => void
  onRefresh: () => void
}) {
  if (loading && !loaded) {
    return (
      <div className="card p-6 text-center text-[12px] text-muted-foreground">
        <span className="inline-block w-4 h-4 border-2 border-border border-t-muted-foreground rounded-full animate-spin mr-2 align-middle" />
        Loading deep analysis report...
      </div>
    )
  }
  if (!result && !history?.items?.length) {
    return (
      <div className="card p-6 text-center text-[12px] text-muted-foreground space-y-2">
        <div>No deep analysis report yet</div>
        <div className="text-[11px] text-muted-foreground/70">
          Click the 🧠 Deep Analysis button on the Positions / Watchlist page to trigger one
        </div>
      </div>
    )
  }

  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion
  const reports = rawData.analyst_reports || { market: '', social: '', news: '', fundamentals: '' }
  const debate = rawData.debate_history
  const costUsd = rawData.cost_usd

  return (
    <div className="space-y-3 text-[13px]">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] text-muted-foreground">
          TradingAgents Deep{result?.timestamp ? ` · ${result.timestamp.slice(0, 16).replace('T', ' ')}` : ''}
        </div>
        <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px]" aria-label="Refresh" onClick={onRefresh} disabled={loading || historyLoading}>
          <RefreshCw className={`w-3.5 h-3.5 ${loading || historyLoading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {sug && (
        <div className="rounded-lg bg-accent/30 p-4 space-y-2">
          <div className="flex items-center gap-3">
            <span className={`text-[20px] font-bold ${DEEP_DECISION_COLOR[sug.action] || ''}`}>
              {sug.action_label}
            </span>
            {typeof sug.confidence === 'number' && (
              <span className="text-[12px] text-muted-foreground">
                Confidence {sug.confidence.toFixed(1)} / 10
              </span>
            )}
          </div>
          {sug.reason && <div className="text-[12px] text-foreground/80">{sug.reason.slice(0, 240)}</div>}
          {typeof costUsd === 'number' && (
            <div className="text-[10px] text-muted-foreground mt-2">Cost: ${costUsd.toFixed(4)}</div>
          )}
        </div>
      )}

      <DeepHistoryComparison history={history} loading={historyLoading} />

      {result?.content && (
        <div className="rounded-lg border border-border/50 p-4">
          <div className="prose prose-sm dark:prose-invert max-w-none break-words">
            <ReactMarkdown>{result.content}</ReactMarkdown>
          </div>
        </div>
      )}

      {result && (
        <div>
          <button
            className="text-[12px] text-muted-foreground hover:text-foreground flex items-center gap-1"
            onClick={() => setShowAnalyst(!showAnalyst)}
          >
            {showAnalyst ? '▼' : '▶'} 4 Analyst Reports
          </button>
          {showAnalyst && (
            <div className="space-y-3 mt-2 pl-3 border-l-2 border-border/40">
              {(['market', 'social', 'news', 'fundamentals'] as const).map((k) => {
                const text = (reports as unknown as Record<string, string>)[k] || ''
                if (!text) return null
                return (
                  <details key={k} open className="text-[12px]">
                    <summary className="font-medium cursor-pointer">{DEEP_STAGE_LABEL[k] || k}</summary>
                    <div className="mt-2 text-[11px] text-foreground/80 whitespace-pre-wrap">
                      {text.slice(0, 1500)}
                      {text.length > 1500 && '... (truncated)'}
                    </div>
                  </details>
                )
              })}
            </div>
          )}
        </div>
      )}

      {debate && debate.history && (
        <div>
          <button
            className="text-[12px] text-muted-foreground hover:text-foreground flex items-center gap-1"
            onClick={() => setShowDebate(!showDebate)}
          >
            {showDebate ? '▼' : '▶'} Bull/Bear Debate
          </button>
          {showDebate && (
            <div className="mt-2 pl-3 border-l-2 border-border/40 text-[11px] text-foreground/80 whitespace-pre-wrap max-h-96 overflow-y-auto">
              {debate.history}
              {debate.judge_decision && (
                <>
                  <div className="font-medium mt-3 mb-1">Research Director's Verdict:</div>
                  <div>{debate.judge_decision}</div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      <div className="text-[10px] text-muted-foreground/70 italic border-t border-border/30 pt-2">
        This analysis is generated by an AI multi-agent framework, for learning and research reference only, and does not constitute investment advice.
      </div>
    </div>
  )
}

function DeepHistoryComparison({
  history,
  loading,
}: {
  history: HistoryComparisonResponse | null
  loading: boolean
}) {
  if (loading && !history) {
    return (
      <div className="rounded-lg border border-border/40 p-3 text-[11px] text-muted-foreground text-center">
        Loading historical comparison...
      </div>
    )
  }
  if (!history || history.items.length === 0) return null

  const stats = history.stats
  const fmtPct = (v: number | null): string => (v == null ? '-' : `${(v * 100).toFixed(0)}%`)
  const fmtRet = (v: number | null): string => (v == null ? '-' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
  const retCls = (v: number | null): string =>
    v == null ? 'text-muted-foreground' : v > 0 ? 'text-stock-up' : v < 0 ? 'text-stock-down' : 'text-muted-foreground'

  return (
    <div className="rounded-lg border border-border/50 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[12px] font-medium">Historical Decisions vs Actual Price Moves</div>
        <div className="text-[10px] text-muted-foreground">Based only on decisions with a full 20 trading days elapsed</div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">Overall Hit Rate</div>
          <div className="font-semibold">{fmtPct(stats.overall_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">Buy ({stats.buy_count})</div>
          <div className="font-semibold">{fmtPct(stats.buy_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">Sell ({stats.sell_count})</div>
          <div className="font-semibold">{fmtPct(stats.sell_hit_rate)}</div>
        </div>
        <div className="rounded bg-accent/30 px-2 py-1.5">
          <div className="text-muted-foreground">Avg 20-Day Return</div>
          <div className={`font-semibold ${retCls(stats.avg_return_20d_pct)}`}>{fmtRet(stats.avg_return_20d_pct)}</div>
        </div>
      </div>
      <div className="overflow-x-auto -mx-1 mt-2">
        <table className="w-full text-[11px]">
          <thead className="text-muted-foreground">
            <tr className="border-b border-border/40">
              <th className="text-left px-1 py-1 font-normal">Date</th>
              <th className="text-left px-1 py-1 font-normal">Decision</th>
              <th className="text-right px-1 py-1 font-normal">Price at Analysis</th>
              <th className="text-right px-1 py-1 font-normal">1D</th>
              <th className="text-right px-1 py-1 font-normal">5D</th>
              <th className="text-right px-1 py-1 font-normal">20D</th>
              <th className="text-center px-1 py-1 font-normal">Hit</th>
            </tr>
          </thead>
          <tbody>
            {history.items.map((item, i) => (
              <tr key={`${item.analysis_date}-${i}`} className="border-b border-border/20 hover:bg-accent/10">
                <td className="px-1 py-1 text-muted-foreground whitespace-nowrap">{item.analysis_date}</td>
                <td className="px-1 py-1">
                  <span className={DEEP_DECISION_COLOR[item.action] || ''}>{item.action_label}</span>
                  {typeof item.confidence === 'number' && (
                    <span className="text-muted-foreground text-[10px] ml-1">({item.confidence.toFixed(1)})</span>
                  )}
                </td>
                <td className="px-1 py-1 text-right text-foreground/80">{item.price_at_analysis ?? '-'}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_1d_pct)}`}>{fmtRet(item.return_1d_pct)}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_5d_pct)}`}>{fmtRet(item.return_5d_pct)}</td>
                <td className={`px-1 py-1 text-right ${retCls(item.return_20d_pct)}`}>{fmtRet(item.return_20d_pct)}</td>
                <td className="px-1 py-1 text-center">
                  {item.hit_20d == null ? <span className="text-muted-foreground">-</span> : item.hit_20d ? '✓' : '✗'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
