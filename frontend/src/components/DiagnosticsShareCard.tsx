import { type PortfolioDiagnostics } from '@tickerkeep/api'
import ShareCardDialog from './ShareCardDialog'
import { marketLabel } from '@/lib/markets'

interface DiagnosticsShareCardProps {
  open: boolean
  onClose: () => void
  diag: PortfolioDiagnostics
  /** Optional: excess return vs. the benchmark over the last N days (%), shown in the sub-metrics if present. */
  excessReturn?: number | null
  benchmarkLabel?: string
}

// Western convention: green = up / favourable, red = down / adverse, amber = neutral.
const UP = '#059669'
const DOWN = '#e11d48'
const NEUTRAL = '#d97706'
const SLATE = '#0f172a'

function signColor(v?: number | null): string {
  if (v == null || !isFinite(v)) return NEUTRAL
  if (v > 0) return UP
  if (v < 0) return DOWN
  return NEUTRAL
}

function pct(v?: number | null, digits = 1): string {
  if (v == null || !isFinite(v)) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}


/**
 * Qualitative concentration (HHI): 0~1, higher = more concentrated. 0.4+ high, 0.25~0.4 moderate, <0.25 diversified.
 */
function hhiBand(hhi: number): { label: string; color: string } {
  if (hhi >= 0.4) return { label: 'Concentrated', color: NEUTRAL }
  if (hhi >= 0.25) return { label: 'Moderate', color: SLATE }
  return { label: 'Diversified', color: UP }
}

function StatBox({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div
      style={{
        flex: 1,
        background: '#ffffff',
        border: '1px solid #e2e8f0',
        borderRadius: 12,
        padding: '12px 14px',
      }}
    >
      <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: color ?? SLATE, fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

/**
 * Portfolio checkup card. Privacy-scrubbed: shows only ratios / counts / risk alerts, never any monetary amount ($).
 * total_market_value is only used to convert by_market's market value into a "share %"; the raw value is never shown.
 */
export default function DiagnosticsShareCard({
  open,
  onClose,
  diag,
  excessReturn,
  benchmarkLabel,
}: DiagnosticsShareCardProps) {
  const band = hhiBand(diag.hhi)
  const totalMv = diag.total_market_value || 0
  const markets = Object.entries(diag.by_market || {})
    .map(([m, v]) => ({ m, w: totalMv > 0 ? (v / totalMv) * 100 : 0 }))
    .sort((a, b) => b.w - a.w)
  const alerts = (diag.alerts || []).slice(0, 3)
  const hasExcess = excessReturn != null && isFinite(excessReturn)

  return (
    <ShareCardDialog open={open} onClose={onClose} filename="portfolio-checkup-card">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.2, color: SLATE }}>Portfolio Checkup</div>
        <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>Holdings Structure · Risk</div>
      </div>

      {/* Hero: concentration (HHI) */}
      <div
        style={{
          marginTop: 18,
          borderRadius: 18,
          padding: '22px 24px',
          background: `linear-gradient(135deg, ${band.color} 0%, ${band.color}cc 100%)`,
          color: '#ffffff',
          boxShadow: `0 10px 30px -8px ${band.color}66`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: 1, opacity: 0.92, flexShrink: 0 }}>
            Concentration (HHI)
          </div>
          <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
            <span style={{ fontSize: 42, fontWeight: 900, lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>
              {diag.hhi.toFixed(2)}
            </span>
            <span style={{ fontSize: 18, fontWeight: 700, marginLeft: 10 }}>{band.label}</span>
          </div>
        </div>
      </div>

      {/* Key metrics */}
      <div style={{ marginTop: 16, display: 'flex', gap: 12 }}>
        <StatBox label="Positions" value={`${diag.position_count}`} sub="stocks" />
        <StatBox
          label="Largest Position Weight"
          value={`${(diag.max_weight * 100).toFixed(0)}%`}
          color={diag.max_weight >= 0.4 ? NEUTRAL : SLATE}
        />
        {hasExcess && (
          <StatBox
            label={`Recent Excess vs. ${benchmarkLabel || 'Market'}`}
            value={pct(excessReturn)}
            color={signColor(excessReturn)}
          />
        )}
      </div>

      {/* Market distribution */}
      {markets.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#334155', marginBottom: 8 }}>Market Distribution</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {markets.map(({ m, w }) => (
              <div key={m}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                  <span style={{ color: '#475569' }}>{marketLabel(m)}</span>
                  <span style={{ color: SLATE, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
                    {w.toFixed(0)}%
                  </span>
                </div>
                <div style={{ height: 8, borderRadius: 999, background: '#e2e8f0', overflow: 'hidden' }}>
                  <div
                    style={{
                      height: '100%',
                      width: `${Math.min(100, w)}%`,
                      borderRadius: 999,
                      background: '#6366f1',
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Risk alerts */}
      <div style={{ marginTop: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#334155', marginBottom: 8 }}>Risk Alerts</div>
        {alerts.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {alerts.map((a, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  background: '#fffbeb',
                  border: '1px solid #fde68a',
                  borderRadius: 10,
                  padding: '10px 12px',
                  fontSize: 13,
                  lineHeight: 1.5,
                  color: '#92400e',
                }}
              >
                <span style={{ flexShrink: 0, fontWeight: 900 }}>!</span>
                <span>{a}</span>
              </div>
            ))}
          </div>
        ) : (
          <div
            style={{
              background: '#ecfdf5',
              border: '1px solid #a7f3d0',
              borderRadius: 10,
              padding: '10px 12px',
              fontSize: 13,
              color: '#065f46',
            }}
          >
            ✓ No significant concentration / distribution risk detected
          </div>
        )}
      </div>
    </ShareCardDialog>
  )
}
