import { NavLink, useLocation } from 'react-router-dom'
import { roomForPath, tabForPath } from './rooms'

/**
 * The room header: one title, and the room's tabs when it has more than one
 * tool. Pages rendered inside a room do not print their own title, so the
 * heading appears exactly once per screen.
 */
export default function RoomShell({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const room = roomForPath(location.pathname)

  if (!room) return <>{children}</>

  const activeTab = tabForPath(room, location.pathname)

  return (
    <div className="flex flex-col gap-3.5">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
        <h1 className="page-title">{room.label}</h1>

        {room.tabs && (
          <nav aria-label={`${room.label} sections`} className="flex items-center gap-1">
            {room.tabs.map(({ to, label, icon: Icon }) => {
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
