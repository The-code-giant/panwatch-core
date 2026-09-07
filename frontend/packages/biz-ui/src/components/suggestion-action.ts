export type SuggestionAction =
  | 'buy'
  | 'add'
  | 'reduce'
  | 'sell'
  | 'hold'
  | 'watch'
  | 'alert'
  | 'avoid'

export const suggestionActionColors: Record<SuggestionAction, string> = {
  // buy/add track the "price goes up" direction — Western convention, green.
  buy: 'bg-[hsl(var(--stock-up)/0.16)] text-[hsl(var(--stock-up))]',
  add: 'bg-[hsl(var(--stock-up)/0.10)] text-[hsl(var(--stock-up))]',
  // reduce/sell track the "down" direction — red.
  reduce: 'bg-[hsl(var(--stock-down)/0.12)] text-[hsl(var(--stock-down))]',
  sell: 'bg-[hsl(var(--stock-down)/0.16)] text-[hsl(var(--stock-down))]',
  // hold/watch are neutral, not a direction.
  hold: 'bg-muted text-muted-foreground',
  watch: 'bg-muted text-muted-foreground',
  // alert is informational, not a direction or the accent — a quiet neutral chip.
  alert: 'bg-[hsl(var(--foreground)/0.08)] text-foreground',
  // avoid is a warning, not a price move — the destructive tone.
  avoid: 'bg-destructive/10 text-destructive',
}

export const suggestionActionLabels: Record<SuggestionAction, string> = {
  buy: 'Buy',
  add: 'Add',
  reduce: 'Reduce',
  sell: 'Sell',
  hold: 'Hold',
  watch: 'Watch',
  avoid: 'Avoid',
  alert: 'Alert',
}

export function normalizeSuggestionAction(action?: string, label?: string): SuggestionAction | null {
  const raw = (action || label || '').toLowerCase()
  if (!raw) return null
  if (raw === 'buy') return 'buy'
  if (raw === 'add' || raw === 'increase') return 'add'
  if (raw === 'reduce' || raw === 'decrease') return 'reduce'
  if (raw === 'sell') return 'sell'
  if (raw === 'hold') return 'hold'
  if (raw === 'watch' || raw === 'neutral') return 'watch'
  if (raw === 'avoid') return 'avoid'
  if (raw === 'alert') return 'alert'
  if (/买入|买|建仓|buy|open position/.test(raw)) return 'buy'
  if (/加仓|增持|补仓|prepare to add|add/.test(raw)) return 'add'
  if (/减仓|减持|consider reducing|prepare to reduce|reduce/.test(raw)) return 'reduce'
  if (/清仓|卖出|止损|卖|close out|stop loss|consider stop loss|sell/.test(raw)) return 'sell'
  if (/持有|持仓|continue holding|hold/.test(raw)) return 'hold'
  if (/观望|中性|等待|watch|neutral|watch tomorrow/.test(raw)) return 'watch'
  if (/回避|规避|避免|avoid/.test(raw)) return 'avoid'
  return null
}

export function resolveSuggestionAction(action?: string, label?: string): SuggestionAction {
  return normalizeSuggestionAction(action, label) || 'watch'
}

export function resolveSuggestionLabel(action?: string, label?: string, fallback = 'Watch'): string {
  const normalized = normalizeSuggestionAction(action, label)
  if (normalized) return suggestionActionLabels[normalized] || fallback
  return String(label || '').trim() || fallback
}

export function resolveSuggestionColorClass(action?: string, label?: string): string {
  const normalized = resolveSuggestionAction(action, label)
  return suggestionActionColors[normalized] || suggestionActionColors.watch
}

// ACTIVE-presentation only: renders the canonical English label for the current
// UI, never the raw stored label. Historical/stored records (e.g. Chinese
// action_label values already persisted to the DB) keep their original text —
// this function must not be used to rewrite anything at rest, only what is
// shown live. When a stored label/action doesn't map to a known
// SuggestionAction, it falls back to 'Review' rather than echoing the raw
// (possibly non-English) stored text.
export function resolveActiveActionLabel(action?: string, label?: string): string {
  const normalized = normalizeSuggestionAction(action, label)
  return normalized ? suggestionActionLabels[normalized] : 'Review'
}
