import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BellRing, Banknote, Check, ChevronDown, Clock, Dot, ListPlus, PlugZap, PieChart, RefreshCw,
  TrendingDown, TrendingUp, Wallet, type LucideIcon,
} from 'lucide-react'
import {
  dashboardApi, fetchAPI, homeApi, portfolioApi,
  type AlertHitToday, type DashboardMarketIndex,
  type DashboardQuoteResponse, type DashboardWatchStock, type PortfolioBenchmark,
  type PortfolioTodo,
} from '@tickerkeep/api'
import { EmptyState } from '@tickerkeep/base-ui/components/ui/empty-state'
import { InfoTip } from '@tickerkeep/base-ui/components/ui/tooltip'
import {
  resolveSuggestionColorClass, resolveSuggestionLabel,
} from '@tickerkeep/biz-ui/components/suggestion-action'
import {
  dirChip, dirText, fmtCompact, fmtNum, fmtPct, marketBadgeClass, marketClass, symbolMark,
} from '@/lib/market'
import { isMarket, marketLabel } from '@/lib/markets'
import type { RawPortfolioSummary } from '@/lib/portfolio-valuation'

/* ------------------------------------------------------------------ *
 * Today: what needs you, where the machine is in its day, and the book.
 * The decision queue comes first because deciding is the job; the numbers
 * come second. Every panel here reads a real endpoint. Nothing is mocked.
 * ------------------------------------------------------------------ */

interface AgentHealthRow {
  name: string
  display_name: string
  enabled: boolean
  schedule: string
  next_runs: string[]
  last_run: { status: string; created_at: string; duration_ms: number; error: string } | null
}
interface AgentsHealth {
  timezone: string
  summary: { next_24h_count: number; recent_failed_count: number }
  agents: AgentHealthRow[]
}

/** One entry of the agent suggestion pool (`GET /suggestions`). */
interface PoolSuggestion {
  id: number
  stock_symbol: string
  stock_market?: string
  stock_name: string
  action: string
  action_label: string
  reason: string
  agent_label: string
  created_at: string
  is_expired: boolean
}

/**
 * The benchmark endpoint reports why it has nothing to draw as a machine code.
 * A code is not a sentence, so it never reaches the screen unmapped.
 */
const BENCHMARK_REASON: Record<string, string> = {
  no_holdings: 'You hold nothing yet, so there is no book to compare against the benchmark.',
  insufficient_data: 'Not enough sessions of history yet. This fills in once your positions have a few days behind them.',
  // You DO hold positions — none of them could be priced, so there is nothing to
  // value. Saying "wait a few sessions" here would be false: time does not fix a
  // missing price. Keep this distinct from no_holdings and insufficient_data.
  all_unpriced: 'You hold positions, but none of them has a current price, so there is nothing to value against the benchmark yet. Their quantities and costs are unchanged.',
}

/** The verdicts that ask the operator to do something, as opposed to hold. */
const ACTIONABLE = new Set(['buy', 'add', 'reduce', 'sell', 'avoid'])

type Load<T> = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; data: T }

function useLoad<T>(fn: () => Promise<T>): [Load<T>, () => void] {
  const [tick, setTick] = useState(0)
  const [res, setRes] = useState<Load<T>>({ state: 'loading' })
  const fnRef = useRef(fn)
  fnRef.current = fn
  useEffect(() => {
    let cancelled = false
    setRes({ state: 'loading' })
    fnRef.current()
      .then(data => { if (!cancelled) setRes({ state: 'ready', data }) })
      .catch((e: unknown) => {
        if (cancelled) return
        setRes({ state: 'error', message: e instanceof Error ? e.message : 'Request failed' })
      })
    return () => { cancelled = true }
  }, [tick])
  return [res, useCallback(() => setTick(t => t + 1), [])]
}

/* ---------------------------------- parts --------------------------------- */

function Panel({
  title, sub, action, children,
}: { title: string; sub?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="card">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 pt-4">
        <h2 className="section-title">{title}</h2>
        {sub && <span className="section-sub">{sub}</span>}
        {action && <div className="ml-auto">{action}</div>}
      </div>
      {children}
    </section>
  )
}

function PanelError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 px-5 py-9 text-center">
      <PlugZap className="h-6 w-6 text-destructive" aria-hidden strokeWidth={1.5} />
      <p className="group-title">This panel could not load</p>
      <p className="helper-text max-w-[36ch]">{message}. Nothing was changed. Try again, or check Data Sources.</p>
      <button onClick={onRetry} className="btn-mini mt-1.5">Retry now</button>
    </div>
  )
}

function RowSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="pb-2 pt-1">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="grid grid-cols-[2rem_1fr_4.5rem_3.5rem] items-center gap-3 border-t border-border px-5 py-3">
          <span className="skeleton h-8 w-8 rounded-xl" />
          <span className="skeleton h-3" style={{ width: `${38 + (i % 3) * 12}%` }} />
          <span className="skeleton h-3" />
          <span className="skeleton h-3" />
        </div>
      ))}
    </div>
  )
}

/** A single series, drawn thin, no axis: the shape of the last N closes. */
function Sparkline({ points, up }: { points: number[]; up: boolean }) {
  if (!points || points.length < 2) return <span className="block h-6 w-24" />
  const w = 96, h = 24
  const min = Math.min(...points), max = Math.max(...points)
  const span = max - min || 1
  const d = points
    .map((p, i) => `${i ? 'L' : 'M'}${((i / (points.length - 1)) * w).toFixed(1)},${(h - ((p - min) / span) * h).toFixed(1)}`)
    .join(' ')
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden className="block">
      <path d={d} fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        className={up ? 'stroke-stock-up' : 'stroke-stock-down'} />
    </svg>
  )
}

/**
 * One cell of the stat strip.
 *
 * `hero` is the dark anchor tile; `tint` is one of the four stat-tile grounds,
 * which label which metric you are reading. The icon repeats that label for
 * anyone who cannot separate the hues.
 */
function StatTile({
  label, value, unit, chip, hero, tint, icon: Icon,
}: {
  label: React.ReactNode
  value: string
  unit?: string
  chip?: React.ReactNode
  hero?: boolean
  tint?: 'mint' | 'lilac' | 'peach' | 'sky'
  icon?: LucideIcon
}) {
  // Written out, never interpolated: Tailwind scans source text, so a
  // template literal would compile to no background at all.
  const TINT: Record<string, string> = {
    mint: 'surface-tile bg-tile-mint',
    lilac: 'surface-tile bg-tile-lilac',
    peach: 'surface-tile bg-tile-peach',
    sky: 'surface-tile bg-tile-sky',
  }
  const ground = hero ? 'surface-hero' : tint ? TINT[tint] : 'card'
  return (
    <div className={`relative overflow-hidden px-5 py-4 ${ground}`}>
      {Icon && (
        <Icon
          aria-hidden
          strokeWidth={1.5}
          className={`pointer-events-none absolute -right-3 -top-3 h-[4.5rem] w-[4.5rem] ${
            hero ? 'text-rail-foreground/10' : 'text-foreground/[0.07]'
          }`}
        />
      )}
      <p className={`stat-label relative ${hero ? 'text-rail-muted' : ''}`}>{label}</p>
      <p className={`stat-value relative mt-2.5 ${hero ? 'text-rail-foreground' : ''}`}>
        {value}{unit && <span className={`stat-unit ${hero ? 'text-rail-muted' : ''}`}>{unit}</span>}
      </p>
      {chip && <div className="relative mt-2.5">{chip}</div>}
    </div>
  )
}

/* --------------------------------- the day -------------------------------- */

function DayTimeline({ health }: { health: AgentsHealth }) {
  const now = Date.now()
  const stops = useMemo(() => {
    return health.agents
      .filter(a => a.enabled)
      .map(a => {
        const next = a.next_runs?.[0] ? new Date(a.next_runs[0]) : null
        const last = a.last_run?.created_at ? new Date(a.last_run.created_at) : null
        const ranToday = !!last && last.toDateString() === new Date().toDateString()
        return {
          name: a.display_name || a.name,
          at: next ?? last,
          done: ranToday && (!next || next.getTime() > now),
          failed: a.last_run?.status === 'failed' && ranToday,
        }
      })
      .filter(s => s.at)
      .sort((a, b) => (a.at as Date).getTime() - (b.at as Date).getTime())
  }, [health, now])

  if (stops.length === 0) {
    return (
      <div className="px-5 pb-6">
        <EmptyState
          size="sm"
          icon={Clock}
          title="No agent is scheduled"
          description="Enable an agent and give it a schedule, and its runs will appear here as the day's stops."
        />
      </div>
    )
  }

  const doneCount = stops.filter(s => s.done).length
  const progress = stops.length > 1 ? (doneCount / stops.length) * 92 + 4 : 50

  return (
    <div className="px-5 pb-5 pt-1">
      <div className="relative h-[4.75rem]">
        <div className="absolute left-0 right-0 top-[2.15rem] h-0.5 rounded bg-border" />
        <div className="absolute left-0 top-[2.15rem] h-0.5 rounded bg-rail" style={{ width: `${progress}%` }} />
        <div className="absolute top-4 h-9 w-0.5 bg-foreground" style={{ left: `${progress}%` }}>
          <span className="absolute -top-3.5 left-1/2 -translate-x-1/2 text-[9.5px] font-black tracking-[0.09em] text-foreground">
            NOW
          </span>
        </div>
        {stops.map((s, i) => {
          const left = stops.length > 1 ? 4 + i * (92 / (stops.length - 1)) : 50
          const Icon = s.failed ? PlugZap : s.done ? Check : Clock
          return (
            <div key={`${s.name}-${i}`}
              className="absolute top-6 flex -translate-x-1/2 flex-col items-center gap-1.5"
              style={{ left: `${left}%` }}>
              <span className={`grid h-[22px] w-[22px] place-items-center rounded-full border-2 ${
                s.failed ? 'border-destructive bg-destructive text-destructive-foreground'
                  : s.done ? 'border-rail bg-rail text-rail-foreground'
                  : 'border-border bg-card text-muted-foreground'
              }`}>
                <Icon className="h-2.5 w-2.5" aria-hidden strokeWidth={3} />
              </span>
              <span className={`whitespace-nowrap text-[10.5px] ${s.done ? 'font-bold text-foreground' : 'font-medium text-muted-foreground'}`}>
                {s.name}
              </span>
              <span className="-mt-1 whitespace-nowrap text-[10px] tabular-nums text-muted-foreground">
                {s.at?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* ------------------------------ book value -------------------------------- */

/** Portfolio against its benchmark, both normalised to 100 at the start. */
function BenchmarkChart({ data }: { data: PortfolioBenchmark }) {
  const [hover, setHover] = useState<number | null>(null)
  const curve = data.curve ?? []
  if (curve.length < 2) return null

  const W = 720, H = 220, pad = { t: 14, r: 16, b: 24, l: 44 }
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b
  const all = curve.flatMap(p => [p.portfolio, p.benchmark])
  const min = Math.min(...all), max = Math.max(...all)
  const span = (max - min) || 1
  const x = (i: number) => pad.l + (i / (curve.length - 1)) * iw
  const y = (v: number) => pad.t + ih - ((v - min) / span) * ih
  const path = (key: 'portfolio' | 'benchmark') =>
    curve.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(' ')

  const at = hover != null ? curve[hover] : curve[curve.length - 1]

  // A populated curve can still be a partial one — some held positions may
  // have no current price or no FX rate, so "your book" here is only the
  // priced subset, not the whole portfolio. That must say so even when
  // there's a curve to draw, not just when there's nothing to draw at all.
  //
  // The server's own `note` (src/web/api/accounts.py `_valuation_coverage`)
  // is preferred whenever present: it already gets the two distinct
  // failures right (holdings EXCLUDED for having no price/FX at all, vs. an
  // analysed holding priced with a STALE last-known FX rate) and this file
  // has no finer-grained data to improve on — `excluded_unpriced_holdings`
  // is a single combined count, not split by cause, so a fallback sentence
  // must not claim a specific one of the two ("last-known price or FX
  // rate") for positions that were simply excluded.
  const cov = data.valuation
  const partial = cov?.valuation_complete === false
  const coverageNote = partial
    ? cov?.note
      ?? (cov?.excluded_unpriced_holdings
        ? `${cov.excluded_unpriced_holdings} held position(s) have no current price or FX rate and are excluded from this curve.`
        : cov?.fx_status === 'last_known'
          ? 'Some analysed positions were converted at a last-known (stale) CAD/USD rate rather than a fresh one.'
          : 'This curve reflects only the priced subset of your positions.')
    : null

  return (
    <div className="px-2 pb-3">
      <div className="flex flex-wrap items-center gap-4 px-3 pb-1">
        <span className="flex items-center gap-2 text-[12px] font-semibold text-muted-foreground">
          <span className="h-0.5 w-4 rounded bg-foreground" aria-hidden /> Your book
          {partial && (
            <>
              <span className="text-muted-foreground/70">(priced only)</span>
              {coverageNote && <InfoTip label={coverageNote} />}
            </>
          )}
        </span>
        <span className="flex items-center gap-2 text-[12px] font-semibold text-muted-foreground">
          <span className="h-0.5 w-4 rounded bg-muted-foreground/50" aria-hidden />
          {data.benchmark_label || data.benchmark_code || 'Benchmark'}
        </span>
        <span className="ml-auto text-[12px] font-semibold tabular-nums text-muted-foreground">
          {at?.date} · book {at?.portfolio.toFixed(2)} · benchmark {at?.benchmark.toFixed(2)}
        </span>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="block h-[220px] w-full"
        role="img"
        aria-label={`Your book against ${data.benchmark_label || 'the benchmark'} over ${data.days ?? 60} days`}
        onPointerMove={e => {
          const r = (e.target as SVGElement).ownerSVGElement?.getBoundingClientRect()
          if (!r) return
          const sx = ((e.clientX - r.left) / r.width) * W
          const i = Math.round(((sx - pad.l) / iw) * (curve.length - 1))
          setHover(Math.max(0, Math.min(curve.length - 1, i)))
        }}
        onPointerLeave={() => setHover(null)}
      >
        {[0, 1, 2, 3].map(k => {
          const v = min + (k / 3) * span
          return (
            <g key={k}>
              <line x1={pad.l} y1={y(v)} x2={W - pad.r} y2={y(v)} className="stroke-border" strokeWidth="1" />
              <text x={pad.l - 8} y={y(v) + 4} textAnchor="end"
                className="fill-muted-foreground" fontSize="10.5" fontWeight="600">
                {v.toFixed(0)}
              </text>
            </g>
          )
        })}
        <path d={path('benchmark')} fill="none" strokeWidth="2" strokeLinecap="round"
          className="stroke-muted-foreground/50" />
        <path d={path('portfolio')} fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          className="stroke-foreground" />
        {hover != null && (
          <>
            <line x1={x(hover)} y1={pad.t} x2={x(hover)} y2={pad.t + ih}
              className="stroke-foreground" strokeWidth="1" opacity="0.35" />
            <circle cx={x(hover)} cy={y(curve[hover].portfolio)} r="4.5"
              className="fill-foreground stroke-card" strokeWidth="2" />
          </>
        )}
        <rect x={pad.l} y={pad.t} width={iw} height={ih} fill="transparent" />
      </svg>
    </div>
  )
}

/* ------------------------------- exposure --------------------------------- */

/**
 * Where the book's USD value sits, by market. Sums only USD-valued
 * positions (`market_value_cny` — the USD-converted market value; see
 * portfolio-valuation.ts) — a position with no current price or no FX rate
 * to USD contributes nothing here, rather than standing in for its market
 * value with cost (which would silently misstate exposure the moment a
 * quote or FX rate goes missing). Positions excluded that way are counted
 * and disclosed instead of just vanishing from the chart.
 */
function Exposure({ summary }: { summary: RawPortfolioSummary }) {
  const { rows, excluded, excludedNoFx, excludedUnsupported, fxLastKnown } = useMemo(() => {
    const byMarket = new Map<string, number>()
    let excluded = 0
    let excludedNoFx = 0
    // A retired/unsupported market is a THIRD reason, distinct from both a missing
    // price and a missing FX rate: the instrument is not analysed at all here.
    let excludedUnsupported = 0
    for (const acct of summary.accounts ?? []) {
      for (const p of acct.positions ?? []) {
        if (!p.priced || p.market_value_cny == null) {
          // Only count a position as "excluded" once it holds a quantity —
          // a genuinely empty/closed position has nothing to weigh either
          // way, so it shouldn't inflate the disclosure count.
          if ((p.quantity ?? 0) !== 0) {
            excluded += 1
            // Distinguish "no current price" from "priced, but no FX rate to
            // USD" — the same two failures Stocks.tsx's per-position badges
            // separate; conflating them here would misreport which is wrong.
            if (p.valuation_status === 'unsupported') excludedUnsupported += 1
            else if (p.current_price != null && p.fx_status !== 'known' && p.fx_status !== 'last_known') excludedNoFx += 1
          }
          continue
        }
        const key = String(p.market || '').toUpperCase() || 'OTHER'
        byMarket.set(key, (byMarket.get(key) ?? 0) + p.market_value_cny)
      }
    }
    const list = [...byMarket.entries()].map(([k, v]) => ({ key: k, value: v })).filter(r => r.value !== 0)
    list.sort((a, b) => b.value - a.value)
    // A last-known (stale) CAD/USD rate can price every CA position in this
    // chart without excluding a single one — `excluded` only counts
    // no-price/no-FX/unsupported holdings, a different failure. So this
    // disclosure fires independently of `excluded`, whenever a CAD row is
    // actually included here priced at a stale rate, rather than only when
    // something was dropped entirely.
    const fxLastKnown = summary.fx_status?.CAD_USD === 'last_known' && list.some(r => r.key === 'CA')
    return { rows: list, excluded, excludedNoFx, excludedUnsupported, fxLastKnown }
  }, [summary])

  const total = rows.reduce((a, b) => a + b.value, 0)
  // "No FX rate" and "no current price" are different failures (a position
  // can have one without the other) — say which positions have which,
  // rather than folding both into one blanket "missing" reason.
  const excludedNoPrice = excluded - excludedNoFx - excludedUnsupported
  const exclusionReason = [
    excludedNoFx > 0 ? `${excludedNoFx} with no FX rate to USD` : null,
    excludedNoPrice > 0 ? `${excludedNoPrice} with no current price` : null,
    excludedUnsupported > 0 ? `${excludedUnsupported} in a market this deployment no longer analyses` : null,
  ].filter(Boolean).join(', ')

  if (rows.length === 0) {
    return (
      <div className="px-5 pb-6">
        <EmptyState
          size="sm"
          icon={ListPlus}
          title={excluded > 0 ? 'Nothing priced to weigh yet' : 'No positions to weigh yet'}
          description={excluded > 0
            ? `${excluded} position(s) are held but have no current USD value yet (${exclusionReason}).`
            : 'Once you hold something, this shows how much of the book sits in each market.'}
        />
      </div>
    )
  }

  return (
    <div className="px-5 pb-5 pt-3">
      {(excluded > 0 || fxLastKnown) && (
        <p className="mb-2.5 px-0 text-[11.5px] text-muted-foreground">
          {excluded > 0 && <>{excluded} position(s) excluded — {exclusionReason}.</>}
          {excluded > 0 && fxLastKnown && ' '}
          {fxLastKnown && 'CAD positions here are converted at a last-known (stale) rate, not a fresh one.'}
        </p>
      )}
      <div className="mb-3.5 flex h-11 gap-0.5" role="img"
        aria-label={rows.map(r => `${marketLabel(r.key)} ${((r.value / total) * 100).toFixed(1)}%`).join(', ')}>
        {rows.map(r => (
          <div key={r.key} className={`rounded ${marketClass(r.key)}`} style={{ flex: r.value }} />
        ))}
      </div>
      <div>
        {rows.map(r => (
          <div key={r.key}
            className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-3 border-t border-border py-2.5 text-[13px] first:border-t-0">
            <span className={`h-2.5 w-2.5 rounded-sm ${marketClass(r.key)}`} aria-hidden />
            <span className="font-semibold">{marketLabel(r.key)}</span>
            <span className="text-[12.5px] font-semibold tabular-nums text-muted-foreground">
              {((r.value / total) * 100).toFixed(1)}%
            </span>
            <span className="font-bold tabular-nums">{fmtCompact(r.value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ------------------------------- needs you -------------------------------- */

interface QueueItem {
  id: string
  kind: 'alert' | 'suggestion' | 'todo'
  title: string
  symbol?: string
  market?: string
  line: string
  verdict?: { label: string; cls: string }
  why?: string
  to: string
}

function QueueRow({ item, onOpen }: { item: QueueItem; onOpen: () => void }) {
  const [open, setOpen] = useState(false)
  const Icon = item.kind === 'alert' ? BellRing : item.kind === 'suggestion' ? TrendingUp : ListPlus

  return (
    <div className="border-t border-border">
      <div className="grid grid-cols-[auto_1fr_auto] items-center gap-3.5 px-5 py-3">
        <span className="grid h-[34px] w-[34px] place-items-center rounded-xl bg-muted text-muted-foreground">
          <Icon className="h-[17px] w-[17px]" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-[13.5px] font-bold">
            {item.title}
            {item.market && (
              <span className={`h-1.5 w-1.5 rounded-full ${marketClass(item.market)}`} aria-hidden />
            )}
            {item.symbol && (
              <span className="text-[11.5px] font-semibold tabular-nums text-muted-foreground">{item.symbol}</span>
            )}
            {item.verdict && (
              <span className={`verdict ${item.verdict.cls}`}>{item.verdict.label}</span>
            )}
          </p>
          <p className="mt-0.5 truncate text-[12.5px] text-muted-foreground">{item.line}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {item.why && (
            <button
              onClick={() => setOpen(o => !o)}
              aria-expanded={open}
              className="btn-mini hidden sm:inline-flex"
            >
              Why?
              <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden />
            </button>
          )}
          <button onClick={onOpen} className="btn-mini-solid">Open</button>
        </div>
      </div>
      {open && item.why && (
        <p className="border-t border-border bg-muted/40 px-5 py-3 text-[12.5px] leading-relaxed text-muted-foreground">
          {item.why}
        </p>
      )}
    </div>
  )
}

/* ---------------------------------- page ---------------------------------- */

export default function TodayPage() {
  const navigate = useNavigate()

  // `dashboardApi.portfolioSummary` is typed `DashboardPortfolioSummary` in
  // packages/api/src/dashboard.ts, which is now an alias of
  // `RawPortfolioSummary` (see portfolio-valuation.ts and contract 3 in
  // round 7) — same accurate nullability/coverage/FX shape Stocks.tsx reads,
  // so no cast is needed here.
  const [summary, reloadSummary] = useLoad<RawPortfolioSummary>(
    () => dashboardApi.portfolioSummary({ include_quotes: true }))
  const [health, reloadHealth] = useLoad<AgentsHealth>(() => fetchAPI<AgentsHealth>('/agents/health'))
  const [indices, reloadIndices] = useLoad<DashboardMarketIndex[]>(() => dashboardApi.indices())
  const [bench, reloadBench] = useLoad<PortfolioBenchmark>(() => portfolioApi.benchmark({ days: 60 }))

  const [queue, reloadQueue] = useLoad<{ hits: AlertHitToday[]; todos: PortfolioTodo[]; picks: PoolSuggestion[] }>(
    async () => {
      const [hits, todos, pool] = await Promise.all([
        homeApi.alertHitsToday().catch(() => [] as AlertHitToday[]),
        homeApi.todos().catch(() => ({ todos: [] as PortfolioTodo[], count: 0 })),
        fetchAPI<Record<string, PoolSuggestion>>('/suggestions').catch(() => ({} as Record<string, PoolSuggestion>)),
      ])
      const picks = Object.values(pool ?? {})
        .filter(s => s && !s.is_expired && ACTIONABLE.has(String(s.action || '').toLowerCase()))
      return { hits: hits ?? [], todos: todos?.todos ?? [], picks }
    })

  const [watch, reloadWatch] = useLoad<{
    rows: DashboardWatchStock[]
    quotes: Record<string, DashboardQuoteResponse>
    pool: Record<string, PoolSuggestion>
  }>(async () => {
    const rows = await dashboardApi.watchlist()
    const list = Array.isArray(rows) ? rows : []
    if (list.length === 0) return { rows: [], quotes: {}, pool: {} }
    const quotable = list.filter(r => isMarket(r.market))
    const [quotes, pool] = await Promise.all([
      quotable.length > 0
        ? dashboardApi.batchQuotes(quotable.map(r => ({ symbol: r.symbol, market: r.market })))
            .catch(() => [] as DashboardQuoteResponse[])
        : Promise.resolve([] as DashboardQuoteResponse[]),
      fetchAPI<Record<string, PoolSuggestion>>('/suggestions?include_expired=true')
        .catch(() => ({} as Record<string, PoolSuggestion>)),
    ])
    const map: Record<string, DashboardQuoteResponse> = {}
    for (const q of quotes ?? []) map[`${q.market}:${q.symbol}`] = q
    return { rows: list, quotes: map, pool: pool ?? {} }
  })

  // A price that changes flashes its direction and settles. The one motion
  // this world allows, and it carries the same meaning the number does.
  const prevPrices = useRef<Record<string, number>>({})
  const [flash, setFlash] = useState<Record<string, 'up' | 'down'>>({})
  useEffect(() => {
    if (watch.state !== 'ready') return
    const next: Record<string, 'up' | 'down'> = {}
    for (const [key, q] of Object.entries(watch.data.quotes)) {
      const p = q.current_price
      if (p == null) continue
      const before = prevPrices.current[key]
      if (before != null && before !== p) next[key] = p > before ? 'up' : 'down'
      prevPrices.current[key] = p
    }
    if (Object.keys(next).length === 0) return
    setFlash(next)
    const id = setTimeout(() => setFlash({}), 950)
    return () => clearTimeout(id)
  }, [watch])

  const total = summary.state === 'ready' ? summary.data.total : null

  // Coverage disclosure shared by the stat strip below — mirrors the same
  // derivation Stocks.tsx uses (see its `coverageSuffix`/`coverageTip`) so a
  // populated-but-partial figure reads as partial here too, not just an
  // empty one. A missing coverage field (an older payload) fails toward
  // "unknown/incomplete", never toward asserting a full valuation.
  const valuationComplete = total?.valuation_complete === true
  const unpricedCount = total?.unpriced_positions ?? 0
  const lastKnownCount = total?.last_known_positions ?? 0
  const fxUnknownCount = total?.fx_unknown_positions ?? 0
  const totalAssetsComplete = total?.total_assets_complete === true
  // Fails toward "incomplete" (never toward "complete") on a payload that
  // doesn't carry the field yet — the same convention every coverage flag
  // here follows.
  const dailyComplete = total?.daily_pnl_complete === true
  // "partial" when something is unpriced, "last known" when everything is
  // priced but some quote or FX rate is not fresh — the same two-way split
  // Stocks.tsx's tiles use, so the same word means the same thing everywhere.
  const coverageSuffix = total == null || valuationComplete ? null : unpricedCount > 0 ? 'partial' : 'last known'
  const coverageTip = total == null || valuationComplete
    ? null
    : [
        unpricedCount > 0
          // "No current price" and "no FX rate" are different failures — say
          // which positions are missing which, never collapse them into one.
          ? `${unpricedCount} position(s) have no current price${fxUnknownCount > 0 ? ` (${fxUnknownCount} with no FX rate to USD)` : ''} and are excluded from this figure.`
          : null,
        lastKnownCount > 0
          ? `${lastKnownCount} position(s) use a last-known price or FX rate rather than a fresh quote.`
          : null,
      ].filter(Boolean).join(' ')

  const items = useMemo<QueueItem[]>(() => {
    if (queue.state !== 'ready') return []
    const out: QueueItem[] = []
    for (const s of queue.data.picks) {
      out.push({
        id: `pick-${s.id}`,
        kind: 'suggestion',
        title: s.stock_name || s.stock_symbol,
        symbol: s.stock_symbol,
        market: s.stock_market,
        line: `${s.agent_label || 'An agent'} flagged this on ${new Date(s.created_at).toLocaleDateString()}.`,
        verdict: {
          label: resolveSuggestionLabel(s.action, s.action_label),
          cls: resolveSuggestionColorClass(s.action, s.action_label),
        },
        why: s.reason || undefined,
        to: '/portfolio/watchlist',
      })
    }
    for (const hit of queue.data.hits) {
      out.push({
        id: `hit-${hit.rule_id}-${hit.symbol}-${hit.trigger_time}`,
        kind: 'alert',
        title: hit.name || hit.symbol,
        symbol: hit.symbol,
        market: hit.market,
        line: `${hit.rule_name} fired at ${new Date(hit.trigger_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}.`,
        why: `Rule "${hit.rule_name}" matched on ${hit.symbol}. Open Alerts to see the rule and adjust its threshold.`,
        to: '/portfolio/alerts',
      })
    }
    queue.data.todos.forEach((todo, i) => {
      out.push({
        id: `todo-${i}`,
        kind: 'todo',
        title: todo.symbol || 'Portfolio',
        symbol: todo.symbol,
        market: todo.market,
        line: todo.message,
        to: todo.symbol ? '/portfolio' : '/portfolio/alerts',
      })
    })
    return out
  }, [queue])

  return (
    <div className="flex flex-col gap-3.5">
      {/* ------------------------------ needs you ------------------------------ */}
      <Panel
        title="Needs you"
        sub="Agents surfaced these. Nothing is traded for you - paper trading acts only inside the simulation."
        action={items.length > 0
          ? <span className="rounded-full bg-foreground px-2 py-0.5 text-[11px] font-bold text-background">{items.length}</span>
          : undefined}
      >
        {queue.state === 'loading' && <RowSkeleton rows={2} />}
        {queue.state === 'error' && <PanelError message={queue.message} onRetry={reloadQueue} />}
        {queue.state === 'ready' && items.length === 0 && (
          <div className="px-5 pb-6">
            <EmptyState
              size="sm"
              icon={Check}
              title="Nothing needs you right now"
              description="Agent verdicts, fired alerts and positions that drift will land here the moment one appears."
            />
          </div>
        )}
        {queue.state === 'ready' && items.map(item => (
          <QueueRow key={item.id} item={item} onOpen={() => navigate(item.to)} />
        ))}
      </Panel>

      {/* ------------------------------ stat strip ----------------------------- */}
      {summary.state === 'error' ? (
        <section className="card"><PanelError message={summary.message} onRetry={reloadSummary} /></section>
      ) : (
        <section className="grid grid-cols-2 gap-3.5 lg:grid-cols-4">
          {summary.state === 'loading'
            ? Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="card px-5 py-4">
                  <span className="skeleton mb-3 block h-3 w-20" />
                  <span className="skeleton block h-7 w-28" />
                </div>
              ))
            : (
              <>
                <StatTile hero icon={Wallet}
                  label={<span className="inline-flex items-center gap-1">
                    Book value
                    {!totalAssetsComplete && <span className="text-rail-muted">· {coverageSuffix}</span>}
                  </span>}
                  value={fmtCompact(total?.total_assets)} unit="USD"
                  chip={<span className={dirChip(total?.total_daily_pnl)}>
                    {(total?.total_daily_pnl ?? 0) > 0 && <TrendingUp className="h-3 w-3" aria-hidden />}
                    {(total?.total_daily_pnl ?? 0) < 0 && <TrendingDown className="h-3 w-3" aria-hidden />}
                    {fmtCompact(total?.total_daily_pnl)} today{!dailyComplete && ' · partial'}
                  </span>} />
                <StatTile tint="mint" icon={TrendingUp}
                  label={<span className="inline-flex items-center gap-1">
                    Open P&L
                    {!valuationComplete && <span className="text-muted-foreground/70">· {coverageSuffix}</span>}
                    {coverageTip && <InfoTip label={coverageTip} />}
                  </span>}
                  value={fmtCompact(total?.total_pnl)} unit="USD"
                  chip={<span className={dirChip(total?.total_pnl_pct)}>
                    {fmtPct(total?.total_pnl_pct)} on cost
                  </span>} />
                <StatTile tint="lilac" icon={PieChart}
                  label={<span className="inline-flex items-center gap-1">
                    Market value
                    {!valuationComplete && <span className="text-muted-foreground/70">· {coverageSuffix}</span>}
                    {coverageTip && <InfoTip label={coverageTip} />}
                  </span>}
                  value={fmtCompact(total?.total_market_value)} unit="USD"
                  chip={<span className="chip-neutral">Cost {fmtCompact(total?.total_cost)}</span>} />
                <StatTile tint="peach" icon={Banknote} label="Available funds" value={fmtCompact(total?.available_funds)} unit="USD"
                  chip={<span className="chip-neutral">
                    {total?.total_assets == null
                      // total_assets is null exactly when the book holds
                      // positions but none of them is priced (see
                      // portfolio-valuation.ts) — never a genuine "all cash".
                      ? 'Positions unpriced'
                      : total.total_assets > 0
                        // total_assets can be non-null while still PARTIAL —
                        // it's summed over the priced subset only when
                        // something is unpriced/last-known (see
                        // totalAssetsComplete above). Asserting "% of book"
                        // against a partial denominator would misstate the
                        // ratio as if it covered the whole portfolio, so a
                        // partial denominator is called out as such rather
                        // than silently passing for complete.
                        ? `${((total.available_funds / total.total_assets) * 100).toFixed(0)}% of ${totalAssetsComplete ? 'book' : 'priced book'}`
                        // A genuine zero total (no holdings, no funds) still
                        // divides to 0/0 — read it as "cash on hand", never
                        // as an unknown ratio, since there's nothing else to
                        // account for.
                        : 'Cash on hand'}
                  </span>} />
              </>
            )}
        </section>
      )}

      {/* -------------------------------- the day ------------------------------ */}
      <Panel
        title="The day"
        sub={health.state === 'ready'
          ? `${health.data.summary.next_24h_count} runs in the next 24 hours`
          : undefined}
        action={<button onClick={reloadHealth} className="btn-mini">
          <RefreshCw className="h-3.5 w-3.5" aria-hidden /> Refresh
        </button>}
      >
        {health.state === 'loading' && (
          <div className="px-5 pb-6 pt-4"><span className="skeleton block h-12 w-full" /></div>
        )}
        {health.state === 'error' && <PanelError message={health.message} onRetry={reloadHealth} />}
        {health.state === 'ready' && <DayTimeline health={health.data} />}
      </Panel>

      {/* -------------------- book value + where the money sits ---------------- */}
      <div className="grid gap-3.5 lg:grid-cols-[1.55fr_1fr]">
        <Panel
          title="Book value"
          sub={bench.state === 'ready' && bench.data.days ? `Last ${bench.data.days} sessions` : undefined}
        >
          {bench.state === 'loading' && (
            <div className="px-5 pb-6 pt-4"><span className="skeleton block h-[200px] w-full" /></div>
          )}
          {bench.state === 'error' && <PanelError message={bench.message} onRetry={reloadBench} />}
          {bench.state === 'ready' && (bench.data.empty || !bench.data.curve?.length) && (
            <div className="px-5 pb-6">
              <EmptyState
                size="sm"
                icon={TrendingUp}
                title="No curve to draw yet"
                description={BENCHMARK_REASON[String(bench.data.reason ?? '')]
                  ?? 'This cannot be compared against the benchmark right now.'}
                action={bench.data.reason === 'all_unpriced' ? undefined : (
                  <button onClick={() => navigate('/portfolio')} className="btn-mini-solid">Add a position</button>
                )}
              />
            </div>
          )}
          {bench.state === 'ready' && !bench.data.empty && !!bench.data.curve?.length && (
            <BenchmarkChart data={bench.data} />
          )}
        </Panel>

        <Panel title="Where the money sits">
          {summary.state === 'loading' && (
            <div className="px-5 pb-6 pt-4"><span className="skeleton block h-[200px] w-full" /></div>
          )}
          {summary.state === 'ready' && <Exposure summary={summary.data} />}
        </Panel>
      </div>

      {/* ------------------------------- indices ------------------------------- */}
      <Panel title="Where the market is" sub="Last 20 sessions">
        {indices.state === 'loading' && <RowSkeleton rows={2} />}
        {indices.state === 'error' && <PanelError message={indices.message} onRetry={reloadIndices} />}
        {indices.state === 'ready' && indices.data.length === 0 && (
          <div className="px-5 pb-6">
            <EmptyState size="sm" icon={Dot} title="No index data"
              description="The quote source returned no indices. Check Data Sources for the market you follow." />
          </div>
        )}
        {indices.state === 'ready' && indices.data.length > 0 && (
          <div className="grid grid-cols-2 divide-x divide-y divide-border border-t border-border md:grid-cols-3 lg:grid-cols-6 lg:divide-y-0">
            {indices.data.map(ix => (
              <div key={`${ix.market}-${ix.symbol}`} className="min-w-0 px-5 py-4">
                <p className="stat-label flex items-center gap-1.5 truncate">
                  <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${marketClass(ix.market)}`} aria-hidden />
                  {ix.name}
                </p>
                <p className="mt-1.5 text-[19px] font-black leading-tight tabular-nums tracking-[-0.03em]">
                  {fmtNum(ix.current_price)}
                </p>
                <span className={`mt-1 block text-[12.5px] font-bold tabular-nums ${dirText(ix.change_pct)}`}>
                  {fmtPct(ix.change_pct)}
                </span>
                <div className="mt-2">
                  <Sparkline points={ix.spark ?? []} up={(ix.change_pct ?? 0) >= 0} />
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {/* ------------------------------ watchlist ------------------------------ */}
      <Panel
        title="Watchlist"
        sub={watch.state === 'ready' ? `${watch.data.rows.length} symbols` : undefined}
        action={<button onClick={() => navigate('/portfolio/watchlist')} className="btn-mini">Open watchlist</button>}
      >
        {watch.state === 'loading' && <RowSkeleton rows={4} />}
        {watch.state === 'error' && <PanelError message={watch.message} onRetry={reloadWatch} />}
        {watch.state === 'ready' && watch.data.rows.length === 0 && (
          <div className="px-5 pb-6">
            <EmptyState
              size="sm"
              icon={ListPlus}
              title="Nothing on the watchlist yet"
              description="Add the symbols you want watched and the agents will review them on every scheduled run."
              action={<button onClick={() => navigate('/portfolio/watchlist')} className="btn-mini-solid">Add a symbol</button>}
            />
          </div>
        )}
        {watch.state === 'ready' && watch.data.rows.length > 0 && (
          <div className="scrollbar overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th className="col-head border-b border-t border-border px-5 py-2.5 text-left">Symbol</th>
                  <th className="col-head border-b border-t border-border px-5 py-2.5 text-left">Market</th>
                  <th className="col-head border-b border-t border-border px-5 py-2.5 text-right">Last</th>
                  <th className="col-head border-b border-t border-border px-5 py-2.5 text-right">Change</th>
                  <th className="col-head border-b border-t border-border px-5 py-2.5 text-right">Agent says</th>
                </tr>
              </thead>
              <tbody>
                {watch.data.rows.map(row => {
                  const key = `${row.market}:${row.symbol}`
                  const q = watch.data.quotes[key]
                  const s = watch.data.pool[row.symbol]
                  const tick = flash[key]
                  return (
                    <tr
                      key={key}
                      onClick={() => navigate(`/portfolio/watchlist?symbol=${encodeURIComponent(row.symbol)}`)}
                      className="row-interactive border-b border-border last:border-b-0"
                    >
                      <td className="px-5 py-3">
                        <span className="flex items-center gap-3">
                          <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-xl text-[10.5px] font-black text-mkt-ink ${marketBadgeClass(row.market)}`}>
                            {symbolMark(row.name, row.symbol)}
                          </span>
                          <span className="min-w-0">
                            <span className="block truncate text-[13.5px] font-bold leading-tight">{row.name}</span>
                            <span className="block text-[11.5px] font-semibold tabular-nums text-muted-foreground">{row.symbol}</span>
                          </span>
                        </span>
                      </td>
                      <td className="px-5 py-3">
                        <span className={`inline-block rounded-md px-1.5 py-0.5 text-[11px] font-bold text-mkt-ink ${marketBadgeClass(row.market)}`}>
                          {row.market}
                        </span>
                      </td>
                      <td className={`px-5 py-3 text-right text-[13.5px] font-bold tabular-nums ${
                        tick === 'up' ? 'tick-up' : tick === 'down' ? 'tick-down' : ''}`}>
                        {fmtNum(q?.current_price)}
                      </td>
                      <td className={`px-5 py-3 text-right text-[13.5px] font-bold tabular-nums ${dirText(q?.change_pct)}`}>
                        {fmtPct(q?.change_pct)}
                      </td>
                      <td className="px-5 py-3 text-right">
                        {s
                          ? <span className={`verdict ${resolveSuggestionColorClass(s.action, s.action_label)}`}>
                              {resolveSuggestionLabel(s.action, s.action_label)}
                            </span>
                          : <span className="text-[12px] font-medium text-muted-foreground">Not reviewed</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}
