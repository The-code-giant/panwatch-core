import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Trash2, FileText, ArrowLeft } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { fetchAPI } from '@tickerkeep/api'
import { Button } from '@tickerkeep/base-ui/components/ui/button'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@tickerkeep/base-ui/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@tickerkeep/base-ui/components/ui/dialog'
import { EmptyState } from '@tickerkeep/base-ui/components/ui/empty-state'
import { InfoTip } from '@tickerkeep/base-ui/components/ui/tooltip'
import { useToast } from '@tickerkeep/base-ui/components/ui/toast'

interface HistoryRecord {
  id: number
  agent_name: string
  agent_kind?: 'workflow' | 'capability'
  stock_symbol: string
  analysis_date: string
  title: string
  content: string
  context_payload?: Record<string, unknown> | null
  prompt_context?: string | null
  prompt_stats?: Record<string, unknown> | null
  news_debug?: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

const AGENT_LABELS: Record<string, string> = {
  daily_report: 'Post-market Review',
  premarket_outlook: 'Pre-market Outlook',
  intraday_monitor: 'Intraday Monitor',
  news_digest: 'News Digest',
  chart_analyst: 'Technical Analysis',
  tradingagents: 'TradingAgents Deep Analysis',
}

const WORKFLOW_AGENT_KEYS = ['daily_report', 'premarket_outlook', 'intraday_monitor', 'tradingagents']
const CAPABILITY_AGENT_KEYS = ['news_digest', 'chart_analyst']

export default function HistoryPage() {
  const { toast } = useToast()
  const navigate = useNavigate()
  const [records, setRecords] = useState<HistoryRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedAgent, setSelectedAgent] = useState<string>('all')
  const [historyKind, setHistoryKind] = useState<'workflow' | 'capability' | 'all'>('workflow')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [mobileView, setMobileView] = useState<'list' | 'reader'>('list')
  const [detailRecord, setDetailRecord] = useState<HistoryRecord | null>(null)

  const displayTime = (record: HistoryRecord) => record.updated_at || record.created_at
  const formatDateTime = (iso?: string) => {
    if (!iso) return '--'
    const s = String(iso).trim()
    if (!s) return '--'
    // Keep original offset semantics; only normalize display format and strip fractional seconds.
    let normalized = s.replace(' ', 'T').replace(/Z$/, '+00:00')
    normalized = normalized.replace(/\.\d+(?=[+-]\d{2}:\d{2}$)/, '')
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/.test(normalized)) {
      return normalized
    }
    const d = new Date(s)
    if (isNaN(d.getTime())) return s
    const pad = (n: number) => String(n).padStart(2, '0')
    const year = d.getFullYear()
    const month = pad(d.getMonth() + 1)
    const day = pad(d.getDate())
    const hour = pad(d.getHours())
    const minute = pad(d.getMinutes())
    const second = pad(d.getSeconds())
    const tz = -d.getTimezoneOffset()
    const sign = tz >= 0 ? '+' : '-'
    const tzHour = pad(Math.floor(Math.abs(tz) / 60))
    const tzMinute = pad(Math.abs(tz) % 60)
    return `${year}-${month}-${day}T${hour}:${minute}:${second}${sign}${tzHour}:${tzMinute}`
  }

  const formatTimeShort = (iso?: string) => {
    const full = formatDateTime(iso)
    const m = full.match(/T(\d{2}:\d{2}):\d{2}[+-]\d{2}:\d{2}$/)
    return m ? m[1] : '--:--'
  }

  const load = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (selectedAgent && selectedAgent !== 'all') params.set('agent_name', selectedAgent)
      params.set('kind', historyKind)
      params.set('limit', '50')
      const data = await fetchAPI<HistoryRecord[]>(`/history?${params.toString()}`)
      setRecords(data || [])
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to load', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [selectedAgent, historyKind])

  useEffect(() => {
    const available = historyKind === 'workflow'
      ? WORKFLOW_AGENT_KEYS
      : historyKind === 'capability'
        ? CAPABILITY_AGENT_KEYS
        : [...WORKFLOW_AGENT_KEYS, ...CAPABILITY_AGENT_KEYS]
    if (selectedAgent !== 'all' && !available.includes(selectedAgent)) {
      setSelectedAgent('all')
    }
  }, [historyKind, selectedAgent])

  useEffect(() => {
    if (!records.length) {
      setSelectedId(null)
      setMobileView('list')
      return
    }
    if (selectedId && records.some(r => r.id === selectedId)) return
    setSelectedId(records[0].id)
  }, [records, selectedId])

  const deleteRecord = async (id: number) => {
    if (!confirm('Delete this record?')) return
    try {
      await fetchAPI(`/history/${id}`, { method: 'DELETE' })
      toast('Deleted', 'success')
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Delete failed', 'error')
    }
  }

  // 格式化标题（带日期）
  const formatTitle = (record: HistoryRecord) => {
    const agentLabel = AGENT_LABELS[record.agent_name] || record.agent_name
    if (record.title) {
      return `${record.analysis_date} ${record.title}`
    }
    return `${record.analysis_date} ${agentLabel}`
  }

  const selectedRecord = selectedId ? records.find(r => r.id === selectedId) || null : null
  const agentOptions = historyKind === 'workflow'
    ? WORKFLOW_AGENT_KEYS
    : historyKind === 'capability'
      ? CAPABILITY_AGENT_KEYS
      : [...WORKFLOW_AGENT_KEYS, ...CAPABILITY_AGENT_KEYS]
  // "workflow" scope + "all agents" is the page default — anything narrower is a filter,
  // so an empty result there reads as "filtered to nothing" rather than "no data at all".
  const isFiltered = selectedAgent !== 'all' || historyKind !== 'workflow'
  const clearFilters = () => {
    setSelectedAgent('all')
    setHistoryKind('workflow')
  }

  const selectRecord = (id: number) => {
    setSelectedId(id)
    // On mobile, jump to reader view for a smoother experience
    setMobileView('reader')
    try {
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch {
      // ignore
    }
  }

  return (
    <div className="w-full space-y-4 md:space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <span className="section-sub"><span className="font-mono tabular-nums text-foreground/90">{records.length}</span> total</span>

        <div className="flex items-center gap-2">
          <InfoTip label="Main Workflow = scheduled agent reports (daily review, outlook, intraday monitor, TradingAgents). Capability Layer = on-demand tools like news digest and technical analysis." />
          <Select value={historyKind} onValueChange={(v) => setHistoryKind(v as 'workflow' | 'capability' | 'all')}>
            <SelectTrigger className="w-full sm:w-[150px] h-9">
              <SelectValue placeholder="History Scope" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="workflow">Main Workflow</SelectItem>
              <SelectItem value="capability">Capability Layer</SelectItem>
              <SelectItem value="all">All</SelectItem>
            </SelectContent>
          </Select>
          <Select value={selectedAgent} onValueChange={setSelectedAgent}>
            <SelectTrigger className="w-full sm:w-[180px] h-9">
              <SelectValue placeholder="All Agents" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Agents</SelectItem>
              {agentOptions.map((key) => (
                <SelectItem key={key} value={key}>{AGENT_LABELS[key] || key}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-12 gap-4">
          <div className="md:col-span-5 card overflow-hidden">
            <div className="px-4 py-3 border-b border-border helper-text">
              Contents (click to view)
            </div>
            <div className="divide-y divide-border">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <span className="skeleton h-4 w-16 rounded-full flex-shrink-0" />
                    <span className="skeleton h-3.5" style={{ width: `${45 + (i % 3) * 15}%` }} />
                  </div>
                  <div className="mt-2 flex items-center justify-between">
                    <span className="skeleton h-3 w-16" />
                    <span className="skeleton h-3 w-10" />
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="md:col-span-7 card p-4 md:p-6">
            <div className="flex items-center gap-2">
              <span className="skeleton h-4 w-24 rounded-full" />
              <span className="skeleton h-3 w-28" />
            </div>
            <span className="skeleton mt-2 block h-5 w-2/3" />
            <div className="mt-4 space-y-2.5">
              {Array.from({ length: 8 }).map((_, i) => (
                <span key={i} className="skeleton block h-3" style={{ width: `${92 - (i % 4) * 18}%` }} />
              ))}
            </div>
          </div>
        </div>
      ) : records.length === 0 ? (
        <div className="card">
          {isFiltered ? (
            <EmptyState
              icon={FileText}
              title="No matches for this filter"
              description="No analysis reports match the current scope and agent filter. Try a different agent or clear the filters."
              action={<button type="button" className="btn-mini" onClick={clearFilters}>Clear filters</button>}
            />
          ) : (
            <EmptyState
              icon={FileText}
              title="No analysis records yet"
              description="Reports your agents generate - post-market reviews, pre-market outlooks, technical analyses - will show up here once they run."
            />
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-12 gap-4">
          {/* Mobile view switch */}
          <div className="md:hidden card p-2">
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => setMobileView('list')}
                className={`h-9 rounded-lg text-[12px] font-medium transition-colors duration-150 ${mobileView === 'list' ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/70'}`}
              >
                Contents
              </button>
              <button
                onClick={() => setMobileView('reader')}
                className={`h-9 rounded-lg text-[12px] font-medium transition-colors duration-150 ${mobileView === 'reader' ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/70'}`}
                disabled={!selectedRecord}
              >
                Body
              </button>
            </div>
          </div>

          {/* List */}
          <div className={`md:col-span-5 card overflow-hidden ${mobileView === 'reader' ? 'hidden md:block' : ''}`}>
            <div className="px-4 py-3 border-b border-border helper-text">
              Contents (click to view)
            </div>
            <div className="max-h-[70vh] md:max-h-[70vh] overflow-y-auto scrollbar divide-y divide-border">
              {records.map(r => {
                const active = selectedId === r.id
                return (
                  <button
                    key={r.id}
                    onClick={() => selectRecord(r.id)}
                    className={`w-full text-left px-4 py-3 row-interactive ${active ? 'bg-primary/8' : ''}`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="chip-neutral text-[10px] flex-shrink-0">
                        {AGENT_LABELS[r.agent_name] || r.agent_name}
                      </span>
                      <span className={`text-[13px] font-medium truncate ${active ? 'text-foreground' : 'text-foreground/90'}`}>{r.title || 'Analysis Report'}</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between text-[11px] text-muted-foreground">
                      <span className="font-mono">{r.analysis_date}</span>
                      <span>{formatTimeShort(displayTime(r))}</span>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Reader */}
          <div className={`md:col-span-7 card p-4 md:p-6 ${mobileView === 'list' ? 'hidden md:block' : ''}`}>
            {selectedRecord ? (
              <div>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        className="btn-mini md:hidden -ml-1"
                        onClick={() => setMobileView('list')}
                      >
                        <ArrowLeft className="w-3.5 h-3.5" />
                        Contents
                      </button>
                      <span className="chip-neutral text-[10px]">{AGENT_LABELS[selectedRecord.agent_name] || selectedRecord.agent_name}</span>
                      <span className="text-[11px] text-muted-foreground font-mono">{formatDateTime(displayTime(selectedRecord))}</span>
                    </div>
                    <div className="mt-1 text-[15px] md:text-[16px] font-semibold text-foreground truncate">
                      {formatTitle(selectedRecord)}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      type="button"
                      className="btn-mini"
                      onClick={() => {
                        // TradingAgents deep-analysis records go to their own detailed reading page; other agents keep the original detail dialog
                        if (selectedRecord.agent_name === 'tradingagents' && selectedRecord.stock_symbol) {
                          navigate(`/analysis/${selectedRecord.stock_symbol}/${selectedRecord.analysis_date}`)
                        } else {
                          setDetailRecord(selectedRecord)
                        }
                      }}
                    >
                      View Details
                    </button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-9 w-9 hover:text-destructive"
                      onClick={() => deleteRecord(selectedRecord.id)}
                      title="Delete"
                      aria-label="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>

                <div className="mt-4 p-4 bg-accent/20 rounded-xl prose prose-sm dark:prose-invert max-w-none max-h-[62vh] md:max-h-[62vh] overflow-y-auto scrollbar">
                  <ReactMarkdown>{selectedRecord.content}</ReactMarkdown>
                </div>
              </div>
            ) : (
              <EmptyState
                size="sm"
                icon={FileText}
                title="No report selected"
                description="Pick a report from the list on the left to read it here."
              />
            )}
          </div>
        </div>
      )}

      {/* Detail Dialog */}
      <Dialog open={!!detailRecord} onOpenChange={open => !open && setDetailRecord(null)}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{detailRecord ? formatTitle(detailRecord) : 'Analysis Details'}</DialogTitle>
            <DialogDescription>
              {detailRecord && (
                <span className="flex items-center gap-2">
                  <span className="chip-neutral">{AGENT_LABELS[detailRecord.agent_name] || detailRecord.agent_name}</span>
                </span>
              )}
            </DialogDescription>
          </DialogHeader>
          <div className="mt-4 p-4 bg-accent/20 rounded-lg prose prose-sm dark:prose-invert max-w-none">
            {detailRecord && <ReactMarkdown>{detailRecord.content}</ReactMarkdown>}
          </div>
          {detailRecord?.prompt_stats ? (
            <div className="mt-3 rounded-lg border border-border/50 p-3">
              <div className="text-[12px] font-medium mb-1">Prompt Stats</div>
              <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto">{JSON.stringify(detailRecord.prompt_stats, null, 2)}</pre>
            </div>
          ) : null}
          {detailRecord?.context_payload ? (
            <div className="mt-3 rounded-lg border border-border/50 p-3">
              <div className="text-[12px] font-medium mb-1">Context Snapshot</div>
              <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto max-h-[280px] overflow-y-auto">{JSON.stringify(detailRecord.context_payload, null, 2)}</pre>
            </div>
          ) : null}
          {detailRecord?.news_debug ? (
            <div className="mt-3 rounded-lg border border-border/50 p-3">
              <div className="text-[12px] font-medium mb-1">News Injection Details</div>
              <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto">{JSON.stringify(detailRecord.news_debug, null, 2)}</pre>
            </div>
          ) : null}
          {detailRecord?.prompt_context ? (
            <div className="mt-3 rounded-lg border border-border/50 p-3">
              <div className="text-[12px] font-medium mb-1">Raw Prompt</div>
              <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words overflow-x-auto max-h-[280px] overflow-y-auto">{detailRecord.prompt_context}</pre>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}
