import { useEffect, useState } from 'react'
import { Copy, Plus, Trash2, KeyRound } from 'lucide-react'
import { patsApi, type PatItem } from '@tickerkeep/api'
import { Input } from '@tickerkeep/base-ui/components/ui/input'
import { Button } from '@tickerkeep/base-ui/components/ui/button'
import { useToast } from '@tickerkeep/base-ui/components/ui/toast'

/**
 * MCP access token (PAT) management.
 *
 * Tokens are used by MCP clients like Claude to connect to TickerKeep's MCP endpoint (/mcp).
 * The plaintext token is returned only once, at creation; the list only shows the prefix.
 */
export default function PatSection() {
  const { toast } = useToast()
  const [items, setItems] = useState<PatItem[]>([])
  const [loading, setLoading] = useState(false)
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [newToken, setNewToken] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const res = await patsApi.list()
      setItems(res.items || [])
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to load tokens', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const create = async () => {
    if (!name.trim()) {
      toast('Please enter a note for the token', 'error')
      return
    }
    setCreating(true)
    try {
      const res = await patsApi.create({ name: name.trim() })
      setNewToken(res.token)
      setName('')
      await load()
      toast('Token created. The plaintext is only shown once, so save it now', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to create', 'error')
    } finally {
      setCreating(false)
    }
  }

  const revoke = async (id: number) => {
    try {
      await patsApi.revoke(id)
      await load()
      toast('Token revoked', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to revoke', 'error')
    }
  }

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text)
    toast('Copied to clipboard', 'success')
  }

  return (
    <section id="sec-pat" className="card p-4 md:p-6 lg:col-span-12">
      <div className="flex items-start justify-between mb-4 gap-3">
        <div>
          <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground flex items-center gap-1.5">
            <KeyRound className="w-3.5 h-3.5" /> MCP Access Tokens
          </h3>
          <p className="text-[11px] text-muted-foreground mt-1">
            For MCP clients like Claude to connect to this site's MCP endpoint (<span className="font-mono">/mcp</span>), read-only access to quotes and positions. The plaintext is shown only once, at creation.
          </p>
        </div>
      </div>

      {/* Create */}
      <div className="flex flex-col sm:flex-row gap-2 mb-4">
        <Input
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="Note, e.g. Claude Desktop"
          className="sm:max-w-xs"
        />
        <Button size="sm" className="h-9" onClick={create} disabled={creating}>
          <Plus className="w-3.5 h-3.5" /> Create Token
        </Button>
      </div>

      {/* One-time plaintext display */}
      {newToken ? (
        <div className="mb-4 rounded-xl border border-amber-400/40 bg-amber-50/60 dark:bg-amber-950/20 p-3">
          <div className="text-[11px] text-amber-700 dark:text-amber-400 mb-1.5">
            Copy and save this now - it can't be viewed again after closing:
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 min-w-0 truncate rounded bg-background/70 px-2 py-1 font-mono text-[12px]">{newToken}</code>
            <Button variant="secondary" size="sm" className="h-8" onClick={() => copy(newToken)}>
              <Copy className="w-3.5 h-3.5" /> Copy
            </Button>
            <Button variant="ghost" size="sm" className="h-8" onClick={() => setNewToken(null)}>Got it</Button>
          </div>
        </div>
      ) : null}

      {/* List */}
      {loading ? (
        <div className="text-[12px] text-muted-foreground">Loading...</div>
      ) : items.length === 0 ? (
        <div className="text-[12px] text-muted-foreground">No tokens yet.</div>
      ) : (
        <div className="space-y-2">
          {items.map(it => (
            <div
              key={it.id}
              className="flex items-center justify-between gap-3 rounded-xl border border-border/40 bg-accent/20 px-3 py-2"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[12px] font-medium text-foreground truncate">{it.name || 'Unnamed'}</span>
                  <code className="font-mono text-[11px] text-muted-foreground">{it.prefix}…</code>
                  {it.revoked ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-rose-500/15 text-rose-600">Revoked</span>
                  ) : (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600">Active</span>
                  )}
                </div>
                <div className="text-[10px] text-muted-foreground mt-0.5">
                  {it.last_used_at ? `Last used ${it.last_used_at.slice(0, 10)}` : 'Never used'}
                  {it.expires_at ? ` · Expires ${it.expires_at.slice(0, 10)}` : ' · Never expires'}
                </div>
              </div>
              {!it.revoked ? (
                <Button variant="ghost" size="sm" className="h-8 text-rose-600" onClick={() => revoke(it.id)}>
                  <Trash2 className="w-3.5 h-3.5" /> Revoke
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
