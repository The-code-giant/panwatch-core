import { NavLink, useLocation } from 'react-router-dom'
import { useCapabilities } from '@/lib/capabilities'
import { roomForPath, tabForPath } from './rooms'

/**
 * The room header: one title, and the room's tabs when it has more than one
 * tool. Pages rendered inside a room do not print their own title, so the
 * heading appears exactly once per screen.
 *
 * Capability gating: if the current room's own capability is not (yet, or
 * ever) granted, this renders no header at all -- the route itself is what
 * decides whether to render `children` or redirect away (see
 * `RequireCapability`), so this never shows a room's chrome around content
 * the visitor cannot see, not even for the one render before a redirect
 * lands. A room's tabs are filtered the same way `visibleRooms` filters the
 * nav, so a tab whose own capability is absent never renders here either,
 * even inside an otherwise-visible room.
 */
export default function RoomShell({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const capabilities = useCapabilities()
  const room = roomForPath(location.pathname)

  if (!room) return <>{children}</>

  const roomVisible = !room.capability || capabilities.has(room.capability)
  if (!roomVisible) return <>{children}</>

  const activeTab = tabForPath(room, location.pathname)
  const tabs = room.tabs?.filter(t => !t.capability || capabilities.has(t.capability))

  return (
    <div className="flex flex-col gap-3.5">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
        <h1 className="page-title">{room.label}</h1>

        {tabs && tabs.length > 0 && (
          <nav aria-label={`${room.label} sections`} className="flex items-center gap-1">
            {tabs.map(({ to, label, icon: Icon }) => {
              const isActive = activeTab?.to === to
              return (
                <NavLink
                  key={to}
                  to={to}
                  end
                  aria-current={isActive ? 'page' : undefined}
                  className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] transition-colors duration-150 ${
                    isActive
                      ? 'bg-primary font-bold text-primary-foreground'
                      : 'font-semibold text-muted-foreground hover:bg-muted'
                  }`}
                >
                  <Icon className="h-[15px] w-[15px] shrink-0" aria-hidden />
                  {label}
                </NavLink>
              )
            })}
          </nav>
        )}
      </div>

      {children}
    </div>
  )
}
