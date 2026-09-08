import * as React from 'react'
import { cn } from '../../cn'

/**
 * Card — formalizes the `.card` class that was already everywhere.
 *
 * One elevation, declared once (see DESIGN.md § Elevation & Panels):
 * hairline border + soft offset shadow. Never a glow, never a nested card.
 *
 * Variants:
 *  - `default`  the standard bordered panel
 *  - `subtle`   a tinted borderless-feeling group (the old `.card-subtle`)
 *  - `strip`    the unified stat strip container — a single instrument
 *               divided internally by hairlines, never a grid of tiles
 */
type CardVariant = 'default' | 'subtle' | 'strip'

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant
  /** Adds hover feedback. Only for cards that are themselves clickable. */
  interactive?: boolean
}

const variantClass: Record<CardVariant, string> = {
  default: 'card',
  subtle: 'rounded-[--radius-card] bg-muted/60',
  strip: 'card grid divide-x divide-y divide-border',
}

const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant = 'default', interactive = false, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        interactive && variant === 'default' ? 'card-interactive' : variantClass[variant],
        className,
      )}
      {...props}
    />
  ),
)
Card.displayName = 'Card'

/** Card header row: title on the left, actions on the right. */
const CardHeader = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn('flex items-center justify-between gap-3 px-4 py-3 border-b border-border/60', className)}
    {...props}
  />
))
CardHeader.displayName = 'CardHeader'

const CardTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h2 ref={ref} className={cn('section-title flex items-center gap-2', className)} {...props} />
))
CardTitle.displayName = 'CardTitle'

/** Optional one-line explanation under the title. */
const CardDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p ref={ref} className={cn('helper-text', className)} {...props} />
))
CardDescription.displayName = 'CardDescription'

const CardContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('px-4 pt-3 pb-4', className)} {...props} />
))
CardContent.displayName = 'CardContent'

/**
 * One cell of a stat strip: muted label over a tabular value.
 * No per-metric icon — the icons in the old hero-metric grid were
 * decorative, not informational.
 */
export interface StatCellProps extends React.HTMLAttributes<HTMLDivElement> {
  label: React.ReactNode
  value: React.ReactNode
  /** Small trailing element on the value line (a change chip, a sparkline). */
  aside?: React.ReactNode
  /** Tone the value. `up`/`down` follow the Western convention (green up, red down). */
  tone?: 'default' | 'up' | 'down' | 'muted'
}

const toneClass = {
  default: 'text-foreground',
  up: 'text-stock-up',
  down: 'text-stock-down',
  muted: 'text-muted-foreground',
} as const

const StatCell = React.forwardRef<HTMLDivElement, StatCellProps>(
  ({ className, label, value, aside, tone = 'default', ...props }, ref) => (
    <div ref={ref} className={cn('p-4 min-w-0', className)} {...props}>
      <div className="stat-label mb-1 truncate">{label}</div>
      {/* Neither number is ever clipped. The headline figure keeps its full
          width (`100.0%` ellipsised to `100....` loses the unit entirely), and
          the aside wraps to a second line rather than truncating -- `truncate`
          on an inline span hides the overflow WITHOUT an ellipsis, which
          rendered `(+8.13%)` as a bare `13%)`. */}
      <div className="flex flex-wrap items-baseline gap-x-2 min-w-0">
        <span className={cn('stat-value whitespace-nowrap', toneClass[tone])}>{value}</span>
        {aside ? <span className="whitespace-nowrap">{aside}</span> : null}
      </div>
    </div>
  ),
)
StatCell.displayName = 'StatCell'

export { Card, CardHeader, CardTitle, CardDescription, CardContent, StatCell }
