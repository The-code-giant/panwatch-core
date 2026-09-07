import { Bell, Search } from 'lucide-react'
import type { ThemeMode } from '@/hooks/use-theme'
import AccountMenu, { type AccountNavItem } from '@/components/AccountMenu'
import MarketClocks from './MarketClocks'

interface TopBarProps {
  onOpenPalette: () => void
  mode: ThemeMode
  onSetMode: (m: ThemeMode) => void
  onOpenSelfCheck: () => void
  overflowNav: AccountNavItem[]
  /** Unread alert hits; the dot is suppressed at zero. */
  alertCount?: number
  onOpenAlerts: () => void
}

/**
 * The instrument above every room: what is open, one way to reach anything,
 * and the account.
 */
export default function TopBar({
  onOpenPalette, mode, onSetMode, onOpenSelfCheck, overflowNav, alertCount = 0, onOpenAlerts,
}: TopBarProps) {
  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

  return (
    <div className="card flex items-center gap-3 px-4 py-2.5">
      <div className="hidden min-w-0 lg:block">
        <MarketClocks />
      </div>

      <button
        onClick={onOpenPalette}
        className="ml-auto flex min-w-0 flex-1 items-center gap-2.5 rounded-full bg-muted px-3.5 py-2.5 text-left text-[13px] font-medium text-muted-foreground transition-shadow hover:shadow-[inset_0_0_0_1px_hsl(var(--border))] lg:max-w-[19rem] lg:flex-none"
      >
        <Search className="h-4 w-4 shrink-0" aria-hidden />
        <span className="truncate">Search a symbol or jump to a room</span>
        <kbd className="ml-auto hidden shrink-0 rounded-md border border-border bg-card px-1.5 py-0.5 text-[10.5px] font-semibold sm:block">
          {isMac ? '⌘K' : 'Ctrl K'}
        </kbd>
      </button>

      <button
        onClick={onOpenAlerts}
        title="Price alerts"
        className="relative grid h-9 w-9 shrink-0 place-items-center rounded-full bg-muted text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
      >
        <Bell className="h-[18px] w-[18px]" aria-hidden />
        {alertCount > 0 && (
          <span className="absolute right-2 top-1.5 h-[7px] w-[7px] rounded-full bg-stock-up ring-2 ring-card" />
        )}
        <span className="sr-only">
          {alertCount > 0 ? `Price alerts, ${alertCount} fired today` : 'Price alerts'}
        </span>
      </button>

      <AccountMenu
        navItems={overflowNav}
        mode={mode}
        onSetMode={onSetMode}
        onOpenSelfCheck={onOpenSelfCheck}
      />
    </div>
  )
}
