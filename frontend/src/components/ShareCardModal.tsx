import { type DeepAnalysisResult } from '@panwatch/api'
import { normalizeSuggestionAction } from '@panwatch/biz-ui/components/suggestion-action'
import ShareCardDialog from './ShareCardDialog'

interface ShareCardModalProps {
  open: boolean
  onClose: () => void
  result: DeepAnalysisResult
  symbol: string
  date: string
}

/**
 * 5-tier rating → display label + Western color convention (green=up, red=down).
 * Reuses the technical-badge / suggestion-action normalization: buy/add = green (bullish), sell/reduce = red (bearish), hold = amber (neutral).
 * Uses self-contained explicit hex colors here so the exported PNG renders correctly under any theme (light/dark).
 */
const RATING_VISUAL: Record<
  string,
  { label: string; color: string; soft: string; gradFrom: string; gradTo: string }
> = {
  // Bullish (green)
  buy: { label: 'Buy', color: '#059669', soft: '#ecfdf5', gradFrom: '#34d399', gradTo: '#059669' },
  add: { label: 'Add', color: '#059669', soft: '#ecfdf5', gradFrom: '#6ee7b7', gradTo: '#059669' },
  // Neutral (amber)
  hold: { label: 'Hold', color: '#d97706', soft: '#fffbeb', gradFrom: '#fbbf24', gradTo: '#d97706' },
  // Bearish (red)
  reduce: { label: 'Reduce', color: '#e11d48', soft: '#fff1f2', gradFrom: '#fda4af', gradTo: '#e11d48' },
  sell: { label: 'Sell', color: '#e11d48', soft: '#fff1f2', gradFrom: '#fb7185', gradTo: '#e11d48' },
}
const RATING_FALLBACK = {
  label: 'Watch',
  color: '#475569',
  soft: '#f8fafc',
  gradFrom: '#94a3b8',
  gradTo: '#475569',
}

/** Maps possible raw backend 5-tier values (overweight/underweight) to words the normalizer understands. */
function mapRatingRaw(raw?: string): string | undefined {
  if (!raw) return undefined
  const r = raw.toLowerCase().trim()
  if (r === 'overweight') return 'add'
  if (r === 'underweight') return 'reduce'
  return r
}

/**
 * Parses stock name + code from the title: strips a leading bracket tag like 【Deep Dive】, and a trailing ": rating".
 * Example: "【Deep Dive】GAC Group(601238): Hold" → "GAC Group(601238)"
 */
function parseStockName(title: string, symbol: string): string {
  let s = (title || '').trim()
  s = s.replace(/^【[^】]*】\s*/, '') // Strip the leading【...】tag
  s = s.replace(/[:：]\s*[^:：]*$/, '') // Strip the trailing ":xxx" (rating)
  s = s.trim()
  return s || symbol
}

/**
 * Cleans the conclusion into a single paragraph: strips markdown bold **, then strips a leading "Action: x Reasoning:" prefix.
 * Collapses extra whitespace to a single space for line-clamp display.
 */
function cleanConclusion(text: string): string {
  let s = (text || '').replace(/\*\*/g, '')
  s = s.replace(/^Action\s*[:：]\s*\S+\s*Reasoning\s*[:：]\s*/i, '')
  s = s.replace(/\s+/g, ' ').trim()
  return s
}

export default function ShareCardModal({ open, onClose, result, symbol, date }: ShareCardModalProps) {
  const sug = result.raw_data?.suggestion
  // Rating source: prefer the backend's raw 5-tier value rating_raw (untyped, may exist at runtime), else fall back to action, plus action_label as a final fallback
  const ratingRaw = mapRatingRaw((sug as { rating_raw?: string } | undefined)?.rating_raw)
  const normalized = normalizeSuggestionAction(ratingRaw || sug?.action, sug?.action_label)
  const visual = (normalized && RATING_VISUAL[normalized]) || RATING_FALLBACK

  const stockName = parseStockName(result.title || '', symbol)
  const confidence = sug?.confidence
  const costUsd = result.raw_data?.cost_usd
  const conclusion = cleanConclusion(sug?.signal || sug?.reason || '')
  const confPct = Math.max(0, Math.min(100, (confidence ?? 0) * 10))

  return (
    <ShareCardDialog open={open} onClose={onClose} filename={`${stockName}-${date}-analysis-card`}>
      {/* Header: stock name + code / date */}
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <div style={{ fontSize: 24, fontWeight: 800, lineHeight: 1.2, color: '#0f172a' }}>
          {stockName}
        </div>
        <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>{date}</div>
      </div>

      {/* Hero: large rating + confidence bar + cost */}
      <div
        style={{
          marginTop: 20,
          borderRadius: 18,
          padding: '22px 24px',
          background: `linear-gradient(135deg, ${visual.gradFrom} 0%, ${visual.gradTo} 100%)`,
          color: '#ffffff',
          boxShadow: `0 10px 30px -8px ${visual.color}66`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              letterSpacing: 1,
              opacity: 0.92,
              flexShrink: 0,
            }}
          >
            AI Research Verdict
          </div>
          <div
            style={{
              fontSize: 42,
              fontWeight: 900,
              lineHeight: 1,
              letterSpacing: 2,
              marginLeft: 'auto',
            }}
          >
            {visual.label}
          </div>
        </div>

        {/* Confidence bar */}
        <div style={{ marginTop: 18 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 12.5,
              opacity: 0.92,
              marginBottom: 6,
            }}
          >
            <span>Confidence</span>
            <span style={{ fontWeight: 700 }}>
              {confidence != null ? confidence.toFixed(1) : '-'} / 10
            </span>
          </div>
          <div
            style={{
              height: 8,
              borderRadius: 999,
              background: 'rgba(255,255,255,0.3)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${confPct}%`,
                borderRadius: 999,
                background: '#ffffff',
              }}
            />
          </div>
          <div style={{ marginTop: 10, fontSize: 12, opacity: 0.85 }}>
            Analysis cost ${costUsd != null ? costUsd.toFixed(4) : '-'}
          </div>
        </div>
      </div>

      {/* Conclusion paragraph: up to ~5 lines */}
      {conclusion && (
        <div
          style={{
            marginTop: 22,
            fontSize: 15.5,
            lineHeight: 1.7,
            color: '#334155',
            display: '-webkit-box',
            WebkitLineClamp: 5,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {conclusion}
        </div>
      )}

      {/* TA card's dedicated subtitle (9-Agent), placed above the shell's divider/footer */}
      <div style={{ marginTop: 22, fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
        AI Research Team (9-Agent) Deep Analysis
      </div>
    </ShareCardDialog>
  )
}
