import {
  LayoutDashboard, Briefcase, Compass, Bot, SlidersHorizontal,
  List, Activity, BellRing, Clock, Database, type LucideIcon,
} from 'lucide-react'

/**
 * Navigation is five rooms, not nine flat destinations.
 *
 * A room owns a task ("what needs me today", "my book", "what the machine
 * found"). Tabs inside a room are the tools for that task, so moving between
 * Positions and Paper is a tab, not a trip across the app.
 */
export interface RoomTab {
  /** Full path of the tab. */
  to: string
  label: string
  icon: LucideIcon
}

export interface Room {
  /** The room's own path; also the path of its first tab. */
  to: string
  label: string
  icon: LucideIcon
  /** Every path that belongs to this room, for active-state matching. */
  match: string[]
  tabs?: RoomTab[]
}

export const ROOMS: Room[] = [
  {
    to: '/today',
    label: 'Today',
    icon: LayoutDashboard,
    match: ['/today'],
  },
  {
    to: '/portfolio',
    label: 'Portfolio',
    icon: Briefcase,
    match: ['/portfolio'],
    tabs: [
      { to: '/portfolio', label: 'Positions', icon: Briefcase },
      { to: '/portfolio/watchlist', label: 'Watchlist', icon: List },
      { to: '/portfolio/paper', label: 'Paper', icon: Activity },
      { to: '/portfolio/alerts', label: 'Alerts', icon: BellRing },
    ],
  },
  {
    to: '/discover',
    label: 'Discover',
    icon: Compass,
    match: ['/discover'],
  },
  {
    to: '/agents',
    label: 'Agents',
    icon: Bot,
    match: ['/agents'],
    tabs: [
      { to: '/agents', label: 'Agents', icon: Bot },
      { to: '/agents/reports', label: 'Reports', icon: Clock },
    ],
  },
  {
    to: '/settings',
    label: 'Settings',
    icon: SlidersHorizontal,
    match: ['/settings'],
    tabs: [
      { to: '/settings', label: 'Settings', icon: SlidersHorizontal },
      { to: '/settings/data-sources', label: 'Data Sources', icon: Database },
    ],
  },
]

/** Old flat routes, kept alive as redirects so no bookmark 404s. */
export const LEGACY_REDIRECTS: Record<string, string> = {
  '/opportunities': '/discover',
  '/paper-trading': '/portfolio/paper',
  '/alerts': '/portfolio/alerts',
  '/history': '/agents/reports',
  '/datasources': '/settings/data-sources',
}

/** The room a path belongs to, or undefined for a full-page route. */
export function roomForPath(pathname: string): Room | undefined {
  return ROOMS.find(r => r.match.some(m => pathname === m || pathname.startsWith(m + '/')))
}

/** The active tab inside a room: the longest matching tab path wins. */
export function tabForPath(room: Room, pathname: string): RoomTab | undefined {
  if (!room.tabs) return undefined
  return [...room.tabs]
    .sort((a, b) => b.to.length - a.to.length)
    .find(t => pathname === t.to || pathname.startsWith(t.to + '/'))
}
