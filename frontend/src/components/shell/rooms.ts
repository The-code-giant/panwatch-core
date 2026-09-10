import {
  LayoutDashboard, Briefcase, Compass, Bot, SlidersHorizontal,
  List, Activity, BellRing, Clock, Database, type LucideIcon,
} from 'lucide-react'
import type { Capabilities } from '@/lib/capabilities'

/**
 * Navigation is five rooms, not nine flat destinations.
 *
 * A room owns a task ("what needs me today", "my book", "what the machine
 * found"). Tabs inside a room are the tools for that task, so moving between
 * Positions and Paper is a tab, not a trip across the app.
 *
 * Capability gating: a room or tab whose `capability` is set is only shown
 * once `useCapabilities()` (frontend/src/lib/capabilities.tsx) confirms it
 * granted -- see `visibleRooms` below. No `capability` means always shown.
 * Core's own `/auth/me` always grants the nine product strings
 * (watchlist:read, portfolio:read, paper:read, alerts:read, discover:read,
 * agents:read, reports:read, settings:read, datasources:read), so nothing is
 * ever hidden in core; a deployment that grants fewer of them (see
 * cloud/capabilities.py) is the one this mechanism exists for.
 */
export interface RoomTab {
  /** Full path of the tab. */
  to: string
  label: string
  icon: LucideIcon
  /** Capability required to see and reach this tab. Absent = always shown. */
  capability?: string
}

export interface Room {
  /** The room's own path; also the path of its first tab. */
  to: string
  label: string
  icon: LucideIcon
  /** Every path that belongs to this room, for active-state matching. */
  match: string[]
  tabs?: RoomTab[]
  /** Capability required to see and reach this room. Absent = always shown. */
  capability?: string
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
    capability: 'portfolio:read',
    tabs: [
      { to: '/portfolio', label: 'Positions', icon: Briefcase, capability: 'portfolio:read' },
      { to: '/portfolio/watchlist', label: 'Watchlist', icon: List, capability: 'watchlist:read' },
      { to: '/portfolio/paper', label: 'Paper', icon: Activity, capability: 'paper:read' },
      { to: '/portfolio/alerts', label: 'Alerts', icon: BellRing, capability: 'alerts:read' },
    ],
  },
  {
    to: '/discover',
    label: 'Discover',
    icon: Compass,
    match: ['/discover'],
    capability: 'discover:read',
  },
  {
    to: '/agents',
    label: 'Agents',
    icon: Bot,
    match: ['/agents'],
    capability: 'agents:read',
    tabs: [
      { to: '/agents', label: 'Agents', icon: Bot, capability: 'agents:read' },
      { to: '/agents/reports', label: 'Reports', icon: Clock, capability: 'reports:read' },
    ],
  },
  {
    to: '/settings',
    label: 'Settings',
    icon: SlidersHorizontal,
    match: ['/settings'],
    capability: 'settings:read',
    tabs: [
      { to: '/settings', label: 'Settings', icon: SlidersHorizontal, capability: 'settings:read' },
      { to: '/settings/data-sources', label: 'Data Sources', icon: Database, capability: 'datasources:read' },
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

/** Capability required to view the route at this exact path, or undefined for none. */
export function capabilityForPath(pathname: string): string | undefined {
  for (const room of ROOMS) {
    const tab = room.tabs?.find(t => t.to === pathname)
    if (tab) return tab.capability
    if (room.to === pathname) return room.capability
  }
  return undefined
}

/**
 * `rooms`, filtered to what `capabilities` actually grants right now.
 *
 * Deliberately fails closed while capabilities have not loaded yet: before
 * `Capabilities.loaded` is true, `has()` always answers false (see
 * capabilities.tsx), so every gated room and tab is left out here too --
 * nothing with a capability requirement flashes into the nav and then
 * disappears once `/auth/me` actually answers. Ungated entries (no
 * `capability`, e.g. Today) are unaffected and always included.
 */
export function visibleRooms(rooms: Room[], capabilities: Pick<Capabilities, 'has'>): Room[] {
  return rooms
    .filter(r => !r.capability || capabilities.has(r.capability))
    .map(r => (r.tabs ? { ...r, tabs: r.tabs.filter(t => !t.capability || capabilities.has(t.capability)) } : r))
}
