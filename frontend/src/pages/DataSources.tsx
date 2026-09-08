import { useState, useEffect } from 'react'
import { Pencil, Play, Database, Newspaper, LineChart, TrendingUp, Image, Layers, Zap, Check, X, Clock, Trash2, ChevronUp, ChevronDown, ChevronRight, Eye, EyeOff, RotateCcw, AlertTriangle, BarChart3, Gift, Plus, Users, FileText } from 'lucide-react'
import { fetchAPI, resetDataSourcesToSeed, type DataSource } from '@tickerkeep/api'
import { Input } from '@tickerkeep/base-ui/components/ui/input'
import { Label } from '@tickerkeep/base-ui/components/ui/label'
import { Button } from '@tickerkeep/base-ui/components/ui/button'
import { Switch } from '@tickerkeep/base-ui/components/ui/switch'
import { Badge } from '@tickerkeep/base-ui/components/ui/badge'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@tickerkeep/base-ui/components/ui/dialog'
import { EmptyState } from '@tickerkeep/base-ui/components/ui/empty-state'
import { InfoTip } from '@tickerkeep/base-ui/components/ui/tooltip'
import { useToast } from '@tickerkeep/base-ui/components/ui/toast'

interface TestLogItem {
  timestamp: string
  source_name: string
  source_type: string
  action: 'start' | 'success' | 'error'
  message: string
  duration_ms: number
  count: number
}

interface TestResult {
  test_passed: boolean
  source_name: string
  source_type: string
  type_label: string
  provider: string
  supports_batch: boolean
  test_symbols: string[]
  count: number
  duration_ms: number
  error?: string
  items?: unknown[] | { image?: string }  // array for most types, object for chart
  logs: TestLogItem[]
}

interface DataSourceForm {
  name: string
  type: string
  provider: string
  config: Record<string, unknown>
  priority: number
  supports_batch: boolean
  test_symbols: string[]
}

// Per-kind identity marker. None of these map to a specific market (they cut
// across US/CA/crypto/gold), so they stay neutral rather than borrowing
// the mkt-* identity hues or the lime accent.
const DATASOURCE_TYPES = {
  news: { label: 'News', icon: Newspaper, color: 'text-muted-foreground' },
  kline: { label: 'Daily Bars', icon: LineChart, color: 'text-muted-foreground' },
  quote: { label: 'Real-time Quotes', icon: TrendingUp, color: 'text-muted-foreground' },
  events: { label: 'Corporate Events', icon: Layers, color: 'text-muted-foreground' },
  chart: { label: 'Chart Rendering', icon: Image, color: 'text-muted-foreground' },
  flash_news: { label: 'Market Headlines', icon: Zap, color: 'text-muted-foreground' },
  fundamentals: { label: 'Fundamentals', icon: BarChart3, color: 'text-muted-foreground' },
  dividend: { label: 'Dividends', icon: Gift, color: 'text-muted-foreground' },
  holders: { label: 'Holders & Insiders', icon: Users, color: 'text-muted-foreground' },
  filings: { label: 'Filings', icon: FileText, color: 'text-muted-foreground' },
}

// Data source category grouping: only for secondary grouping in page display, doesn't affect data structure or backend
const DATASOURCE_CATEGORIES: { key: string; label: string; types: string[] }[] = [
  { key: 'quote_kline', label: 'Quotes & Bars', types: ['quote', 'kline'] },
  { key: 'news', label: 'News & Headlines', types: ['news', 'flash_news'] },
  { key: 'events', label: 'Events & Filings', types: ['events', 'filings'] },
  { key: 'fundamentals', label: 'Fundamentals & Ownership', types: ['fundamentals', 'dividend', 'holders'] },
  { key: 'chart', label: 'Charts', types: ['chart'] },
]

// Fallback: any type not covered by the categories above goes into "Other" (prevents future types from being hidden)
const CATEGORIZED_TYPES = new Set(DATASOURCE_CATEGORIES.flatMap(c => c.types))
const UNCATEGORIZED_TYPES = Object.keys(DATASOURCE_TYPES).filter(t => !CATEGORIZED_TYPES.has(t))
const ALL_DATASOURCE_CATEGORIES = UNCATEGORIZED_TYPES.length > 0
  ? [...DATASOURCE_CATEGORIES, { key: 'other', label: 'Other', types: UNCATEGORIZED_TYPES }]
  : DATASOURCE_CATEGORIES

interface CredentialFieldDef { key: string; label: string; placeholder: string; secret?: boolean; help?: string }

// provider → credential fields (frontend holds this UI metadata; add a row here when adding a new provider with credentials)
const PROVIDER_CREDENTIAL_FIELDS: Record<string, CredentialFieldDef[]> = {
  sec_edgar: [
    { key: 'contact_email', label: 'Contact Email', placeholder: 'you@example.com', help: 'SEC requires a descriptive User-Agent with a contact address' },
  ],
  yfinance: [
    { key: 'proxy', label: 'Proxy', placeholder: 'http://user:pass@host:port (optional)', help: 'HTTP(S) proxy for Yahoo Finance requests; leave blank to use HTTPS_PROXY' },
  ],
}

const emptyForm: DataSourceForm = {
  name: '',
  type: '',
  provider: '',
  config: {},
  priority: 0,
  supports_batch: false,
  test_symbols: [],
}

// Loading placeholder shaped like the category groups + source rows below,
// so the page never flashes a bare spinner while its first load is in flight.
function DataSourcesSkeleton() {
  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-3">
        <span className="skeleton block h-3 w-72" />
        <span className="skeleton h-8 w-32 rounded-full" />
      </div>
      <div className="space-y-6">
        {Array.from({ length: 2 }).map((_, gi) => (
          <div key={gi}>
            <div className="flex items-center gap-2 mb-3 py-1">
              <span className="skeleton h-3.5 w-3.5 rounded-sm" />
              <span className="skeleton h-3 w-32" />
              <span className="skeleton h-2.5 w-16" />
              <div className="flex-1 h-px bg-border ml-2" />
            </div>
            <div className="space-y-6">
              <section className="card p-4 md:p-6">
                <div className="flex items-center gap-2 mb-4">
                  <span className="skeleton h-4 w-4 rounded-sm" />
                  <span className="skeleton h-3.5 w-28" />
                  <span className="skeleton h-2.5 w-4 ml-auto" />
                </div>
                <div className="divide-y divide-border">
                  {Array.from({ length: 2 }).map((_, i) => (
                    <div key={i} className="flex items-center justify-between gap-3 py-3">
                      <div className="flex items-center gap-3 min-w-0 flex-1">
                        <span className="skeleton h-4 w-4 rounded-sm flex-shrink-0" />
                        <div className="min-w-0 flex-1">
                          <span className="skeleton block h-3 w-36 mb-1.5" />
                          <span className="skeleton block h-2.5 w-52" />
                        </div>
                      </div>
                      <div className="flex items-center gap-1 flex-shrink-0">
                        <span className="skeleton h-7 w-7 rounded-lg" />
                        <span className="skeleton h-7 w-7 rounded-lg" />
                        <span className="skeleton h-7 w-7 rounded-lg" />
                        <span className="skeleton h-5 w-9 rounded-full" />
                        <span className="skeleton h-7 w-7 rounded-lg" />
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function DataSourcesPage() {
  const [sources, setSources] = useState<DataSource[]>([])
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [form, setForm] = useState<DataSourceForm>(emptyForm)
  const [editId, setEditId] = useState<number | null>(null)
  const [testing, setTesting] = useState<number | null>(null)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  const [testResultOpen, setTestResultOpen] = useState(false)
  const [testSymbolsInput, setTestSymbolsInput] = useState('')
  const [secretVisible, setSecretVisible] = useState(false)
  const [resetting, setResetting] = useState(false)
  // 分类折叠态:key 不存在或为 false 视为展开(默认全部展开)
  const [collapsedCategories, setCollapsedCategories] = useState<Record<string, boolean>>({})
  const toggleCategory = (key: string) => setCollapsedCategories(prev => ({ ...prev, [key]: !prev[key] }))

  const { toast } = useToast()

  const load = async () => {
    try {
      const data = await fetchAPI<DataSource[]>('/datasources')
      setSources(data)
    } catch (e) {
      console.error(e)
      toast('Failed to load data sources', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const openDialog = (source?: DataSource, presetType?: string) => {
    if (source) {
      setForm({
        name: source.name,
        type: source.type,
        provider: source.provider,
        config: source.config || {},
        priority: source.priority,
        supports_batch: source.supports_batch || false,
        test_symbols: source.test_symbols || [],
      })
      setTestSymbolsInput((source.test_symbols || []).join(', '))
      setEditId(source.id)
    } else {
      setForm({ ...emptyForm, type: presetType || '' })
      setTestSymbolsInput('')
      setEditId(null)
    }
    setSecretVisible(false)
    setDialogOpen(true)
  }

  const saveSource = async () => {
    const testSymbols = testSymbolsInput.split(/[,，\s]+/).map(s => s.trim()).filter(Boolean)
    try {
      if (editId) {
        await fetchAPI(`/datasources/${editId}`, { method: 'PUT',
          body: JSON.stringify({ priority: form.priority, test_symbols: testSymbols, config: form.config || {} }) })
      } else {
        if (!form.name || !form.type || !form.provider) { toast('Name/type/provider are required', 'error'); return }
        await fetchAPI('/datasources', { method: 'POST', body: JSON.stringify({
          name: form.name, type: form.type, provider: form.provider,
          config: form.config || {}, priority: form.priority,
          supports_batch: form.supports_batch, test_symbols: testSymbols, enabled: true }) })
      }
      setDialogOpen(false); load(); toast(editId ? 'Settings saved' : 'Data source added', 'success')
    } catch (e) { toast(e instanceof Error ? e.message : 'Save failed', 'error') }
  }

  const toggleEnabled = async (source: DataSource) => {
    try {
      await fetchAPI(`/datasources/${source.id}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: !source.enabled }),
      })
      load()
    } catch {
      toast('Operation failed', 'error')
    }
  }

  const testSource = async (id: number) => {
    setTesting(id)
    try {
      const result = await fetchAPI<TestResult>(`/datasources/${id}/test`, { method: 'POST' })
      setTestResult(result)
      setTestResultOpen(true)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Test failed', 'error')
    } finally {
      setTesting(null)
    }
  }

  // Group sources by type
  const groupedSources = sources.reduce((acc, source) => {
    const type = source.type
    if (!acc[type]) acc[type] = []
    acc[type].push(source)
    return acc
  }, {} as Record<string, DataSource[]>)

  // 组内按当前顺序(API 已按 type,priority,id 排序)与相邻源交换优先级
  const moveSource = async (source: DataSource, dir: -1 | 1) => {
    const group = groupedSources[source.type] || []
    const idx = group.findIndex(s => s.id === source.id)
    const swap = group[idx + dir]
    if (!swap) return
    try {
      await Promise.all([
        fetchAPI(`/datasources/${source.id}`, { method: 'PUT', body: JSON.stringify({ priority: swap.priority }) }),
        fetchAPI(`/datasources/${swap.id}`, { method: 'PUT', body: JSON.stringify({ priority: source.priority }) }),
      ])
      load()
    } catch { toast('Failed to reorder', 'error') }
  }

  const resetToSeed = async () => {
    if (!window.confirm('This will delete orphaned rows with no matching data source, add back any missing default sources, and keep your custom config and credentials. Continue?')) return
    setResetting(true)
    try {
      const result = await resetDataSourcesToSeed()
      load()
      toast(`Cleaned up ${result.deleted.length} orphaned source(s), added back ${result.seeded_missing.length} default source(s)`, 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to restore defaults', 'error')
    } finally {
      setResetting(false)
    }
  }

  const deleteSource = async () => {
    if (!editId) return
    if (!window.confirm(`Delete data source "${form.name}"?`)) return
    try {
      await fetchAPI(`/datasources/${editId}`, { method: 'DELETE' })
      setDialogOpen(false); load(); toast('Deleted', 'success')
    } catch (e) { toast(e instanceof Error ? e.message : 'Delete failed', 'error') }
  }

  // 单个 type 的 section 渲染(结构与此前平铺版本完全一致,仅抽成函数以便按分类复用)
  const renderTypeSection = (type: string) => {
    const meta = DATASOURCE_TYPES[type as keyof typeof DATASOURCE_TYPES]
    if (!meta) return null
    const { label, icon: Icon, color } = meta
    return (
      <section key={type} className="card p-4 md:p-6">
        <div className="flex items-center gap-2 mb-4">
          <Icon className={`w-4 h-4 ${color}`} />
          <h3 className="section-title">{label}</h3>
          <span className="text-[11px] text-muted-foreground ml-auto">
            {groupedSources[type]?.length || 0}
          </span>
        </div>

        {(!groupedSources[type] || groupedSources[type].length === 0) ? (
          <EmptyState
            size="sm"
            icon={Database}
            title={`No ${label.toLowerCase()} sources yet`}
            description={`Add a ${label.toLowerCase()} source so agents relying on it can pull data.`}
            action={<Button variant="secondary" size="sm" onClick={() => openDialog(undefined, type)}><Plus className="w-3.5 h-3.5" />Add source</Button>}
          />
        ) : (
          <div className="divide-y divide-border">
            {groupedSources[type].map(source => (
                <div
                  key={source.id}
                  className="row-hover flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
                >
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <Database className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[13px] font-medium text-foreground">{source.name}</span>
                        {source.supports_batch && (
                          <span className="flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                            <Layers className="w-2.5 h-2.5" />
                            Batch
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                        <span className="text-[11px] text-muted-foreground font-mono">{source.provider}</span>
                        <span className="text-[11px] text-muted-foreground">Priority: {source.priority}</span>
                        {source.engine_attached ? (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-success/10 text-success">Connected to new engine</span>
                        ) : (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">Legacy path · pending migration</span>
                        )}
                        {source.is_orphan && (
                          <Badge variant="destructive" className="text-[10px] px-1.5 py-0.5">
                            <AlertTriangle className="w-2.5 h-2.5" />
                            No matching source · pending cleanup
                          </Badge>
                        )}
                        {source.engine_attached && source.health && source.health.success_rate != null && (
                          <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                            <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                              source.health.success_rate >= 0.95 ? 'bg-success'
                              : source.health.success_rate >= 0.8 ? 'bg-muted-foreground/50' : 'bg-destructive'}`} />
                            Success rate {Math.round(source.health.success_rate * 100)}%
                            <InfoTip
                              className="ml-0.5"
                              label="Share of test/live calls to this source that returned data successfully over its recent window, plus p50 latency and whether the last call errored."
                            />
                            {source.health.p50_latency_ms != null && ` · p50 ${source.health.p50_latency_ms}ms`}
                            {source.health.last_error ? ` · recent error` : ''}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => moveSource(source, -1)} title="Move up (higher priority)" aria-label="Move up (higher priority)">
                      <ChevronUp className="w-3.5 h-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => moveSource(source, 1)} title="Move down" aria-label="Move down">
                      <ChevronDown className="w-3.5 h-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => testSource(source.id)}
                      disabled={testing === source.id || !source.enabled}
                      title="Test connection"
                      aria-label="Test connection"
                    >
                      {testing === source.id ? (
                        <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                      ) : (
                        <Play className="w-3.5 h-3.5" />
                      )}
                    </Button>
                    <Switch checked={source.enabled} onCheckedChange={() => toggleEnabled(source)} />
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openDialog(source)} title="Settings" aria-label="Settings">
                      <Pencil className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </div>
            ))}
          </div>
        )}
      </section>
    )
  }

  if (loading) {
    return <DataSourcesSkeleton />
  }

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-3">
        <p className="helper-text">Free English-language sources for US and Canadian markets: Yahoo Finance, SEC EDGAR, Google News and publisher RSS feeds.</p>
        <button
          type="button"
          className="btn-secondary text-[12px] flex-shrink-0"
          onClick={resetToSeed}
          disabled={resetting}
        >
          {resetting ? (
            <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
          ) : (
            <RotateCcw className="w-3.5 h-3.5" />
          )}
          Restore Defaults
        </button>
      </div>

      <div className="space-y-6">
        {ALL_DATASOURCE_CATEGORIES.map(category => {
          const categoryCount = category.types.reduce((sum, t) => sum + (groupedSources[t]?.length || 0), 0)
          const isOpen = collapsedCategories[category.key] !== true
          return (
            <div key={category.key}>
              <button
                type="button"
                className="w-full flex items-center gap-2 mb-3 py-1 text-left group"
                onClick={() => toggleCategory(category.key)}
              >
                <ChevronRight className={`w-3.5 h-3.5 text-muted-foreground flex-shrink-0 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                <span className="text-[13px] font-semibold text-muted-foreground group-hover:text-foreground transition-colors">
                  {category.label}
                </span>
                <span className="text-[11px] text-muted-foreground/70">{categoryCount} source(s)</span>
                <div className="flex-1 h-px bg-border ml-2" />
              </button>
              {isOpen && (
                <div className="space-y-6 mb-6">
                  {category.types.map(type => renderTypeSection(type))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Edit Dialog - edit mode only allows changing config; add mode includes name/type/provider */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Data Source Settings - {form.name}</DialogTitle>
            <DialogDescription>{form.provider}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>Priority <span className="text-muted-foreground font-normal">(lower is higher)</span></Label>
                <Input
                  type="number"
                  value={form.priority}
                  onChange={e => setForm({ ...form, priority: parseInt(e.target.value) || 0 })}
                  min={0}
                />
              </div>
            </div>
            <div>
              <Label>Test Stock Codes <span className="text-muted-foreground font-normal">(comma-separated)</span></Label>
              <Input
                value={testSymbolsInput}
                onChange={e => setTestSymbolsInput(e.target.value)}
                placeholder="e.g. AAPL, SHOP.TO"
              />
            </div>

            {/* Credential config: dynamically render fields based on provider */}
            {(PROVIDER_CREDENTIAL_FIELDS[form.provider] || []).map(field => (
              <div key={field.key}>
                <Label>{field.label}
                  {field.help && <span className="text-muted-foreground font-normal ml-1">({field.help})</span>}
                </Label>
                <div className="relative">
                  <Input
                    type={field.secret && !secretVisible ? 'password' : 'text'}
                    value={(form.config?.[field.key] as string) || ''}
                    onChange={e => setForm({ ...form, config: { ...form.config, [field.key]: e.target.value } })}
                    placeholder={field.placeholder}
                    className={field.secret ? 'pr-10 font-mono' : 'font-mono'}
                  />
                  {field.secret && (
                    <Button type="button" variant="ghost" size="icon"
                      className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8"
                      onClick={() => setSecretVisible(!secretVisible)}
                      aria-label={secretVisible ? 'Hide value' : 'Show value'}>
                      {secretVisible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </Button>
                  )}
                </div>
              </div>
            ))}

            {/* Advanced: full JSON edit (read-only form, editable once expanded) */}
            {Object.keys(form.config || {}).length > 0 && (
              <details className="text-[12px]">
                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                  Advanced: view/edit full config JSON
                </summary>
                <textarea
                  className="mt-2 w-full font-mono text-[11px] p-2 border border-border rounded bg-background min-h-[100px]"
                  value={JSON.stringify(form.config || {}, null, 2)}
                  onChange={e => {
                    try {
                      const parsed = JSON.parse(e.target.value)
                      setForm({ ...form, config: parsed })
                    } catch {
                      // Don't update on parse failure; allow the user to keep typing
                    }
                  }}
                />
              </details>
            )}

            <div className="flex justify-between gap-2 pt-2">
              {editId ? (
                <Button variant="ghost" className="text-destructive hover:text-destructive/80" onClick={deleteSource}>
                  <Trash2 className="w-4 h-4 mr-1" />Delete
                </Button>
              ) : <span />}
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => setDialogOpen(false)}>Cancel</Button>
                <button type="button" className="btn-primary" onClick={saveSource}>{editId ? 'Save' : 'Add'}</button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Test Result Dialog */}
      <Dialog open={testResultOpen} onOpenChange={setTestResultOpen}>
        <DialogContent
          className="max-w-2xl w-[92vw] max-h-[85vh] overflow-y-auto scrollbar"
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {testResult?.test_passed ? (
                <Check className="w-5 h-5 text-success" />
              ) : (
                <X className="w-5 h-5 text-destructive" />
              )}
              Test Result - {testResult?.source_name}
            </DialogTitle>
            <DialogDescription>
              {testResult?.type_label} · {testResult?.provider}
              {testResult?.supports_batch && ' · supports batch'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 mt-2 pr-1">
            {/* Summary */}
            <div className="flex items-center gap-4 p-3 rounded-lg bg-accent/30">
              <div className="flex-1">
                <div className="text-[11px] text-muted-foreground">Status</div>
                <div className={`text-[13px] font-medium ${testResult?.test_passed ? 'text-success' : 'text-destructive'}`}>
                  {testResult?.test_passed ? 'Test succeeded' : 'Test failed'}
                </div>
              </div>
              <div className="flex-1">
                <div className="text-[11px] text-muted-foreground">Data Count</div>
                <div className="text-[13px] font-medium">{testResult?.count ?? 0}</div>
              </div>
              <div className="flex-1">
                <div className="text-[11px] text-muted-foreground">Duration</div>
                <div className="text-[13px] font-medium">{testResult?.duration_ms ?? 0} ms</div>
              </div>
            </div>

            {/* Error message */}
            {testResult?.error && (
              <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20">
                <div className="text-[11px] text-destructive font-medium mb-1">Error Message</div>
                <div className="text-[12px] text-destructive break-words whitespace-pre-wrap">{testResult.error}</div>
              </div>
            )}

            {/* Execution Logs */}
            {testResult?.logs && testResult.logs.length > 0 && (
              <div>
                <div className="group-title mb-2 flex items-center gap-1.5">
                  <Clock className="w-3.5 h-3.5" />
                  Execution Log
                </div>
                <div className="space-y-1.5 max-h-40 overflow-y-auto">
                  {testResult.logs.map((log, i) => (
                    <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30 text-[11px]">
                      <span className="text-muted-foreground font-mono flex-shrink-0">{log.timestamp}</span>
                      <span className={`px-1 py-0.5 rounded text-[10px] flex-shrink-0 ${
                        log.action === 'start' ? 'bg-muted text-muted-foreground' :
                        log.action === 'success' ? 'bg-success/10 text-success' :
                        'bg-destructive/10 text-destructive'
                      }`}>
                        {log.action === 'start' ? 'Start' : log.action === 'success' ? 'Success' : 'Failed'}
                      </span>
                      <span className="text-foreground flex-1">{log.message}</span>
                      {log.duration_ms > 0 && (
                        <span className="text-muted-foreground flex-shrink-0">{log.duration_ms}ms</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Data Preview */}
            {/* Chart type - show image outside scrollable area */}
            {testResult?.test_passed && testResult.source_type === 'chart' && (testResult.items as {image?: string})?.image && (
              <div>
                <div className="text-[12px] font-medium text-foreground mb-2">Data Preview</div>
                <div className="rounded-lg overflow-hidden border">
                  <img src={(testResult.items as {image: string}).image} alt="Rendered candlestick chart" className="w-full" />
                </div>
              </div>
            )}

            {/* Other data types - in scrollable container */}
            {testResult?.test_passed && testResult.items && testResult.source_type !== 'chart' && Array.isArray(testResult.items) && testResult.items.length > 0 && (
              <div>
                <div className="group-title mb-2">Data Preview</div>
                <div className="space-y-1.5 max-h-60 overflow-y-auto">

                  {/* News type */}
                  {testResult.source_type === 'news' && testResult.items.map((item, i) => {
                    const newsItem = item as { title?: string; time?: string }
                    return (
                      <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] text-foreground flex-1">{newsItem.title}</span>
                        <span className="text-[11px] text-muted-foreground flex-shrink-0">{newsItem.time}</span>
                      </div>
                    )
                  })}

                  {/* Events type */}
                  {testResult.source_type === 'events' && testResult.items.map((item, i) => {
                    const ev = item as { title?: string; time?: string; event_type?: string }
                    return (
                      <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30">
                        <span className="text-[11px] font-mono text-muted-foreground/80 flex-shrink-0">{ev.event_type || 'notice'}</span>
                        <span className="text-[12px] text-foreground flex-1">{ev.title}</span>
                        <span className="text-[11px] text-muted-foreground flex-shrink-0">{ev.time}</span>
                      </div>
                    )
                  })}

                  {/* Quote type */}
                  {testResult.source_type === 'quote' && testResult.items.map((item, i) => {
                    const quoteItem = item as { symbol?: string; name?: string; price?: number; change_pct?: number }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{quoteItem.name || quoteItem.symbol}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-[12px] font-mono tabular-nums">{quoteItem.price?.toFixed(2)}</span>
                          <span className={`text-[11px] font-medium tabular-nums ${
                            (quoteItem.change_pct ?? 0) > 0 ? 'text-stock-up' : (quoteItem.change_pct ?? 0) < 0 ? 'text-stock-down' : 'text-muted-foreground'
                          }`}>
                            {(quoteItem.change_pct ?? 0) > 0 ? '+' : ''}{quoteItem.change_pct?.toFixed(2)}%
                          </span>
                        </div>
                      </div>
                    )
                  })}

                  {/* Kline type */}
                  {testResult.source_type === 'kline' && testResult.items.map((item, i) => {
                    const klineItem = item as { symbol?: string; last_close?: number; trend?: string }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{klineItem.symbol}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-[12px] font-mono tabular-nums">{klineItem.last_close?.toFixed(2)}</span>
                          <span className="text-[11px] text-muted-foreground">{klineItem.trend}</span>
                        </div>
                      </div>
                    )
                  })}

                  {/* Flash news type */}
                  {testResult.source_type === 'flash_news' && testResult.items.map((item, i) => {
                    const flashItem = item as { title?: string; time?: string; symbols?: string[] }
                    return (
                      <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] text-foreground flex-1">
                          {flashItem.title}
                          {flashItem.symbols && flashItem.symbols.length > 0 && (
                            <span className="ml-2 text-[11px] text-muted-foreground">{flashItem.symbols.join(', ')}</span>
                          )}
                        </span>
                        <span className="text-[11px] text-muted-foreground flex-shrink-0">{flashItem.time}</span>
                      </div>
                    )
                  })}

                  {/* Fundamentals type */}
                  {testResult.source_type === 'fundamentals' && testResult.items.map((item, i) => {
                    const fundItem = item as { symbol?: string; name?: string; pe_ttm?: number; pb?: number; roe?: number }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{fundItem.name || fundItem.symbol}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-[11px] text-muted-foreground tabular-nums">PE {fundItem.pe_ttm?.toFixed(2) ?? '-'}</span>
                          <span className="text-[11px] text-muted-foreground tabular-nums">PB {fundItem.pb?.toFixed(2) ?? '-'}</span>
                          <span className="text-[11px] text-muted-foreground tabular-nums">ROE {fundItem.roe?.toFixed(2) ?? '-'}%</span>
                        </div>
                      </div>
                    )
                  })}

                  {/* Dividend type */}
                  {testResult.source_type === 'dividend' && testResult.items.map((item, i) => {
                    const divItem = item as { symbol?: string; ex_date?: string; dividend_per_share?: number }
                    return (
                      <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-accent/30">
                        <span className="text-[12px] font-medium text-foreground">{divItem.symbol}</span>
                        <div className="flex items-center gap-3">
                          <span className="text-[12px] font-mono tabular-nums">{divItem.dividend_per_share?.toFixed(4) ?? '-'} /share</span>
                          <span className="text-[11px] text-muted-foreground">{divItem.ex_date}</span>
                        </div>
                      </div>
                    )
                  })}

                  {/* Holders type: breakdown / institution / insider_tx rows */}
                  {testResult.source_type === 'holders' && testResult.items.map((item, i) => {
                    const holderItem = item as { symbol?: string; kind?: string; holder?: string; date?: string; shares?: number | null }
                    return (
                      <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30">
                        <span className="text-[11px] font-mono text-muted-foreground/80 flex-shrink-0">{holderItem.kind || 'holder'}</span>
                        <span className="text-[12px] text-foreground flex-1">
                          {holderItem.holder}
                          {holderItem.symbol && <span className="ml-2 text-[11px] text-muted-foreground">{holderItem.symbol}</span>}
                        </span>
                        {holderItem.shares != null && (
                          <span className="text-[12px] font-mono tabular-nums flex-shrink-0">{holderItem.shares.toLocaleString()}</span>
                        )}
                        <span className="text-[11px] text-muted-foreground flex-shrink-0">{holderItem.date}</span>
                      </div>
                    )
                  })}

                  {/* Filings type */}
                  {testResult.source_type === 'filings' && testResult.items.map((item, i) => {
                    const filingItem = item as { symbol?: string; form_type?: string; title?: string; filed_at?: string }
                    return (
                      <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-accent/30">
                        <span className="text-[11px] font-mono text-muted-foreground/80 flex-shrink-0">{filingItem.form_type || 'filing'}</span>
                        <span className="text-[12px] text-foreground flex-1">
                          {filingItem.title}
                          {filingItem.symbol && <span className="ml-2 text-[11px] text-muted-foreground">{filingItem.symbol}</span>}
                        </span>
                        <span className="text-[11px] text-muted-foreground flex-shrink-0">{filingItem.filed_at ? String(filingItem.filed_at).slice(0, 10) : ''}</span>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Test symbols info */}
            {testResult?.test_symbols && testResult.test_symbols.length > 0 && (
              <div className="text-[11px] text-muted-foreground">
                Test stocks: {testResult.test_symbols.join(', ')}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
