import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, CornerDownLeft, type LucideIcon } from 'lucide-react'
import { stocksApi } from '@tickerkeep/api'
import { ROOMS } from './rooms'

interface Item {
  id: string
  label: string
  hint?: string
  icon?: LucideIcon
  group: 'Rooms' | 'Watchlist'
  run: () => void
}

interface StockLite { symbol?: string; code?: string; name?: string; market?: string }

/**
 * Cmd+K. One box that reaches every room and every symbol the user watches.
 *
 * The nine-item sidebar is gone, so this is how you get anywhere fast without
 * the nav having to grow back.
 */
export default function CommandPalette({
  open, onOpenChange,
}: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [stocks, setStocks] = useState<StockLite[]>([])
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const restoreFocusTo = useRef<HTMLElement | null>(null)

  // Global shortcut. Cmd+K on mac, Ctrl+K elsewhere, Escape to leave.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        onOpenChange(!open)
      } else if (e.key === 'Escape' && open) {
        onOpenChange(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onOpenChange])

  useEffect(() => {
    if (!open) {
      setQuery(''); setActive(0)
      // Send focus back where it came from, so closing does not dump the
      // keyboard user at the top of the document.
      restoreFocusTo.current?.focus?.()
      restoreFocusTo.current = null
      return
    }
    restoreFocusTo.current = document.activeElement as HTMLElement | null
    inputRef.current?.focus()
    let cancelled = false
    stocksApi.list()
      .then((res: unknown) => {
        if (cancelled) return
        const rows = Array.isArray(res) ? res : (res as { items?: StockLite[] })?.items
        setStocks(Array.isArray(rows) ? rows : [])
      })
      .catch(() => { /* the palette still navigates rooms without the watchlist */ })
    return () => { cancelled = true }
  }, [open])

  const items = useMemo<Item[]>(() => {
    const q = query.trim().toLowerCase()
    const roomItems: Item[] = ROOMS.flatMap(r => {
      const own: Item = {
        id: `room:${r.to}`, label: r.label, icon: r.icon, group: 'Rooms',
        run: () => navigate(r.to),
      }
      const tabs: Item[] = (r.tabs ?? [])
        .filter(t => t.to !== r.to)
        .map(t => ({
          id: `tab:${t.to}`, label: t.label, hint: r.label, icon: t.icon,
          group: 'Rooms' as const, run: () => navigate(t.to),
        }))
      return [own, ...tabs]
    })

    const stockItems: Item[] = stocks.map(s => {
      const code = String(s.symbol ?? s.code ?? '')
      const name = String(s.name ?? code)
      return {
        id: `stock:${code}`,
        label: name,
        hint: code,
        group: 'Watchlist' as const,
        run: () => navigate(`/portfolio/watchlist?symbol=${encodeURIComponent(code)}`),
      }
    })

    const all = [...roomItems, ...stockItems]
    if (!q) return all.slice(0, 24)
    return all
      .filter(i => i.label.toLowerCase().includes(q) || (i.hint ?? '').toLowerCase().includes(q))
      .slice(0, 24)
  }, [query, stocks, navigate])

  useEffect(() => { setActive(0) }, [query])

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>('[data-active="true"]')
      ?.scrollIntoView({ block: 'nearest' })
  }, [active])

  if (!open) return null

  const choose = (i: number) => {
    const item = items[i]
    if (!item) return
    item.run()
    onOpenChange(false)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, items.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); choose(active) }
  }

  let lastGroup = ''

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center bg-foreground/25 px-4 pt-[12vh]"
      onClick={() => onOpenChange(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="card w-full max-w-xl overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-border px-4">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search a symbol or jump to a room"
            aria-label="Search a symbol or jump to a room"
            role="combobox"
            aria-expanded="true"
            aria-controls="command-palette-list"
            aria-autocomplete="list"
            aria-activedescendant={items[active] ? `cmd-opt-${active}` : undefined}
            className="h-12 w-full bg-transparent text-[14px] outline-none placeholder:text-muted-foreground"
          />
          <kbd className="shrink-0 rounded-md border border-border px-1.5 py-0.5 text-[10.5px] font-semibold text-muted-foreground">
            esc
          </kbd>
        </div>

        <div
          ref={listRef}
          id="command-palette-list"
          role="listbox"
          aria-label="Results"
          className="scrollbar max-h-[52vh] overflow-y-auto py-1.5"
        >
          {items.length === 0 && (
            <p role="status" className="px-4 py-8 text-center text-[13px] text-muted-foreground">
              Nothing matches "{query}". Try a symbol code or a room name.
            </p>
          )}
          {items.map((item, i) => {
            const header = item.group !== lastGroup ? item.group : null
            lastGroup = item.group
            const Icon = item.icon
            return (
              <div key={item.id}>
                {header && <div className="col-head px-4 pb-1 pt-3">{header}</div>}
                <button
                  id={`cmd-opt-${i}`}
                  role="option"
                  aria-selected={i === active}
                  tabIndex={-1}
                  data-active={i === active}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => choose(i)}
                  className={`flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors ${
                    i === active ? 'bg-muted' : ''
                  }`}
                >
                  {Icon
                    ? <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                    : <span className="h-4 w-4 shrink-0" />}
                  <span className="truncate text-[13.5px] font-semibold">{item.label}</span>
                  {item.hint && (
                    <span className="truncate text-[12px] tabular-nums text-muted-foreground">{item.hint}</span>
                  )}
                  {i === active && (
                    <CornerDownLeft className="ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  )}
                </button>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
