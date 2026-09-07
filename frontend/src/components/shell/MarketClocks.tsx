import { useEffect, useState } from 'react'

/**
 * The three clocks that actually govern this app's day.
 *
 * A self-hosted operator watching US equities, Canadian equities and crypto is
 * really asking one question when they open the app: what is open right now?
 * A greeting line cannot answer that; three clocks can.
 */
interface Session {
  key: string
  city: string
  tz: string
  /** Local trading hours as [openMinutes, closeMinutes] spans, or null for 24/7. */
  spans: [number, number][] | null
  /** Weekend closed (equities) or never closed (crypto). */
  weekdaysOnly: boolean
}

const HM = (h: number, m = 0) => h * 60 + m

const SESSIONS: Session[] = [
  { key: 'us', city: 'New York', tz: 'America/New_York',
    spans: [[HM(9, 30), HM(16)]], weekdaysOnly: true },
  { key: 'ca', city: 'Toronto', tz: 'America/Toronto',
    spans: [[HM(9, 30), HM(16)]], weekdaysOnly: true },
  { key: 'crypto', city: 'Crypto', tz: 'UTC', spans: null, weekdaysOnly: false },
]

/** Wall-clock minutes and weekday in a given IANA zone, without a date library. */
function zoneNow(tz: string): { minutes: number; weekday: number; label: string } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hour12: false, hour: '2-digit', minute: '2-digit', weekday: 'short',
  }).formatToParts(new Date())
  const get = (t: string) => parts.find(p => p.type === t)?.value ?? '0'
  const hour = Number(get('hour')) % 24
  const minute = Number(get('minute'))
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
  return {
    minutes: hour * 60 + minute,
    weekday: Math.max(0, days.indexOf(get('weekday'))),
    label: `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`,
  }
}

function describe(s: Session): { time: string; open: boolean; status: string } {
  const { minutes, weekday, label } = zoneNow(s.tz)
  if (!s.spans) return { time: '24/7', open: true, status: 'Always open' }

  const isWeekend = s.weekdaysOnly && (weekday === 0 || weekday === 6)
  if (isWeekend) return { time: label, open: false, status: 'Closed for the weekend' }

  const live = s.spans.find(([o, c]) => minutes >= o && minutes < c)
  if (live) {
    const left = live[1] - minutes
    return { time: label, open: true, status: `Closes in ${fmtSpan(left)}` }
  }
  const next = s.spans.find(([o]) => minutes < o)
  if (next) return { time: label, open: false, status: `Opens in ${fmtSpan(next[0] - minutes)}` }
  return { time: label, open: false, status: 'Closed for the day' }
}

function fmtSpan(mins: number): string {
  if (mins < 60) return `${mins}m`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return m ? `${h}h ${m}m` : `${h}h`
}

export default function MarketClocks() {
  const [, tick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => tick(n => n + 1), 20_000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="flex items-center overflow-x-auto scrollbar-none">
      {SESSIONS.map(s => {
        const d = describe(s)
        return (
          <div
            key={s.key}
            className="shrink-0 border-r border-border px-3.5 leading-tight last:border-r-0 first:pl-1"
          >
            <div className="col-head flex items-center gap-1.5">
              <span
                aria-hidden
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${d.open ? 'bg-success' : 'bg-muted-foreground/45'}`}
              />
              {s.city}
            </div>
            <div className="text-[15px] font-bold tracking-[-0.02em] tabular-nums">{d.time}</div>
            <div className="text-[10.5px] font-medium text-muted-foreground">
              <span className="sr-only">{d.open ? 'Open. ' : 'Closed. '}</span>
              {d.status}
            </div>
          </div>
        )
      })}
    </div>
  )
}
