import { useState, useEffect, useRef, lazy, Suspense } from 'react'
import { Routes, Route, NavLink, useLocation, useNavigate, Navigate } from 'react-router-dom'
import { useTheme } from '@/hooks/use-theme'
import { appApi, fetchAPI, homeApi, isAuthenticated } from '@panwatch/api'
import { isMarketingPath } from '@/marketing/routes'
import Rail from '@/components/shell/Rail'
import TopBar from '@/components/shell/TopBar'
import RoomShell from '@/components/shell/RoomShell'
import { ROOMS, LEGACY_REDIRECTS, roomForPath } from '@/components/shell/rooms'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'

// Every page is its own chunk, so the public marketing home does not download
// the app, and the app does not download the marketing site.
const MarketingRoutes = lazy(() => import('@/marketing'))
// Heavy app-only overlays (markdown chat, logs, self-check, command palette)
// load after the shell, so the public pages never download them.
const LogsModal = lazy(() => import('@panwatch/biz-ui/components/logs-modal'))
const ChatWidget = lazy(() => import('@/components/ChatWidget'))
const SelfCheckModal = lazy(() => import('@/components/SelfCheckModal'))
const CommandPalette = lazy(() => import('@/components/shell/CommandPalette'))
const TodayPage = lazy(() => import('@/pages/Today'))
const OpportunitiesPage = lazy(() => import('@/pages/Opportunities'))
const StocksPage = lazy(() => import('@/pages/Stocks'))
const AgentsPage = lazy(() => import('@/pages/Agents'))
const SettingsPage = lazy(() => import('@/pages/Settings'))
const DataSourcesPage = lazy(() => import('@/pages/DataSources'))
const HistoryPage = lazy(() => import('@/pages/History'))
const AnalysisDetailPage = lazy(() => import('@/pages/AnalysisDetail'))
const PriceAlertsPage = lazy(() => import('@/pages/PriceAlerts'))
const PaperTradingPage = lazy(() => import('@/pages/PaperTrading'))
const LoginPage = lazy(() => import('@/pages/Login'))

const routeFallback = (
  <div className="flex min-h-screen items-center justify-center bg-background">
    <span className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-foreground" />
  </div>
)

// Auth guard component
function RequireAuth({ children }: { children: React.ReactNode }) {
  const [authState, setAuthState] = useState<'checking' | 'authenticated' | 'unauthenticated'>('checking')
  const location = useLocation()

  useEffect(() => {
    if (isAuthenticated()) {
      setAuthState('authenticated')
      return
    }
    setAuthState('unauthenticated')
  }, [])

  if (authState === 'checking') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-foreground" />
      </div>
    )
  }

  if (authState === 'unauthenticated') {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return <>{children}</>
}

function App() {
  const { mode, setMode } = useTheme()
  const location = useLocation()
  const navigate = useNavigate()
  const [version, setVersion] = useState('')
  const [logsOpen, setLogsOpen] = useState(false)
  const [selfCheckOpen, setSelfCheckOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [upgradeOpen, setUpgradeOpen] = useState(false)
  const [upgradeInfo, setUpgradeInfo] = useState<{ latest: string; url: string } | null>(null)
  const [nextRun, setNextRun] = useState<{ label: string; at: string; note: string } | null>(null)
  const [alertCount, setAlertCount] = useState(0)
  const checkedUpdateRef = useRef(false)

  useEffect(() => {
    appApi.version()
      .then(data => setVersion(data?.version || ''))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (checkedUpdateRef.current) return
    if (!isAuthenticated()) return
    const current = String(version || '').trim()
    if (!current || current === 'dev') return
    checkedUpdateRef.current = true

    fetchAPI<any>('/settings/update-check')
      .then((res) => {
        const latest = String(res?.latest_version || '').trim()
        const shouldOpen = !!res?.update_available && !!latest
        if (!shouldOpen) return
        const dismissed = localStorage.getItem('panwatch_upgrade_dismissed_version') || ''
        if (dismissed === latest) return
        setUpgradeInfo({ latest, url: String(res?.release_url || '') })
        setUpgradeOpen(true)
      })
      .catch(() => {})
  }, [version])

  // The rail's watcher card and the top bar's bell both report real state:
  // the next scheduled agent run, and how many alert rules fired today.
  useEffect(() => {
    if (!isAuthenticated()) return
    let cancelled = false

    fetchAPI<{ agents: { display_name: string; name: string; enabled: boolean; next_runs: string[] }[] }>('/agents/health')
      .then(res => {
        if (cancelled) return
        const soonest = (res?.agents ?? [])
          .filter(a => a.enabled && a.next_runs?.length)
          .map(a => ({ name: a.display_name || a.name, at: new Date(a.next_runs[0]) }))
          .sort((x, y) => x.at.getTime() - y.at.getTime())[0]
        if (!soonest) return
        setNextRun({
          label: soonest.name,
          at: soonest.at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          note: 'Runs on your configured model. A local model can take a few minutes.',
        })
      })
      .catch(() => {})

    homeApi.alertHitsToday()
      .then(hits => { if (!cancelled) setAlertCount(Array.isArray(hits) ? hits.length : 0) })
      .catch(() => {})

    return () => { cancelled = true }
  }, [])

  // The public marketing site: no session, no app shell, its own visual world.
  if (isMarketingPath(location.pathname)) {
    return (
      <Suspense fallback={<div className="mk min-h-[100dvh] bg-mk-canvas" />}>
        <MarketingRoutes />
      </Suspense>
    )
  }

  // Login stands outside the room shell: one centred form, no navigation.
  if (location.pathname === '/login') {
    return (
      <div className="min-h-screen bg-background">
        <Suspense fallback={routeFallback}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
          </Routes>
        </Suspense>
      </div>
    )
  }

  const currentRoom = roomForPath(location.pathname)

  return (
    <RequireAuth>
      <div className="relative min-h-screen overflow-x-clip bg-background pb-16 md:pb-0">
        <Rail
          version={version}
          onOpenLogs={() => setLogsOpen(true)}
          nextRun={nextRun ? { ...nextRun, onRun: () => navigate('/agents') } : undefined}
        />

        {/* Mobile: the five rooms as a bottom tab bar, same set as the rail. */}
        <nav
          aria-label="Rooms"
          className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-card px-2 pb-[env(safe-area-inset-bottom)] md:hidden"
        >
          <div className="flex h-14 items-center justify-around">
            {ROOMS.map(({ to, label, icon: Icon }) => {
              const isActive = currentRoom?.to === to
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

        <main className="w-full px-3 py-3 md:py-3 md:pl-[15rem] md:pr-3">
          <div className="flex flex-col gap-3.5">
            <TopBar
              onOpenPalette={() => setPaletteOpen(true)}
              mode={mode}
              onSetMode={setMode}
              onOpenSelfCheck={() => setSelfCheckOpen(true)}
              overflowNav={[]}
              alertCount={alertCount}
              onOpenAlerts={() => navigate('/portfolio/alerts')}
            />

            <RoomShell>
              <Suspense fallback={routeFallback}>
              <Routes>
                {/* Rooms */}
                <Route path="/today" element={<TodayPage />} />

                <Route path="/portfolio" element={<StocksPage view="positions" />} />
                <Route path="/portfolio/watchlist" element={<StocksPage view="watchlist" />} />
                <Route path="/portfolio/paper" element={<PaperTradingPage />} />
                <Route path="/portfolio/alerts" element={<PriceAlertsPage />} />

                <Route path="/discover" element={<OpportunitiesPage />} />

                <Route path="/agents" element={<AgentsPage />} />
                <Route path="/agents/reports" element={<HistoryPage />} />

                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/settings/data-sources" element={<DataSourcesPage />} />

                {/* Full-page routes, outside any room */}
                <Route path="/analysis/:symbol/:date" element={<AnalysisDetailPage />} />

                {/* The old flat routes keep working */}
                {Object.entries(LEGACY_REDIRECTS).map(([from, to]) => (
                  <Route key={from} path={from} element={<Navigate to={to} replace />} />
                ))}
                <Route path="*" element={<Navigate to="/today" replace />} />
              </Routes>
              </Suspense>
            </RoomShell>
          </div>
        </main>

        <Suspense fallback={null}>
          <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
          <ChatWidget />
          <LogsModal open={logsOpen} onOpenChange={setLogsOpen} />
          <SelfCheckModal open={selfCheckOpen} onClose={() => setSelfCheckOpen(false)} />
        </Suspense>
        <Dialog open={upgradeOpen} onOpenChange={setUpgradeOpen}>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>New version available</DialogTitle>
              <DialogDescription>
                You are on v{version}. Version v{upgradeInfo?.latest} is out.
              </DialogDescription>
            </DialogHeader>
            <div className="text-[12px] text-muted-foreground">
              Upgrading brings the latest features and fixes.
            </div>
            <div className="flex items-center justify-end gap-2">
              <Button
                variant="secondary"
                onClick={() => {
                  if (upgradeInfo?.latest) localStorage.setItem('panwatch_upgrade_dismissed_version', upgradeInfo.latest)
                  setUpgradeOpen(false)
                }}
              >
                Remind me later
              </Button>
              {upgradeInfo?.url && (
                <Button
                  onClick={() => {
                    window.open(upgradeInfo.url, '_blank', 'noopener,noreferrer')
                  }}
                >
                  Upgrade
                </Button>
              )}
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </RequireAuth>
  )
}

export default App
