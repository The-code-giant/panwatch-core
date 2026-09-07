import * as React from 'react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '../../cn'

/**
 * EmptyState — the one treatment for every zero-data surface.
 *
 * Before Pass 2 each empty surface invented its own: a bare centered
 * sentence here, nothing at all there, a spinner that never resolved
 * somewhere else. The rules:
 *
 *  1. Say what would be here, not that there is nothing here.
 *     ("No positions yet" is the title; the body says what filling it does.)
 *  2. Give the user the next step when there is one, as a real control.
 *  3. Distinguish *empty* (nothing added yet) from *filtered to nothing*
 *     (data exists, the filter hides it) — the recovery differs.
 *  4. One small outline icon, muted, never an illustration.
 *
 * Sizes: `sm` inside a panel or table body, `md` for a whole page section.
 */

export interface EmptyStateProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, 'title'> {
  icon?: LucideIcon
  title: React.ReactNode
  /** What this surface holds once it has data, or how to recover. */
  description?: React.ReactNode
  /** The next step. A Button, a link — a real control, not a hint. */
  action?: React.ReactNode
  size?: 'sm' | 'md'
}

const EmptyState = React.forwardRef<HTMLDivElement, EmptyStateProps>(
  (
    { className, icon: Icon, title, description, action, size = 'md', ...props },
    ref,
  ) => (
    <div
      ref={ref}
      className={cn(
        'flex flex-col items-center justify-center text-center',
        size === 'sm' ? 'px-4 py-8 gap-1.5' : 'px-6 py-14 gap-2',
        className,
      )}
      {...props}
    >
      {Icon && (
        <Icon
          aria-hidden
          strokeWidth={1.5}
          className={cn(
            'text-muted-foreground/50',
            size === 'sm' ? 'w-5 h-5 mb-0.5' : 'w-7 h-7 mb-1',
          )}
        />
      )}
      <p
        className={cn(
          'font-medium text-foreground',
          size === 'sm' ? 'text-[13px]' : 'text-[14px]',
        )}
      >
        {title}
      </p>
      {description && (
        <p className="helper-text max-w-[38ch] text-balance">{description}</p>
      )}
      {action && <div className={size === 'sm' ? 'mt-2' : 'mt-3'}>{action}</div>}
    </div>
  ),
)
EmptyState.displayName = 'EmptyState'

export { EmptyState }
