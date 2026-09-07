/**
 * Market identity colours.
 *
 * Identity only: which market a symbol trades in. Never reused to mean up,
 * down, good or bad, and never mixed with the lime accent.
 *
 * Two sets, because they are checked against different things:
 *  - `marketDot` is the validated chart/identity hue, used for a dot, a legend
 *    swatch or a chart mark. Checked for colour-vision separation and >=3:1
 *    against the page ground.
 *  - `marketBadge` is the filled-badge variant, used where a LABEL sits on top
 *    of the colour. Checked at >=4.5:1 against `text-mkt-ink`, which the chart
 *    hues do not all pass.
 */
const MARKET_DOT: Record<string, string> = {
  US: 'bg-mkt-us',
  CA: 'bg-mkt-ca',
  CRYPTO: 'bg-mkt-crypto',
  GOLD: 'bg-mkt-gold',
}

const MARKET_BADGE: Record<string, string> = {
  US: 'bg-mkt-us-solid',
  CA: 'bg-mkt-ca-solid',
  CRYPTO: 'bg-mkt-crypto-solid',
  GOLD: 'bg-mkt-gold-solid',
}

/** A dot, swatch or chart mark. No text sits on this. */
export function marketClass(market?: string): string {
  return MARKET_DOT[String(market || '').toUpperCase()] ?? 'bg-muted-foreground'
}

/** A filled badge or avatar with a label on it. Pair with `text-mkt-ink`. */
export function marketBadgeClass(market?: string): string {
  return MARKET_BADGE[String(market || '').toUpperCase()] ?? 'bg-muted-foreground'
}

/** Short mark shown in a symbol's square tag: initials, never a truncated code. */
export function symbolMark(name?: string, symbol?: string): string {
  const n = String(name || '').trim()
  if (n) {
    const ascii = n.replace(/[^A-Za-z0-9 ]/g, '').trim()
    if (ascii) {
      const words = ascii.split(/\s+/)
      if (words.length > 1) return (words[0][0] + words[1][0]).toUpperCase()
      return ascii.slice(0, 3).toUpperCase()
    }
    // CJK names read best as their first two characters.
    return n.slice(0, 2)
  }
  return String(symbol || '?').slice(0, 3).toUpperCase()
}

/** Direction classes for a change value, Western convention: green rises, red falls. */
export function dirText(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return 'text-muted-foreground'
  if (v > 0) return 'text-stock-up'
  if (v < 0) return 'text-stock-down'
  return 'text-muted-foreground'
}

export function dirChip(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return 'chip-neutral'
  if (v > 0) return 'chip-up'
  if (v < 0) return 'chip-down'
  return 'chip-neutral'
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '--'
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

export function fmtCompact(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '--'
  return Math.round(v).toLocaleString()
}
