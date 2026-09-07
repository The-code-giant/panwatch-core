import { useState, useEffect } from 'react'
import { Play, Power, Clock, Cpu, Bot, Bell, Settings2, History as HistoryIcon, Search } from 'lucide-react'
import { fetchAPI, type AIService, type NotifyChannel } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Card, CardHeader, CardTitle, CardContent } from '@panwatch/base-ui/components/ui/card'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectLabel, SelectItem } from '@panwatch/base-ui/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { EmptyState } from '@panwatch/base-ui/components/ui/empty-state'
import { InfoTip } from '@panwatch/base-ui/components/ui/tooltip'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

interface AgentConfig {
  id: number
  name: string
  display_name: string
  description: string
  enabled: boolean
  schedule: string
  execution_mode: string
  ai_model_id: number | null
  notify_channel_ids: number[]
  config: Record<string, unknown>
}

interface StockAgentInfo {
  agent_name: string
  schedule: string
  ai_model_id: number | null
  notify_channel_ids: number[]
}

interface StockConfig {
  id: number
  symbol: string
  name: string
  market: string
  agents: StockAgentInfo[]
}

interface SchedulePreview {
  schedule: string
  timezone: string
  next_runs: string[]
}

interface AgentRun {
  id: number
  agent_name: string
  status: string
  result: string
  error: string
  duration_ms: number
  created_at: string
}

interface AgentsHealth {
  timezone: string
  summary: {
    next_24h_count: number
    recent_failed_count: number
  }
  agents: Array<{
    name: string
    display_name: string
    enabled: boolean
    schedule: string
    execution_mode: string
    next_runs: string[]
    last_run: null | {
      status: string
      created_at: string
      duration_ms: number
      error: string
    }
  }>
}

// 调度类型
type ScheduleType = 'daily' | 'weekdays' | 'interval' | 'cron'

interface ScheduleConfig {
  type: ScheduleType
  time?: string      // HH:MM 格式
  interval?: number  // 分钟数
  cron?: string      // 自定义 cron
}

// cron 转友好配置
function parseCronToConfig(cron: string): ScheduleConfig {
  if (!cron) return { type: 'daily', time: '15:30' }

  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return { type: 'cron', cron }

  const [minute, hour, , , dayOfWeek] = parts

  // 检测间隔模式 */N
  if (minute.startsWith('*/')) {
    const interval = parseInt(minute.slice(2))
    if (!isNaN(interval)) return { type: 'interval', interval }
  }

  // 检测每天或工作日
  const m = parseInt(minute)
  const h = parseInt(hour)
  if (!isNaN(m) && !isNaN(h)) {
    const time = `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`
    if (dayOfWeek === '1-5') return { type: 'weekdays', time }
    if (dayOfWeek === '*') return { type: 'daily', time }
  }

  return { type: 'cron', cron }
}

// 友好配置转 cron
function configToCron(config: ScheduleConfig): string {
  switch (config.type) {
    case 'daily': {
      const [h, m] = (config.time || '15:30').split(':')
      return `${parseInt(m)} ${parseInt(h)} * * *`
    }
    case 'weekdays': {
      const [h, m] = (config.time || '15:30').split(':')
      return `${parseInt(m)} ${parseInt(h)} * * 1-5`
    }
    case 'interval':
      return `*/${config.interval || 30} * * * *`
    case 'cron':
      return config.cron || '0 15 * * *'
    default:
      return '0 15 * * *'
  }
}

// 友好显示调度
function formatSchedule(cron: string): string {
  const config = parseCronToConfig(cron)
  switch (config.type) {
    case 'daily':
      return `Daily at ${config.time}`
    case 'weekdays':
      return `Weekdays at ${config.time}`
    case 'interval':
      return `Every ${config.interval} min`
    case 'cron':
      return cron
    default:
      return cron
  }
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentConfig[]>([])
  const [stocks, setStocks] = useState<StockConfig[]>([])
  const [services, setServices] = useState<AIService[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [loading, setLoading] = useState(true)
  const [triggering, setTriggering] = useState<string | null>(null)

  const [bindDialogAgent, setBindDialogAgent] = useState<AgentConfig | null>(null)
  const [bindKeyword, setBindKeyword] = useState('')
  const [bindFilter, setBindFilter] = useState<'all' | 'bound' | 'unbound'>('all')
  const [bindSavingStockIds, setBindSavingStockIds] = useState<Set<number>>(new Set())

  const [health, setHealth] = useState<AgentsHealth | null>(null)
  const [healthLoading, setHealthLoading] = useState(false)

  const [previews, setPreviews] = useState<Record<string, SchedulePreview | { error: string }>>({})

  // 调度编辑弹窗
  const [scheduleDialogAgent, setScheduleDialogAgent] = useState<AgentConfig | null>(null)
  // TradingAgents 深度配置弹窗(双模型 / 预算 / 超时 / 模拟盘对接)
  const [taConfigAgent, setTaConfigAgent] = useState<AgentConfig | null>(null)
  const [taConfigForm, setTaConfigForm] = useState<Record<string, unknown>>({})
  const [scheduleConfig, setScheduleConfig] = useState<ScheduleConfig>({ type: 'daily', time: '15:30' })
  const [schedulePreview, setSchedulePreview] = useState<SchedulePreview | { error: string } | null>(null)
  const [schedulePreviewLoading, setSchedulePreviewLoading] = useState(false)

  const [runsOpen, setRunsOpen] = useState<Record<string, boolean>>({})
  const [runsLoading, setRunsLoading] = useState<Record<string, boolean>>({})
  const [runs, setRuns] = useState<Record<string, AgentRun[] | { error: string }>>({})

  const { toast } = useToast()

  const formatPreviewTime = (iso: string, tz?: string): string => {
    try {
      const d = new Date(iso)
      if (isNaN(d.getTime())) return iso
      return d.toLocaleString('zh-CN', {
        timeZone: tz || undefined,
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    } catch {
      return iso
    }
  }

  const load = async () => {
    try {
      const [agentData, stockData, servicesData, channelData] = await Promise.all([
        fetchAPI<AgentConfig[]>('/agents'),
        fetchAPI<StockConfig[]>('/stocks'),
        fetchAPI<AIService[]>('/providers/services'),
        fetchAPI<NotifyChannel[]>('/channels'),
      ])
      setAgents(agentData)
      setStocks(stockData)
      setServices(servicesData)
      setChannels(channelData)

      // 预加载未来触发时间（避免“工作日/周末”语义误解）
      const previewPairs = await Promise.all(agentData.map(async a => {
        if (!a.schedule) return [a.name, { schedule: '', timezone: '', next_runs: [] }] as const
        try {
          const p = await fetchAPI<SchedulePreview>(`/agents/${a.name}/schedule/preview?count=3`)
          return [a.name, p] as const
        } catch (e) {
          const msg = e instanceof Error ? e.message : 'Preview failed'
          return [a.name, { error: msg }] as const
        }
      }))
      setPreviews(Object.fromEntries(previewPairs))
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const loadHealth = async () => {
    setHealthLoading(true)
    try {
      const h = await fetchAPI<AgentsHealth>('/agents/health')
      setHealth(h)
    } catch (e) {
      console.error(e)
      setHealth(null)
    } finally {
      setHealthLoading(false)
    }
  }

  useEffect(() => { load(); loadHealth() }, [])

  // 调度编辑弹窗：实时预览未来触发时间（防止工作日/周末语义误解）
  useEffect(() => {
    if (!scheduleDialogAgent) {
      setSchedulePreview(null)
      return
    }

    const cron = configToCron(scheduleConfig)
    const timer = setTimeout(async () => {
      setSchedulePreviewLoading(true)
      try {
        const p = await fetchAPI<SchedulePreview>(`/agents/schedule/preview?schedule=${encodeURIComponent(cron)}&count=5`)
        setSchedulePreview(p)
      } catch (e) {
        const msg = e instanceof Error ? e.message : 'Preview failed'
        setSchedulePreview({ error: msg })
      } finally {
        setSchedulePreviewLoading(false)
      }
    }, 350)

    return () => clearTimeout(timer)
  }, [scheduleDialogAgent, scheduleConfig])

  const toggleAgent = async (agent: AgentConfig) => {
    await fetchAPI(`/agents/${agent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ enabled: !agent.enabled }),
    })
    load()
  }

  const openBindDialog = (agent: AgentConfig) => {
    setBindDialogAgent(agent)
    setBindKeyword('')
    setBindFilter('all')
  }

  const hasAgentBound = (stock: StockConfig, agentName: string) =>
    (stock.agents || []).some(a => a.agent_name === agentName)

  const getAgentBoundCount = (agentName: string) =>
    stocks.filter(s => hasAgentBound(s, agentName)).length

  const getBoundStocks = (agentName: string) =>
    stocks.filter(s => hasAgentBound(s, agentName))

  const filteredBindStocks = stocks
    .filter(s => {
      const q = bindKeyword.trim().toLowerCase()
      if (!q) return true
      return s.symbol.toLowerCase().includes(q) || s.name.toLowerCase().includes(q)
    })
    .filter(s => {
      if (!bindDialogAgent) return true
      const bound = hasAgentBound(s, bindDialogAgent.name)
      if (bindFilter === 'bound') return bound
      if (bindFilter === 'unbound') return !bound
      return true
    })

  const updateBindSaving = (stockId: number, saving: boolean) => {
    setBindSavingStockIds(prev => {
      const next = new Set(prev)
      if (saving) next.add(stockId)
      else next.delete(stockId)
      return next
    })
  }

  const buildNextAgents = (stock: StockConfig, agentName: string, shouldBind: boolean) => {
    const current = stock.agents || []
    const exists = current.some(a => a.agent_name === agentName)
    if (shouldBind && !exists) {
      return [...current, { agent_name: agentName, schedule: '', ai_model_id: null, notify_channel_ids: [] }]
    }
    if (!shouldBind && exists) {
      return current.filter(a => a.agent_name !== agentName)
    }
    return current
  }

  const toggleStockBindingForAgent = async (stock: StockConfig, agentName: string) => {
    if (!agentName) return
    if (bindSavingStockIds.has(stock.id)) return
    updateBindSaving(stock.id, true)
    try {
      const exists = (stock.agents || []).some(a => a.agent_name === agentName)
      const nextAgents = buildNextAgents(stock, agentName, !exists)

      const updated = await fetchAPI<StockConfig>(`/stocks/${stock.id}/agents`, {
        method: 'PUT',
        // 保留该股票已有 Agent 的 schedule/模型/通知覆盖，仅切换当前 Agent 绑定状态
        body: JSON.stringify({
          agents: nextAgents.map(a => ({
            agent_name: a.agent_name,
            schedule: a.schedule || '',
            ai_model_id: a.ai_model_id ?? null,
            notify_channel_ids: a.notify_channel_ids || [],
          })),
        }),
      })
      setStocks(prev => prev.map(s => (s.id === stock.id ? updated : s)))
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to toggle binding', 'error')
    } finally {
      updateBindSaving(stock.id, false)
    }
  }

  const applyBulkBindingForAgent = async (shouldBind: boolean) => {
    if (!bindDialogAgent) return
    const target = filteredBindStocks.filter(s => hasAgentBound(s, bindDialogAgent.name) !== shouldBind)
    if (target.length === 0) {
      toast(shouldBind ? 'All currently filtered stocks are already bound' : 'All currently filtered stocks are already unbound', 'info')
      return
    }

    setBindSavingStockIds(new Set(target.map(s => s.id)))
    try {
      const tasks = target.map(async (stock) => {
        const nextAgents = buildNextAgents(stock, bindDialogAgent.name, shouldBind)
        const updated = await fetchAPI<StockConfig>(`/stocks/${stock.id}/agents`, {
          method: 'PUT',
          body: JSON.stringify({
            agents: nextAgents.map(a => ({
              agent_name: a.agent_name,
              schedule: a.schedule || '',
              ai_model_id: a.ai_model_id ?? null,
              notify_channel_ids: a.notify_channel_ids || [],
            })),
          }),
        })
        return updated
      })
      const updatedList = await Promise.all(tasks)
      const map = new Map(updatedList.map(s => [s.id, s]))
      setStocks(prev => prev.map(s => map.get(s.id) || s))
      toast(shouldBind ? `Bound ${updatedList.length} stock(s)` : `Unbound ${updatedList.length} stock(s)`, 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Bulk operation failed', 'error')
    } finally {
      setBindSavingStockIds(new Set())
    }
  }

  const triggerAgent = async (name: string) => {
    setTriggering(name)
    try {
      const res = await fetchAPI<{ queued?: boolean; message?: string }>(`/agents/${name}/trigger`, { method: 'POST' })
      toast(res?.queued ? 'Agent submitted for background execution' : (res?.message || 'Agent triggered'), 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Trigger failed', 'error')
    } finally {
      setTriggering(null)
    }
  }

  const toggleRuns = async (agentName: string) => {
    const nextOpen = !runsOpen[agentName]
    setRunsOpen(prev => ({ ...prev, [agentName]: nextOpen }))
    if (!nextOpen) return

    if (runs[agentName] || runsLoading[agentName]) return
    setRunsLoading(prev => ({ ...prev, [agentName]: true }))
    try {
      const data = await fetchAPI<AgentRun[]>(`/agents/${agentName}/history?limit=5`)
      setRuns(prev => ({ ...prev, [agentName]: data }))
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to load'
      setRuns(prev => ({ ...prev, [agentName]: { error: msg } }))
    } finally {
      setRunsLoading(prev => ({ ...prev, [agentName]: false }))
    }
  }

  const updateAgentModel = async (agent: AgentConfig, modelId: number | null) => {
    await fetchAPI(`/agents/${agent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ ai_model_id: modelId }),
    })
    load()
  }

  const toggleAgentChannel = async (agent: AgentConfig, channelId: number) => {
    const current = agent.notify_channel_ids || []
    const newIds = current.includes(channelId)
      ? current.filter(id => id !== channelId)
      : [...current, channelId]
    await fetchAPI(`/agents/${agent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ notify_channel_ids: newIds }),
    })
    load()
  }

  // 当 taConfigAgent 切换时,把它的 config 拷到表单
  useEffect(() => {
    if (taConfigAgent) {
      setTaConfigForm({ ...(taConfigAgent.config || {}) })
    }
  }, [taConfigAgent])

  const saveTaConfig = async () => {
    if (!taConfigAgent) return
    try {
      await fetchAPI(`/agents/${taConfigAgent.name}`, {
        method: 'PUT',
        body: JSON.stringify({ config: taConfigForm }),
      })
      toast('TradingAgents config saved', 'success')
      setTaConfigAgent(null)
      load()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Save failed', 'error')
    }
  }

  const openScheduleDialog = (agent: AgentConfig) => {
    setScheduleDialogAgent(agent)
    setScheduleConfig(parseCronToConfig(agent.schedule))
  }

  const saveSchedule = async () => {
    if (!scheduleDialogAgent) return
    const cron = configToCron(scheduleConfig)
    await fetchAPI(`/agents/${scheduleDialogAgent.name}`, {
      method: 'PUT',
      body: JSON.stringify({ schedule: cron }),
    })
    setScheduleDialogAgent(null)
    load()
    toast('Schedule updated', 'success')
  }

  if (loading) {
    return (
      <div>
        <div className="card mb-4 p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="skeleton h-4 w-32 rounded" />
            <span className="skeleton h-7 w-16 rounded-md" />
          </div>
          <span className="skeleton mt-3 block h-3 w-3/4" />
        </div>
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="card p-4 md:p-6">
              <div className="flex items-center gap-3">
                <span className="skeleton h-2.5 w-2.5 rounded-full" />
                <span className="skeleton h-4" style={{ width: `${140 + (i % 2) * 40}px` }} />
                <span className="skeleton h-4 w-14 rounded-full" />
              </div>
              <span className="skeleton mt-2.5 ml-[22px] block h-3" style={{ width: `${50 + (i % 3) * 10}%` }} />
              <span className="skeleton mt-3.5 ml-[22px] block h-6 w-36 rounded-md" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div>
      {/* Scheduler Health */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle>Scheduler Health</CardTitle>
          <button type="button" className="btn-mini" onClick={loadHealth} disabled={healthLoading}>
            {healthLoading ? (
              <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
            ) : (
              'Refresh'
            )}
          </button>
        </CardHeader>
        <CardContent>
          {health ? (
            <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
              <span>Timezone: <span className="font-mono text-foreground/90">{health.timezone}</span></span>
              <span className="opacity-50">|</span>
              <span>Triggering in next 24h: <span className="font-mono text-foreground/90">{health.summary.next_24h_count}</span></span>
              <span className="opacity-50">|</span>
              <span>Recent failures: <span className={`font-mono ${health.summary.recent_failed_count > 0 ? 'text-destructive' : 'text-foreground/90'}`}>{health.summary.recent_failed_count}</span></span>
            </div>
          ) : (
            <div className="text-[12px] text-muted-foreground">-</div>
          )}
        </CardContent>
      </Card>

      {agents.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={Bot}
            title="No agents yet"
            description="Agents register automatically once the backend service starts. Restart the backend if you expect agents here."
          />
        </div>
      ) : (
        <div className="space-y-4">
          {agents.map(agent => {
            const modeLabel = agent.execution_mode === 'single' ? 'Per-stock' : 'Batch'
            const preview = previews[agent.name]
            const boundStocks = getBoundStocks(agent.name)
            const boundSummary = boundStocks.length > 0
              ? `${boundStocks.slice(0, 3).map(s => s.name || s.symbol).join(', ')}${boundStocks.length > 3 ? ', ...more' : ''}`
              : 'No stocks bound'
            return (
              <div key={agent.name} className="card p-4 md:p-6">
                <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4 sm:gap-6">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${agent.enabled ? 'bg-success' : 'bg-border'}`} />
                      <h3 className="section-title">{agent.display_name}</h3>
                      <span className="chip-neutral">{modeLabel}</span>
                      <InfoTip label={agent.execution_mode === 'single' ? 'Per-stock: this agent runs once for each bound stock on its schedule.' : 'Batch: this agent runs once per trigger and covers all bound stocks together.'} />
                      <button
                        type="button"
                        onClick={() => openBindDialog(agent)}
                        className={`max-w-[320px] truncate rounded-full px-2.5 py-0.5 text-[11px] font-semibold transition-colors duration-150 ${
                          boundStocks.length > 0
                            ? 'bg-muted text-foreground hover:bg-muted/70'
                            : 'bg-muted text-muted-foreground hover:bg-muted/70'
                        }`}
                        title={`${boundSummary} (${getAgentBoundCount(agent.name)} / ${stocks.length} bound)`}
                      >
                        {boundSummary}
                      </button>
                    </div>
                    <p className="text-[13px] text-muted-foreground mt-2.5 ml-[22px] leading-relaxed">{agent.description}</p>

                    {/* Execution schedule - click to edit */}
                    <div className="flex items-center gap-2.5 mt-3.5 ml-[22px] flex-wrap">
                      <button
                        onClick={() => openScheduleDialog(agent)}
                        className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-accent/50 hover:bg-accent transition-colors"
                      >
                        <Clock className="w-3.5 h-3.5 text-muted-foreground" />
                        <span className="text-[12px] text-foreground">{formatSchedule(agent.schedule)}</span>
                        <Settings2 className="w-3 h-3 text-muted-foreground/50" />
                      </button>
                      {agent.name === 'tradingagents' && (
                        <button
                          onClick={() => setTaConfigAgent(agent)}
                          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-muted hover:bg-muted/70 transition-colors text-foreground"
                          title="Edit advanced TradingAgents config: dual model tiers/budget/timeout/paper trading, etc."
                        >
                          <Settings2 className="w-3.5 h-3.5" />
                          <span className="text-[12px]">Advanced Config</span>
                        </button>
                      )}
                    </div>

                    {/* Upcoming trigger times (by schedule timezone) */}
                    {'error' in (preview || {}) ? (
                      <div className="mt-2 ml-[22px] text-[11px] text-muted-foreground">
                        Upcoming trigger times: {(preview as { error: string }).error}
                      </div>
                    ) : (preview as SchedulePreview | undefined)?.next_runs?.length ? (
                      <div className="mt-2 ml-[22px] flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                        <span className="opacity-80">Next 3 (auto):</span>
                        {(preview as SchedulePreview).next_runs.map((t, i) => (
                          <span
                            key={i}
                            className="chip-neutral font-mono"
                            title={t}
                          >
                            {formatPreviewTime(t, (preview as SchedulePreview).timezone)}
                          </span>
                        ))}
                        {(preview as SchedulePreview).timezone ? (
                          <span className="opacity-60">({(preview as SchedulePreview).timezone})</span>
                        ) : null}
                      </div>
                    ) : null}

                    <div className="mt-4 ml-[22px] space-y-3">
                      {/* AI Model select */}
                      <div className="flex items-center gap-2">
                        <Cpu className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                        <Select
                          value={agent.ai_model_id?.toString() ?? '__default__'}
                          onValueChange={val => updateAgentModel(agent, val === '__default__' ? null : parseInt(val))}
                        >
                          <SelectTrigger className="h-7 text-[12px] w-auto min-w-[140px] px-2.5 bg-accent/50 border-border/50">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="__default__">System default</SelectItem>
                            {services.map(svc => (
                              <SelectGroup key={svc.id}>
                                <SelectLabel>{svc.name}</SelectLabel>
                                {svc.models.map(m => (
                                  <SelectItem key={m.id} value={m.id.toString()}>
                                    {m.name}{m.name !== m.model ? ` (${m.model})` : ''}
                                  </SelectItem>
                                ))}
                              </SelectGroup>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>

                      {/* Notify Channel multi-select */}
                      {channels.length > 0 && (
                        <div className="flex items-center gap-2 flex-wrap">
                          <Bell className="w-3.5 h-3.5 text-muted-foreground flex-shrink-0" />
                          {channels.map(ch => {
                            const isSelected = (agent.notify_channel_ids || []).includes(ch.id)
                            return (
                              <button
                                key={ch.id}
                                onClick={() => toggleAgentChannel(agent, ch.id)}
                                className={`text-[11px] px-2 py-0.5 rounded-md border transition-colors ${
                                  isSelected
                                    ? 'bg-primary border-primary text-primary-foreground font-medium'
                                    : 'bg-accent/30 border-border/50 text-muted-foreground hover:border-primary/30'
                                }`}
                              >
                                {ch.name}
                              </button>
                            )
                          })}
                          {(agent.notify_channel_ids || []).length === 0 && (
                            <span className="text-[11px] text-muted-foreground">System default</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0 ml-[22px] sm:ml-0">
                    <button
                      type="button"
                      className="btn-mini"
                      onClick={() => triggerAgent(agent.name)}
                      disabled={!agent.enabled || triggering === agent.name}
                    >
                      {triggering === agent.name ? (
                        <span className="w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                      ) : (
                        <Play className="w-3.5 h-3.5" />
                      )}
                      <span className="hidden sm:inline">{triggering === agent.name ? 'Running' : 'Trigger'}</span>
                    </button>
                    <button
                      type="button"
                      className="btn-mini"
                      onClick={() => toggleRuns(agent.name)}
                    >
                      Recent Runs
                    </button>
                    <button
                      type="button"
                      className={agent.enabled ? 'btn-mini text-destructive' : 'btn-mini-solid'}
                      onClick={() => toggleAgent(agent)}
                    >
                      <Power className="w-3.5 h-3.5" />
                      <span className="hidden sm:inline">{agent.enabled ? 'Disable' : 'Enable'}</span>
                    </button>
                  </div>
                </div>

                {runsOpen[agent.name] && (
                  <div className="mt-4 ml-[22px] sm:ml-0 pt-3 border-t border-border">
                    <div className="flex items-center justify-between">
                      <div className="group-title">Last 5 Runs</div>
                      {runsLoading[agent.name] && (
                        <span className="w-3.5 h-3.5 border-2 border-border border-t-foreground rounded-full animate-spin" />
                      )}
                    </div>
                    {(() => {
                      const data = runs[agent.name]
                      if (!data) {
                        return (
                          <div className="mt-2 space-y-2">
                            {Array.from({ length: 2 }).map((_, i) => (
                              <div key={i} className="flex items-center justify-between gap-3">
                                <span className="skeleton h-3" style={{ width: `${120 + (i % 2) * 30}px` }} />
                                <span className="skeleton h-3 w-10" />
                              </div>
                            ))}
                          </div>
                        )
                      }
                      if ('error' in data) {
                        return <div className="mt-2 text-[11px] text-muted-foreground">{data.error}</div>
                      }
                      if (data.length === 0) {
                        return (
                          <EmptyState
                            size="sm"
                            icon={HistoryIcon}
                            title="No runs yet"
                            description="This agent hasn't executed. Trigger it manually above, or wait for its schedule."
                          />
                        )
                      }
                      return (
                        <div className="mt-2 space-y-2">
                          {data.map(r => (
                            <div key={r.id} className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="text-[11px] text-muted-foreground">
                                  <span className={`inline-block w-1.5 h-1.5 rounded-full mr-2 ${r.status === 'failed' ? 'bg-destructive' : 'bg-success'}`} />
                                  <span className="font-mono">{r.created_at}</span>
                                  <span className="ml-2 font-mono opacity-70">{Math.round((r.duration_ms || 0) / 1000)}s</span>
                                </div>
                                {r.error ? (
                                  <div className="mt-0.5 text-[11px] text-destructive break-words">{r.error}</div>
                                ) : null}
                              </div>
                              <div className="text-[10px] text-muted-foreground/70 font-mono">{r.status}</div>
                            </div>
                          ))}
                        </div>
                      )
                    })()}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Schedule settings dialog */}
      <Dialog open={!!scheduleDialogAgent} onOpenChange={open => !open && setScheduleDialogAgent(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Set Execution Schedule</DialogTitle>
            <DialogDescription>{scheduleDialogAgent?.display_name}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>Schedule Type</Label>
              <Select
                value={scheduleConfig.type}
                onValueChange={val => setScheduleConfig({ ...scheduleConfig, type: val as ScheduleType })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="daily">Daily at a fixed time</SelectItem>
                  <SelectItem value="weekdays">Weekdays at a fixed time</SelectItem>
                  <SelectItem value="interval">Fixed interval</SelectItem>
                  <SelectItem value="cron">Custom Cron</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {(scheduleConfig.type === 'daily' || scheduleConfig.type === 'weekdays') && (
              <div>
                <Label>Execution Time</Label>
                <Input
                  type="time"
                  value={scheduleConfig.time || '15:30'}
                  onChange={e => setScheduleConfig({ ...scheduleConfig, time: e.target.value })}
                />
                <p className="text-[11px] text-muted-foreground mt-1">
                  Runs at this time {scheduleConfig.type === 'weekdays' ? 'Monday through Friday' : 'every day'}
                </p>
              </div>
            )}

            {scheduleConfig.type === 'interval' && (
              <div>
                <Label>Execution Interval (minutes)</Label>
                <Select
                  value={(scheduleConfig.interval || 30).toString()}
                  onValueChange={val => setScheduleConfig({ ...scheduleConfig, interval: parseInt(val) })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="5">Every 5 min</SelectItem>
                    <SelectItem value="10">Every 10 min</SelectItem>
                    <SelectItem value="15">Every 15 min</SelectItem>
                    <SelectItem value="30">Every 30 min</SelectItem>
                    <SelectItem value="60">Every hour</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            {scheduleConfig.type === 'cron' && (
              <div>
                <Label>Cron Expression</Label>
                <Input
                  value={scheduleConfig.cron || ''}
                  onChange={e => setScheduleConfig({ ...scheduleConfig, cron: e.target.value })}
                  placeholder="0 15 * * 1-5"
                  className="font-mono"
                />
                <p className="text-[11px] text-muted-foreground mt-1">
                  Format: minute hour day month weekday (e.g. 0 15 * * 1-5 means weekdays at 15:00)
                </p>
              </div>
            )}

            {/* Preview */}
            <div className="rounded-lg border border-border/50 bg-accent/20 p-3">
              <div className="flex items-center justify-between">
                <div className="group-title">Upcoming Trigger Time Preview</div>
                {schedulePreviewLoading && (
                  <span className="w-3.5 h-3.5 border-2 border-border border-t-foreground rounded-full animate-spin" />
                )}
              </div>
              {'error' in (schedulePreview || {}) ? (
                <div className="mt-2 text-[11px] text-muted-foreground">
                  {(schedulePreview as { error: string }).error}
                </div>
              ) : (schedulePreview as SchedulePreview | null)?.next_runs?.length ? (
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                  {(schedulePreview as SchedulePreview).next_runs.map((t, i) => (
                    <span
                      key={i}
                      className="chip-neutral font-mono"
                      title={t}
                    >
                      {formatPreviewTime(t, (schedulePreview as SchedulePreview).timezone)}
                    </span>
                  ))}
                  {(schedulePreview as SchedulePreview | null)?.timezone ? (
                    <span className="opacity-60">({(schedulePreview as SchedulePreview).timezone})</span>
                  ) : null}
                </div>
              ) : (
                <div className="mt-2 text-[11px] text-muted-foreground">-</div>
              )}
              <div className="mt-2 text-[11px] text-muted-foreground/70 font-mono">
                schedule: {configToCron(scheduleConfig)}
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setScheduleDialogAgent(null)}>Cancel</Button>
              <button type="button" className="btn-primary" onClick={saveSchedule}>Save</button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={!!bindDialogAgent} onOpenChange={(open) => !open && setBindDialogAgent(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {bindDialogAgent ? `${bindDialogAgent.display_name} Stock Binding` : 'Stock Binding'}
            </DialogTitle>
            <DialogDescription>Click to toggle bound/unbound; this won't overwrite this stock's other Agent-specific settings</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <div>
              <Label>Filter Stocks</Label>
              <Input
                value={bindKeyword}
                onChange={(e) => setBindKeyword(e.target.value)}
                placeholder="Filter by code or name"
              />
            </div>

            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-1.5">
                <button type="button" className={bindFilter === 'all' ? 'btn-mini-solid' : 'btn-mini'} onClick={() => setBindFilter('all')}>All</button>
                <button type="button" className={bindFilter === 'bound' ? 'btn-mini-solid' : 'btn-mini'} onClick={() => setBindFilter('bound')}>Bound</button>
                <button type="button" className={bindFilter === 'unbound' ? 'btn-mini-solid' : 'btn-mini'} onClick={() => setBindFilter('unbound')}>Unbound</button>
              </div>
              <div className="flex items-center gap-1.5">
                <button type="button" className="btn-mini" disabled={!bindDialogAgent || bindSavingStockIds.size > 0} onClick={() => applyBulkBindingForAgent(true)}>Bind All</button>
                <button type="button" className="btn-mini" disabled={!bindDialogAgent || bindSavingStockIds.size > 0} onClick={() => applyBulkBindingForAgent(false)}>Unbind All</button>
              </div>
            </div>

            <div className="max-h-[40vh] overflow-y-auto rounded border border-border/50 p-3">
              {filteredBindStocks.length === 0 ? (
                stocks.length === 0 ? (
                  <EmptyState
                    size="sm"
                    icon={Bot}
                    title="No stocks configured"
                    description="Add a stock on the Portfolio page, then come back here to bind it to this agent."
                  />
                ) : (
                  <EmptyState
                    size="sm"
                    icon={Search}
                    title="No matches"
                    description="No stock matches the current keyword and filter."
                    action={<button type="button" className="btn-mini" onClick={() => { setBindKeyword(''); setBindFilter('all') }}>Clear filters</button>}
                  />
                )
              ) : (
                <div className="flex flex-wrap gap-2">
                  {filteredBindStocks.map((s) => {
                    const bound = bindDialogAgent ? hasAgentBound(s, bindDialogAgent.name) : false
                    const saving = bindSavingStockIds.has(s.id)
                    return (
                      <button
                        key={s.id}
                        type="button"
                        disabled={!bindDialogAgent || saving}
                        onClick={() => bindDialogAgent && toggleStockBindingForAgent(s, bindDialogAgent.name)}
                        className={`h-8 px-3 rounded-full text-[12px] border transition-colors disabled:opacity-60 ${
                          bound
                            ? 'bg-primary border-primary text-primary-foreground hover:bg-primary/90'
                            : 'bg-accent/30 border-border/60 text-muted-foreground hover:border-primary/30'
                        }`}
                        title={`${s.name} (${s.symbol})`}
                      >
                        {saving ? 'Processing...' : `${s.name || s.symbol}`}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={() => setBindDialogAgent(null)}>Close</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* TradingAgents advanced config dialog */}
      <Dialog open={!!taConfigAgent} onOpenChange={open => !open && setTaConfigAgent(null)}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>TradingAgents Advanced Config</DialogTitle>
            <DialogDescription>
              Dual model tiers / monthly budget / timeout / paper trading integration. Full docs at
              <code className="ml-1 text-[11px] bg-accent/40 px-1">.docs/tradingagents/USER_GUIDE.md § 12</code>
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-5 mt-2 text-[13px]">
            {/* Model tiers (optional) — chosen from the same AI Service as the Agent's default model */}
            {(() => {
              // The default model selected on the Agent card determines the service; deep/quick can only be chosen from the same service
              const defaultModelId = taConfigAgent?.ai_model_id ?? null
              const agentService = defaultModelId
                ? services.find(s => s.models.some(m => m.id === defaultModelId))
                : null
              const defaultModel = defaultModelId
                ? agentService?.models.find(m => m.id === defaultModelId)
                : null
              // Candidate model list: limited to that service if one is set, otherwise all models across all services
              const candidateModels = agentService
                ? agentService.models
                : services.flatMap(s => s.models)

              return (
                <section>
                  <div className="group-title mb-2">Model Tiers (optional)</div>

                  {/* Show where the Agent's default model comes from, so the user knows the service context */}
                  <div className="rounded-md bg-accent/30 border border-border/40 p-2 text-[11px] text-muted-foreground mb-3">
                    {defaultModel && agentService ? (
                      <>Current Agent default model: <span className="text-foreground font-medium">{defaultModel.model}</span>
                       <span className="opacity-70"> (from {agentService.name})</span></>
                    ) : (
                      <>This Agent currently uses the system default AI service (selected in "Model" on the Agent card).
                       We recommend picking a Service first before configuring tiers.</>
                    )}
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <Label className="text-[12px]">
                        Deep Thinking Model <span className="text-muted-foreground/70 font-normal">(debate/risk/PM)</span>
                      </Label>
                      <Select
                        value={(taConfigForm.deep_model as string) || '__default__'}
                        onValueChange={val => setTaConfigForm({ ...taConfigForm, deep_model: val === '__default__' ? '' : val })}
                      >
                        <SelectTrigger className="h-9 text-[12px]">
                          <SelectValue placeholder="Use Agent default" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__default__">Use Agent default</SelectItem>
                          {candidateModels.map(m => (
                            <SelectItem key={`deep-${m.id}`} value={m.model}>
                              {m.model}{m.name !== m.model ? ` · ${m.name}` : ''}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <Label className="text-[12px]">
                        Quick Thinking Model <span className="text-muted-foreground/70 font-normal">(analysts/tools)</span>
                      </Label>
                      <Select
                        value={(taConfigForm.quick_model as string) || '__default__'}
                        onValueChange={val => setTaConfigForm({ ...taConfigForm, quick_model: val === '__default__' ? '' : val })}
                      >
                        <SelectTrigger className="h-9 text-[12px]">
                          <SelectValue placeholder="= Deep model" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__default__">= Deep model</SelectItem>
                          {candidateModels.map(m => (
                            <SelectItem key={`quick-${m.id}`} value={m.model}>
                              {m.model}{m.name !== m.model ? ` · ${m.name}` : ''}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-2 space-y-0.5">
                    <div>• Leave blank = use the Agent's default model, no tiering</div>
                    <div>• Both tiers must be under the same AI service (TradingAgents shares one backend_url)</div>
                    <div className="text-destructive font-medium">⚠️ Don't select a reasoning model (e.g. deepseek-r1 / o1) - they output garbled text in the langchain agent loop. Use chat-type models: claude-sonnet / deepseek-chat / gpt-4o-mini</div>
                  </div>
                </section>
              )
            })()}

            {/* Budget & policy */}
            <section>
              <div className="group-title mb-2">Budget & Policy</div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-[12px]">Monthly Budget (USD)</Label>
                  <Input
                    type="number"
                    step="0.5"
                    value={String(taConfigForm.monthly_budget_usd ?? 10)}
                    onChange={e => setTaConfigForm({ ...taConfigForm, monthly_budget_usd: parseFloat(e.target.value) || 0 })}
                  />
                </div>
                <div>
                  <Label className="text-[12px]">Over-Budget Behavior</Label>
                  <select
                    className="input-field"
                    value={(taConfigForm.over_budget_action as string) || 'reject'}
                    onChange={e => setTaConfigForm({ ...taConfigForm, over_budget_action: e.target.value })}
                  >
                    <option value="reject">Reject new triggers</option>
                    <option value="warn">Warn but continue</option>
                    <option value="continue">No warning or block</option>
                  </select>
                </div>
                <div>
                  <Label className="text-[12px]">Debate Rounds</Label>
                  <Input
                    type="number"
                    min={1}
                    max={5}
                    value={String(taConfigForm.debate_rounds ?? 1)}
                    onChange={e => setTaConfigForm({ ...taConfigForm, debate_rounds: parseInt(e.target.value) || 1 })}
                  />
                </div>
                <div>
                  <Label className="text-[12px]">Timeout (minutes)</Label>
                  <Input
                    type="number"
                    min={1}
                    max={60}
                    value={String(taConfigForm.timeout_minutes ?? 15)}
                    onChange={e => setTaConfigForm({ ...taConfigForm, timeout_minutes: parseInt(e.target.value) || 15 })}
                  />
                </div>
              </div>
            </section>

            {/* Paper trading integration */}
            <section>
              <div className="flex items-center gap-2 mb-2">
                <input
                  type="checkbox"
                  id="emit-paper-trading"
                  checked={!!taConfigForm.emit_paper_trading_signal}
                  onChange={e => setTaConfigForm({ ...taConfigForm, emit_paper_trading_signal: e.target.checked })}
                />
                <label htmlFor="emit-paper-trading" className="font-medium cursor-pointer">
                  Write BUY decisions to paper trading signals
                </label>
              </div>
              <div className="text-[11px] text-muted-foreground">
                When enabled, a StrategySignalRun is written whenever TA outputs a BUY decision, and PaperTradingEngine
                opens a paper position on its next tick (stop loss -5%, take profit +10%). <strong>Disabled by default</strong> to
                prevent accidental position opens. SELL does not auto-close positions.
              </div>
            </section>

            {/* Linked trigger (intraday sharp rise/fall) */}
            {(() => {
              const autoTrigger = (taConfigForm.auto_trigger as Record<string, unknown>) || {}
              const setAuto = (patch: Record<string, unknown>) =>
                setTaConfigForm({ ...taConfigForm, auto_trigger: { ...autoTrigger, ...patch } })
              return (
                <section>
                  <div className="flex items-center gap-2 mb-2">
                    <input
                      type="checkbox"
                      id="auto-trigger-enabled"
                      checked={!!autoTrigger.enabled}
                      onChange={e => setAuto({ enabled: e.target.checked })}
                    />
                    <label htmlFor="auto-trigger-enabled" className="font-medium cursor-pointer">
                      Auto-trigger deep analysis on intraday sharp rise/fall
                    </label>
                  </div>
                  <div className="grid grid-cols-2 gap-3 mb-2">
                    <div>
                      <Label className="text-[12px]">% Change Threshold</Label>
                      <Input
                        type="number"
                        step="0.5"
                        min={1}
                        max={20}
                        value={String(autoTrigger.change_pct_threshold ?? 5)}
                        onChange={e => setAuto({ change_pct_threshold: parseFloat(e.target.value) || 5 })}
                      />
                    </div>
                    <div>
                      <Label className="text-[12px]">Cooldown (hours)</Label>
                      <Input
                        type="number"
                        min={1}
                        max={168}
                        value={String(autoTrigger.cooldown_hours ?? 24)}
                        onChange={e => setAuto({ cooldown_hours: parseInt(e.target.value) || 24 })}
                      />
                    </div>
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    When enabled, if intraday_monitor detects |% change| ≥ the threshold during analysis, it automatically
                    fires a fire-and-forget TA deep analysis trigger. The same symbol won't retrigger within the cooldown
                    period, and it also stops once the monthly budget is exhausted. <strong>Disabled by default</strong> to
                    prevent runaway costs.
                  </div>
                </section>
              )
            })()}

            {/* Advanced JSON */}
            <details className="text-[12px]">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                Advanced: full config JSON
              </summary>
              <textarea
                className="mt-2 w-full font-mono text-[11px] p-2 border border-border rounded bg-background min-h-[120px]"
                value={JSON.stringify(taConfigForm, null, 2)}
                onChange={e => {
                  try {
                    setTaConfigForm(JSON.parse(e.target.value))
                  } catch {
                    /* Input not yet complete — allow editing to continue */
                  }
                }}
              />
            </details>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="ghost" onClick={() => setTaConfigAgent(null)}>Cancel</Button>
              <button type="button" className="btn-primary" onClick={saveTaConfig}>Save</button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
