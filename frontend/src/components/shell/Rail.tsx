import { NavLink, useLocation } from 'react-router-dom'
import { Radar, ScrollText } from 'lucide-react'
import { ROOMS, roomForPath } from './rooms'

interface RailProps {
  version: string
  onOpenLogs: () => void
  /** Rendered at the foot of the rail: the next scheduled agent run. */
  nextRun?: { label: string; at: string; note: string; onRun: () => void }
}

/**
 * The dark console rail. Five rooms, and the watcher card at the foot telling
 * you what the machine does next.
 *
 * Desktop only: on a phone the rail becomes the bottom tab bar in App.
 */
export default function Rail({ version, onOpenLogs, nextRun }: RailProps) {
  const location = useLocation()
  const current = roomForPath(location.pathname)

  return (
    <aside className="fixed inset-y-3 left-3 z-40 hidden w-[13.75rem] flex-col rounded-rail bg-rail px-3.5 py-5 md:flex">
      <NavLink to="/today" className="flex items-center gap-2.5 px-2 pb-5">
        <span className="grid h-6.5 w-6.5 shrink-0 place-items-center rounded-lg bg-primary p-1 text-primary-foreground">
          <Radar className="h-4 w-4" aria-hidden />
        </span>
        <span className="text-[16.5px] font-black tracking-[-0.03em] text-rail-foreground">TickerKeep</span>
      </NavLink>

      <p className="px-2 pb-2 text-[10.5px] font-bold uppercase tracking-[0.1em] text-rail-muted/70">
        Rooms
      </p>
      <nav className="flex flex-col gap-0.5">
        {ROOMS.map(({ to, label, icon: Icon }) => {
          const isActive = current?.to === to
          return (
            <NavLink
              key={to}
              to={to}
              aria-current={isActive ? 'page' : undefined}
              className={`flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-[13.5px] transition-colors duration-150 ${
                isActive
                  ? 'bg-primary font-bold text-primary-foreground'
                  : 'font-medium text-rail-muted hover:bg-rail-2 hover:text-rail-foreground'
              }`}
            >
              <Icon className="h-[17px] w-[17px] shrink-0" aria-hidden />
              {label}
            </NavLink>
          )
        })}
      </nav>

      {nextRun && (
        <div className="mt-auto rounded-2xl bg-rail-2 p-3.5 text-center">
          <span className="mx-auto mb-2.5 grid h-14 w-14 place-items-center rounded-2xl bg-primary text-primary-foreground">
            <Radar className="h-7 w-7" aria-hidden />
          </span>
          <p className="text-[12.5px] font-bold text-rail-foreground">
            {nextRun.label} at {nextRun.at}
          </p>
          <p className="mb-3 mt-0.5 text-[11.5px] leading-snug text-rail-muted">{nextRun.note}</p>
          <button onClick={nextRun.onRun} className="btn-primary w-full">Run it now</button>
        </div>
      )}

      <div className={`flex items-center gap-1 px-1 ${nextRun ? 'pt-3' : 'mt-auto'}`}>
        <button
          onClick={onOpenLogs}
          title="View logs"
          className="grid h-8 w-8 place-items-center rounded-lg text-rail-muted transition-colors hover:bg-rail-2 hover:text-rail-foreground"
        >
          <ScrollText className="h-4 w-4" aria-hidden />
          <span className="sr-only">View logs</span>
        </button>
        {version && (
          <span className="ml-auto pr-1 text-[10.5px] font-medium tabular-nums text-rail-muted/70">
            v{version}
          </span>
        )}
      </div>
    </aside>
  )
}
