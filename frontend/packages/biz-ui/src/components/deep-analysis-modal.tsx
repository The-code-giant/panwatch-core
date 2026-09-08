/**
 * Deep Analysis modal (TradingAgents).
 *
 * Three states:
 * 1. Trigger — shows "Analysis takes 3-5 minutes, confirm start?" + cost estimate
 * 2. Running — polling /agents/runs/{trace_id}/progress, shows stage progress
 * 3. Done — top summary + Markdown reasoning + expandable 4-analyst reports + debate
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { buildAnalysisSections, type AnalysisSection } from '../analysis-sections'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@tickerkeep/base-ui/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@tickerkeep/base-ui/components/ui/tabs'
import { Button } from '@tickerkeep/base-ui/components/ui/button'
import { useToast } from '@tickerkeep/base-ui/components/ui/toast'
import { HoverPopover } from '@tickerkeep/base-ui/components/ui/hover-popover'
import {
  subscribeSSE,
  tradingAgentsApi,
  type BudgetInfo,
  type DeepAnalysisResult,
  type ProgressResponse,
  type ProgressStage,
} from '@tickerkeep/api'

const STAGE_LABEL: Record<string, string> = {
  market_analyst: 'Technical Analyst',
  social_analyst: 'Sentiment Analyst',
  news_analyst: 'News Analyst',
  fundamentals_analyst: 'Fundamentals Analyst',
  bull_bear_debate: 'Bull/Bear Debate',
  research_manager: 'Research Manager',
  trader: 'Trader Decision',
  risk_judge: 'Risk Judge',
  final_decision: 'PM Synthesis',
}

const DECISION_COLOR: Record<string, string> = {
  // Western convention: buy tracks "up" (green), sell tracks "down" (red).
  buy: 'text-stock-up',
  hold: 'text-amber-600 dark:text-amber-400',
  sell: 'text-stock-down',
}

const POLL_INTERVAL_MS = 2000

/** Records a stock's most recently triggered trace_id in localStorage; restores polling when the modal is reopened */
const STORAGE_KEY_PREFIX = 'tickerkeep:tradingagents:running:'
/** How long a trace_id can persist before it's considered possibly no longer running (avoids showing idle for a stale trace) */
const TRACE_MAX_AGE_MS = 20 * 60 * 1000  // 20 minutes

function loadRunningTrace(stockSymbol: string): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PREFIX + stockSymbol)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { traceId: string; startedAt: number }
    if (!parsed.traceId || !parsed.startedAt) return null
    if (Date.now() - parsed.startedAt > TRACE_MAX_AGE_MS) {
      localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
      return null
    }
    return parsed.traceId
  } catch {
    return null
  }
}

function saveRunningTrace(stockSymbol: string, traceId: string): void {
  try {
    localStorage.setItem(
      STORAGE_KEY_PREFIX + stockSymbol,
      JSON.stringify({ traceId, startedAt: Date.now() }),
    )
  } catch {
    /* ignore quota errors etc. */
  }
}

function clearRunningTrace(stockSymbol: string): void {
  try {
    localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
  } catch {
    /* ignore */
  }
}

export interface DeepAnalysisModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  stockId: number
  stockName: string
  stockSymbol: string
  /** Historical analysis (if present, show it directly) */
  initialResult?: DeepAnalysisResult | null
}

export function DeepAnalysisModal({
  open,
  onOpenChange,
  stockId,
  stockName,
  stockSymbol,
  initialResult = null,
}: DeepAnalysisModalProps) {
  const { toast } = useToast()
  const [stage, setStage] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [traceId, setTraceId] = useState<string | null>(null)
  const [progress, setProgress] = useState<ProgressResponse | null>(null)
  const [result, setResult] = useState<DeepAnalysisResult | null>(initialResult)
  const [error, setError] = useState<string>('')
  const [budget, setBudget] = useState<BudgetInfo | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // SSE unsubscribe function (progress prefers SSE, falls back to polling on failure)
  const sseCloseRef = useRef<(() => void) | null>(null)
  // trigger timestamp: not_found is tolerated for the first 60s (backend log may not be written yet), don't reset
  const triggerStartedRef = useRef<number>(0)
  const NOT_FOUND_GRACE_MS = 60_000

  /** Stop all progress watching (SSE + polling) */
  const stopWatching = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (sseCloseRef.current) {
      sseCloseRef.current()
      sseCloseRef.current = null
    }
  }, [])

  // Clean up progress watching when the modal closes
  useEffect(() => {
    if (!open) stopWatching()
  }, [open, stopWatching])

  // Reset initial state + query backend for a running/completed task
  useEffect(() => {
    if (!open) return

    if (initialResult) {
      setResult(initialResult)
      setStage('done')
      return
    }

    // Reset to idle first (avoid leftover state from last time), then query backend async
    setStage('idle')
    setResult(null)
    setError('')
    setProgress(null)
    setTraceId(null)

    // Fetch 3 things concurrently:
    //   - findRunning: is there a run in progress for this stock in the last 30 minutes
    //   - getLatestForStock: is there a result completed today (still counts past 30 minutes)
    //   - getBudget: this month's budget (shown in idle state)
    // Priority: running > done (existing result) > idle
    Promise.all([
      tradingAgentsApi.findRunning(stockSymbol).catch(() => ({ trace_id: null, status: 'none' as const })),
      tradingAgentsApi.getLatestForStock(stockSymbol).catch(() => null),
      tradingAgentsApi.getBudget().catch(() => null),
    ]).then(([runningInfo, latestResult, budgetInfo]) => {
      setBudget(budgetInfo)

      // Priority: running (actually running) > done (cached today, can re-analyze) > idle
      //   - stale / failed / success / none are all treated as "not running"
      //   - In any state, show DoneView (with an "ignore cache, re-analyze" button) if there's a cache from today
      //   - In any state, IdleView's "Start Analysis" button is always available; the backend handles idempotent dedup

      // 1) Actually running (backend is the source of truth) → enter running
      if (runningInfo.status === 'running' && runningInfo.trace_id) {
        const tid = runningInfo.trace_id
        setTraceId(tid)
        setStage('running')
        // Backend confirms it's running → grace period has passed, no longer protecting not_found
        triggerStartedRef.current = Date.now() - NOT_FOUND_GRACE_MS - 1
        tradingAgentsApi.getProgress(tid).then(resp => setProgress(resp))
        startWatching(tid)
        return
      }

      // 2) Backend says stale/failed → old task died/failed, clear local trace, continue to cache check
      //    Don't go back to running; allow the user to trigger again
      if (runningInfo.status === 'stale' || runningInfo.status === 'failed') {
        clearRunningTrace(stockSymbol)
      }

      // 3) localStorage fallback (just triggered, backend hasn't written the log yet) — only tried when backend is 'none'
      if (runningInfo.status === 'none') {
        const localTrace = loadRunningTrace(stockSymbol)
        if (localTrace) {
          setTraceId(localTrace)
          setStage('running')
          triggerStartedRef.current = Date.now()
          tradingAgentsApi.getProgress(localTrace).then(resp => setProgress(resp))
          startWatching(localTrace)
          return
        }
      }

      // 4) There's a result completed today → done view (user can click "ignore cache, re-analyze")
      if (latestResult) {
        latestResult.raw_data.from_cache = true
        setResult(latestResult)
        setStage('done')
        clearRunningTrace(stockSymbol)
        return
      }

      // 5) Neither → idle (Start Analysis button available, backend handles idempotency)
      clearRunningTrace(stockSymbol)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialResult, stockSymbol])

  /** Handle one progress snapshot (SSE push and polling share the same state machine) */
  const handleProgressResponse = useCallback(
    async (resp: ProgressResponse) => {
      setProgress(resp)
      if (resp.status === 'success' && resp.run) {
        // Done, fetch the historical result
        stopWatching()
        clearRunningTrace(stockSymbol)
        const latest = await tradingAgentsApi.getLatestForStock(stockSymbol)
        if (latest) {
          setResult(latest)
          setStage('done')
        } else {
          setError('Result not saved yet, check "AI History" later')
          setStage('error')
        }
      } else if (resp.status === 'failed') {
        stopWatching()
        clearRunningTrace(stockSymbol)
        setError(resp.run?.error || 'Analysis failed')
        setStage('error')
      } else if (resp.status === 'stale') {
        // Backend detected a zombie running task (no new progress for 5 minutes, server restarted / process died)
        // → auto-reset to idle, user can trigger again
        stopWatching()
        clearRunningTrace(stockSymbol)
        setTraceId('')
        setProgress(null)
        setStage('idle')
      } else if (resp.status === 'not_found') {
        // Backend may not have written the log yet right after trigger; treat the first 60s as normal wait,
        // still not_found past the grace period → treat as trigger failure, reset to idle
        const sinceTrigger = Date.now() - triggerStartedRef.current
        if (triggerStartedRef.current > 0 && sinceTrigger > NOT_FOUND_GRACE_MS) {
          stopWatching()
          clearRunningTrace(stockSymbol)
          setTraceId('')
          setProgress(null)
          setStage('idle')
        }
      }
    },
    [stockSymbol, stopWatching],
  )

  const pollProgress = useCallback(
    async (tid: string) => {
      try {
        const resp = await tradingAgentsApi.getProgress(tid)
        await handleProgressResponse(resp)
      } catch (e) {
        // Don't terminate immediately on a polling failure, just log it
        console.warn('progress poll error:', e)
      }
    },
    [handleProgressResponse],
  )

  /** Fallback: setInterval polling (when SSE is unavailable) */
  const startPolling = useCallback(
    (tid: string) => {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = setInterval(() => pollProgress(tid), POLL_INTERVAL_MS)
      void pollProgress(tid)
    },
    [pollProgress],
  )

  /** Start watching progress: prefer SSE (server push), fall back to polling on failure/stream close (polling code kept as a fallback) */
  const startWatching = useCallback(
    (tid: string) => {
      stopWatching()
      let terminal = false
      sseCloseRef.current = subscribeSSE(`/agents/runs/${tid}/progress/stream`, {
        onEvent: (ev) => {
          if (ev.event === 'progress' && ev.data && typeof ev.data === 'object') {
            const resp = ev.data as ProgressResponse
            if (['success', 'failed', 'stale'].includes(resp.status)) terminal = true
            void handleProgressResponse(resp)
          } else if (ev.event === 'done' && ev.data?.status && ev.data.status !== 'timeout') {
            terminal = true
          }
        },
        onClosed: () => {
          // Server closed the stream normally: end if terminal; fall back to polling if not (e.g. stream timeout)
          if (!terminal) startPolling(tid)
        },
        onFailed: () => {
          // SSE unavailable (legacy proxy buffering / network issue) → fall back to polling
          startPolling(tid)
        },
      })
    },
    [handleProgressResponse, startPolling, stopWatching],
  )

  const handleStart = useCallback(async (force = false) => {
    setStage('running')
    setError('')
    setProgress(null)
    triggerStartedRef.current = Date.now()
    try {
      const triggerResp = await tradingAgentsApi.trigger(stockId, { force })
      const tid = triggerResp.trace_id || ''
      setTraceId(tid)
      if (!tid) {
        // Backend didn't return a trace_id, just show the message
        setStage('done')
        toast(triggerResp.message || 'Triggered', 'success')
        return
      }
      // Persist the trace_id so closing and reopening can resume progress
      saveRunningTrace(stockSymbol, tid)
      // Start watching progress (prefer SSE, fall back to polling on failure)
      startWatching(tid)
      // Fetch once immediately to render initial progress as soon as possible
      pollProgress(tid)
    } catch (e) {
      setStage('error')
      setError(e instanceof Error ? e.message : 'Trigger failed')
    }
  }, [stockId, stockSymbol, startWatching, pollProgress, toast])

  const handleClose = useCallback(() => {
    stopWatching()
    onOpenChange(false)
  }, [onOpenChange, stopWatching])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[92vw] max-w-6xl max-h-[85vh] overflow-y-auto scrollbar">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            🧠 Deep Analysis · {stockName} ({stockSymbol})
          </DialogTitle>
          <DialogDescription>
            TradingAgents multi-agent decision framework · For research and learning only, not investment advice
          </DialogDescription>
        </DialogHeader>

        {stage === 'idle' && (
          <IdleView
            stockSymbol={stockSymbol}
            budget={budget}
            onStart={() => handleStart(false)}
            onCancel={handleClose}
          />
        )}

        {stage === 'running' && (
          <RunningView progress={progress} traceId={traceId || ''} onClose={handleClose} />
        )}

        {stage === 'done' && result && <DoneView
          result={result}
          stockSymbol={stockSymbol}
          onRerun={() => handleStart(true)}
        />}

        {stage === 'error' && (
          <div className="space-y-3 text-[13px]">
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 p-3 text-rose-600">
              <div className="font-semibold mb-1">Analysis failed</div>
              <div className="text-[12px]">{error}</div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleClose}>Close</Button>
              <Button onClick={() => handleStart(false)}>Retry</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function IdleView({
  stockSymbol,
  budget,
  onStart,
  onCancel,
}: {
  stockSymbol: string
  budget: BudgetInfo | null
  onStart: () => void
  onCancel: () => void
}) {
  const overBudget = budget?.exceeded && budget.over_budget_action === 'reject'
  const est = budget?.estimate_next_run
  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-1.5">
        <div className="font-medium">About to analyze: {stockSymbol}</div>
        <div className="text-muted-foreground">
          Runs 4 analyst types (Technical / Sentiment / News / Fundamentals) + Bull/Bear debate + Risk control + PM synthesis
        </div>
        <div className="text-[11px] text-muted-foreground mt-2 space-y-0.5">
          <div>⏱ Estimated time: 3-8 minutes</div>
          {est ? (
            <div>💰 Estimated cost: ${est.cost_low_usd.toFixed(2)} - ${est.cost_high_usd.toFixed(2)} ({est.model})</div>
          ) : (
            <div>💰 Estimated cost: Loading...</div>
          )}
          <div>ℹ️ Runs asynchronously, you can close this dialog; you'll be notified via your notification channels when done</div>
        </div>
      </div>

      {/* Monthly budget */}
      {budget && (
        <div className={`rounded-lg p-3 text-[12px] ${overBudget ? 'bg-rose-500/10 border border-rose-500/30' : 'bg-accent/20'}`}>
          <div className="flex items-center justify-between">
            <span className="font-medium">Monthly budget</span>
            <span className={overBudget ? 'text-rose-600' : 'text-muted-foreground'}>
              ${budget.used.toFixed(2)} / ${budget.limit.toFixed(2)}
              {budget.runs_this_month > 0 && ` · ${budget.runs_this_month} runs`}
            </span>
          </div>
          {overBudget && (
            <div className="text-[11px] text-rose-600 mt-1">
              ⚠️ Monthly budget exhausted. To continue, raise `monthly_budget_usd` under Settings → Agent → TradingAgents.
            </div>
          )}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>Cancel</Button>
        <Button onClick={onStart} disabled={overBudget}>Start Analysis</Button>
      </div>
    </div>
  )
}

function RunningView({
  progress,
  traceId,
  onClose,
}: {
  progress: ProgressResponse | null
  traceId: string
  onClose: () => void
}) {
  const elapsed = progress?.elapsed_sec ?? 0
  const cost = progress?.total_cost_usd ?? 0
  const stages = progress?.stages ?? []

  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-2">
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-primary animate-pulse" />
          <span className="font-medium">Analysis in progress...</span>
          <span className="ml-auto text-[11px] text-muted-foreground">
            {formatElapsed(elapsed)} elapsed · ${cost.toFixed(4)}
          </span>
        </div>
        <div className="space-y-1 mt-3">
          {stages.length > 0 ? stages.map((s) => (
            <StageRow key={s.name} stage={s} />
          )) : (
            <div className="text-[12px] text-muted-foreground">Preparing...</div>
          )}
        </div>
        <div className="text-[10px] text-muted-foreground/70 mt-3 font-mono">
          trace_id: {traceId.slice(0, 16)}...
        </div>
      </div>

      <ToolkitDiagnostics
        summary={progress?.toolkit_summary}
        recent={progress?.toolkit_recent || []}
      />

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onClose}>
          Run in background (notify when done)
        </Button>
      </div>
    </div>
  )
}

interface ToolkitDiagItem {
  action?: string
  method?: string
  symbol?: string
  chars?: number
  snippet?: string
  source?: string
  reason?: string
}
interface ToolkitDiagSummary {
  hit: number
  miss: number
  passthrough: number
  fallthrough?: number
  error: number
}

export function ToolkitDiagnostics({
  summary,
  recent,
  defaultOpen = false,
}: {
  summary: ToolkitDiagSummary | undefined
  recent: ToolkitDiagItem[]
  defaultOpen?: boolean
}) {
  if (!summary && recent.length === 0) return null

  const hit = summary?.hit ?? 0
  const miss = summary?.miss ?? 0
  const pass = summary?.passthrough ?? 0
  const fall = summary?.fallthrough ?? 0
  const err = summary?.error ?? 0
  const total = hit + miss + pass + fall + err

  const ACTION_CLS: Record<string, string> = {
    HIT: 'text-emerald-600 dark:text-emerald-400',
    MISS: 'text-amber-600 dark:text-amber-400',
    PASSTHROUGH: 'text-sky-600 dark:text-sky-400',
    FALLTHROUGH: 'text-orange-600 dark:text-orange-400',
    ERROR: 'text-rose-600',
  }

  return (
    <details className="rounded-lg border border-border/40 bg-accent/10 p-3 text-[12px]" open={defaultOpen}>
      <summary className="cursor-pointer flex items-center gap-2 flex-wrap">
        <span className="font-medium">Data injection diagnostics</span>
        <span className="text-[11px] text-muted-foreground">
          (TickerKeep data → TradingAgents tools)
        </span>
        <span className="ml-auto text-[11px] whitespace-nowrap">
          <span className={ACTION_CLS.HIT}>HIT {hit}</span>
          <span className="text-muted-foreground"> · MISS {miss}</span>
          <span className={ACTION_CLS.PASSTHROUGH}> · Passthrough {pass}</span>
          {fall > 0 && <span className={ACTION_CLS.FALLTHROUGH}> · Fallback {fall}</span>}
          {err > 0 && <span className="text-rose-600"> · Error {err}</span>}
        </span>
      </summary>
      <div className="text-[10.5px] text-muted-foreground/80 mt-2 leading-relaxed">
        <span className={ACTION_CLS.HIT}>HIT</span>: used TickerKeep data ·{' '}
        <span className={ACTION_CLS.MISS}>MISS</span>: matched but not implemented by TickerKeep ·{' '}
        <span className={ACTION_CLS.PASSTHROUGH}>Passthrough</span>: not served from the local cache, went straight to the upstream vendor ·{' '}
        <span className={ACTION_CLS.FALLTHROUGH}>Fallback</span>: cache was empty, went upstream
      </div>
      {total === 0 ? (
        <div className="text-[11px] text-muted-foreground mt-2">
          ⚠️ No tool calls recorded yet (TradingAgents may still be in the preparation stage).
        </div>
      ) : (
        <div className="mt-2 space-y-1 max-h-64 overflow-y-auto">
          {recent.map((h, i) => {
            const action = (h.action || '').toUpperCase()
            const row = (
              <div className="font-mono text-[10.5px] flex items-center gap-2 hover:bg-accent/30 px-1 rounded cursor-help w-full">
                <span className={`${ACTION_CLS[action] || 'text-muted-foreground'} w-20 shrink-0`}>
                  {action}
                </span>
                <span className="text-foreground/80 truncate flex-1 text-left">
                  {h.method} ({h.symbol || '-'})
                  {h.reason && <span className="text-muted-foreground"> · {h.reason}</span>}
                  {h.chars != null && <span className="text-muted-foreground"> · {h.chars} chars</span>}
                  {h.source && <span className="text-muted-foreground/70"> · {h.source}</span>}
                </span>
              </div>
            )
            const hasDetail = !!(h.snippet || h.reason)
            if (!hasDetail) return <div key={i}>{row}</div>
            return (
              <HoverPopover
                key={i}
                className="block w-full"
                trigger={row}
                title={
                  <span>
                    <span className={ACTION_CLS[action] || 'text-muted-foreground'}>{action}</span>
                    <span className="text-muted-foreground"> · {h.method}({h.symbol || '-'})</span>
                    {h.source && (
                      <span className="text-muted-foreground/70"> · {h.source}</span>
                    )}
                  </span>
                }
                content={
                  <div className="space-y-2">
                    {h.reason && (
                      <div className="text-[11px] text-amber-600 dark:text-amber-400">
                        {h.reason}
                      </div>
                    )}
                    {h.snippet && (
                      <pre className="whitespace-pre-wrap break-words font-mono text-[10.5px] leading-snug bg-accent/30 rounded p-2 text-foreground/85 max-h-[60vh] overflow-y-auto">
                        {h.snippet}
                        {h.chars != null && h.chars > h.snippet.length && (
                          <span className="text-muted-foreground/60">
                            {'\n\n'}...({h.chars} chars total, showing first {h.snippet.length})
                          </span>
                        )}
                      </pre>
                    )}
                  </div>
                }
                popoverClassName="w-[44rem] max-w-[90vw]"
                side="top"
                align="start"
              />
            )
          })}
        </div>
      )}
    </details>
  )
}

function StageRow({ stage }: { stage: ProgressStage }) {
  const label = STAGE_LABEL[stage.name] || stage.name
  const icon =
    stage.status === 'done' ? '✓' : stage.status === 'running' ? '🔄' : '⏸'
  const cls =
    stage.status === 'done'
      ? 'text-emerald-600 dark:text-emerald-400'
      : stage.status === 'running'
      ? 'text-foreground'
      : 'text-muted-foreground/60'
  return (
    <div className={`flex items-center gap-2 text-[12px] ${cls}`}>
      <span className="w-4">{icon}</span>
      <span>{label}</span>
      {stage.cost_usd ? (
        <span className="ml-auto text-[10px] opacity-70 font-mono">
          ${stage.cost_usd.toFixed(4)}
        </span>
      ) : null}
    </div>
  )
}

function DoneView({
  result,
  stockSymbol,
  onRerun,
}: {
  result: DeepAnalysisResult
  stockSymbol: string
  onRerun: () => void
}) {
  // Defensive default: raw_data may be missing when fetching history from the backend, provide a full fallback to avoid a blank screen
  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion || {
    action: 'hold' as const,
    action_label: 'Hold',
    signal: '',
    reason: '',
    should_alert: false,
    agent_name: 'tradingagents',
    agent_label: 'TradingAgents Deep Analysis',
    confidence: 5.0,
  }
  const fromCache = rawData.from_cache
  const costUsd = rawData.cost_usd
  const sections = buildAnalysisSections(rawData)
  const analysisDate = result.timestamp
    ? String(result.timestamp).slice(0, 10)
    : new Date().toISOString().slice(0, 10)

  return (
    <div className="space-y-4 text-[13px]">
      {fromCache && (
        <div className="rounded-lg bg-amber-500/10 border border-amber-500/30 p-2 text-[12px] text-amber-700 dark:text-amber-400 flex items-center justify-between">
          <span>ℹ️ Cached from today: this stock was already analyzed today, showing the cached result (no new cost)</span>
          <Button variant="outline" size="sm" onClick={onRerun} className="ml-3 h-7 text-[11px]">
            Ignore cache and re-analyze
          </Button>
        </div>
      )}

      {/* Top summary (condensed to one line: decision + confidence + cost; full reasoning is in the "Final Decision" tab) */}
      <div className="rounded-lg bg-accent/30 px-4 py-2.5 flex items-center gap-3 flex-wrap">
        <span className={`text-[18px] font-bold ${DECISION_COLOR[sug.action] || ''}`}>
          {sug.action_label}
        </span>
        <span className="text-[12px] text-muted-foreground">
          Confidence {sug.confidence?.toFixed(1) ?? '-'} / 10
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-[11px] ml-auto"
          onClick={() => window.open(`/analysis/${stockSymbol}/${analysisDate}`, '_blank')}
        >
          View details
        </Button>
        <span className="text-[10px] text-muted-foreground">
          Cost: ${costUsd?.toFixed(4) ?? '-'}
        </span>
      </div>

      {/* Unified tabs: Final Decision + 4 analysts + Bull/Bear debate + Risk debate (full content + GFM tables) */}
      <AnalysisTabs sections={sections} />

      {/* Data injection diagnostics (historical report): read from raw_data.toolkit_diagnostic */}
      {rawData.toolkit_diagnostic && (
        <ToolkitDiagnostics
          summary={rawData.toolkit_diagnostic.summary}
          recent={rawData.toolkit_diagnostic.recent || []}
        />
      )}

      {/* Disclaimer */}
      <div className="text-[10px] text-muted-foreground/70 italic border-t border-border/30 pt-2">
        This analysis is generated by an AI multi-agent framework, for research and learning only, not investment advice.
        Investing involves risk; make your own decisions.
      </div>
    </div>
  )
}

/** Unified decision/analysis tabs. Content is assembled by buildAnalysisSections (shared by the modal and the detail page); only tabs with content are rendered. */
function AnalysisTabs({ sections }: { sections: AnalysisSection[] }) {
  if (sections.length === 0) return null
  return (
    <div className="rounded-lg border border-border/50 p-4">
      <Tabs defaultValue={sections[0].id}>
        <TabsList>
          {sections.map((s) => (
            <TabsTrigger key={s.id} value={s.id}>
              {s.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {sections.map((s) => (
          <TabsContent key={s.id} value={s.id}>
            <div className="prose prose-sm dark:prose-invert max-w-none leading-relaxed prose-headings:mt-4 prose-headings:mb-2 prose-p:my-2 prose-table:my-3 prose-th:px-3 prose-th:py-1.5 prose-td:px-3 prose-td:py-1.5 prose-table:text-[12px] prose-strong:text-foreground">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.markdown}</ReactMarkdown>
            </div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec.toFixed(0)}s`
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}m${s.toString().padStart(2, '0')}s`
}
