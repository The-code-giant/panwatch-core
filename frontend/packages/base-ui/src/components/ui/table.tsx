import * as React from 'react'
import { cn } from '../../cn'

/**
 * Table — formalizes the hand-duplicated `<table>` markup used by the
 * Positions / Watchlist / Paper Trading / Opportunities lists.
 *
 * Conventions baked in (see DESIGN.md):
 *  - one elevation on the outer container, hairline dividers between rows;
 *    rows never get their own border, background, or radius
 *  - header cells are muted, small, and non-bold — the data is the content
 *  - numeric columns are tabular-nums and right-aligned via `<TableCell numeric>`
 *  - `<TableRow interactive>` tints on hover/press, it does not lift
 *
 * Always wrap in `<TableWrap>` so wide tables scroll inside themselves
 * instead of scrolling the page sideways.
 */

const TableWrap = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & { bordered?: boolean }
>(({ className, bordered = true, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      'w-full overflow-x-auto scrollbar',
      bordered && 'card overflow-hidden',
      className,
    )}
    {...props}
  />
))
TableWrap.displayName = 'TableWrap'

const Table = React.forwardRef<
  HTMLTableElement,
  React.TableHTMLAttributes<HTMLTableElement>
>(({ className, ...props }, ref) => (
  <table
    ref={ref}
    className={cn('w-full text-[13px] border-collapse', className)}
    {...props}
  />
))
Table.displayName = 'Table'

const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <thead
    ref={ref}
    className={cn('border-b border-border bg-accent/25', className)}
    {...props}
  />
))
TableHeader.displayName = 'TableHeader'

const TableBody = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tbody ref={ref} className={cn('divide-y divide-border', className)} {...props} />
))
TableBody.displayName = 'TableBody'

export interface TableRowProps
  extends React.HTMLAttributes<HTMLTableRowElement> {
  /** The row navigates or opens something on click. */
  interactive?: boolean
  /** The row reveals actions on hover but is not itself clickable. */
  hoverable?: boolean
  /** Current selection. Uses the gold accent, per the primary-accent rule. */
  selected?: boolean
}

const TableRow = React.forwardRef<HTMLTableRowElement, TableRowProps>(
  ({ className, interactive, hoverable, selected, ...props }, ref) => (
    <tr
      ref={ref}
      data-state={selected ? 'selected' : undefined}
      className={cn(
        'group',
        interactive && 'row-interactive',
        !interactive && hoverable && 'row-hover',
        selected && 'bg-primary/8',
        className,
      )}
      {...props}
    />
  ),
)
TableRow.displayName = 'TableRow'

export interface TableCellProps
  extends React.TdHTMLAttributes<HTMLTableCellElement> {
  /** Right-aligned tabular figures — every price, quantity, and percentage. */
  numeric?: boolean
}

const TableHead = React.forwardRef<
  HTMLTableCellElement,
  React.ThHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }
>(({ className, numeric, ...props }, ref) => (
  <th
    ref={ref}
    scope="col"
    className={cn(
      'px-3 py-2.5 col-head whitespace-nowrap',
      numeric ? 'text-right' : 'text-left',
      className,
    )}
    {...props}
  />
))
TableHead.displayName = 'TableHead'

const TableCell = React.forwardRef<HTMLTableCellElement, TableCellProps>(
  ({ className, numeric, ...props }, ref) => (
    <td
      ref={ref}
      className={cn(
        'px-3 py-2.5 align-middle',
        numeric && 'text-right tabular-nums',
        className,
      )}
      {...props}
    />
  ),
)
TableCell.displayName = 'TableCell'

const TableCaption = React.forwardRef<
  HTMLTableCaptionElement,
  React.HTMLAttributes<HTMLTableCaptionElement>
>(({ className, ...props }, ref) => (
  <caption
    ref={ref}
    className={cn('helper-text px-3 py-2 text-left', className)}
    {...props}
  />
))
TableCaption.displayName = 'TableCaption'

export {
  TableWrap,
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  TableCaption,
}
