import type { MouseEventHandler } from 'react'
import { cn } from '@panwatch/base-ui'
import { BadgeChip, type BadgeChipSize } from '@panwatch/biz-ui/components/badge-chip'
import { normalizeSuggestionAction, type SuggestionAction } from '@panwatch/biz-ui/components/suggestion-action'

export type TechnicalBadgeTone = 'neutral' | 'bullish' | 'bearish' | 'warning' | 'info' | SuggestionAction

const toneClassMap: Record<TechnicalBadgeTone, string> = {
  neutral: 'bg-accent/50 text-muted-foreground',
  // bullish/bearish track price direction — Western convention, green up / red down.
  bullish: 'bg-[hsl(var(--stock-up)/0.10)] text-[hsl(var(--stock-up))]',
  bearish: 'bg-[hsl(var(--stock-down)/0.10)] text-[hsl(var(--stock-down))]',
  warning: 'bg-destructive/10 text-destructive',
  info: 'bg-muted text-muted-foreground',
  buy: 'bg-[hsl(var(--stock-up)/0.16)] text-[hsl(var(--stock-up))]',
  add: 'bg-[hsl(var(--stock-up)/0.10)] text-[hsl(var(--stock-up))]',
  reduce: 'bg-[hsl(var(--stock-down)/0.12)] text-[hsl(var(--stock-down))]',
  sell: 'bg-[hsl(var(--stock-down)/0.16)] text-[hsl(var(--stock-down))]',
  hold: 'bg-muted text-muted-foreground',
  watch: 'bg-muted text-muted-foreground',
  avoid: 'bg-destructive/10 text-destructive',
  alert: 'bg-[hsl(var(--foreground)/0.08)] text-foreground',
}

interface TechnicalBadgeProps {
  label: string
  tone?: TechnicalBadgeTone
  size?: BadgeChipSize
  className?: string
  title?: string
  help?: boolean
  onClick?: MouseEventHandler<HTMLButtonElement>
}

export function technicalToneFromSuggestionAction(action?: string, actionLabel?: string): TechnicalBadgeTone {
  return normalizeSuggestionAction(action, actionLabel) || 'watch'
}

export function TechnicalBadge({
  label,
  tone = 'neutral',
  size = 'sm',
  className,
  title,
  help = false,
  onClick,
}: TechnicalBadgeProps) {
  return (
    <BadgeChip
      label={label}
      size={size}
      onClick={onClick}
      title={title}
      className={cn(
        toneClassMap[tone],
        !onClick && help && 'cursor-help hover:opacity-80',
        className,
      )}
    />
  )
}
