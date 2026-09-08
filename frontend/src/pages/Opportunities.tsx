import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, FilterX, PlayCircle, RefreshCw, SearchX, Share2 } from 'lucide-react'
import {
  recommendationsApi,
  stocksApi,
  type EntryCandidateItem,
  type StrategyCatalogItem,
  type StrategySignalItem,
  type StrategyStatsResponse,
} from '@tickerkeep/api'
import { DEFAULT_MARKET, EQUITY_MARKETS, MARKET_LABEL, marketLabel, type MarketView } from '@/lib/markets'
import { Button } from '@tickerkeep/base-ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@tickerkeep/base-ui/components/ui/select'
import { useLocalStorage } from '@/lib/utils'
import StockInsightModal from '@tickerkeep/biz-ui/components/stock-insight-modal'
import { resolveActiveActionLabel } from '@tickerkeep/biz-ui/components/suggestion-action'
import FactorWeightsPanel from '@/components/FactorWeightsPanel'
import SignalScoreShareCard from '@/components/SignalScoreShareCard'
import { Card } from '@tickerkeep/base-ui/components/ui/card'
import { EmptyState } from '@tickerkeep/base-ui/components/ui/empty-state'
import { InfoTip } from '@tickerkeep/base-ui/components/ui/tooltip'

type SourceFilter = 'all' | 'market_scan' | 'watchlist' | 'mixed'
type HoldingFilter = 'all' | 'held' | 'unheld'
type RiskFilter = 'all' | 'low' | 'medium' | 'high'

type GroupedSignal = {
  key: string
  primary: StrategySignalItem
  members: StrategySignalItem[]
  strategyNames: string[]
  sourceAgents: string[]
  hasMarketScan: boolean
  topScore: number
}

const sourceAgentLabelMap: Record<string, string> = {
  premarket_outlook: 'Pre-market Outlook',
  intraday_monitor: 'Intraday Monitor',
  daily_report: 'Post-market Review',
  news_digest: 'News Digest',
  market_scan: 'Market Scan',
}

const sourceAgentLabel = (agent?: string) => {
  const key = (agent || '').trim()
  if (!key) return '--'
  return sourceAgentLabelMap[key] || key
}

const formatPlanPrice = (value: number | null | undefined) => {
  if (value == null || Number.isNaN(value)) return '--'
  const abs = Math.abs(value)
  const fixed = abs >= 100 ? 2 : abs >= 1 ? 3 : 4
  return Number(value).toFixed(fixed).replace(/\.?0+$/, '')
}

const toNumberOrNull = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const num = Number(value)
    if (Number.isFinite(num)) return num
  }
  return null
}

const sleep = (ms: number) => new Promise<void>((resolve) => {
  window.setTimeout(resolve, ms)
})

const formatMetric = (value: unknown, digits = 1) => {
  const n = toNumberOrNull(value)
  if (n == null) return '--'
  return n.toFixed(digits)
}

const DEFAULT_FILTERS = {
  market: 'ALL' as const,
  source: 'all' as const,
  holding: 'unheld' as const,
  strategy: 'all',
  risk: 'all' as const,
  minScore: '70',
}

/** Verdict pill for the agent's recommendation. This is not a price move, so
 *  it never borrows the stock-up/stock-down direction colours or the lime
 *  accent - only weight distinguishes an actionable call from a watch call. */
const verdictClass = (action?: string) => {
  const key = (action || '').toLowerCase()
  if (key === 'buy' || key === 'add') return 'verdict bg-foreground/10 text-foreground'
  return 'verdict chip-neutral'
}

const displayActionLabel = (item: StrategySignalItem) => {
  const action = (item.action || '').toLowerCase()
  if (!item.is_holding_snapshot && action === 'hold') return 'Watch'
  if (!item.is_holding_snapshot && action === 'add') return 'Open position'
  return resolveActiveActionLabel(item.action, item.action_label)
}

const scoreOf = (item: StrategySignalItem) => Number(item.rank_score || item.score || 0)

const actionPriority = (item: StrategySignalItem) => {
  const key = (item.action || '').toLowerCase()
  if (key === 'buy') return 4
  if (key === 'add') return 3
  if (key === 'hold') return item.is_holding_snapshot ? 2 : 1
  return 0
}

const hasEntryPlan = (item: StrategySignalItem) => {
  const breakdown = item.score_breakdown || {}
  if (typeof breakdown.has_entry_plan === 'boolean') return breakdown.has_entry_plan
  return toNumberOrNull(item.entry_low) != null || toNumberOrNull(item.entry_high) != null
}

const itemTimestamp = (item: StrategySignalItem) => {
  const t = Date.parse(item.updated_at || item.created_at || '')
  return Number.isFinite(t) ? t : 0
}

const shouldReplacePrimary = (next: StrategySignalItem, current: StrategySignalItem) => {
  const activeDelta = Number((next.status || '').toLowerCase() === 'active') - Number((current.status || '').toLowerCase() === 'active')
  if (activeDelta !== 0) return activeDelta > 0
  const actionDelta = actionPriority(next) - actionPriority(current)
  if (actionDelta !== 0) return actionDelta > 0
  const entryDelta = Number(hasEntryPlan(next)) - Number(hasEntryPlan(current))
  if (entryDelta !== 0) return entryDelta > 0
  const scoreDelta = scoreOf(next) - scoreOf(current)
  if (Math.abs(scoreDelta) > 0.001) return scoreDelta > 0
  return itemTimestamp(next) > itemTimestamp(current)
}

const toSignalFromCandidate = (row: EntryCandidateItem): StrategySignalItem => {
  const source = row.candidate_source || 'watchlist'
  const sourceLabel = row.candidate_source_label || (source === 'market_scan' ? 'Market Pool' : source === 'mixed' ? 'Market + Watchlist' : 'Watchlist Pool')
  const riskLevel: 'low' | 'medium' | 'high' = Number(row.score || 0) >= 85 ? 'high' : Number(row.score || 0) >= 70 ? 'medium' : 'low'
  const riskLabel = riskLevel === 'high' ? 'High Risk' : riskLevel === 'low' ? 'Low Risk' : 'Medium Risk'
  return {
    id: Number(row.id || 0),
    snapshot_date: row.snapshot_date || '',
    stock_symbol: row.stock_symbol,
    stock_market: row.stock_market || DEFAULT_MARKET,
    stock_name: row.stock_name || row.stock_symbol,
    strategy_code: (row.strategy_tags && row.strategy_tags[0]) || 'watchlist_agent',
    strategy_name: (row.strategy_labels && row.strategy_labels[0]) || 'Candidate Recommendation',
    strategy_version: 'v1',
    risk_level: riskLevel,
    risk_level_label: riskLabel,
    source_pool: source,
    source_pool_label: sourceLabel,
    score: Number(row.score || 0),
    rank_score: Number(row.score || 0),
    confidence: row.confidence ?? null,
    status: row.status || 'inactive',
    action: row.action || 'watch',
    action_label: row.action_label || 'Watch',
    signal: row.signal || '',
    reason: row.reason || '',
    evidence: row.evidence || [],
    holding_days: 3,
    entry_low: row.entry_low ?? null,
    entry_high: row.entry_high ?? null,
    stop_loss: row.stop_loss ?? null,
    target_price: row.target_price ?? null,
    invalidation: row.invalidation || '',
    plan_quality: row.plan_quality ?? 0,
    source_agent: row.source_agent || '',
    source_suggestion_id: row.source_suggestion_id ?? null,
    source_candidate_id: row.id ?? null,
    trace_id: '',
    is_holding_snapshot: !!row.is_holding_snapshot,
    context_quality_score: null,
    score_breakdown: {
      weighted_score: Number(row.score || 0),
      has_entry_plan: !!(row.entry_low != null || row.entry_high != null),
    },
    market_regime: {},
    cross_feature: {},
    news_metric: {},
    constrained: false,
    constraint_reasons: [],
    payload: {
      source_meta: {
        plan: row.plan || {},
      },
    },
    created_at: row.created_at || '',
    updated_at: row.updated_at || row.created_at || '',
  }
}

const formatEntryDisplay = (action: string | undefined, entryLow: number | null, entryHigh: number | null) => {
  if (entryLow != null || entryHigh != null) {
    return `${formatPlanPrice(entryLow)} ~ ${formatPlanPrice(entryHigh)}`
  }
  const key = (action || '').toLowerCase()
  if (key === 'buy' || key === 'add') return 'Entry level TBD'
  return 'Opening a position is not currently recommended'
}

/** Market regime is an aggregate price-direction call for the whole market,
 *  so - unlike an agent's per-stock verdict - it legitimately reuses the
 *  CN/HK direction chips. */
const regimeToneClass = (regime?: string) => {
  if (regime === 'bullish') return 'chip-up'
  if (regime === 'bearish') return 'chip-down'
  return 'chip-neutral'
}

export default function OpportunitiesPage() {
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [items, setItems] = useState<StrategySignalItem[]>([])
  const [stats, setStats] = useState<StrategyStatsResponse | null>(null)
  const [strategyCatalog, setStrategyCatalog] = useState<StrategyCatalogItem[]>([])
  const [watchlist, setWatchlist] = useState<Set<string>>(new Set())

  // Key bumped to v4 when the market list changed, so a stored retired code is not read back.
  const [market, setMarket] = useLocalStorage<MarketView>('tickerkeep_opportunities_market_v4', DEFAULT_FILTERS.market)
  const [source, setSource] = useLocalStorage<SourceFilter>('tickerkeep_opportunities_source_v3', DEFAULT_FILTERS.source)
  const [holding, setHolding] = useLocalStorage<HoldingFilter>('tickerkeep_opportunities_holding_v3', DEFAULT_FILTERS.holding)
  const [strategy, setStrategy] = useLocalStorage('tickerkeep_opportunities_strategy_v3', DEFAULT_FILTERS.strategy)
  const [risk, setRisk] = useLocalStorage<RiskFilter>('tickerkeep_opportunities_risk_v3', DEFAULT_FILTERS.risk)
  const [minScore, setMinScore] = useLocalStorage('tickerkeep_opportunities_min_score_v3', DEFAULT_FILTERS.minScore)
  const [snapshotDate, setSnapshotDate] = useState('')

  const [insightOpen, setInsightOpen] = useState(false)
  const [insightSymbol, setInsightSymbol] = useState('')
  const [insightMarket, setInsightMarket] = useState<string>(DEFAULT_MARKET)
  const [insightName, setInsightName] = useState<string | undefined>(undefined)
  const [insightHasPosition, setInsightHasPosition] = useState(false)

  // 个股 AI 评分分享卡:当前分享的信号
  const [shareSignal, setShareSignal] = useState<StrategySignalItem | null>(null)

  const openInsight = useCallback((item: StrategySignalItem) => {
    setInsightSymbol(item.stock_symbol)
    setInsightMarket(item.stock_market || DEFAULT_MARKET)
    setInsightName(item.stock_name)
    setInsightHasPosition(!!item.is_holding_snapshot)
    setInsightOpen(true)
  }, [])

  const loadWatchlist = useCallback(async () => {
    try {
      const rows = await stocksApi.list()
      const set = new Set<string>((rows || []).map((s) => `${s.market}:${s.symbol}`))
      setWatchlist(set)
    } catch {
      setWatchlist(new Set())
    }
  }, [])

  const loadStats = useCallback(async () => {
    try {
      const s = await recommendationsApi.getStrategyStats(45)
      setStats(s)
    } catch {
      setStats(null)
    }
  }, [])

  const loadCatalog = useCallback(async () => {
    try {
      const res = await recommendationsApi.listStrategyCatalog(true)
      setStrategyCatalog(res.items || [])
    } catch {
      setStrategyCatalog([])
    }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const req = {
        status: 'active' as const,
        source_pool: source,
        holding,
        market: market === 'ALL' ? '' : market,
        strategy_code: strategy === 'all' ? '' : strategy,
        risk_level: risk,
        min_score: Number(minScore) || 0,
        limit: 120,
        include_payload: false,
      }
      let data: Awaited<ReturnType<typeof recommendationsApi.listStrategySignals>>
      try {
        data = await recommendationsApi.listStrategySignals({
          ...req,
          timeoutMs: 45000,
        })
      } catch (firstErr) {
        const msg = firstErr instanceof Error ? firstErr.message : ''
        if (!msg.includes('超时') && !msg.toLowerCase().includes('timed out')) throw firstErr
        try {
          // Retry once for transient DB lock/contention.
          data = await recommendationsApi.listStrategySignals({
            ...req,
            timeoutMs: 90000,
          })
        } catch (secondErr) {
          const secondMsg = secondErr instanceof Error ? secondErr.message : ''
          if (!secondMsg.includes('超时') && !secondMsg.toLowerCase().includes('timed out')) throw secondErr
          const fallback = await recommendationsApi.listEntryCandidates({
            market: req.market,
            status: 'active',
            min_score: req.min_score,
            limit: req.limit,
            snapshot_date: '',
            source: source === 'all' ? 'all' : source,
            holding: req.holding,
            timeoutMs: 90000,
          })
          data = {
            snapshot_date: fallback.snapshot_date || '',
            count: fallback.count || 0,
            items: (fallback.items || []).map(toSignalFromCandidate),
          }
          setError('Strategy layer request timed out - showing candidate snapshot as a fallback')
        }
      }
      if ((!data.items || data.items.length === 0) && market !== 'ALL') {
        const fallback = await recommendationsApi.listStrategySignals({
          ...req,
          market: '',
          timeoutMs: 45000,
        })
        if (fallback.items && fallback.items.length > 0) {
          setError(`No qualifying opportunities in ${marketLabel(market)} right now - showing all-market results instead`)
          data = fallback
        }
      }
      setItems(data.items || [])
      setSnapshotDate(data.snapshot_date || '')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [holding, market, minScore, risk, source, strategy])

  useEffect(() => {
    load()
    loadStats()
    loadCatalog()
    loadWatchlist()
  }, [load, loadCatalog, loadStats, loadWatchlist])

  const pollRefreshCompletion = useCallback(async () => {
    const maxPolls = 20
    for (let i = 0; i < maxPolls; i += 1) {
      try {
        const state = await recommendationsApi.getStrategyRefreshStatus()
        if (!state.running) {
          if (state.last_error) {
            setError(`Background refresh failed: ${state.last_error}`)
          } else {
            setError('')
          }
          await Promise.all([load(), loadStats()])
          return
        }
      } catch {
        // Ignore transient polling error and continue.
      }
      await sleep(3000)
    }
    await Promise.all([load(), loadStats()])
    setError((prev) => prev || 'Refresh is still running in the background - please try again later')
  }, [load, loadStats])

  const handleRefresh = async () => {
    setRefreshing(true)
    setError('')
    try {
      const resp = await recommendationsApi.refreshStrategySignals({
        rebuild_candidates: true,
        max_inputs: 500,
        market_scan_limit: 80,
        max_kline_symbols: 60,
        limit_candidates: 2000,
        wait: false,
      })
      if (resp.queued) {
        setError(resp.accepted ? 'Background refresh task submitted - will update automatically when done' : 'A refresh task is already running - will update automatically when done')
        void pollRefreshCompletion()
        return
      }
      await Promise.all([load(), loadStats()])
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Refresh failed'
      if (msg.includes('超时') || msg.toLowerCase().includes('timed out')) {
        setError('Refresh is taking a while and is continuing in the background - click Refresh again shortly')
        await load()
      } else {
        setError(msg)
      }
    } finally {
      setRefreshing(false)
    }
  }

  const resetFilters = useCallback(() => {
    setMarket(DEFAULT_FILTERS.market)
    setSource(DEFAULT_FILTERS.source)
    setHolding(DEFAULT_FILTERS.holding)
    setStrategy(DEFAULT_FILTERS.strategy)
    setRisk(DEFAULT_FILTERS.risk)
    setMinScore(DEFAULT_FILTERS.minScore)
  }, [setHolding, setMarket, setMinScore, setRisk, setSource, setStrategy])

  const strategyOptions = useMemo(() => {
    return strategyCatalog.map((row) => ({ value: row.code, label: row.name || row.code }))
  }, [strategyCatalog])

  const groupedItems = useMemo<GroupedSignal[]>(() => {
    const grouped = new Map<string, { primary: StrategySignalItem; members: StrategySignalItem[] }>()
    for (const row of items) {
      const key = `${row.stock_market || DEFAULT_MARKET}:${row.stock_symbol}`
      const prev = grouped.get(key)
      if (!prev) {
        grouped.set(key, { primary: row, members: [row] })
        continue
      }
      prev.members.push(row)
      if (shouldReplacePrimary(row, prev.primary)) {
        prev.primary = row
      }
    }

    const out: GroupedSignal[] = []
    for (const [key, val] of grouped.entries()) {
      const strategyNames = Array.from(new Set(val.members.map((x) => x.strategy_name || x.strategy_code).filter(Boolean)))
      const sourceAgents = Array.from(new Set(val.members.map((x) => sourceAgentLabel(x.source_agent)).filter((x) => x && x !== '--')))
      const hasMarketScan = val.members.some((x) => x.source_pool === 'market_scan' || x.source_pool === 'mixed')
      const topScore = Math.max(...val.members.map(scoreOf))
      out.push({
        key,
        primary: val.primary,
        members: val.members,
        strategyNames,
        sourceAgents,
        hasMarketScan,
        topScore,
      })
    }
    out.sort((a, b) => {
      const sourceDelta = Number(b.hasMarketScan) - Number(a.hasMarketScan)
      if (sourceDelta !== 0) return sourceDelta
      const scoreDelta = b.topScore - a.topScore
      if (Math.abs(scoreDelta) > 0.001) return scoreDelta
      return actionPriority(b.primary) - actionPriority(a.primary)
    })
    return out
  }, [items])

  const filteredSummary = useMemo(() => {
    const total = groupedItems.length
    const unheld = groupedItems.filter((x) => !x.primary.is_holding_snapshot).length
    const marketPool = groupedItems.filter((x) => x.hasMarketScan).length
    return { total, unheld, marketPool }
  }, [groupedItems])

  const globalCoverage = stats?.coverage || null
  const factorStats = stats?.factor_stats || null
  const constraintStats = stats?.constraints || null

  const outcome3d = useMemo(() => {
    const rows = (stats?.by_strategy || []).filter((x) => Number(x.horizon_days) === 3)
    if (!rows.length) return null
    let sample = 0
    let wins = 0
    for (const r of rows) {
      sample += Number(r.sample_size || 0)
      wins += Number(r.wins || 0)
    }
    if (!sample) return null
    return {
      total: sample,
      win_rate: (wins / sample) * 100,
    }
  }, [stats])

  const regimeSummary = useMemo(() => {
    return (stats?.regimes || []).map((r) => ({
      market: r.market,
      label: r.regime_label || r.regime || 'Choppy',
      regime: r.regime || 'neutral',
      confidence: Number(r.confidence || 0),
      score: Number(r.regime_score || 0),
    }))
  }, [stats])

  const riskSummary = useMemo(() => {
    return (stats?.portfolio_risk || []).map((r) => ({
      market: r.market,
      riskLevel: r.risk_level || 'medium',
      concentration: Number(r.concentration_top5 || 0),
      highRiskRatio: Number(r.high_risk_ratio || 0),
    }))
  }, [stats])

  return (
    <div className="page-container pb-10">
      {/* Panel header for the first real panel below: the room shell owns the page title,
          so this page starts here with just the controls that were on the old page header. */}
      <div className="flex items-center justify-between gap-3 mb-3">
        <span className="section-sub">
          {snapshotDate ? `Snapshot ${snapshotDate}` : 'Latest snapshot'}
        </span>
        <Button
          variant="secondary"
          size="sm"
          onClick={handleRefresh}
          disabled={refreshing}
        >
          {refreshing ? <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          Refresh
        </Button>
      </div>

      {/* Coverage strip: unified stat-strip primitive - same pattern as the Portfolio page's stat strip */}
      <Card variant="strip" className="mb-4 grid-cols-2 md:grid-cols-4 md:divide-y-0">
        <div className="min-w-0 p-4">
          <div className="stat-label truncate">Current Candidates (Global)</div>
          <div className="stat-value mt-1 truncate">{globalCoverage?.total_signals ?? '--'}</div>
          <div className="helper-text mt-1 truncate">
            Actionable: {globalCoverage?.active_signals ?? '--'}, Watching: {(globalCoverage?.total_signals != null && globalCoverage?.active_signals != null) ? Math.max(0, globalCoverage.total_signals - globalCoverage.active_signals) : '--'}
          </div>
        </div>
        <div className="min-w-0 p-4">
          <div className="stat-label flex min-w-0 items-center gap-1">
            <span className="truncate">Market Pool Share</span>
            <InfoTip label="Share of the current candidates sourced from a full market scan, rather than the watchlist-only pool." />
          </div>
          <div className="stat-value mt-1 truncate">{globalCoverage?.market_scan_share_pct != null ? `${globalCoverage.market_scan_share_pct.toFixed(1)}%` : '--'}</div>
          <div className="helper-text mt-1 truncate">
            Market pool: {globalCoverage?.market_scan_signals ?? '--'}, Watchlist pool: {globalCoverage?.watchlist_signals ?? '--'}, Mixed: {globalCoverage?.mixed_signals ?? '--'}
          </div>
        </div>
        <div className="min-w-0 p-4">
          <div className="stat-label truncate">Current Filter Results</div>
          <div className="stat-value mt-1 truncate">{filteredSummary.total}</div>
          <div className="helper-text mt-1 truncate">
            Not held: {filteredSummary.unheld}, Market pool: {filteredSummary.marketPool}
          </div>
        </div>
        <div className="min-w-0 p-4">
          <div className="stat-label flex min-w-0 items-center gap-1">
            <span className="truncate">3-Day Win Rate (auto-evaluated)</span>
            <InfoTip label="Win rate of signals measured 3 trading days after they fired, computed automatically from realized price outcomes." />
          </div>
          <div className="stat-value mt-1 truncate">{outcome3d ? `${outcome3d.win_rate.toFixed(1)}%` : '--'}</div>
          <div className="helper-text mt-1 truncate">
            Auto sample: {outcome3d ? `${outcome3d.total}` : '--'}
          </div>
        </div>
      </Card>

      {(factorStats || constraintStats) && (
        <Card variant="strip" className="mb-4 grid-cols-2 md:grid-cols-4 md:divide-y-0">
          <div className="min-w-0 p-4">
            <div className="stat-label flex min-w-0 items-center gap-1">
              <span className="truncate">Avg Alpha Factor</span>
              <InfoTip label="Average contribution from each candidate's own technical strength and relative performance versus its market." />
            </div>
            <div className="stat-value mt-1 truncate">{factorStats ? factorStats.avg_alpha_score.toFixed(1) : '--'}</div>
            <div className="helper-text mt-1 truncate">Sample {factorStats?.sample_size ?? '--'}</div>
          </div>
          <div className="min-w-0 p-4">
            <div className="stat-label flex min-w-0 items-center gap-1">
              <span className="truncate">Avg Event Catalyst</span>
              <InfoTip label="Average contribution from recent news, events, and breakout momentum near the signal date." />
            </div>
            <div className="stat-value mt-1 truncate">{factorStats ? factorStats.avg_catalyst_score.toFixed(1) : '--'}</div>
            <div className="helper-text mt-1 truncate">
              Crowding penalty {factorStats ? factorStats.avg_crowd_penalty.toFixed(1) : '--'}
            </div>
          </div>
          <div className="min-w-0 p-4">
            <div className="stat-label flex min-w-0 items-center gap-1">
              <span className="truncate">Avg Quality/Risk</span>
              <InfoTip label="Average contribution from how well-defined each entry plan is (Quality), and average points deducted for elevated risk flags (Risk)." />
            </div>
            <div className="stat-value mt-1 truncate">
              {factorStats ? `${factorStats.avg_quality_score.toFixed(1)} / ${factorStats.avg_risk_penalty.toFixed(1)}` : '--'}
            </div>
            <div className="helper-text mt-1 truncate">Higher quality score is better</div>
          </div>
          <div className="min-w-0 p-4">
            <div className="stat-label flex min-w-0 items-center gap-1">
              <span className="truncate">Portfolio-Constraint Demotions</span>
              <InfoTip label="Top-20 candidates whose score was capped because too many already share the same strategy in that market - a concentration control, not a quality judgment." />
            </div>
            <div className="stat-value mt-1 truncate">{constraintStats?.constrained_top20 ?? 0}</div>
            <div className="helper-text mt-1 truncate">Number demoted by risk control in Top20</div>
          </div>
        </Card>
      )}

      {(regimeSummary.length > 0 || riskSummary.length > 0) && (
        <div className="card p-3 mb-4">
          <div className="group-title mb-2">Market Regime & Portfolio Risk</div>
          <div className="flex flex-wrap gap-2">
            {regimeSummary.map((r) => (
              <span key={`regime-${r.market}`} className={regimeToneClass(r.regime)}>
                {marketLabel(r.market)}: {r.label} · Confidence {Math.round(r.confidence * 100)}%
              </span>
            ))}
            {riskSummary.map((r) => (
              <span key={`risk-${r.market}`} className="chip-neutral">
                {marketLabel(r.market)} risk: {r.riskLevel} · Concentration {(r.concentration * 100).toFixed(0)}% · High-risk share {(r.highRiskRatio * 100).toFixed(0)}%
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="card p-3 md:p-4 mb-4">
        <div className="grid grid-cols-2 md:grid-cols-8 gap-2">
          <Select value={market} onValueChange={(v) => setMarket(v as MarketView)}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">All Markets</SelectItem>
              {EQUITY_MARKETS.map(m => <SelectItem key={m} value={m}>{MARKET_LABEL[m]}</SelectItem>)}
            </SelectContent>
          </Select>
          <Select value={source} onValueChange={(v) => setSource(v as SourceFilter)}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Sources</SelectItem>
              <SelectItem value="market_scan">Market Pool</SelectItem>
              <SelectItem value="mixed">Mixed Pool</SelectItem>
              <SelectItem value="watchlist">Watchlist Pool</SelectItem>
            </SelectContent>
          </Select>
          <Select value={holding} onValueChange={(v) => setHolding(v as HoldingFilter)}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Position States</SelectItem>
              <SelectItem value="unheld">Not Held Only</SelectItem>
              <SelectItem value="held">Held Only</SelectItem>
            </SelectContent>
          </Select>
          <Select value={strategy} onValueChange={setStrategy}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Strategies</SelectItem>
              {strategyOptions.map((op) => (
                <SelectItem key={op.value} value={op.value}>{op.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={risk} onValueChange={(v) => setRisk(v as RiskFilter)}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Risk Levels</SelectItem>
              <SelectItem value="low">Low Risk</SelectItem>
              <SelectItem value="medium">Medium Risk</SelectItem>
              <SelectItem value="high">High Risk</SelectItem>
            </SelectContent>
          </Select>
          <Select value={minScore} onValueChange={setMinScore}>
            <SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="90">Score 90+</SelectItem>
              <SelectItem value="80">Score 80+</SelectItem>
              <SelectItem value="70">Score 70+</SelectItem>
              <SelectItem value="60">Score 60+</SelectItem>
              <SelectItem value="50">Score 50+</SelectItem>
              <SelectItem value="0">No Score Filter</SelectItem>
            </SelectContent>
          </Select>
          <Button size="sm" className="h-8 text-[12px]" onClick={load} disabled={loading}>
            {loading ? 'Loading...' : 'Apply Filters'}
          </Button>
          <Button variant="ghost" size="sm" className="h-8 text-[12px]" onClick={resetFilters}>
            Clear Filters
          </Button>
        </div>
      </div>

      {error && (
        <div className="card p-3 mb-4 text-[12px] text-destructive flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {loading && groupedItems.length === 0 && Array.from({ length: 4 }).map((_, i) => (
          <div key={`skeleton-${i}`} className="card p-4">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0 flex-1">
                <span className="skeleton block h-4" style={{ width: `${40 + (i % 3) * 10}%` }} />
                <span className="skeleton mt-1.5 block h-3 w-24" />
              </div>
              <div className="text-right">
                <span className="skeleton inline-block h-5 w-14 rounded-full" />
                <span className="skeleton mt-1.5 ml-auto block h-3 w-12" />
              </div>
            </div>
            <span className="skeleton mt-3 block h-3 w-full" />
            <span className="skeleton mt-1.5 block h-3 w-2/3" />
            <div className="mt-3 grid grid-cols-2 gap-2">
              {Array.from({ length: 8 }).map((_, j) => (
                <span key={j} className="skeleton h-3" style={{ width: `${50 + ((i + j) % 3) * 15}%` }} />
              ))}
            </div>
            <div className="mt-3 flex items-center justify-between">
              <span className="skeleton h-3 w-20" />
              <span className="skeleton h-3 w-16" />
            </div>
          </div>
        ))}
        {groupedItems.map((group) => {
          const item = group.primary
          const payload = item.payload && typeof item.payload === 'object' ? item.payload as Record<string, unknown> : {}
          const sourceMeta = payload.source_meta && typeof payload.source_meta === 'object' ? payload.source_meta as Record<string, unknown> : {}
          const sourcePlan = sourceMeta.plan && typeof sourceMeta.plan === 'object' ? sourceMeta.plan as Record<string, unknown> : {}
          const entryLow = toNumberOrNull(item.entry_low) ?? toNumberOrNull(sourcePlan.entry_low)
          const entryHigh = toNumberOrNull(item.entry_high) ?? toNumberOrNull(sourcePlan.entry_high)
          const stopLoss = toNumberOrNull(item.stop_loss) ?? toNumberOrNull(sourcePlan.stop_loss)
          const targetPrice = toNumberOrNull(item.target_price) ?? toNumberOrNull(sourcePlan.target_price)
          const stateKey = `${item.snapshot_date}:${group.key}`
          const inWatchlist = watchlist.has(group.key)
          const breakdown = item.score_breakdown || {}
          const marketRegime = item.market_regime || {}
          const crossFeature = item.cross_feature || {}
          const newsMetric = item.news_metric || {}
          const strategyHead = group.strategyNames.slice(0, 2).join(' / ') || (item.strategy_name || item.strategy_code)
          const strategyTailCount = Math.max(0, group.strategyNames.length - 2)
          const sourceAgentHead = group.sourceAgents[0] || sourceAgentLabel(item.source_agent)
          const sourceAgentTailCount = Math.max(0, group.sourceAgents.length - 1)
          const eventScore = toNumberOrNull(newsMetric.event_score)
          const eventCount = Number(newsMetric.news_count || 0)
          const sourceFlags: string[] = []
          if (group.hasMarketScan) sourceFlags.push('Market candidate')
          if (inWatchlist) sourceFlags.push('Watchlisted')
          if (sourceFlags.length <= 0) sourceFlags.push('Watchlist pool')
          const sourcePoolLabel = group.hasMarketScan
            ? (group.members.some((x) => x.source_pool === 'mixed') ? 'Market + Watchlist' : 'Market Pool')
            : (item.source_pool_label || 'Watchlist Pool')
          return (
            <div key={stateKey} className="card p-4">
              <button className="w-full text-left row-interactive rounded-md -m-1 p-1" onClick={() => openInsight(item)}>
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-[15px] font-semibold truncate">{item.stock_name || item.stock_symbol}</div>
                    <div className="text-[11px] text-muted-foreground font-mono">{item.stock_market}:{item.stock_symbol}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-[12px]">
                      <span className={verdictClass(item.action)}>
                        {displayActionLabel(item)}
                      </span>
                    </div>
                    <div
                      className={`text-[12px] font-mono mt-1 tabular-nums ${Number(item.rank_score || item.score || 0) >= 80 ? 'text-foreground font-semibold' : 'text-muted-foreground'}`}
                      title="0-100 composite ranking score: weighted alpha, catalyst and quality contributions plus a source bonus, minus risk and crowding penalties."
                    >
                      Score {Math.round(item.rank_score || item.score || 0)}
                    </div>
                    {item.ai_score != null && (
                      <div className="mt-1 flex items-center justify-end gap-1">
                        <span className="text-[10px] text-muted-foreground">Rank</span>
                        <span
                          className="chip-neutral min-w-[18px] justify-center px-1.5 py-0.5 font-semibold tabular-nums"
                          title="1-10 band derived from the composite ranking score above (Score / 10, rounded). A ranking transformation, not a calibrated confidence or a probability of profit."
                        >
                          {item.ai_score}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
                <div className="mt-2 text-[12px] text-foreground line-clamp-2">{item.signal || item.reason || '--'}</div>
                <div className="mt-2 grid grid-cols-2 gap-2 helper-text">
                  <div>Entry: {formatEntryDisplay(item.action, entryLow, entryHigh)}</div>
                  <div>Stop loss: {formatPlanPrice(stopLoss)}</div>
                  <div>Target: {formatPlanPrice(targetPrice)}</div>
                  <div>Invalidation: {item.invalidation || '--'}</div>
                  <div>
                    Strategy: <span className="font-medium text-foreground">{strategyHead}{strategyTailCount > 0 ? ` +${strategyTailCount}` : ''}</span>
                  </div>
                  <div>Source pool: <span className="font-medium text-foreground">{sourcePoolLabel}</span></div>
                  <div>
                    Source Agent: <span className="font-medium text-foreground">{sourceAgentHead}{sourceAgentTailCount > 0 ? ` +${sourceAgentTailCount}` : ''}</span>
                  </div>
                  <div>Risk: {item.risk_level_label || item.risk_level || '--'}</div>
                  <div>Market regime: {marketRegime.regime_label || marketRegime.regime || '--'}</div>
                  <div>Position: {item.is_holding_snapshot ? 'Held' : 'Not held'}</div>
                  <div>Market: {marketLabel(item.stock_market)}</div>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 helper-text">
                  <div>Alpha: {formatMetric(breakdown.alpha_score)}</div>
                  <div>Catalyst: {formatMetric(breakdown.catalyst_score)}</div>
                  <div>Quality: {formatMetric(breakdown.quality_score)}</div>
                  <div>Risk penalty: {formatMetric(breakdown.risk_penalty)}</div>
                  <div>Relative strength: {crossFeature.relative_strength_pct != null ? `${Number(crossFeature.relative_strength_pct).toFixed(0)}th pct` : '--'}</div>
                  <div>Event catalyst: {eventScore != null ? eventScore.toFixed(1) : '--'}{eventCount > 0 ? ` (${eventCount} item(s))` : ' (no hits)'}</div>
                </div>
                {item.factor_explain && (((item.factor_explain.positive?.length ?? 0) > 0) || ((item.factor_explain.negative?.length ?? 0) > 0)) && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {(item.factor_explain.positive ?? []).map((f) => (
                      <span key={`p-${f.factor}`} className="chip-neutral">
                        {f.label} +{Math.abs(f.contribution).toFixed(1)}
                      </span>
                    ))}
                    {(item.factor_explain.negative ?? []).map((f) => (
                      <span key={`n-${f.factor}`} className="chip-neutral">
                        {f.label} {f.contribution.toFixed(1)}
                      </span>
                    ))}
                  </div>
                )}
                {item.constrained && (
                  <div className="helper-text mt-2">
                    Portfolio constraint: {(item.constraint_reasons || []).join('; ') || 'Auto-demoted'}
                  </div>
                )}
              </button>

              <div className="mt-3 flex items-center justify-between">
                <div className="text-[10px] text-muted-foreground">
                  Source: {sourceFlags.join(' + ')}
                </div>
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={() => setShareSignal(item)}
                    className="inline-flex items-center gap-1 text-[10px] text-muted-foreground transition-colors duration-150 hover:text-foreground"
                    title="Generate an AI score share image"
                  >
                    <Share2 className="h-3 w-3" />
                    Share image
                  </button>
                  <div className="text-[10px] text-muted-foreground">Evaluation: auto post-hoc</div>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {!loading && !snapshotDate && (
        <EmptyState
          size="md"
          icon={PlayCircle}
          title="No scan has run yet"
          description="Run a scan to generate strategy signals and opportunity candidates across the markets you track."
          action={
            <Button size="sm" onClick={handleRefresh} disabled={refreshing}>
              {refreshing ? 'Refreshing...' : 'Refresh'}
            </Button>
          }
          className="card mt-4"
        />
      )}

      {!loading && snapshotDate && groupedItems.length === 0 && (globalCoverage?.total_signals ?? 0) === 0 && (
        <EmptyState
          size="md"
          icon={SearchX}
          title="No opportunities found"
          description="The latest scan did not surface any qualifying candidates. Try again after the next market session, or run a fresh scan now."
          action={
            <Button size="sm" onClick={handleRefresh} disabled={refreshing}>
              {refreshing ? 'Refreshing...' : 'Refresh'}
            </Button>
          }
          className="card mt-4"
        />
      )}

      {!loading && snapshotDate && groupedItems.length === 0 && (globalCoverage?.total_signals ?? 0) > 0 && (
        <EmptyState
          size="md"
          icon={FilterX}
          title="No candidates match these filters"
          description="Candidates exist in the current snapshot, but none pass the selected market, source, risk, or score filters."
          action={
            <Button variant="outline" size="sm" onClick={resetFilters}>
              Clear Filters
            </Button>
          }
          className="card mt-4"
        />
      )}

      <details className="mt-6 group">
        <summary className="cursor-pointer list-none flex items-center gap-2 text-[12px] font-medium text-muted-foreground hover:text-foreground transition-colors">
          <span className="text-[11px] opacity-60 transition-transform group-open:rotate-90">▶</span>
          Factor Weights & Track Record
        </summary>
        <div className="mt-3">
          <FactorWeightsPanel />
        </div>
      </details>

      <StockInsightModal
        open={insightOpen}
        onOpenChange={setInsightOpen}
        symbol={insightSymbol}
        market={insightMarket}
        stockName={insightName}
        hasPosition={insightHasPosition}
      />

      {shareSignal && (
        <SignalScoreShareCard
          open={!!shareSignal}
          onClose={() => setShareSignal(null)}
          item={shareSignal}
        />
      )}
    </div>
  )
}
