import { useCallback, useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { fetchAPI } from '@tickerkeep/api'
import { DEFAULT_MARKET } from '@tickerkeep/api/markets'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@tickerkeep/base-ui/components/ui/dialog'
import { Button } from '@tickerkeep/base-ui/components/ui/button'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { HoverPopover } from '@tickerkeep/base-ui/components/ui/hover-popover'
import { TechnicalBadge, technicalToneFromSuggestionAction } from '@tickerkeep/biz-ui/components/technical-badge'

export interface KlineSummaryData {
  // meta (from backend)
  timeframe?: string
  computed_at?: string
  asof?: string
  params?: Record<string, any>

  last_close?: number | null
  recent_5_up?: number | null
  trend?: string
  macd_status?: string
  macd_cross?: string | null
  macd_cross_days?: number | null
  macd_hist?: number | null
  rsi6?: number | null
  rsi_status?: string
  kdj_k?: number | null
  kdj_d?: number | null
  kdj_j?: number | null
  kdj_status?: string
  volume_ratio?: number | null
  volume_trend?: string
  boll_upper?: number | null
  boll_mid?: number | null
  boll_lower?: number | null
  boll_width?: number | null
  boll_status?: string
  ma5?: number | null
  ma10?: number | null
  ma20?: number | null
  ma60?: number | null
  kline_pattern?: string | null
  support?: number | null
  resistance?: number | null
  support_s?: number | null
  support_m?: number | null
  support_l?: number | null
  resistance_s?: number | null
  resistance_m?: number | null
  resistance_l?: number | null
  change_5d?: number | null
  change_20d?: number | null
  amplitude?: number | null
  amplitude_avg5?: number | null
}

interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: KlineSummaryData
}

interface KlineSummaryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  stockName?: string
  hasPosition?: boolean
  initialSummary?: KlineSummaryData | null
}

function formatLocalDateTime(iso?: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
  } catch {
    return ''
  }
}

export function KlineSummaryDialog({
  open,
  onOpenChange,
  symbol,
  market,
  stockName,
  hasPosition,
  initialSummary = null,
}: KlineSummaryDialogProps) {
  const [loading, setLoading] = useState(false)
  const [summary, setSummary] = useState<KlineSummaryData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const buildSuggestion = (s: KlineSummaryData, holding?: boolean) => {
    const scored = buildKlineSuggestion(s, holding)
    const items: Array<{ text: string; delta: number }> = []
    let localScore = 0

    const add = (text: string, delta: number) => { items.push({ text, delta }); localScore += delta }

    if (s.trend?.includes('Bullish')) add('Bullish MA alignment, trend leaning strong', 2)
    else if (s.trend?.includes('Bearish')) add('Bearish MA alignment, trend leaning weak', -2)

    if (s.macd_status?.includes('Golden Cross')) add('MACD golden cross, short-term momentum strengthening', 2)
    if (s.macd_status?.includes('Death Cross')) add('MACD death cross, short-term momentum weakening', -2)
    if (typeof s.macd_hist === 'number') add(`MACD histogram ${s.macd_hist > 0 ? 'positive' : s.macd_hist < 0 ? 'negative' : 'near 0'}`, s.macd_hist > 0 ? 1 : s.macd_hist < 0 ? -1 : 0)

    if (s.rsi_status?.includes('Oversold')) add('RSI oversold, a bounce may be possible', 1)
    else if (s.rsi_status?.includes('Strong')) add('RSI strong, buyers in control', 1)
    else if (s.rsi_status?.includes('Overbought')) add('RSI overbought, watch for pullback risk', -1)
    else if (s.rsi_status?.includes('Weak')) add('RSI weak, short-term pressure', -1)

    if (s.kdj_status?.includes('Golden Cross')) add('KDJ golden cross, turning stronger short-term', 1)
    if (s.kdj_status?.includes('Death Cross')) add('KDJ death cross, turning weaker short-term', -1)

    if (s.boll_status?.includes('Broke Above Upper Band')) add('Broke above the Bollinger upper band, strong trend', 1)
    else if (s.boll_status?.includes('Broke Below Lower Band')) add('Broke below the Bollinger lower band, weakening', -1)

    if (s.volume_trend?.includes('High Volume')) add('High volume confirmation, capital participation rising', 1)
    else if (s.volume_trend?.includes('Low Volume')) add('Low volume, momentum lacking', -1)

    if (s.last_close != null && s.support != null && s.support > 0 && s.last_close <= s.support * 1.02) add('Price near support, chance of a bounce rising', 1)
    if (s.last_close != null && s.resistance != null && s.resistance > 0 && s.last_close >= s.resistance * 0.98) add('Price near resistance, upside limited', -1)

    return { ...scored, score: localScore, items }
  }

  useEffect(() => {
    if (!open || !symbol) return

    // If we already have preloaded summary, use it without refetch
    if (initialSummary) {
      setSummary(initialSummary)
      setError(null)
      setLoading(false)
      return
    }

    setLoading(true)
    setError(null)
    setSummary(null)

    const m = market || DEFAULT_MARKET
    fetchAPI<KlineSummaryResponse>(`/klines/${encodeURIComponent(symbol)}/summary?market=${encodeURIComponent(m)}`)
      .then((data) => setSummary(data.summary || null))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [open, symbol, market, initialSummary])

  const effectiveSummary = initialSummary || summary
  const suggestion = effectiveSummary ? buildSuggestion(effectiveSummary, hasPosition) : null

  const handleAskAI = useCallback(() => {
    if (!effectiveSummary) return
    const s = effectiveSummary
    const parts: string[] = []
    const items = []
    if (s.trend) items.push(`Trend ${s.trend}`)
    if (s.macd_status) items.push(`MACD ${s.macd_status}${s.macd_hist != null ? `(hist=${s.macd_hist.toFixed(3)})` : ''}`)
    if (s.rsi_status) items.push(`RSI ${s.rsi_status}${s.rsi6 != null ? `(${s.rsi6.toFixed(0)})` : ''}`)
    if (s.kdj_status) items.push(`KDJ ${s.kdj_status}${s.kdj_k != null ? `(K=${s.kdj_k.toFixed(1)},D=${s.kdj_d?.toFixed(1)},J=${s.kdj_j?.toFixed(1)})` : ''}`)
    if (s.boll_status) items.push(`Bollinger ${s.boll_status}${s.boll_width != null ? `(bandwidth ${s.boll_width.toFixed(1)}%)` : ''}`)
    if (s.volume_trend) items.push(`Volume ${s.volume_trend}${s.volume_ratio != null ? `(${s.volume_ratio.toFixed(1)}x)` : ''}`)
    if (items.length) parts.push(`Technical indicators: ${items.join(', ')}`)
    if (s.support != null) parts.push(`Support: ${s.support.toFixed(2)}`)
    if (s.resistance != null) parts.push(`Resistance: ${s.resistance.toFixed(2)}`)
    if (s.last_close != null) parts.push(`Close: ${s.last_close.toFixed(2)}`)
    if (s.change_5d != null) parts.push(`5-day change: ${s.change_5d.toFixed(2)}%`)
    if (s.change_20d != null) parts.push(`20-day change: ${s.change_20d.toFixed(2)}%`)
    if (s.ma5 != null) parts.push(`Moving averages: MA5=${s.ma5.toFixed(2)} MA10=${s.ma10?.toFixed(2)} MA20=${s.ma20?.toFixed(2)} MA60=${s.ma60?.toFixed(2)}`)
    if (suggestion) {
      parts.push(`Technical score: ${suggestion.action_label}(score=${suggestion.score}), signal: ${suggestion.signal || 'Neutral'}`)
      if (suggestion.items.length) {
        parts.push(`Score basis: ${suggestion.items.map(e => `${e.text}(${e.delta > 0 ? '+' : ''}${e.delta})`).join('; ')}`)
      }
    }
    window.dispatchEvent(new CustomEvent('tickerkeep-open-chat', {
      detail: { symbol, market, stockName: stockName || symbol, pageContext: parts.join('\n') }
    }))
    onOpenChange(false)
  }, [effectiveSummary, suggestion, symbol, market, stockName, onOpenChange])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <DialogHeader>
          <DialogTitle>Chart / Technical Indicators</DialogTitle>
          <DialogDescription>
            <div className="space-y-0.5">
              <div>{stockName ? `${stockName} (${symbol})` : symbol}</div>
              {(effectiveSummary?.timeframe || effectiveSummary?.computed_at || effectiveSummary?.asof) && (
                <div className="text-[11px] text-muted-foreground/70">
                  {effectiveSummary?.timeframe ? `Timeframe: ${effectiveSummary.timeframe}` : 'Timeframe: 1d'}
                  {effectiveSummary?.asof ? ` · Data as of: ${effectiveSummary.asof}` : ''}
                  {effectiveSummary?.computed_at ? ` · Computed: ${formatLocalDateTime(effectiveSummary.computed_at)}` : ''}
                </div>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>

        {!initialSummary && loading ? (
          <div className="text-[12px] text-muted-foreground">Loading...</div>
        ) : error ? (
          <div className="text-[12px] text-rose-500">{error}</div>
        ) : !effectiveSummary ? (
          <div className="text-[12px] text-muted-foreground">No data available</div>
        ) : (
          <div className="space-y-3">
            {suggestion && (
              <div className="p-3 rounded-lg bg-accent/20 border border-border/30">
                <div className="flex items-center justify-between gap-2">
                  <TechnicalBadge
                    label={suggestion.action_label}
                    tone={technicalToneFromSuggestionAction(suggestion.action, suggestion.action_label)}
                    size="sm"
                  />
                  <span className="text-[10px] text-muted-foreground">
                    {hasPosition ? 'Held' : 'Not held'} · score {suggestion.score}
                  </span>
                </div>
                <div className="mt-2 text-[12px] text-foreground font-medium">
                  {suggestion.signal}
                </div>

                {suggestion.items.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {suggestion.items.map((it, idx) => {
                      const color =
                        it.delta > 0 ? 'text-stock-up' :
                        it.delta < 0 ? 'text-stock-down' :
                        'text-muted-foreground'
                      return (
                        <div key={`${it.text}-${idx}`} className="flex items-center justify-between gap-3 text-[11px]">
                          <span className="text-muted-foreground">{it.text}</span>
                          <span className={`font-mono ${color}`}>
                            {it.delta > 0 ? '+' : ''}{it.delta}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}

                <div className="mt-2 text-[10px] text-muted-foreground/70">
                  Generated from technical-indicator rules only, not investment advice
                </div>
              </div>
            )}

            <div className="text-[10px] text-muted-foreground/60">
              Tip: hover over an indicator badge for a detailed explanation
            </div>

            <div className="flex flex-wrap gap-2 text-[11px]">
              {effectiveSummary.trend && (
                <HoverPopover
                  title="Trend (Moving Average Alignment)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        The trend tag comes from the relative position of the moving averages (MA5/MA10/MA20), computed from daily closing prices. Shorter MAs are more sensitive; longer ones are smoother.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Common interpretation: </span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Bullish Alignment</span> (MA5 &gt; MA10 &gt; MA20): the uptrend is "cleaner"; pullbacks usually first test MA5/MA10 support.</li>
                          <li><span className="font-medium text-foreground">Bearish Alignment</span> (MA5 &lt; MA10 &lt; MA20): the downtrend dominates; bounces to MA10/MA20 often meet resistance.</li>
                          <li><span className="font-medium text-foreground">MA Crossed</span>: a choppy/rotation period - signals rely more on volume and key price levels.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">Current: {effectiveSummary.trend}</div>
                      {(effectiveSummary.ma5 != null || effectiveSummary.ma10 != null || effectiveSummary.ma20 != null || effectiveSummary.ma60 != null) && (
                        <div className="text-[10px] text-muted-foreground/70">
                          MAs: MA5≈{effectiveSummary.ma5 != null ? effectiveSummary.ma5.toFixed(2) : '-'}; MA10≈{effectiveSummary.ma10 != null ? effectiveSummary.ma10.toFixed(2) : '-'}; MA20≈{effectiveSummary.ma20 != null ? effectiveSummary.ma20.toFixed(2) : '-'}; MA60≈{effectiveSummary.ma60 != null ? effectiveSummary.ma60.toFixed(2) : '-'}
                        </div>
                      )}
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: moving averages are a lagging indicator, better suited to "filtering the trend" than as a standalone entry/exit signal.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={effectiveSummary.trend} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary.macd_status && (
                <HoverPopover
                  title="MACD (Trend/Momentum)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        MACD consists of two lines (DIF/DEA) and a histogram (hist). Common formula: DIF=EMA12-EMA26, DEA=EMA(DIF,9), hist≈(DIF-DEA)*2.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">What it means: </span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Golden Cross</span>: DIF crosses above DEA - short-term momentum turning from weak to strong.</li>
                          <li><span className="font-medium text-foreground">Death Cross</span>: DIF crosses below DEA - short-term momentum turning from strong to weak.</li>
                          <li><span className="font-medium text-foreground">Positive/negative histogram</span>: positive usually means bullish momentum dominates; negative usually means bearish momentum dominates.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Current: {effectiveSummary.macd_status}{effectiveSummary.macd_hist != null ? `, histogram ${effectiveSummary.macd_hist > 0 ? 'positive' : effectiveSummary.macd_hist < 0 ? 'negative' : 'near 0'} (hist≈${effectiveSummary.macd_hist.toFixed(3)})` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: MACD is prone to frequent "false crosses" in choppy ranges - usually needs confirmation from trend (moving averages) and volume-price.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={`MACD ${effectiveSummary.macd_status}`} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary.rsi_status && (
                <HoverPopover
                  title="RSI (Relative Strength Index)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        RSI measures the relative strength of gains vs. losses over a period (0-100). Shown here is RSI6 (last 6 trading days).
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Thresholds used in this app: </span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>RSI6 &gt; 80: Overbought (higher pullback risk)</li>
                          <li>RSI6 70-80: Strong (momentum leaning bullish)</li>
                          <li>RSI6 &lt; 20: Oversold (a bounce is possible, but can stay oversold for a long time in a downtrend)</li>
                          <li>RSI6 20-30: Weak (momentum leaning bearish)</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Current: {effectiveSummary.rsi_status}{effectiveSummary.rsi6 != null ? `, RSI6≈${effectiveSummary.rsi6.toFixed(0)}` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: overbought doesn't mean an immediate drop, and oversold doesn't mean an immediate bounce; it's more reliable when combined with trend and key levels to spot "divergence/exhaustion."
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`RSI ${effectiveSummary.rsi_status}${effectiveSummary.rsi6 != null ? ` (${effectiveSummary.rsi6.toFixed(0)})` : ''}`}
                      tone={effectiveSummary.rsi_status === 'Overbought' ? 'bullish' : effectiveSummary.rsi_status === 'Oversold' ? 'bearish' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.kdj_status && (
                <HoverPopover
                  title="KDJ (Stochastic Oscillator)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        KDJ is a momentum indicator that reflects where the price sits within a recent range (similar to a stochastic oscillator). The commonly used signal is a golden/death cross between K and D.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">What it means: </span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Golden Cross</span>: a sign of short-term strengthening, more effective alongside an uptrend.</li>
                          <li><span className="font-medium text-foreground">Death Cross</span>: a sign of short-term weakening, more effective alongside a downtrend.</li>
                          <li>Extreme J values (&gt;100 or &lt;0) are often treated as "overbought/oversold," but can be distorted in a strong trend.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>Current: {effectiveSummary.kdj_status}</div>
                        {(effectiveSummary.kdj_k != null || effectiveSummary.kdj_d != null || effectiveSummary.kdj_j != null) && (
                          <div>
                            K≈{effectiveSummary.kdj_k != null ? effectiveSummary.kdj_k.toFixed(1) : '-'}{' '}
                            D≈{effectiveSummary.kdj_d != null ? effectiveSummary.kdj_d.toFixed(1) : '-'}{' '}
                            J≈{effectiveSummary.kdj_j != null ? effectiveSummary.kdj_j.toFixed(1) : '-'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: KDJ can whipsaw frequently in choppy markets - combine it with support/resistance levels.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={`KDJ ${effectiveSummary.kdj_status}`} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary?.volume_trend && (
                <HoverPopover
                  title="Volume (High/Low)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        Volume is used to judge whether a move has "trading support." The volume trend here comes from volume_ratio (today's volume / 5-day average volume).
                      </div>
                      <div>
                        <span className="font-medium text-foreground">How to interpret it: </span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">High Volume</span>: usually means rising participation; on an up move, favors trend continuation.</li>
                          <li><span className="font-medium text-foreground">Low Volume</span>: may indicate hesitation/exhaustion; on a down move, sometimes signals easing sell pressure.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Current: {effectiveSummary.volume_trend}{effectiveSummary.volume_ratio != null ? `, ratio≈${effectiveSummary.volume_ratio.toFixed(1)}x` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: the significance of volume needs to be judged together with price direction (up+volume up / up+volume down / down+volume up / down+volume down).
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`${effectiveSummary.volume_trend}${effectiveSummary.volume_ratio != null ? ` (${effectiveSummary.volume_ratio.toFixed(1)}x)` : ''}`}
                      tone={effectiveSummary.volume_trend === 'High Volume' ? 'warning' : effectiveSummary.volume_trend === 'Low Volume' ? 'info' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.boll_status && (
                <HoverPopover
                  title="Bollinger Bands (Volatility/Channel)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        Bollinger Bands consist of a middle band (usually MA20) and upper/lower bands (middle ±2 standard deviations), used to describe the price channel and changes in volatility.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">What it means: </span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Broke Above Upper Band</span>: short-term strength, but could also "spike and fade" - confirm with volume.</li>
                          <li><span className="font-medium text-foreground">Broke Below Lower Band</span>: short-term weakness, but an oversold bounce can occur during panic selling.</li>
                          <li>Bands narrowing is common when volatility is contracting, and often precedes a directional move; bands widening means volatility is expanding.</li>
                        </ul>
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Bandwidth thresholds used in this app: </span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>Bandwidth &lt; 5: Narrow (leaning toward consolidation/basing)</li>
                          <li>Bandwidth &gt; 15: Widening (volatility expanding)</li>
                          <li>Otherwise: Normal volatility</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>
                          Current: {effectiveSummary.boll_status}{effectiveSummary.boll_width != null ? `, bandwidth≈${effectiveSummary.boll_width.toFixed(1)}%` : ''}
                        </div>
                        {(effectiveSummary.boll_upper != null || effectiveSummary.boll_mid != null || effectiveSummary.boll_lower != null) && (
                          <div>
                            Upper≈{effectiveSummary.boll_upper != null ? effectiveSummary.boll_upper.toFixed(2) : '-'}; Mid≈{effectiveSummary.boll_mid != null ? effectiveSummary.boll_mid.toFixed(2) : '-'}; Lower≈{effectiveSummary.boll_lower != null ? effectiveSummary.boll_lower.toFixed(2) : '-'}
                          </div>
                        )}
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`Bollinger ${effectiveSummary.boll_status}`}
                      tone={effectiveSummary.boll_status === 'Broke Above Upper Band' ? 'bullish' : effectiveSummary.boll_status === 'Broke Below Lower Band' ? 'bearish' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.kline_pattern && (
                <HoverPopover
                  title="Candlestick Pattern (Local Structure)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        The pattern comes from recognizing the shape of the last 1-2 candles (e.g. doji, hammer, engulfing, etc.) - a "local signal."
                      </div>
                      <div>
                        <span className="font-medium text-foreground">What it means: </span>
                        Most patterns need confirmation from trend, volume, and key levels. A hammer means more near the end of a downtrend; an engulfing pattern is more about "comparing two consecutive candles."
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">Current: {effectiveSummary.kline_pattern}</div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: single-candle patterns have a high false-positive rate - treat as a hint only, not a standalone decision.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={effectiveSummary.kline_pattern} tone="warning" help />
                  }
                />
              )}
            </div>

            <div className="flex flex-wrap gap-2 text-[11px]">
              {effectiveSummary && effectiveSummary.support != null && (
                <HoverPopover
                  title="Support (Key Support Zone)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        Support can be thought of as a price zone where "buyers are more likely to show up." Near support, the price is more likely to stabilize, bounce, or consolidate.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">How this app computes it: </span>
                        The support shown here comes from the lowest low over the most recent 20 trading days - a medium-term reference level.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">How to use it: </span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>Think of it as a "zone" rather than a line precise to the cent - a common practice is to allow some margin (e.g. ±1%~2%).</li>
                          <li>Near support, a low-volume stabilization or high-volume bounce is usually a more reliable signal; a high-volume break below can invalidate support and flip it into resistance.</li>
                          <li>Good for setting stop-loss/take-profit/position-sizing ranges: use key levels to bound risk rather than predict exact highs/lows.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>Current: support≈{effectiveSummary.support.toFixed(2)}</div>
                        {effectiveSummary.last_close != null && effectiveSummary.support > 0 && (
                          <div>
                            Distance (from close)≈{(((effectiveSummary.last_close - effectiveSummary.support) / effectiveSummary.support) * 100).toFixed(2)}%
                            {' '}
                            {effectiveSummary.last_close <= effectiveSummary.support * 1.02 ? '(near support, scoring rule adds points)' : ''}
                          </div>
                        )}
                        {(effectiveSummary.support_s != null || effectiveSummary.support_m != null || effectiveSummary.support_l != null) && (
                          <div>
                            By timeframe: short (5d)≈{effectiveSummary.support_s != null ? effectiveSummary.support_s.toFixed(2) : '-'}; medium (20d)≈{effectiveSummary.support_m != null ? effectiveSummary.support_m.toFixed(2) : '-'}; long (60d)≈{effectiveSummary.support_l != null ? effectiveSummary.support_l.toFixed(2) : '-'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: support/resistance are "statistically derived key levels," not points guaranteed to cause a reversal - a strong trend can break straight through them.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`Support ${effectiveSummary.support.toFixed(2)}`}
                      tone="bearish"
                      help
                    />
                  }
                />
              )}
              {effectiveSummary && effectiveSummary.resistance != null && (
                <HoverPopover
                  title="Resistance (Key Resistance Zone)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        Resistance can be thought of as a price zone where "sellers are more likely to show up." Near resistance, upward moves are more likely to stall, pull back, or enter a range.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">How this app computes it: </span>
                        The resistance shown here comes from the highest high over the most recent 20 trading days - a medium-term reference level.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">How to use it: </span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>The closer to resistance, the worse the risk/reward of chasing; a more common approach is to wait for a "high-volume breakout that holds on the retest" before considering entry.</li>
                          <li>If a high-volume breakout of resistance holds, the old resistance often "switches roles" and becomes new support.</li>
                          <li>Use the area near resistance to plan staged take-profit/reductions, or watch for volume-price divergence, spike-and-fade, and other risk signals.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>Current: resistance≈{effectiveSummary.resistance.toFixed(2)}</div>
                        {effectiveSummary.last_close != null && effectiveSummary.resistance > 0 && (
                          <div>
                            Distance (from close)≈{(((effectiveSummary.resistance - effectiveSummary.last_close) / effectiveSummary.resistance) * 100).toFixed(2)}%
                            {' '}
                            {effectiveSummary.last_close >= effectiveSummary.resistance * 0.98 ? '(near resistance, scoring rule subtracts points)' : ''}
                          </div>
                        )}
                        {(effectiveSummary.resistance_s != null || effectiveSummary.resistance_m != null || effectiveSummary.resistance_l != null) && (
                          <div>
                            By timeframe: short (5d)≈{effectiveSummary.resistance_s != null ? effectiveSummary.resistance_s.toFixed(2) : '-'}; medium (20d)≈{effectiveSummary.resistance_m != null ? effectiveSummary.resistance_m.toFixed(2) : '-'}; long (60d)≈{effectiveSummary.resistance_l != null ? effectiveSummary.resistance_l.toFixed(2) : '-'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: whether a breakout is valid usually depends on "high volume + holding/retest confirmation." Piercing a level for a moment alone is often a false breakout.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`Resistance ${effectiveSummary.resistance.toFixed(2)}`}
                      tone="bullish"
                      help
                    />
                  }
                />
              )}
            </div>

            {(effectiveSummary.change_5d != null || effectiveSummary.change_20d != null || effectiveSummary.amplitude != null) && (
              <div className="flex gap-4 text-[11px] text-muted-foreground">
                {effectiveSummary.change_5d != null && (
                  <HoverPopover
                    title="5-Day Change (Short-term Momentum)"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">What it is: </span>
                          The 5-day change is the overall return over the last 5 trading days, for a quick read on short-term momentum.
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How this app computes it: </span>
                          Today's close vs. the close 5 trading days ago: (Close[t]-Close[t-5]) / Close[t-5].
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How to interpret it: </span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>Positive: short-term strength; more likely to continue alongside high volume/a bullish trend.</li>
                            <li>Negative: short-term weakness; more risk if MAs are bearish and MACD shows a death cross.</li>
                            <li>An excessively large gain can also mean "short-term overheating" - guard against a pullback; better to set risk controls around support/resistance.</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70">
                          Current: {effectiveSummary.change_5d >= 0 ? '+' : ''}{effectiveSummary.change_5d.toFixed(2)}%
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        5d{' '}
                        <span className={effectiveSummary.change_5d >= 0 ? 'text-stock-up' : 'text-stock-down'}>
                          {effectiveSummary.change_5d >= 0 ? '+' : ''}{effectiveSummary.change_5d.toFixed(2)}%
                        </span>
                      </span>
                    }
                  />
                )}
                {effectiveSummary.change_20d != null && (
                  <HoverPopover
                    title="20-Day Change (Swing/Monthly Momentum)"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">What it is: </span>
                          The 20-day change is close to one trading month's overall return, more reflective of the "swing trend."
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How this app computes it: </span>
                          Today's close vs. the close 20 trading days ago: (Close[t]-Close[t-20]) / Close[t-20].
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How to interpret it: </span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>Positive with a bullish trend: usually trend-following; watch support and volume on pullbacks.</li>
                            <li>Negative with a bearish trend: usually going against the wind; bounces near resistance tend to stall.</li>
                            <li>5-day and 20-day diverging: may mean a "short-term bounce/pullback" is occurring within a bigger trend - be careful judging whether it's a reversal.</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70">
                          Current: {effectiveSummary.change_20d >= 0 ? '+' : ''}{effectiveSummary.change_20d.toFixed(2)}%
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        20d{' '}
                        <span className={effectiveSummary.change_20d >= 0 ? 'text-stock-up' : 'text-stock-down'}>
                          {effectiveSummary.change_20d >= 0 ? '+' : ''}{effectiveSummary.change_20d.toFixed(2)}%
                        </span>
                      </span>
                    }
                  />
                )}
                {effectiveSummary.amplitude != null && (
                  <HoverPopover
                    title="Amplitude (Volatility Strength)"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">What it is: </span>
                          Amplitude describes the range between today's high and low, measuring "how much it moved."
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How this app computes it: </span>
                          Today's amplitude≈(High-Low)/Low. The larger the number, the more intense the intraday swing - bigger risk and bigger opportunity.
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How to interpret it: </span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>High amplitude is common with high-volume breakouts, panic sell-offs, news-driven moves, etc.; combine with volume and trend to judge whether it's "expansion or a crash."</li>
                            <li>Low amplitude is common during consolidation/contraction; if Bollinger Bands are narrowing at the same time, a directional move is more likely to follow.</li>
                            <li>When amplitude is high, consider a smaller position/tighter stop - avoid using the same stop-loss distance across different volatility regimes.</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70 space-y-1">
                          <div>Current: {effectiveSummary.amplitude.toFixed(2)}%</div>
                          {effectiveSummary.amplitude_avg5 != null && (
                            <div>5-day average: {effectiveSummary.amplitude_avg5.toFixed(2)}%</div>
                          )}
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        Amplitude: {effectiveSummary.amplitude.toFixed(2)}%
                      </span>
                    }
                  />
                )}
              </div>
            )}

            <details className="group">
              <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                Recommendation/scoring rules <span className="text-[10px]">(click to expand)</span>
              </summary>
              <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 space-y-2">
                <div className="font-medium text-foreground">Recommendation rules (by whether held)</div>
                <div className="space-y-1">
                  <div>Not held: score ≥ 3 → Buy; score ≤ -2 → Avoid; otherwise → Watch</div>
                  <div>Held: score ≥ 3 → Add; score ≥ 1 → Hold; score ≤ -3 → Sell; score ≤ -1 → Reduce; otherwise → Watch</div>
                </div>
                <div className="font-medium text-foreground">Scoring rules (each item adds up, 0 is neutral)</div>
                <div className="space-y-1">
                  <div>Trend (MA): Bullish Alignment +2; Bearish Alignment -2</div>
                  <div>MACD: Golden Cross +2; Death Cross -2; positive histogram +1; negative histogram -1</div>
                  <div>RSI: Oversold +1; Strong +1; Overbought -1; Weak -1</div>
                  <div>KDJ: Golden Cross +1; Death Cross -1</div>
                  <div>Bollinger: Broke above upper band +1; broke below lower band -1</div>
                  <div>Volume: High volume +1; low volume -1</div>
                  <div>Support/Resistance: close ≤ support×1.02 → +1; close ≥ resistance×0.98 → -1</div>
                </div>
              </div>
            </details>

            <Button variant="secondary" size="sm" className="w-full mt-1" onClick={handleAskAI}>
              <Sparkles className="w-3.5 h-3.5 mr-1" /> Ask AI to analyze these indicators
            </Button>

          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
