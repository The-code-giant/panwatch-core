import { useState, useEffect, useRef, lazy, Suspense } from 'react'
import { Routes, Route, useLocation, useNavigate, Navigate } from 'react-router-dom'
import { useTheme } from '@/hooks/use-theme'
import { appApi, fetchAPI, homeApi } from '@tickerkeep/api'
import { isMarketingPath } from '@/marketing/routes'
import { isEnterprisePath } from '@/enterprise/routes'
import { isAuthPath, LOGIN_PATH } from '@/auth/routes'
import { useAuthSession } from '@/auth/session'
import { CapabilityProvider } from '@/lib/capabilities'
import Rail from '@/components/shell/Rail'
import TopBar from '@/components/shell/TopBar'
import RoomShell from '@/components/shell/RoomShell'
import MobileRoomNav from '@/components/shell/MobileRoomNav'
import RequireCapability from '@/components/shell/RequireCapability'
import { LEGACY_REDIRECTS, roomForPath, capabilityForPath } from '@/components/shell/rooms'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@tickerkeep/base-ui/components/ui/dialog'
import { Button } from '@tickerkeep/base-ui/components/ui/button'

// Every page is its own chunk, so the public marketing home does not download
// the app, and the app does not download the marketing site.
const MarketingRoutes = lazy(() => import('@/marketing'))
// Enterprise pages are their own chunk too; core's stub renders nothing.
const EnterpriseRoutes = lazy(() => import('@/enterprise'))
// The sign-in UI is a seam as well (src/auth): core ships its single login
// page; the cloud overlay replaces the directory with its own pages.
const AuthRoutes = lazy(() => import('@/auth'))
// Heavy app-only overlays (markdown chat, logs, self-check, command palette)
// load after the shell, so the public pages never download them.
const LogsModal = lazy(() => import('@tickerkeep/biz-ui/components/logs-modal'))
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

const routeFallback = (
  <div className="flex min-h-screen items-center justify-center bg-background">
    <span className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-foreground" />
  </div>
)

// Auth guard component
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status: authState } = useAuthSession()
  const location = useLocation()

  if (authState === 'checking') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-foreground" />
      </div>
    )
  }

  if (authState === 'unauthenticated') {
    return <Navigate to={LOGIN_PATH} state={{ from: location }} replace />
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
  // The shell's own /api/* bootstrap calls below wait for the seam session to
  // be 'authenticated'. In core that resolves in the hook's mount effect, so
  // nothing changes for self-hosted users; an overlay whose session check is
  // asynchronous simply delays these calls until it is known.
  const { status: sessionStatus } = useAuthSession()

  useEffect(() => {
    appApi.version()
      .then(data => setVersion(data?.version || ''))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (checkedUpdateRef.current) return
    if (sessionStatus !== 'authenticated') return
    const current = String(version || '').trim()
    if (!current || current === 'dev') return
    checkedUpdateRef.current = true

    fetchAPI<any>('/settings/update-check')
      .then((res) => {
        const latest = String(res?.latest_version || '').trim()
        const shouldOpen = !!res?.update_available && !!latest
        if (!shouldOpen) return
        const dismissed = localStorage.getItem('tickerkeep_upgrade_dismissed_version') || ''
        if (dismissed === latest) return
        setUpgradeInfo({ latest, url: String(res?.release_url || '') })
        setUpgradeOpen(true)
      })
      .catch(() => {})
  }, [version, sessionStatus])

  // The rail's watcher card and the top bar's bell both report real state:
  // the next scheduled agent run, and how many alert rules fired today.
  useEffect(() => {
    if (sessionStatus !== 'authenticated') return
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
  }, [sessionStatus])

  // The public marketing site: no session, no app shell, its own visual world.
  if (isMarketingPath(location.pathname)) {
    return (
      <Suspense fallback={<div className="mk min-h-[100dvh] bg-mk-canvas" />}>
        <MarketingRoutes />
      </Suspense>
    )
  }

  // The sign-in UI stands outside the room shell: no rail, no top bar, no
  // session guard. Which paths and what renders there is the auth seam's.
  if (isAuthPath(location.pathname)) {
    return (
      <Suspense fallback={routeFallback}>
        <AuthRoutes />
      </Suspense>
    )
  }

  const currentRoom = roomForPath(location.pathname)

  return (
    <RequireAuth>
      <CapabilityProvider>
      <div className="relative min-h-screen overflow-x-clip bg-background pb-16 md:pb-0">
        <Rail
          version={version}
          onOpenLogs={() => setLogsOpen(true)}
          nextRun={nextRun ? { ...nextRun, onRun: () => navigate('/agents') } : undefined}
        />

        {/* Mobile: the five rooms as a bottom tab bar, same set as the rail.
            A separate component so its useCapabilities() call runs inside
            <CapabilityProvider>'s subtree -- see MobileRoomNav's own doc. */}
        <MobileRoomNav currentTo={currentRoom?.to} />

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
              {/* Enterprise pages (private overlay) sit inside the signed-in shell:
                  same rail, top bar, room shell and session. Checked ahead of the
                  route table so the `*` catch-all never swallows them. */}
              {isEnterprisePath(location.pathname) ? (
                <EnterpriseRoutes />
              ) : (
              <Routes>
                {/* Rooms. Each gated route is wrapped in RequireCapability so
                    direct navigation (a bookmark, a LEGACY_REDIRECTS target)
                    is coherent with what the nav shows: never
                    reachable-but-broken, never unreachable-but-linked. The
                    capability string itself comes from rooms.ts, the single
                    source of truth the nav filter also reads. */}
                <Route path="/today" element={<TodayPage />} />

                <Route path="/portfolio" element={<RequireCapability capability={capabilityForPath('/portfolio')}><StocksPage view="positions" /></RequireCapability>} />
                <Route path="/portfolio/watchlist" element={<RequireCapability capability={capabilityForPath('/portfolio/watchlist')}><StocksPage view="watchlist" /></RequireCapability>} />
                <Route path="/portfolio/paper" element={<RequireCapability capability={capabilityForPath('/portfolio/paper')}><PaperTradingPage /></RequireCapability>} />
                <Route path="/portfolio/alerts" element={<RequireCapability capability={capabilityForPath('/portfolio/alerts')}><PriceAlertsPage /></RequireCapability>} />

                <Route path="/discover" element={<RequireCapability capability={capabilityForPath('/discover')}><OpportunitiesPage /></RequireCapability>} />

                <Route path="/agents" element={<RequireCapability capability={capabilityForPath('/agents')}><AgentsPage /></RequireCapability>} />
                <Route path="/agents/reports" element={<RequireCapability capability={capabilityForPath('/agents/reports')}><HistoryPage /></RequireCapability>} />

                <Route path="/settings" element={<RequireCapability capability={capabilityForPath('/settings')}><SettingsPage /></RequireCapability>} />
                <Route path="/settings/data-sources" element={<RequireCapability capability={capabilityForPath('/settings/data-sources')}><DataSourcesPage /></RequireCapability>} />

                {/* Full-page routes, outside any room */}
                <Route path="/analysis/:symbol/:date" element={<AnalysisDetailPage />} />

                {/* The old flat routes keep working */}
                {Object.entries(LEGACY_REDIRECTS).map(([from, to]) => (
                  <Route key={from} path={from} element={<Navigate to={to} replace />} />
                ))}
                <Route path="*" element={<Navigate to="/today" replace />} />
              </Routes>
              )}
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
                  if (upgradeInfo?.latest) localStorage.setItem('tickerkeep_upgrade_dismissed_version', upgradeInfo.latest)
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
      </CapabilityProvider>
    </RequireAuth>
  )
}

export default App
