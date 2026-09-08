import { useState, useEffect, useRef } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { Moon, Sun, Monitor, Check, LogOut, User, Stethoscope, type LucideIcon } from 'lucide-react'
import { isAuthenticated, logout } from '@tickerkeep/api'
import type { ThemeMode } from '@/hooks/use-theme'
import { useAvatar } from '@/hooks/use-avatar'

export interface AccountNavItem {
  to: string
  icon: LucideIcon
  label: string
}

const THEME_OPTIONS: { value: ThemeMode; icon: LucideIcon; label: string }[] = [
  { value: 'light', icon: Sun, label: 'Light' },
  { value: 'dark', icon: Moon, label: 'Dark' },
  { value: 'system', icon: Monitor, label: 'System' },
]

interface AccountMenuProps {
  /** Nav items previously collapsed under "More" (Agents / History / Data Sources / Settings). */
  navItems: AccountNavItem[]
  mode: ThemeMode
  onSetMode: (m: ThemeMode) => void
  /** Opens the "System Self-Check" dialog (state is hosted by the parent App to avoid duplicate instances on desktop/mobile). */
  onOpenSelfCheck: () => void
  /** Avatar size: md on desktop, sm on mobile. */
  size?: 'sm' | 'md'
}

/**
 * Top-right avatar area + dropdown menu (inspired by beecount-cloud):
 * Moves the old "More" nav, theme (light/dark/system), and logout into the avatar dropdown
 * (view logs / GitHub remain outside).
 */
export default function AccountMenu({
  navItems,
  mode,
  onSetMode,
  onOpenSelfCheck,
  size = 'md',
}: AccountMenuProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  const location = useLocation()
  const avatar = useAvatar()
  // Enable hover-to-expand only on devices that support hover (desktop); touch devices keep click behavior
  const [canHover] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(hover: hover)').matches,
  )

  // Close on outside click
  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      if (open && ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  // Close on route change
  useEffect(() => {
    setOpen(false)
  }, [location.pathname])

  const avatarSize = size === 'sm' ? 'w-6 h-6' : 'w-7 h-7'
  const iconSize = size === 'sm' ? 'w-3.5 h-3.5' : 'w-4 h-4'

  return (
    <div
      className="relative"
      ref={ref}
      onMouseEnter={canHover ? () => setOpen(true) : undefined}
      onMouseLeave={canHover ? () => setOpen(false) : undefined}
    >
      <button
        onClick={() => setOpen(v => !v)}
        className={`${avatarSize} rounded-full overflow-hidden bg-primary text-primary-foreground flex items-center justify-center ring-1 transition-colors ${
          open ? 'ring-primary/50' : 'ring-border/40 hover:ring-primary/40'
        }`}
        title="Account & Settings"
        aria-label="Account & Settings"
      >
        {avatar ? (
          <img src={avatar} alt="Avatar" className="w-full h-full object-cover" />
        ) : (
          <User className={`${iconSize} text-white`} />
        )}
      </button>

      {open && (
        // top-full + pt-2: transparent padding bridges the avatar and menu so hover doesn't break on mouseover
        <div className="absolute right-0 top-full pt-2 z-50">
          <div className="card w-48 border border-border p-1.5">
          {/* Old "More" nav */}
          {navItems.map(({ to, icon: Icon, label }) => {
            const isActive = location.pathname.startsWith(to)
            return (
              <NavLink
                key={to}
                to={to}
                onClick={() => setOpen(false)}
                className={`flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] transition-colors ${
                  isActive
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
              </NavLink>
            )
          })}

          <div className="my-1 h-px bg-border/50" />

          {/* Theme: light / dark / system */}
          <div className="px-2.5 pt-0.5 pb-1 text-[11px] text-muted-foreground">Theme</div>
          {THEME_OPTIONS.map(({ value, icon: Icon, label }) => {
            const active = mode === value
            return (
              <button
                key={value}
                onClick={() => onSetMode(value)}
                className={`flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] transition-colors ${
                  active
                    ? 'text-foreground bg-accent/40'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
                {active && <Check className="w-3.5 h-3.5 ml-auto text-foreground" />}
              </button>
            )
          })}

          <div className="my-1 h-px bg-border/50" />
          {/* System Self-Check: opens a dialog (checks data source/AI/notification connectivity item by item) */}
          <button
            onClick={() => {
              setOpen(false)
              onOpenSelfCheck()
            }}
            className="flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
          >
            <Stethoscope className="w-3.5 h-3.5" />
            System Self-Check
          </button>

          {isAuthenticated() && (
            <>
              <div className="my-1 h-px bg-border/50" />
              <button
                onClick={logout}
                className="flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
              >
                <LogOut className="w-3.5 h-3.5" />
                Log Out
              </button>
            </>
          )}
          </div>
        </div>
      )}
    </div>
  )
}
