import * as React from 'react'
import { HelpCircle } from 'lucide-react'
import * as PopoverPrimitive from '@radix-ui/react-popover'
import { cn } from '../../cn'

/**
 * Tooltip — short explanatory text on hover or keyboard focus.
 *
 * Built on the Radix Popover already in the tree rather than adding a
 * second Radix package. Unlike `HoverPopover` (a wide rich panel for
 * analysis explanations) this is a narrow one- or two-line label: the
 * "what does this number mean" affordance the app was missing.
 *
 * Accessible by design: the trigger is focusable, the label is announced
 * via `aria-describedby`, and Escape closes it.
 */

export interface TooltipProps {
  /** The element the tooltip describes. Must be focusable or use `<InfoTip>`. */
  children: React.ReactNode
  /** The explanation. Keep it to a sentence or two. */
  label: React.ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
  align?: 'start' | 'center' | 'end'
  /** ms before opening on hover. Prevents flicker while sweeping the cursor. */
  delay?: number
  className?: string
  contentClassName?: string
}

export function Tooltip({
  children,
  label,
  side = 'top',
  align = 'center',
  delay = 250,
  className,
  contentClassName,
}: TooltipProps) {
  const [open, setOpen] = React.useState(false)
  const timer = React.useRef<number | null>(null)
  const id = React.useId()

  const clear = React.useCallback(() => {
    if (timer.current != null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  const show = React.useCallback(
    (immediate = false) => {
      clear()
      if (immediate) {
        setOpen(true)
        return
      }
      timer.current = window.setTimeout(() => setOpen(true), delay)
    },
    [clear, delay],
  )

  const hide = React.useCallback(() => {
    clear()
    setOpen(false)
  }, [clear])

  React.useEffect(() => clear, [clear])

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Trigger asChild>
        <span
          tabIndex={0}
          role="button"
          aria-describedby={open ? id : undefined}
          className={cn(
            'inline-flex items-center rounded-sm outline-none',
            'focus-visible:ring-2 focus-visible:ring-primary/40',
            className,
          )}
          onMouseEnter={() => show()}
          onMouseLeave={hide}
          onFocus={() => show(true)}
          onBlur={hide}
          onKeyDown={(e) => {
            if (e.key === 'Escape') hide()
          }}
        >
          {children}
        </span>
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          id={id}
          role="tooltip"
          side={side}
          align={align}
          sideOffset={6}
          onOpenAutoFocus={(e) => e.preventDefault()}
          className={cn(
            'z-50 max-w-[18rem] rounded-md border border-border bg-popover px-2.5 py-1.5',
            'text-[11.5px] leading-relaxed text-popover-foreground',
            'shadow-[0_4px_16px_-8px_hsl(0_0%_0%/0.35)] outline-none',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
            'data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95',
            contentClassName,
          )}
        >
          {label}
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  )
}

/**
 * The standard "?" affordance next to a label whose meaning isn't obvious
 * from its name — a derived metric, a threshold, a scoring input.
 */
export function InfoTip({
  label,
  side,
  align,
  className,
}: Pick<TooltipProps, 'label' | 'side' | 'align' | 'className'>) {
  return (
    <Tooltip label={label} side={side} align={align} className={className}>
      <HelpCircle
        aria-hidden
        className="w-3.5 h-3.5 text-muted-foreground/70 hover:text-muted-foreground transition-colors duration-150"
      />
      <span className="sr-only">What is this?</span>
    </Tooltip>
  )
}
