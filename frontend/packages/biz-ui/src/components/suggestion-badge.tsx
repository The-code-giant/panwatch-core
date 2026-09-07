import { DEFAULT_MARKET } from '@panwatch/api/markets'
import { useEffect, useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { KlineSummaryDialog } from '@panwatch/biz-ui/components/kline-summary-dialog'
import { KlineIndicators } from '@panwatch/biz-ui/components/kline-indicators'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { fetchAPI } from '@panwatch/api'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import { AiSuggestionBadge } from '@panwatch/biz-ui/components/ai-suggestion-badge'
import { TechnicalBadge, technicalToneFromSuggestionAction } from '@panwatch/biz-ui/components/technical-badge'

export interface SuggestionInfo {
  id?: number
  action: string  // buy/add/reduce/sell/hold/watch
  action_label: string
  signal: string
  reason: string
  should_alert: boolean
  raw?: string
  // Fields added by the suggestion pool
  agent_name?: string     // intraday_monitor/daily_report/premarket_outlook
  agent_label?: string    // Intraday Monitor/Daily Report/Premarket Outlook
  created_at?: string     // ISO timestamp
  is_expired?: boolean    // Whether it has expired
  prompt_context?: string // Prompt context
  ai_response?: string    // Raw AI response
  meta?: Record<string, any>
}

export interface KlineSummary {
  // meta (from backend)
  timeframe?: string
  computed_at?: string
  asof?: string
  params?: Record<string, any>

  trend: string
  macd_status: string
  macd_cross?: string
  macd_cross_days?: number
  recent_5_up: number
  change_5d: number | null
  change_20d: number | null
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60?: number | null
  // RSI
  rsi6?: number | null
  rsi_status?: string
  // KDJ
  kdj_k?: number | null
  kdj_d?: number | null
  kdj_j?: number | null
  kdj_status?: string
  // Bollinger Bands
  boll_upper?: number | null
  boll_mid?: number | null
  boll_lower?: number | null
  boll_status?: string
  // Volume
  volume_ratio?: number | null
  volume_trend?: string
  // Amplitude
  amplitude?: number | null
  // Multi-level support/resistance
  support: number | null
  resistance: number | null
  support_s?: number | null
  support_m?: number | null
  resistance_s?: number | null
  resistance_m?: number | null
  // Candlestick pattern
  kline_pattern?: string
}

interface SuggestionBadgeProps {
  suggestion: SuggestionInfo | null
  stockName?: string
  stockSymbol?: string
  kline?: KlineSummary | null
  showFullInline?: boolean  // Whether to show full info inline (Dashboard mode)
  market?: string           // Market (used for the technical indicators dialog)
  hasPosition?: boolean     // Whether there's a position (used for the technical indicators dialog)
  showTechnicalCompanion?: boolean // Whether to show the technical comparison badge
}

// Format suggestion time (auto-converts to local timezone, shows only hh:mm)
function formatSuggestionTime(isoTime?: string): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    // Check whether the date is valid
    if (isNaN(date.getTime())) return ''
    // Display in the local timezone
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

// Format full date/time (local timezone)
function formatSuggestionDateTime(isoTime?: string): string {
  if (!isoTime) return ''
  try {
    const date = new Date(isoTime)
    if (isNaN(date.getTime())) return ''
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return ''
  }
}

function formatKlineMeta(meta?: Record<string, any>): string {
  if (!meta) return ''
  const computedAt = meta?.kline_meta?.computed_at
  const asof = meta?.kline_meta?.asof
  const parts: string[] = []
  if (asof) parts.push(`As of ${asof}`)
  if (computedAt) parts.push(`Computed ${formatSuggestionTime(computedAt)}`)
  return parts.join(' · ')
}

export function SuggestionBadge({
  suggestion,
  stockName,
  stockSymbol,
  kline,
  showFullInline = false,
  market = DEFAULT_MARKET,
  hasPosition = false,
  showTechnicalCompanion = true,
}: SuggestionBadgeProps) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [klineDialogOpen, setKlineDialogOpen] = useState(false)
  const [feedback, setFeedback] = useState<'useful' | 'useless' | null>(null)
  const { toast } = useToast()

  useEffect(() => {
    setFeedback(null)
  }, [suggestion?.id])

  const canFeedback = !!suggestion?.id && suggestion?.agent_label !== 'Technical Indicators'
  const submitFeedback = async (useful: boolean) => {
    if (!suggestion?.id) return
    try {
      await fetchAPI('/feedback', {
        method: 'POST',
        body: JSON.stringify({ suggestion_id: suggestion.id, useful }),
      })
      setFeedback(useful ? 'useful' : 'useless')
      toast('Feedback submitted', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to submit feedback', 'error')
    }
  }

  const onDialogOpenChange = (open: boolean) => {
    setDialogOpen(open)
    if (!open) {
      try {
        ;(window as any).__panwatch_suppress_card_click_until = Date.now() + 600
      } catch {
        // ignore
      }
    }
  }

  if (!suggestion && !kline) return null

  // Dashboard mode: show full info inline (suggestion badge only)
  if (showFullInline) {
    if (!suggestion) return null
    const isAI = !!suggestion.agent_name && suggestion.agent_label !== 'Technical Indicators'
    const tech = kline ? buildKlineSuggestion(kline as any, hasPosition) : null
    const timeStr = formatSuggestionTime(suggestion.created_at)
    const klineMetaStr = formatKlineMeta(suggestion.meta)
    return (
      <>
        <div className="pt-3 border-t border-border/30">
          <div className="flex items-start gap-3">
            <div className="shrink-0 flex items-center gap-2">
              <AiSuggestionBadge
                action={suggestion.action}
                actionLabel={suggestion.action_label}
                isAI={isAI}
                isExpired={!!suggestion.is_expired}
                size="lg"
                onClick={(e) => {
                  e.stopPropagation()
                  if (suggestion.agent_label === 'Technical Indicators') setKlineDialogOpen(true)
                  else setDialogOpen(true)
                }}
                title="Click to view suggestion details"
              />
              {isAI && showTechnicalCompanion && (
                <TechnicalBadge
                  label={tech ? tech.action_label : 'Watch'}
                  tone={technicalToneFromSuggestionAction(tech?.action, tech?.action_label)}
                  size="lg"
                  onClick={(e) => { e.stopPropagation(); setKlineDialogOpen(true) }}
                  title="Click to view technical details"
                />
              )}
            </div>
            <div className="flex-1 min-w-0">
              {suggestion.signal && (
                <p className="text-[12px] font-medium text-foreground mb-0.5">{suggestion.signal}</p>
              )}
              {suggestion.reason ? (
                <p className="text-[11px] text-muted-foreground">{suggestion.reason}</p>
              ) : suggestion.raw && !suggestion.signal ? (
                <p className="text-[11px] text-muted-foreground">{suggestion.raw}</p>
              ) : null}

              {(suggestion.agent_label || timeStr) && (
                <div className="mt-1 text-[10px] text-muted-foreground/70">
                  Source: {suggestion.agent_label || (isAI ? 'AI' : 'Unknown')}
                  {timeStr && ` · ${timeStr}`}
                  {suggestion.is_expired && <span className="ml-1 text-muted-foreground">(Expired)</span>}
                </div>
              )}

              {klineMetaStr && (
                <div className="mt-1 text-[10px] text-muted-foreground/70">
                  {klineMetaStr}
                </div>
              )}
            </div>
          </div>
        </div>

        <Dialog open={dialogOpen} onOpenChange={onDialogOpenChange}>
          <DialogContent
            className="max-w-md"
            onPointerDownOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
            onInteractOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
            onClick={(e) => e.stopPropagation()}
          >
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <AiSuggestionBadge
                  action={suggestion.action}
                  actionLabel={suggestion.action_label}
                  isAI={isAI}
                  isExpired={!!suggestion.is_expired}
                  size="lg"
                />
                {/* AI label is already shown in the button text, no need to repeat */}
                {stockName && (
                  <span className="text-[14px] font-normal text-muted-foreground">
                    {stockName} {stockSymbol && `(${stockSymbol})`}
                  </span>
                )}
              </DialogTitle>
              {/* Source info */}
              {(suggestion.agent_label || suggestion.created_at) && (
                <div className="text-[11px] text-muted-foreground/70 mt-1">
                  Source: {suggestion.agent_label || 'Unknown'}
                  {suggestion.created_at && ` · ${formatSuggestionDateTime(suggestion.created_at)}`}
                  {suggestion.is_expired && <span className="ml-2 text-muted-foreground">(Expired)</span>}
                </div>
              )}
            </DialogHeader>

            <div className="space-y-4">
              {/* Feedback */}
              {canFeedback && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">Was this suggestion helpful?</div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => submitFeedback(true)}
                      disabled={feedback !== null}
                      className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                        feedback === 'useful'
                          ? 'bg-success/10 border-success/30 text-success'
                          : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      Helpful
                    </button>
                    <button
                      onClick={() => submitFeedback(false)}
                      disabled={feedback !== null}
                      className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                        feedback === 'useless'
                          ? 'bg-destructive/10 border-destructive/30 text-destructive'
                          : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      Not helpful
                    </button>
                    {feedback && (
                      <span className="text-[11px] text-muted-foreground">Recorded, thanks for the feedback</span>
                    )}
                  </div>
                </div>
              )}

              {/* Signal */}
              {suggestion.signal && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">Signal</div>
                  <p className="text-[13px] font-medium text-foreground">{suggestion.signal}</p>
                </div>
              )}

              {/* Reason */}
              {(suggestion.reason || suggestion.raw) && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">Reason</div>
                  <p className="text-[13px] text-foreground">
                    {suggestion.reason || suggestion.raw}
                  </p>
                </div>
              )}

              {/* Technical indicators */}
              {kline && (
                <div className="space-y-3">
                  <div className="text-[11px] text-muted-foreground">Technical Indicators</div>
                  <KlineIndicators summary={kline as any} />
                </div>
              )}

              {/* Raw AI response */}
              {suggestion.ai_response && (
                <div>
                  <div className="text-[11px] text-muted-foreground mb-1">AI Response</div>
                  <div className="text-[12px] text-foreground whitespace-pre-wrap bg-accent/30 rounded p-2 max-h-32 overflow-y-auto scrollbar">
                    {suggestion.ai_response}
                  </div>
                </div>
              )}

              {/* Prompt context */}
              {suggestion.prompt_context && (
                <details className="group">
                  <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                    Prompt context <span className="text-[10px]">(click to expand)</span>
                  </summary>
                  <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 max-h-48 overflow-y-auto scrollbar">
                    {suggestion.prompt_context}
                  </div>
                </details>
              )}
            </div>
          </DialogContent>
        </Dialog>
        <KlineSummaryDialog
          open={klineDialogOpen}
          onOpenChange={setKlineDialogOpen}
          symbol={stockSymbol || ''}
          market={market}
          stockName={stockName}
          hasPosition={hasPosition}
          initialSummary={kline as any}
        />
      </>
    )
  }

  // Show technical indicators only (no suggestion)
  if (!suggestion && kline) {
    return (
      <>
        <div className="inline-flex flex-col items-start gap-0.5">
          <TechnicalBadge
            label="Indicators"
            tone="neutral"
            size="xs"
            onClick={(e) => {
              e.stopPropagation()
              setKlineDialogOpen(true)
            }}
            title="Click to view technical indicators"
          />
        </div>

        <KlineSummaryDialog
          open={klineDialogOpen}
          onOpenChange={setKlineDialogOpen}
          symbol={stockSymbol || ''}
          market={market || DEFAULT_MARKET}
          stockName={stockName}
          hasPosition={hasPosition}
          initialSummary={kline as any}
        />
      </>
    )
  }

  if (!suggestion) return null
  const isAI = !!suggestion.agent_name && suggestion.agent_label !== 'Technical Indicators'

  // Positions page mode: small badge + click to open dialog
  const timeStr = formatSuggestionTime(suggestion.created_at)
  const sourceInfo = ''

  return (
    <>
      <div className="inline-flex flex-col items-start gap-0.5">
        <div className="inline-flex items-center gap-1">
          <AiSuggestionBadge
            action={suggestion.action}
            actionLabel={suggestion.action_label}
            isAI={isAI}
            isExpired={!!suggestion.is_expired}
            size="md"
            onClick={(e) => {
              e.stopPropagation()
              if (suggestion.agent_label === 'Technical Indicators') setKlineDialogOpen(true)
              else setDialogOpen(true)
            }}
            title={sourceInfo ? `${sourceInfo} - Click to view details` : 'Click to view suggestion details'}
          />
          {showTechnicalCompanion && suggestion.agent_label !== 'Technical Indicators' && (
            (() => {
              const tech = kline ? buildKlineSuggestion(kline as any, hasPosition) : null
              return (
                <TechnicalBadge
                  label={tech ? tech.action_label : 'Watch'}
                  tone={technicalToneFromSuggestionAction(tech?.action, tech?.action_label)}
                  size="md"
                  onClick={(e) => { e.stopPropagation(); setKlineDialogOpen(true) }}
                  title="Click to view technical details"
                />
              )
            })()
          )}
        </div>
        {/* Source and time (shown below the badge, AI suggestions only, for extra distinction) */}
        {isAI && (
          <div className="mt-1 text-[10px] text-muted-foreground/70">
            Source: {suggestion.agent_label || 'AI'}{timeStr && ` · ${timeStr}`}
            {suggestion.is_expired && <span className="ml-1 text-muted-foreground">(Expired)</span>}
          </div>
        )}
      </div>

      <Dialog open={dialogOpen} onOpenChange={onDialogOpenChange}>
        <DialogContent
          className="max-w-md"
          onPointerDownOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
          onInteractOutside={(e) => { e.preventDefault(); setDialogOpen(false) }}
          onClick={(e) => e.stopPropagation()}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AiSuggestionBadge
                action={suggestion.action}
                actionLabel={suggestion.action_label}
                isAI={isAI}
                isExpired={!!suggestion.is_expired}
                size="md"
              />
              {stockName && (
                <span className="text-[14px] font-normal text-muted-foreground">
                  {stockName} {stockSymbol && `(${stockSymbol})`}
                </span>
              )}
            </DialogTitle>
            {/* Source info */}
            {(suggestion.agent_label || suggestion.created_at) && (
              <div className="text-[11px] text-muted-foreground/70 mt-1">
                Source: {suggestion.agent_label || 'Unknown'}
                {suggestion.created_at && ` · ${formatSuggestionDateTime(suggestion.created_at)}`}
                {suggestion.is_expired && <span className="ml-2 text-muted-foreground">(Expired)</span>}
              </div>
            )}
          </DialogHeader>

          <div className="space-y-4">
            {/* Feedback */}
            {canFeedback && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">Was this suggestion helpful?</div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => submitFeedback(true)}
                    disabled={feedback !== null}
                    className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                      feedback === 'useful'
                        ? 'bg-success/10 border-success/30 text-success'
                        : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    Helpful
                  </button>
                  <button
                    onClick={() => submitFeedback(false)}
                    disabled={feedback !== null}
                    className={`text-[12px] px-3 py-1.5 rounded-md border transition-colors ${
                      feedback === 'useless'
                        ? 'bg-destructive/10 border-destructive/30 text-destructive'
                        : 'bg-background/40 border-border/60 text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    Not helpful
                  </button>
                  {feedback && (
                    <span className="text-[11px] text-muted-foreground">Recorded, thanks for the feedback</span>
                  )}
                </div>
              </div>
            )}

            {/* Signal */}
            {suggestion.signal && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">Signal</div>
                <p className="text-[13px] font-medium text-foreground">{suggestion.signal}</p>
              </div>
            )}

            {/* Reason */}
            {(suggestion.reason || suggestion.raw) && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">Reason</div>
                <p className="text-[13px] text-foreground">
                  {suggestion.reason || suggestion.raw}
                </p>
              </div>
            )}

            {/* Technical indicators */}
            {kline && (
              <div className="space-y-3">
                <div className="text-[11px] text-muted-foreground">Technical Indicators</div>
                <KlineIndicators summary={kline as any} />
              </div>
            )}

            {/* Raw AI response */}
            {suggestion.ai_response && (
              <div>
                <div className="text-[11px] text-muted-foreground mb-1">AI Response</div>
                <div className="text-[12px] text-foreground whitespace-pre-wrap bg-accent/30 rounded p-2 max-h-32 overflow-y-auto">
                  {suggestion.ai_response}
                </div>
              </div>
            )}

            {/* Prompt context */}
            {suggestion.prompt_context && (
              <details className="group">
                <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                  Prompt context <span className="text-[10px]">(click to expand)</span>
                </summary>
                <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 max-h-48 overflow-y-auto">
                  {suggestion.prompt_context}
                </div>
              </details>
            )}
          </div>
        </DialogContent>
      </Dialog>
      {/* Always mount K-line dialog for technical details */}
      <KlineSummaryDialog
        open={klineDialogOpen}
        onOpenChange={setKlineDialogOpen}
        symbol={stockSymbol || ''}
        market={market}
        stockName={stockName}
        hasPosition={hasPosition}
        initialSummary={kline as any}
      />
    </>
  )
}
