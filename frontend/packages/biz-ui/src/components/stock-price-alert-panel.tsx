import { DEFAULT_MARKET } from '@tickerkeep/api/markets'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Bell, Plus, Pencil, Trash2 } from 'lucide-react'
import { fetchAPI, stocksApi, type NotifyChannel } from '@tickerkeep/api'
import { Button } from '@tickerkeep/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@tickerkeep/base-ui/components/ui/dialog'
import PriceAlertFormDialog, { type AlertConditionItem, type PriceAlertFormState, type PriceAlertSubmitPayload } from '@tickerkeep/biz-ui/components/price-alert-form-dialog'
import { useToast } from '@tickerkeep/base-ui/components/ui/toast'

interface StockItem {
  id: number
  symbol: string
  name: string
  market: string
}

interface AlertRule {
  id: number
  stock_id: number
  stock_symbol: string
  stock_name: string
  market: string
  name: string
  enabled: boolean
  condition_group: {
    op: 'and' | 'or'
    items: AlertConditionItem[]
  }
  market_hours_mode: 'always' | 'trading_only'
  cooldown_minutes: number
  max_triggers_per_day: number
  repeat_mode: 'once' | 'repeat'
  expire_at: string | null
  notify_channel_ids: number[]
}

const DEFAULT_FORM: PriceAlertFormState = {
  stock_id: 0,
  name: '',
  op: 'and',
  items: [{ type: 'price', op: '>=', value: 0 }],
  market_hours_mode: 'trading_only',
  cooldown_minutes: 30,
  max_triggers_per_day: 3,
  repeat_mode: 'repeat',
  expire_at: '',
  notify_channel_ids: [],
}

function conditionText(item: AlertConditionItem): string {
  const label: Record<string, string> = {
    price: 'Price',
    change_pct: 'Change %',
    turnover: 'Turnover',
    volume: 'Volume',
    volume_ratio: 'Volume Ratio',
  }
  if (item.op === 'between' && Array.isArray(item.value)) {
    return `${label[item.type] || item.type} ∈ [${item.value[0]}, ${item.value[1]}]`
  }
  return `${label[item.type] || item.type} ${item.op} ${Array.isArray(item.value) ? item.value.join('~') : item.value}`
}

export default function StockPriceAlertPanel(props: {
  symbol: string
  market: string
  stockName?: string
  stockId?: number
  mode?: 'icon' | 'inline'
  initialTotal?: number
  initialEnabled?: number
  onChanged?: () => void
}) {
  const { toast } = useToast()
  const symbol = String(props.symbol || '').trim()
  const market = String(props.market || DEFAULT_MARKET).trim().toUpperCase()
  const mode = props.mode || 'icon'

  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [rules, setRules] = useState<AlertRule[]>([])
  const [stocks, setStocks] = useState<StockItem[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [formOpen, setFormOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<PriceAlertFormState>(DEFAULT_FORM)

  const summary = useMemo(() => {
    const total = rules.length
    const enabled = rules.filter(r => r.enabled).length
    return { total, enabled }
  }, [rules])
  const shownSummary = useMemo(() => {
    if (summary.total > 0 || open) return summary
    return {
      total: Number(props.initialTotal || 0),
      enabled: Number(props.initialEnabled || 0),
    }
  }, [open, props.initialEnabled, props.initialTotal, summary])
  const formStocks = useMemo(() => {
    return stocks
  }, [stocks])

  const ensureStockId = useCallback(async (): Promise<number | null> => {
    if (props.stockId) return props.stockId
    let target = stocks.find(s => s.symbol === symbol && s.market === market)
    if (!target) {
      const all = await stocksApi.list()
      setStocks(all || [])
      target = (all || []).find(s => s.symbol === symbol && s.market === market)
    }
    if (!target) {
      const created = await stocksApi.create({ symbol, market, name: props.stockName || symbol })
      setStocks(prev => prev.some(s => s.id === created.id) ? prev : [created, ...prev])
      return created.id
    }
    return target.id
  }, [market, props.stockId, props.stockName, stocks, symbol])

  const load = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      const [ruleData, stockData, channelData] = await Promise.all([
        fetchAPI<AlertRule[]>('/price-alerts'),
        stocksApi.list(),
        fetchAPI<NotifyChannel[]>('/channels'),
      ])
      setStocks(stockData || [])
      setChannels(channelData || [])
      const filtered = (ruleData || []).filter(r =>
        String(r.stock_symbol || '').toUpperCase() === symbol.toUpperCase() &&
        String(r.market || '').toUpperCase() === market
      )
      setRules(filtered)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to load alerts', 'error')
    } finally {
      setLoading(false)
    }
  }, [market, symbol, toast])

  const loadSummaryOnly = useCallback(async () => {
    if (!symbol) return
    try {
      const ruleData = await fetchAPI<AlertRule[]>('/price-alerts')
      const filtered = (ruleData || []).filter(r =>
        String(r.stock_symbol || '').toUpperCase() === symbol.toUpperCase() &&
        String(r.market || '').toUpperCase() === market
      )
      setRules(filtered)
    } catch {
      // summary preload failure should not block interaction
    }
  }, [market, symbol])

  useEffect(() => {
    if (!open) return
    load()
  }, [open, load])

  useEffect(() => {
    if (open) return
    if (mode !== 'inline') return
    if ((props.initialTotal ?? null) !== null) return
    loadSummaryOnly()
  }, [loadSummaryOnly, mode, open, props.initialTotal])

  const openCreate = async () => {
    try {
      const stockId = await ensureStockId()
      if (!stockId) {
        toast('Could not locate the stock', 'error')
        return
      }
      setEditingId(null)
      setForm({
        ...DEFAULT_FORM,
        stock_id: stockId,
        name: props.stockName ? `${props.stockName} Price Alert` : '',
      })
      setFormOpen(true)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to create alert', 'error')
    }
  }

  const openEdit = (r: AlertRule) => {
    setEditingId(r.id)
    setForm({
      stock_id: r.stock_id,
      name: r.name || '',
      op: r.condition_group?.op || 'and',
      items: (r.condition_group?.items || [{ type: 'price', op: '>=', value: 0 }]) as AlertConditionItem[],
      market_hours_mode: r.market_hours_mode || 'trading_only',
      cooldown_minutes: r.cooldown_minutes ?? 30,
      max_triggers_per_day: r.max_triggers_per_day ?? 3,
      repeat_mode: r.repeat_mode || 'repeat',
      expire_at: r.expire_at ? r.expire_at.slice(0, 16) : '',
      notify_channel_ids: r.notify_channel_ids || [],
    })
    setFormOpen(true)
  }

  const submitForm = async (payload: PriceAlertSubmitPayload) => {
    setSaving(true)
    try {
      if (editingId) {
        await fetchAPI(`/price-alerts/${editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
      } else {
        await fetchAPI('/price-alerts', { method: 'POST', body: JSON.stringify(payload) })
      }
      setFormOpen(false)
      await load()
      props.onChanged?.()
      toast('Alert saved', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Save failed', 'error')
    } finally {
      setSaving(false)
    }
  }

  const toggleRule = async (r: AlertRule) => {
    try {
      await fetchAPI(`/price-alerts/${r.id}/toggle`, { method: 'POST', body: JSON.stringify({ enabled: !r.enabled }) })
      await load()
      props.onChanged?.()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Toggle failed', 'error')
    }
  }

  const removeRule = async (r: AlertRule) => {
    if (!window.confirm(`Delete rule "${r.name || 'Alert'}"?`)) return
    try {
      await fetchAPI(`/price-alerts/${r.id}`, { method: 'DELETE' })
      await load()
      props.onChanged?.()
      toast('Deleted', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Delete failed', 'error')
    }
  }

  const trigger = mode === 'inline' ? (
    <Button
      variant="secondary"
      size="sm"
      className="h-8 px-2.5"
      onClick={() => setOpen(true)}
      type="button"
      title={shownSummary.total > 0 ? `Alerts ${shownSummary.enabled}/${shownSummary.total}` : 'Price Alert'}
    >
      <Bell className="w-3.5 h-3.5" />
      Alerts {shownSummary.total > 0 ? `${shownSummary.enabled}/${shownSummary.total}` : '0'}
    </Button>
  ) : (
    <button
      className="relative inline-flex items-center justify-center h-7 w-7 rounded-md hover:bg-accent/40 transition-colors"
      onClick={() => setOpen(true)}
      title={shownSummary.total > 0 ? `Alerts ${shownSummary.enabled}/${shownSummary.total}` : 'Price Alert'}
      aria-label={shownSummary.total > 0 ? `Alerts ${shownSummary.enabled}/${shownSummary.total}` : 'Price Alert'}
      type="button"
    >
      <Bell className="w-3.5 h-3.5" />
      {shownSummary.total > 0 && (
        <span className="absolute -top-1 -right-1 min-w-[14px] h-[14px] px-1 rounded-full bg-primary text-primary-foreground text-[9px] leading-[14px] text-center">
          {shownSummary.enabled}
        </span>
      )}
    </button>
  )

  return (
    <>
      {trigger}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{props.stockName || symbol} Alerts</DialogTitle>
            <DialogDescription>{market} · {summary.enabled} enabled / {summary.total} total</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex justify-end">
              <Button size="sm" className="h-8" onClick={() => openCreate()}>
                <Plus className="w-3.5 h-3.5" />
                New Alert
              </Button>
            </div>
            {loading ? (
              <div className="text-[12px] text-muted-foreground py-6 text-center">Loading...</div>
            ) : rules.length === 0 ? (
              <div className="text-[12px] text-muted-foreground py-6 text-center">No alert rules for this stock yet</div>
            ) : (
              <div className="space-y-2 max-h-[48vh] overflow-y-auto scrollbar">
                {rules.map(r => (
                  <div key={r.id} className="rounded-lg border border-border/40 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-[13px] font-medium truncate">{r.name || `${props.stockName || symbol} Alert`}</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${r.enabled ? 'bg-emerald-500/15 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                            {r.enabled ? 'Enabled' : 'Paused'}
                          </span>
                        </div>
                        <div className="mt-1 text-[11px] text-muted-foreground">
                          {(r.condition_group?.items || []).map(conditionText).join(r.condition_group?.op === 'or' ? ' OR ' : ' AND ')}
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <Button variant="secondary" size="sm" className="h-7 px-2" aria-label="Edit" onClick={() => openEdit(r)}><Pencil className="w-3 h-3" /></Button>
                        <Button variant={r.enabled ? 'destructive' : 'default'} size="sm" className="h-7 px-2.5" onClick={() => toggleRule(r)}>{r.enabled ? 'Disable' : 'Enable'}</Button>
                        <Button variant="secondary" size="sm" className="h-7 px-2" aria-label="Delete" onClick={() => removeRule(r)}><Trash2 className="w-3 h-3" /></Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <PriceAlertFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        title={editingId ? 'Edit Alert Rule' : 'New Alert Rule'}
        description="Supports price, change %, turnover, and volume ratio conditions, combinable with AND / OR"
        stocks={formStocks}
        channels={channels}
        initial={form}
        submitting={saving}
        submitLabel="Save Rule"
        onSubmit={submitForm}
      />
    </>
  )
}
