import { useEffect, useState, useCallback } from 'react'
import { RefreshCw, Power, RotateCcw, X, BarChart3, Play, Bell, SlidersHorizontal, ListX, History, LineChart } from 'lucide-react'
import {
  paperTradingApi,
  type PaperTradingAccountResponse,
  type PaperTradingPositionItem,
  type PaperTradingTradeItem,
  type EquityCurvePoint,
  type StrategyPerformanceItem,
  type NotifyChannelItem,
  type MarketView,
} from '@tickerkeep/api'
import { EQUITY_MARKETS, MARKET_LABEL, type EquityMarket } from '@/lib/markets'

/** Allocation shown when the account has none saved yet: US 60, Canada 40. */
const DEFAULT_ALLOCATION: Record<EquityMarket, number> = { US: 0.6, CA: 0.4 }
import { Button } from '@tickerkeep/base-ui/components/ui/button'
import { Switch } from '@tickerkeep/base-ui/components/ui/switch'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@tickerkeep/base-ui/components/ui/dialog'
import { useToast } from '@tickerkeep/base-ui/components/ui/toast'
import { Card, CardHeader, CardTitle, StatCell } from '@tickerkeep/base-ui/components/ui/card'
import { TableWrap, Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@tickerkeep/base-ui/components/ui/table'
import { EmptyState } from '@tickerkeep/base-ui/components/ui/empty-state'

const EXIT_REASON_MAP: Record<string, string> = {
  stop_loss: 'Stop loss',
  target_price: 'Take profit',
  signal_reversal: 'Signal reversal',
  manual: 'Manual close',
}

function formatCurrency(v: number) {
  return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function PnlText({ value, suffix = '' }: { value: number; suffix?: string }) {
  const color = value > 0 ? 'text-stock-up' : value < 0 ? 'text-stock-down' : 'text-muted-foreground'
  const prefix = value > 0 ? '+' : ''
  return <span className={`${color} tabular-nums`}>{prefix}{formatCurrency(value)}{suffix}</span>
}

function PnlPctText({ value }: { value: number }) {
  const color = value > 0 ? 'text-stock-up' : value < 0 ? 'text-stock-down' : 'text-muted-foreground'
  const prefix = value > 0 ? '+' : ''
  return <span className={`${color} tabular-nums`}>{prefix}{value.toFixed(2)}%</span>
}

function EquityChart({ data }: { data: EquityCurvePoint[] }) {
  if (data.length < 2) {
    return (
      <EmptyState
        size="sm"
        icon={LineChart}
        title="No equity history yet"
        description="The equity curve fills in as paper trading runs and records daily snapshots."
      />
    )
  }

  const width = 600
  const height = 180
  const pad = { top: 20, right: 20, bottom: 30, left: 60 }
  const w = width - pad.left - pad.right
  const h = height - pad.top - pad.bottom

  const values = data.map(d => d.equity)
  const minV = Math.min(...values)
  const maxV = Math.max(...values)
  const range = maxV - minV || 1

  const points = data.map((d, i) => {
    const x = pad.left + (i / (data.length - 1)) * w
    const y = pad.top + h - ((d.equity - minV) / range) * h
    return { x, y, ...d }
  })

  const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
  const areaD = pathD + ` L${points[points.length - 1].x},${pad.top + h} L${points[0].x},${pad.top + h} Z`

  const isPositive = values[values.length - 1] >= values[0]
  const strokeColor = isPositive ? 'hsl(var(--stock-up))' : 'hsl(var(--stock-down))'
  const fillColor = isPositive ? 'hsl(var(--stock-up) / 0.1)' : 'hsl(var(--stock-down) / 0.1)'

  // Y axis ticks
  const yTicks = 4
  const yLabels = Array.from({ length: yTicks + 1 }, (_, i) => {
    const v = minV + (range / yTicks) * i
    return { v, y: pad.top + h - (i / yTicks) * h }
  })

  // X axis labels (show first, middle, last)
  const xIndices = [0, Math.floor(data.length / 2), data.length - 1]
  const xLabels = xIndices.map(i => ({ label: data[i].date.slice(5), x: points[i].x }))

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto" preserveAspectRatio="xMidYMid meet">
      {/* Grid lines */}
      {yLabels.map((t, i) => (
        <g key={i}>
          <line x1={pad.left} x2={width - pad.right} y1={t.y} y2={t.y} stroke="hsl(var(--border))" strokeWidth={0.5} />
          <text x={pad.left - 6} y={t.y + 4} textAnchor="end" fill="hsl(var(--muted-foreground))" fontSize={10}>
            {(t.v / 1000).toFixed(1)}k
          </text>
        </g>
      ))}
      {/* Area */}
      <path d={areaD} fill={fillColor} />
      {/* Line */}
      <path d={pathD} fill="none" stroke={strokeColor} strokeWidth={2} />
      {/* X labels */}
      {xLabels.map((l, i) => (
        <text key={i} x={l.x} y={height - 6} textAnchor="middle" fill="hsl(var(--muted-foreground))" fontSize={10}>
          {l.label}
        </text>
      ))}
    </svg>
  )
}

/** Loading placeholder shaped like the table it replaces. */
function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton h-9 rounded-lg" />
      ))}
    </div>
  )
}

export default function PaperTradingPage() {
  const { toast } = useToast()
  const [account, setAccount] = useState<PaperTradingAccountResponse | null>(null)
  const [positions, setPositions] = useState<PaperTradingPositionItem[]>([])
  const [trades, setTrades] = useState<PaperTradingTradeItem[]>([])
  const [tradesTotal, setTradesTotal] = useState(0)
  const [equityCurve, setEquityCurve] = useState<EquityCurvePoint[]>([])
  const [strategyPerf, setStrategyPerf] = useState<StrategyPerformanceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [tradesPage, setTradesPage] = useState(0)
  const tradesPageSize = 20

  // 市场视图（分段单选，切换即按该市场口径刷新统计）
  const [marketView, setMarketView] = useState<MarketView>('ALL')

  // 资金配置
  const [configOpen, setConfigOpen] = useState(false)
  const [cfgTotal, setCfgTotal] = useState('')
  const [cfgRatios, setCfgRatios] = useState<Record<EquityMarket, string>>(() => Object.fromEntries(EQUITY_MARKETS.map(m => [m, ''])) as Record<EquityMarket, string>)
  const [cfgSaving, setCfgSaving] = useState(false)

  // 通知设置
  const [tradesOpen, setTradesOpen] = useState(false)
  const [notifyOpen, setNotifyOpen] = useState(false)
  const [notifyEnabled, setNotifyEnabled] = useState(false)
  const [notifyRealtime, setNotifyRealtime] = useState(true)
  const [notifyPremarket, setNotifyPremarket] = useState(true)
  const [notifySummary, setNotifySummary] = useState(true)
  const [notifyChannels, setNotifyChannels] = useState<NotifyChannelItem[]>([])
  const [selectedChannelIds, setSelectedChannelIds] = useState<Set<number>>(new Set())
  const [notifySaving, setNotifySaving] = useState(false)
  const [notifyTesting, setNotifyTesting] = useState(false)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const mkt = marketView === 'ALL' ? undefined : marketView
      const [acc, pos, tradeData, metrics] = await Promise.all([
        paperTradingApi.getAccount(mkt),
        paperTradingApi.listPositions('open', mkt),
        paperTradingApi.listTrades(tradesPageSize, tradesPage * tradesPageSize, mkt),
        paperTradingApi.getMetrics(mkt),
      ])
      setAccount(acc)
      setPositions(pos)
      setTrades(tradeData.items)
      setTradesTotal(tradeData.total)
      setEquityCurve(metrics.equity_curve)
      setStrategyPerf(metrics.strategy_performance || [])
    } catch {
      toast('Failed to load', 'error')
    } finally {
      setLoading(false)
    }
  }, [tradesPage, marketView])

  useEffect(() => { loadData() }, [loadData])

  const handleToggle = async () => {
    if (!account) return
    try {
      const res = await paperTradingApi.toggleAccount(!account.enabled)
      setAccount(res)
      toast(res.enabled ? 'Paper trading started' : 'Paper trading paused', 'success')
    } catch {
      toast('Operation failed', 'error')
    }
  }

  const handleReset = async () => {
    if (!confirm('Reset paper trading? All positions and trade records will be cleared.')) return
    try {
      await paperTradingApi.resetAccount()
      toast('Paper trading reset', 'success')
      loadData()
    } catch {
      toast('Reset failed', 'error')
    }
  }

  const handleScan = async () => {
    setScanning(true)
    try {
      const res = await paperTradingApi.scan()
      toast(`Scan complete: ${res.opened ?? 0} opened, ${res.closed ?? 0} closed`, 'success')
      loadData()
    } catch {
      toast('Scan failed', 'error')
    } finally {
      setScanning(false)
    }
  }

  const handleClosePosition = async (id: number) => {
    try {
      await paperTradingApi.closePosition(id)
      toast('Position closed', 'success')
      loadData()
    } catch {
      toast('Failed to close position', 'error')
    }
  }

  const handleOpenConfig = async () => {
    setConfigOpen(true)
    try {
      // Fetch total capital and per-market ratios under the "all" view
      const acc = await paperTradingApi.getAccount()
      setCfgTotal(String(Math.round(acc.initial_capital)))
      const a = acc.market_allocations || {}
      setCfgRatios(Object.fromEntries(
        EQUITY_MARKETS.map(m => [m, String(Math.round((a[m] ?? DEFAULT_ALLOCATION[m]) * 100))])
      ) as Record<EquityMarket, string>)
    } catch {
      toast('Failed to load config', 'error')
    }
  }

  const handleSaveConfig = async () => {
    const total = Number(cfgTotal)
    const pctSum = EQUITY_MARKETS.reduce((acc, m) => acc + (Number(cfgRatios[m]) || 0), 0)
    if (!(total > 0)) {
      toast('Total capital must be greater than 0', 'error')
      return
    }
    if (pctSum > 100) {
      toast('Ratios cannot add up to more than 100%', 'error')
      return
    }
    setCfgSaving(true)
    try {
      await paperTradingApi.updateSettings({
        initial_capital: total,
        market_allocations: Object.fromEntries(EQUITY_MARKETS.map(m => [m, (Number(cfgRatios[m]) || 0) / 100])),
      })
      toast('Fund allocation saved', 'success')
      setConfigOpen(false)
      loadData()
    } catch {
      toast('Save failed', 'error')
    } finally {
      setCfgSaving(false)
    }
  }

  const loadNotifySettings = async () => {
    try {
      const data = await paperTradingApi.getNotifySettings()
      const s = data.settings
      setNotifyEnabled(s.pt_notify_enabled === 'true')
      setNotifyRealtime(s.pt_notify_realtime === 'true')
      setNotifyPremarket(s.pt_notify_premarket === 'true')
      setNotifySummary(s.pt_notify_summary === 'true')
      setNotifyChannels(data.channels)
      const ids = s.pt_notify_channel_ids
        ? new Set(s.pt_notify_channel_ids.split(',').map(Number).filter(Boolean))
        : new Set<number>()
      setSelectedChannelIds(ids)
    } catch {
      toast('Failed to load notification config', 'error')
    }
  }

  const handleOpenNotify = async () => {
    setNotifyOpen(true)
    await loadNotifySettings()
  }

  const handleSaveNotify = async () => {
    setNotifySaving(true)
    try {
      await paperTradingApi.updateNotifySettings({
        pt_notify_enabled: notifyEnabled ? 'true' : 'false',
        pt_notify_channel_ids: Array.from(selectedChannelIds).join(','),
        pt_notify_realtime: notifyRealtime ? 'true' : 'false',
        pt_notify_premarket: notifyPremarket ? 'true' : 'false',
        pt_notify_summary: notifySummary ? 'true' : 'false',
      })
      toast('Notification config saved', 'success')
      setNotifyOpen(false)
    } catch {
      toast('Save failed', 'error')
    } finally {
      setNotifySaving(false)
    }
  }

  const handleTestNotify = async () => {
    setNotifyTesting(true)
    try {
      await paperTradingApi.testNotify()
      toast('Test notification sent', 'success')
    } catch {
      toast('Failed to send test notification', 'error')
    } finally {
      setNotifyTesting(false)
    }
  }

  const toggleChannel = (id: number) => {
    setSelectedChannelIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const totalPages = Math.ceil(tradesTotal / tradesPageSize)
  const ratioSum = EQUITY_MARKETS.reduce((acc, m) => acc + (Number(cfgRatios[m]) || 0), 0)

  return (
    <div className="space-y-5">
      {/* Account: this room has no page title, so the panel header below carries
          every real control that used to live in the page header. */}
      {account ? (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2 min-w-0">
              <CardTitle>Account</CardTitle>
              <span className={account.enabled ? 'chip bg-[hsl(var(--success)/0.12)] text-success' : 'chip-neutral'}>
                {account.enabled ? 'Running' : 'Paused'}
              </span>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-1.5">
              {tradesTotal > 0 && (
                <button type="button" className="btn-mini" onClick={() => setTradesOpen(true)}>
                  <BarChart3 className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">Closed Trades ({tradesTotal})</span>
                  <span className="sm:hidden">{tradesTotal}</span>
                </button>
              )}
              <button type="button" className="btn-mini" onClick={handleOpenNotify}>
                <Bell className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Notifications</span>
              </button>
              <button type="button" className="btn-mini" onClick={loadData} disabled={loading}>
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
                <span className="hidden sm:inline">Refresh</span>
              </button>
              <button type="button" className="btn-mini" onClick={handleToggle}>
                <Power className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">{account.enabled ? 'Pause' : 'Start'}</span>
              </button>
              <button type="button" className="btn-mini text-destructive" onClick={handleReset}>
                <RotateCcw className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Reset</span>
              </button>
              <button type="button" className="btn-primary" onClick={handleScan} disabled={scanning}>
                <Play className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">{scanning ? 'Scanning...' : 'Scan Now'}</span>
                <span className="sm:hidden">{scanning ? 'Scanning' : 'Scan'}</span>
              </button>
            </div>
          </CardHeader>

          {/* Market view filter + fund allocation */}
          <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-border">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="stat-label mr-0.5">Market</span>
              {(['ALL', ...EQUITY_MARKETS] as const).map(m => {
                const label = m === 'ALL' ? 'All' : MARKET_LABEL[m]
                const active = marketView === m
                const ratio = m !== 'ALL' ? account.market_allocations?.[m] : undefined
                const isOff = m !== 'ALL' && (ratio ?? 0) <= 0
                return (
                  <button
                    key={m}
                    type="button"
                    onClick={() => setMarketView(m)}
                    className={active ? 'btn-mini-solid' : isOff ? 'btn-mini opacity-50' : 'btn-mini'}
                  >
                    {label}{m !== 'ALL' && ratio != null ? ` ${Math.round(ratio * 100)}%` : ''}
                  </button>
                )
              })}
            </div>
            <button type="button" className="btn-mini" onClick={handleOpenConfig}>
              <SlidersHorizontal className="w-3.5 h-3.5" />
              Fund Allocation
            </button>
          </div>

          {/* Summary strip: one bordered instrument, divided by hairlines */}
          <div className="grid grid-cols-2 md:grid-cols-5 divide-x divide-y divide-border md:divide-y-0">
            <StatCell label="Total Equity" value={formatCurrency(account.total_equity)} />
            <StatCell label="Total P&L" value={<PnlText value={account.total_pnl} />} />
            <StatCell
              label="Win Rate"
              value={`${account.win_rate.toFixed(1)}%`}
              aside={<span className="stat-label whitespace-nowrap">{account.winning_trades}/{account.total_trades} trades</span>}
            />
            <StatCell label="Max Drawdown" value={`${account.max_drawdown_pct.toFixed(2)}%`} tone="down" />
            <StatCell label="Available Funds" value={formatCurrency(account.current_capital)} />
          </div>
        </Card>
      ) : loading ? (
        <div className="card p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div className="skeleton h-5 w-28 rounded" />
            <div className="skeleton h-8 w-24 rounded-full" />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="skeleton h-16 rounded-lg" />
            ))}
          </div>
        </div>
      ) : null}

      {/* Equity Curve */}
      <div className="card p-4">
        <h2 className="section-title mb-3">Equity Curve</h2>
        {loading ? <div className="skeleton h-[180px] rounded-lg" /> : <EquityChart data={equityCurve} />}
      </div>

      {/* Strategy Performance */}
      <div className="card p-4">
        <h2 className="section-title mb-3">Strategy Performance</h2>
        {loading ? (
          <SkeletonRows rows={3} />
        ) : strategyPerf.length === 0 ? (
          <EmptyState
            size="sm"
            icon={BarChart3}
            title="No strategy performance yet"
            description="Once paper trading closes a trade under a strategy, its performance shows up here."
          />
        ) : (
          <TableWrap bordered={false}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Strategy</TableHead>
                  <TableHead numeric>Closed</TableHead>
                  <TableHead numeric>Win Rate</TableHead>
                  <TableHead numeric>Realized P&L</TableHead>
                  <TableHead numeric>Avg P&L%</TableHead>
                  <TableHead numeric>Avg Hold Days</TableHead>
                  <TableHead numeric>Open</TableHead>
                  <TableHead numeric>Unrealized P&L</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {strategyPerf.map(s => (
                  <TableRow key={s.strategy_code} hoverable>
                    <TableCell className="font-medium">{s.strategy_code}</TableCell>
                    <TableCell numeric>{s.total_trades}</TableCell>
                    <TableCell numeric>
                      {s.total_trades > 0 ? `${s.win_rate.toFixed(1)}%` : '-'}
                    </TableCell>
                    <TableCell numeric><PnlText value={s.total_pnl} /></TableCell>
                    <TableCell numeric><PnlPctText value={s.avg_pnl_pct} /></TableCell>
                    <TableCell numeric>{s.total_trades > 0 ? `${s.avg_holding_days}d` : '-'}</TableCell>
                    <TableCell numeric>{s.open_positions > 0 ? s.open_positions : '-'}</TableCell>
                    <TableCell numeric>
                      {s.open_positions > 0 ? <PnlText value={s.unrealized_pnl} /> : '-'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableWrap>
        )}
      </div>

      {/* Open Positions */}
      <div className="card p-4">
        <h2 className="section-title mb-3">Open Positions ({positions.length})</h2>
        {loading ? (
          <SkeletonRows rows={4} />
        ) : positions.length === 0 ? (
          <EmptyState
            size="sm"
            icon={ListX}
            title="No open positions"
            description="Paper trading opens simulated positions automatically when a strategy signal fires. Simulation only - no brokerage order is ever placed. Nothing open right now."
            action={
              <button type="button" className="btn-secondary h-8" onClick={handleScan} disabled={scanning}>
                <Play className="w-3.5 h-3.5" />
                {scanning ? 'Scanning...' : 'Scan Now'}
              </button>
            }
          />
        ) : (
          <TableWrap bordered={false}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Stock</TableHead>
                  <TableHead numeric>Entry Price</TableHead>
                  <TableHead numeric>Current Price</TableHead>
                  <TableHead numeric>Unrealized P&L</TableHead>
                  <TableHead numeric>Stop Loss</TableHead>
                  <TableHead numeric>Target Price</TableHead>
                  <TableHead>Strategy</TableHead>
                  <TableHead numeric>Days Held</TableHead>
                  <TableHead numeric>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {positions.map(p => (
                  <TableRow key={p.id} hoverable>
                    <TableCell>
                      <div className="font-medium">{p.stock_name || p.stock_symbol}</div>
                      <div className="text-xs text-muted-foreground">{p.stock_symbol} · {p.stock_market}</div>
                    </TableCell>
                    <TableCell numeric>{p.entry_price.toFixed(2)}</TableCell>
                    <TableCell numeric>{p.current_price?.toFixed(2) ?? '-'}</TableCell>
                    <TableCell numeric>
                      <PnlText value={p.unrealized_pnl} />
                      <div className="text-xs"><PnlPctText value={p.unrealized_pnl_pct} /></div>
                    </TableCell>
                    <TableCell numeric>{p.stop_loss?.toFixed(2) ?? '-'}</TableCell>
                    <TableCell numeric>{p.target_price?.toFixed(2) ?? '-'}</TableCell>
                    <TableCell>
                      {p.strategy_code ? <span className="chip-neutral">{p.strategy_code}</span> : '-'}
                    </TableCell>
                    <TableCell numeric>{p.holding_days}d</TableCell>
                    <TableCell numeric>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-destructive hover:text-destructive"
                        onClick={() => handleClosePosition(p.id)}
                      >
                        <X className="w-3.5 h-3.5 mr-0.5" />
                        Close
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableWrap>
        )}
      </div>

      {/* Trade History Dialog */}
      <Dialog open={tradesOpen} onOpenChange={setTradesOpen}>
        <DialogContent className="max-w-4xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Closed Trades ({tradesTotal})</DialogTitle>
            <DialogDescription>Historical trade details</DialogDescription>
          </DialogHeader>
          {trades.length === 0 ? (
            <EmptyState
              size="sm"
              icon={History}
              title="No trade records yet"
              description="Closed trades appear here once a simulated position exits via stop loss, target price, or signal reversal."
            />
          ) : (
            <>
              <TableWrap bordered={false}>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Stock</TableHead>
                      <TableHead numeric>Entry Price</TableHead>
                      <TableHead numeric>Exit Price</TableHead>
                      <TableHead numeric>P&L</TableHead>
                      <TableHead numeric>P&L%</TableHead>
                      <TableHead>Exit Reason</TableHead>
                      <TableHead>Strategy</TableHead>
                      <TableHead numeric>Days Held</TableHead>
                      <TableHead numeric>Closed At</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {trades.map(t => (
                      <TableRow key={t.id} hoverable>
                        <TableCell>
                          <div className="font-medium">{t.stock_name || t.stock_symbol}</div>
                          <div className="text-xs text-muted-foreground">{t.stock_symbol} · {t.stock_market}</div>
                        </TableCell>
                        <TableCell numeric>{t.entry_price.toFixed(2)}</TableCell>
                        <TableCell numeric>{t.exit_price.toFixed(2)}</TableCell>
                        <TableCell numeric><PnlText value={t.pnl} /></TableCell>
                        <TableCell numeric><PnlPctText value={t.pnl_pct} /></TableCell>
                        <TableCell className="text-xs">{EXIT_REASON_MAP[t.exit_reason] || t.exit_reason}</TableCell>
                        <TableCell>
                          {t.strategy_code ? <span className="chip-neutral">{t.strategy_code}</span> : '-'}
                        </TableCell>
                        <TableCell numeric>{t.holding_days}d</TableCell>
                        <TableCell numeric className="text-xs text-muted-foreground">{t.closed_at?.slice(0, 10) || '-'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableWrap>
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-2 mt-3">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={tradesPage === 0}
                    onClick={() => setTradesPage(p => Math.max(0, p - 1))}
                  >
                    Previous
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    {tradesPage + 1} / {totalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={tradesPage >= totalPages - 1}
                    onClick={() => setTradesPage(p => p + 1)}
                  >
                    Next
                  </Button>
                </div>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Fund allocation dialog */}
      <Dialog open={configOpen} onOpenChange={setConfigOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Fund Allocation</DialogTitle>
            <DialogDescription>Set total capital and per-market investment ratios; a ratio of 0 means no new capital goes into that market (existing positions are unaffected, only new positions stop)</DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div>
              <div className="text-sm font-medium mb-1">Total Capital</div>
              <input
                type="number"
                value={cfgTotal}
                onChange={e => setCfgTotal(e.target.value)}
                className="input-field"
                placeholder="e.g. 1000000"
              />
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm font-medium">
                <span>Per-Market Investment Ratio</span>
                <span className={`text-xs ${ratioSum > 100 ? 'text-destructive' : 'text-muted-foreground'}`}>
                  Total {ratioSum}%{ratioSum > 100 ? ' (exceeds 100%)' : ''}
                </span>
              </div>
              {EQUITY_MARKETS.map(m => {
                const label = MARKET_LABEL[m]
                const pct = Number(cfgRatios[m]) || 0
                const amount = ((Number(cfgTotal) || 0) * pct) / 100
                return (
                  <div key={m} className="flex items-center gap-3">
                    <span className="w-16 text-sm">{label}</span>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={cfgRatios[m]}
                      onChange={e => setCfgRatios(prev => ({ ...prev, [m]: e.target.value }))}
                      className="input-field w-20 text-right"
                    />
                    <span className="text-sm text-muted-foreground">%</span>
                    <span className="text-xs text-muted-foreground ml-auto">≈ {formatCurrency(amount)}</span>
                  </div>
                )
              })}
              <div className="text-xs text-muted-foreground">The total can be less than 100%; the remainder stays idle and uninvested.</div>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <button type="button" className="btn-primary" onClick={handleSaveConfig} disabled={cfgSaving || ratioSum > 100}>
                {cfgSaving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Copy-trade notification settings dialog */}
      <Dialog open={notifyOpen} onOpenChange={setNotifyOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Copy-Trade Notification Settings</DialogTitle>
            <DialogDescription>Configure paper trading notifications to track entries/exits in real time</DialogDescription>
          </DialogHeader>

          <div className="space-y-5">
            {/* Master switch */}
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium">Enable Notifications</div>
                <div className="text-xs text-muted-foreground">When enabled, trade notifications are pushed through the selected channels</div>
              </div>
              <Switch checked={notifyEnabled} onCheckedChange={setNotifyEnabled} />
            </div>

            {notifyEnabled && (
              <>
                {/* Notification channel selection */}
                <div>
                  <div className="text-sm font-medium mb-2">Notification Channels</div>
                  {notifyChannels.length === 0 ? (
                    <div className="text-xs text-muted-foreground">No channels available yet - configure a notification channel in Settings first</div>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {notifyChannels.map(ch => (
                        <button
                          key={ch.id}
                          type="button"
                          onClick={() => toggleChannel(ch.id)}
                          className={selectedChannelIds.has(ch.id) ? 'btn-mini-solid' : 'btn-mini'}
                        >
                          {ch.name}
                        </button>
                      ))}
                    </div>
                  )}
                  {selectedChannelIds.size === 0 && notifyChannels.length > 0 && (
                    <div className="text-xs text-muted-foreground mt-1">The default channel will be used when none is selected</div>
                  )}
                </div>

                {/* Notification modes */}
                <div className="space-y-3">
                  <div className="text-sm font-medium">Notification Modes</div>
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-sm">Real-time Trade Signals</div>
                      <div className="text-xs text-muted-foreground">Pushed immediately on entry/exit</div>
                    </div>
                    <Switch checked={notifyRealtime} onCheckedChange={setNotifyRealtime} />
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-sm">Pre-market Plan</div>
                      <div className="text-xs text-muted-foreground">Pushes today's candidate list daily at 09:00</div>
                    </div>
                    <Switch checked={notifyPremarket} onCheckedChange={setNotifyPremarket} />
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-sm">End-of-Day Summary</div>
                      <div className="text-xs text-muted-foreground">Pushes a summary of today's activity daily at 15:30</div>
                    </div>
                    <Switch checked={notifySummary} onCheckedChange={setNotifySummary} />
                  </div>
                </div>
              </>
            )}

            {/* Action buttons */}
            <div className="flex items-center gap-2 pt-2">
              <button type="button" className="btn-primary" onClick={handleSaveNotify} disabled={notifySaving}>
                {notifySaving ? 'Saving...' : 'Save'}
              </button>
              {notifyEnabled && (
                <Button variant="outline" size="sm" onClick={handleTestNotify} disabled={notifyTesting}>
                  {notifyTesting ? 'Sending...' : 'Test Notification'}
                </Button>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
