import { HoverPopover } from '@tickerkeep/base-ui/components/ui/hover-popover'
import type { KlineSummaryData } from '@tickerkeep/biz-ui/components/kline-summary-dialog'
import { TechnicalBadge } from '@tickerkeep/biz-ui/components/technical-badge'

interface KlineIndicatorsProps {
  summary: KlineSummaryData
}

export function KlineIndicators({ summary: s }: KlineIndicatorsProps) {
  return (
    <div className="space-y-3">
      {/* Trend & patterns (with explanations) */}
      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.trend && (
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
              </div>
            }
            trigger={<TechnicalBadge label={s.trend} tone="neutral" help />}
          />
        )}

        {s.macd_status && (
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
              </div>
            }
            trigger={<TechnicalBadge label={`MACD ${s.macd_status}`} tone="neutral" help />}
          />
        )}

        {s.rsi_status && (
          <HoverPopover
            title="RSI (Relative Strength Index)"
            content={
              <div className="space-y-2">
                <div>
                  <span className="font-medium text-foreground">What it is: </span>
                  RSI measures the relative strength of gains vs. losses over a period (0-100). Shown here is RSI6 (last 6 trading days).
                </div>
                <div>
                  <span className="font-medium text-foreground">Threshold reference: </span>
                  <ul className="list-disc pl-4 mt-1 space-y-1">
                    <li>RSI6 &gt; 80: Overbought (higher pullback risk)</li>
                    <li>RSI6 70-80: Strong (momentum leaning bullish)</li>
                    <li>RSI6 &lt; 20: Oversold (higher bounce probability)</li>
                  </ul>
                </div>
              </div>
            }
            trigger={
              <TechnicalBadge
                label={`RSI ${s.rsi_status}${s.rsi6 != null ? ` (${s.rsi6.toFixed(0)})` : ''}`}
                tone={s.rsi_status === 'Overbought' ? 'bullish' : s.rsi_status === 'Oversold' ? 'bearish' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.kdj_status && (
          <HoverPopover
            title="KDJ (Turning Points/Overbought-Oversold)"
            content={<div>The J value is more sensitive; golden/death crosses are used to watch for short-term turning points, but are prone to noise in choppy markets - combine with trend and volume-price.</div>}
            trigger={<TechnicalBadge label={`KDJ ${s.kdj_status}`} tone="neutral" help />}
          />
        )}

        {s.volume_trend && (
          <HoverPopover
            title="Volume (Volume Confirmation)"
            content={<div>High volume is often used to confirm the validity of a breakout or bounce; low-volume spikes/drops tend to be "hollow." More reliable when combined with trend and key levels.</div>}
            trigger={
              <TechnicalBadge
                label={`${s.volume_trend}${s.volume_ratio != null ? ` (${s.volume_ratio.toFixed(1)}x)` : ''}`}
                tone={s.volume_trend === 'High Volume' ? 'warning' : s.volume_trend === 'Low Volume' ? 'info' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.boll_status && (
          <HoverPopover
            title="Bollinger Bands (Volatility/Deviation)"
            content={<div>Breaking above/below the upper/lower band is common during trending phases or extreme volatility. Combine with volume and pullback/hold confirmation for validity.</div>}
            trigger={
              <TechnicalBadge
                label={`Bollinger ${s.boll_status}`}
                tone={s.boll_status === 'Broke Above Upper Band' ? 'bullish' : s.boll_status === 'Broke Below Lower Band' ? 'bearish' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.kline_pattern && (
          <HoverPopover
            title="Candlestick Pattern (Local Structure)"
            content={<div>A single-candle pattern has limited meaning on its own - what matters more is its position (near trend/support/resistance) combined with volume.</div>}
            trigger={<TechnicalBadge label={s.kline_pattern} tone="warning" help />}
          />
        )}
      </div>

      {/* Support/Resistance (with explanations) */}
      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.support != null && (
          <HoverPopover
            title="Support (Key Support Zone)"
            content={<div>Closer to support makes a stabilization/bounce more likely; a high-volume break below can flip it into resistance. Think of it as a "zone" rather than an exact point.</div>}
            trigger={<TechnicalBadge label={`Support ${s.support.toFixed(2)}`} tone="bearish" help />}
          />
        )}
        {s.resistance != null && (
          <HoverPopover
            title="Resistance (Key Resistance Zone)"
            content={<div>The closer to resistance, the harder it is to advance; after a high-volume breakout that holds, the old resistance often flips into support.</div>}
            trigger={<TechnicalBadge label={`Resistance ${s.resistance.toFixed(2)}`} tone="bullish" help />}
          />
        )}
      </div>
    </div>
  )
}
