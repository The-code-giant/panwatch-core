import { useMemo } from 'react'
import { NavLink } from 'react-router-dom'
import { useCapabilities } from '@/lib/capabilities'
import { ROOMS, visibleRooms } from './rooms'

/**
 * Mobile: the rooms as a bottom tab bar, same set (and same capability
 * filter) as the desktop rail.
 *
 * A separate component, not inline JSX in App.tsx, on purpose: it must call
 * `useCapabilities()` from *inside* `<CapabilityProvider>`'s subtree, and
 * `<CapabilityProvider>` is mounted by App() itself -- a hook call in App()'s
 * own body would see only the context's fail-closed default, never the live
 * value the provider it renders below supplies.
 */
export default function MobileRoomNav({ currentTo }: { currentTo?: string }) {
  const capabilities = useCapabilities()
  const rooms = useMemo(() => visibleRooms(ROOMS, capabilities), [capabilities])

  return (
    <nav
      aria-label="Rooms"
      className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-card px-2 pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      <div className="flex h-14 items-center justify-around">
        {rooms.map(({ to, label, icon: Icon }) => {
          const isActive = currentTo === to
          return (
            <NavLink
              key={to}
              to={to}
              aria-current={isActive ? 'page' : undefined}
              className={`flex min-w-[56px] flex-col items-center justify-center gap-0.5 rounded-xl px-2 py-1.5 transition-colors ${
                isActive ? 'text-foreground' : 'text-muted-foreground'
              }`}
            >
              <span className={`grid h-7 w-11 place-items-center rounded-full transition-colors ${
                isActive ? 'bg-primary text-primary-foreground' : ''
              }`}>
                <Icon className="h-[18px] w-[18px]" aria-hidden />
              </span>
              <span className="text-[10px] font-semibold">{label}</span>
            </NavLink>
          )
        })}
      </div>
    </nav>
  )
}
