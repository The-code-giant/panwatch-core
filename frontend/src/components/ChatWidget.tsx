import { useCallback, useEffect, useRef, useState } from 'react'
import { MessageCircle, X, Plus, Trash2, Send, ChevronLeft, XCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { chatApi, type ChatConversation, type ChatMessage } from '@panwatch/api'

interface StockContext {
  symbol: string
  market: string
  stockName: string
  pageContext?: string
}

// Tool name → progress visualization text
const TOOL_LABELS: Record<string, string> = {
  get_portfolio: 'Fetching positions…',
  get_stock_quote: 'Fetching quote…',
  get_technical_analysis: 'Analyzing technicals…',
  get_stock_suggestions: 'Fetching AI recommendations…',
  get_watchlist: 'Fetching watchlist…',
}

/** Incremental render tolerance: optimistically close unclosed code fences in streaming text to avoid markdown render breakage */
function safeStreamMarkdown(text: string): string {
  const fences = (text.match(/```/g) || []).length
  return fences % 2 === 1 ? `${text}\n\`\`\`` : text
}

export default function ChatWidget() {
  const [open, setOpen] = useState(false)
  const [conversations, setConversations] = useState<ChatConversation[]>([])
  const [activeConvId, setActiveConvId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [view, setView] = useState<'list' | 'chat'>('list')
  const [stockContext, setStockContext] = useState<StockContext | null>(null)
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([])
  // Incremental state for the streaming reply
  const [streamText, setStreamText] = useState('')
  const [streamTool, setStreamTool] = useState<string | null>(null)
  // Plan card state for plan-driven flows (full portfolio diagnostics)
  const [plan, setPlan] = useState<{
    status: string
    steps: { id: number; title: string; status: string }[]
    current?: number
  } | null>(null)
  const tokenBufRef = useRef('')
  const rafRef = useRef<number | null>(null)
  // Auto-scroll: stop when the user scrolls up, resume when back at the bottom
  const autoScrollRef = useRef(true)
  const scrollBoxRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)

  // Batch token updates via rAF to avoid re-rendering on every chunk
  const pushToken = useCallback((t: string) => {
    tokenBufRef.current += t
    if (rafRef.current == null) {
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null
        setStreamText(tokenBufRef.current)
      })
    }
  }, [])

  const resetStream = useCallback(() => {
    tokenBufRef.current = ''
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    setStreamText('')
    setStreamTool(null)
    setPlan(null)
  }, [])

  const handleScroll = useCallback(() => {
    const box = scrollBoxRef.current
    if (!box) return
    // Within 40px of the bottom counts as "at bottom" and resumes auto-scroll; scrolling up stops it
    autoScrollRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 40
  }, [])

  const loadConversations = useCallback(async () => {
    try {
      const list = await chatApi.listConversations(30)
      setConversations(list)
    } catch {
      // ignore
    }
  }, [])

  const loadMessages = useCallback(async (convId: number) => {
    try {
      const detail = await chatApi.getConversation(convId)
      setMessages(detail.messages)
    } catch {
      // ignore
    }
  }, [])

  const loadSuggestedQuestions = useCallback(async (symbol: string, market: string) => {
    try {
      const res = await chatApi.getSuggestedQuestions(symbol, market)
      setSuggestedQuestions(res.questions || [])
    } catch {
      setSuggestedQuestions([])
    }
  }, [])

  // Listen for stock context events from stock insight modal
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as StockContext
      if (!detail?.symbol) return
      setOpen(true)
      setStockContext(detail)
      setSuggestedQuestions([])

      // Create a new conversation bound to this stock, with page context
      chatApi.createConversation({
        stock_symbol: detail.symbol,
        stock_market: detail.market,
        initial_context: detail.pageContext,
      }).then((conv) => {
        setActiveConvId(conv.id)
        setMessages([])
        setView('chat')
        setConversations((prev) => [conv, ...prev])
        loadSuggestedQuestions(detail.symbol, detail.market)
      }).catch(() => {
        // fallback: just open chat
        setView('chat')
      })
    }
    window.addEventListener('panwatch-open-chat', handler)
    return () => window.removeEventListener('panwatch-open-chat', handler)
  }, [loadSuggestedQuestions])

  useEffect(() => {
    if (open) {
      loadConversations()
    }
  }, [open, loadConversations])

  useEffect(() => {
    if (autoScrollRef.current) {
      endRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, streamText, streamTool])

  const openConversation = useCallback(async (conv: ChatConversation) => {
    setActiveConvId(conv.id)
    setView('chat')
    setSuggestedQuestions([])
    if (conv.stock_symbol && conv.stock_market) {
      setStockContext({ symbol: conv.stock_symbol, market: conv.stock_market, stockName: '' })
      loadSuggestedQuestions(conv.stock_symbol, conv.stock_market)
    } else {
      setStockContext(null)
    }
    await loadMessages(conv.id)
  }, [loadMessages, loadSuggestedQuestions])

  const createNewConversation = useCallback(async () => {
    try {
      const conv = await chatApi.createConversation()
      setActiveConvId(conv.id)
      setMessages([])
      setView('chat')
      setStockContext(null)
      setSuggestedQuestions([])
      setConversations((prev) => [conv, ...prev])
    } catch {
      // ignore
    }
  }, [])

  const deleteConversation = useCallback(async (convId: number, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await chatApi.deleteConversation(convId)
      setConversations((prev) => prev.filter((c) => c.id !== convId))
      if (activeConvId === convId) {
        setActiveConvId(null)
        setMessages([])
        setView('list')
        setStockContext(null)
        setSuggestedQuestions([])
      }
    } catch {
      // ignore
    }
  }, [activeConvId])

  const handleSend = useCallback(async (overrideContent?: string) => {
    const content = (overrideContent || input).trim()
    if (!content || sending) return

    let convId = activeConvId
    if (!convId) {
      try {
        const conv = await chatApi.createConversation(
          stockContext ? { stock_symbol: stockContext.symbol, stock_market: stockContext.market } : undefined
        )
        convId = conv.id
        setActiveConvId(conv.id)
        setConversations((prev) => [conv, ...prev])
        setView('chat')
      } catch {
        return
      }
    }

    setInput('')
    setSending(true)
    setSuggestedQuestions([]) // hide after first send

    const tempUserMsg: ChatMessage = {
      id: Date.now(),
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, tempUserMsg])

    resetStream()
    autoScrollRef.current = true
    let receivedAny = false

    try {
      // Prefer SSE streaming (token stream + tool-call progress visualization)
      await chatApi.sendMessageStream(convId, content, {
        onToken: (t) => {
          receivedAny = true
          setStreamTool(null)
          pushToken(t)
        },
        onToolCallStart: ({ name }) => {
          receivedAny = true
          // Transitional text from a tool-call round isn't the final answer, so clear the buffer
          tokenBufRef.current = ''
          setStreamText('')
          setStreamTool(TOOL_LABELS[name] || `Calling ${name}…`)
        },
        onToolResult: () => {
          // Result is ready; wait for the model to continue answering based on the data
        },
        onPlan: (p) => {
          receivedAny = true
          setStreamTool(null)
          setPlan(p)
        },
        onDone: (m) => {
          receivedAny = true
          setMessages((prev) => [...prev, {
            id: m.message_id || Date.now() + 1,
            role: 'assistant',
            content: m.content,
            created_at: m.created_at || new Date().toISOString(),
          }])
        },
      })
      setConversations((prev) =>
        prev.map((c) => c.id === convId ? { ...c, title: c.title || content.slice(0, 20) } : c)
      )
    } catch (e) {
      if (!receivedAny) {
        // Streaming unavailable entirely (old backend/proxy doesn't support it, etc.) → fall back to non-streaming endpoint
        try {
          const reply = await chatApi.sendMessage(convId, content)
          setMessages((prev) => [...prev, reply])
          setConversations((prev) =>
            prev.map((c) => c.id === convId ? { ...c, title: c.title || content.slice(0, 20) } : c)
          )
        } catch (e2) {
          const errMsg: ChatMessage = {
            id: Date.now() + 1,
            role: 'assistant',
            content: `Request failed: ${e2 instanceof Error ? e2.message : 'Unknown error'}`,
            created_at: new Date().toISOString(),
          }
          setMessages((prev) => [...prev, errMsg])
        }
      } else {
        // Some events arrived but the stream broke: generation continues server-side and persists, so poll for the final message shortly
        await new Promise((r) => setTimeout(r, 1500))
        await loadMessages(convId)
      }
    } finally {
      resetStream()
      setSending(false)
    }
  }, [input, sending, activeConvId, stockContext, pushToken, resetStream, loadMessages])

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        aria-label="Open chat assistant"
        className="fixed bottom-20 right-4 md:bottom-5 md:right-5 z-40 w-12 h-12 rounded-full bg-primary text-primary-foreground card flex items-center justify-center hover:bg-primary/90 transition-colors"
      >
        <MessageCircle className="w-5 h-5" />
      </button>
    )
  }

  return (
    <div className="fixed bottom-0 right-0 z-50 w-full h-full md:w-[420px] md:h-[600px] md:bottom-5 md:right-5 md:rounded-xl bg-background border border-border flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/40 bg-accent/20">
        <div className="flex items-center gap-2">
          {view === 'chat' && (
            <button
              onClick={() => { setView('list'); setStockContext(null); setSuggestedQuestions([]); loadConversations() }}
              aria-label="Back to conversations"
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
          )}
          <span className="text-[14px] font-semibold text-foreground">AI Assistant</span>
          {view === 'chat' && stockContext && (
            <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-muted text-foreground">
              {stockContext.market}:{stockContext.symbol}
              {stockContext.stockName && ` ${stockContext.stockName}`}
              <button
                onClick={() => { setStockContext(null); setSuggestedQuestions([]) }}
                aria-label="Clear stock context"
                className="hover:text-muted-foreground transition-colors"
              >
                <XCircle className="w-3 h-3" />
              </button>
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {view === 'list' && (
            <button
              onClick={createNewConversation}
              className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
              title="New conversation"
              aria-label="New conversation"
            >
              <Plus className="w-4 h-4" />
            </button>
          )}
          <button
            onClick={() => setOpen(false)}
            aria-label="Close"
            className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* List view */}
      {view === 'list' && (
        <div className="flex-1 overflow-y-auto scrollbar">
          {conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-[13px] gap-3">
              <MessageCircle className="w-8 h-8 opacity-30" />
              <p>No conversations yet</p>
              <button
                onClick={createNewConversation}
                className="text-[12px] px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                Start a new conversation
              </button>
            </div>
          ) : (
            conversations.map((conv) => (
              <button
                key={conv.id}
                onClick={() => openConversation(conv)}
                className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-accent/30 transition-colors border-b border-border/20"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] text-foreground truncate">
                    {conv.title || 'New conversation'}
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    {conv.stock_symbol ? `${conv.stock_market}:${conv.stock_symbol} · ` : ''}
                    {new Date(conv.created_at).toLocaleDateString()}
                  </div>
                </div>
                <button
                  onClick={(e) => deleteConversation(conv.id, e)}
                  aria-label="Delete conversation"
                  className="p-1 rounded text-muted-foreground/50 hover:text-rose-400 transition-colors shrink-0"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </button>
            ))
          )}
        </div>
      )}

      {/* Chat view */}
      {view === 'chat' && (
        <>
          <div ref={scrollBoxRef} onScroll={handleScroll} className="flex-1 overflow-y-auto scrollbar px-4 py-3 space-y-3">
            {/* Suggested questions */}
            {messages.length === 0 && suggestedQuestions.length > 0 && (
              <div className="flex flex-col gap-2">
                <span className="text-[11px] text-muted-foreground">Suggested questions</span>
                <div className="flex flex-wrap gap-2">
                  {suggestedQuestions.map((q) => (
                    <button
                      key={q}
                      className="text-[11px] px-3 py-1.5 rounded-full bg-muted text-foreground hover:bg-muted/70 transition-colors text-left"
                      onClick={() => handleSend(q)}
                      disabled={sending}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.length === 0 && suggestedQuestions.length === 0 && !sending && (
              <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-[13px] gap-2">
                <MessageCircle className="w-6 h-6 opacity-30" />
                <p>Type a question to start chatting</p>
              </div>
            )}
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`max-w-[85%] rounded-xl px-3 py-2 text-[13px] leading-relaxed ${
                    msg.role === 'user'
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-accent/60 text-foreground'
                  }`}
                >
                  {msg.role === 'assistant' ? (
                    <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_h1]:text-[15px] [&_h2]:text-[14px] [&_h3]:text-[13px]">
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    </div>
                  ) : (
                    msg.content
                  )}
                </div>
              </div>
            ))}
            {sending && plan && plan.steps.length > 0 && (
              // Plan card for plan-driven flows (full portfolio diagnostics): steps + status
              <div className="flex justify-start">
                <div className="max-w-[85%] w-full rounded-xl px-3 py-2 text-[12px] bg-accent/40 border border-border/40">
                  <div className="font-medium text-foreground mb-1.5">
                    Diagnostic plan{plan.status === 'done' ? ' (Done)' : plan.status === 'planning' ? ' (Generating…)' : ''}
                  </div>
                  <ol className="space-y-1">
                    {plan.steps.map((s) => (
                      <li key={s.id} className="flex items-center gap-2">
                        <span
                          className={
                            s.status === 'done'
                              ? 'text-emerald-600'
                              : s.status === 'failed'
                              ? 'text-rose-600'
                              : s.status === 'running'
                              ? 'text-foreground'
                              : 'text-muted-foreground'
                          }
                        >
                          {s.status === 'done'
                            ? '✓'
                            : s.status === 'failed'
                            ? '✕'
                            : s.status === 'running'
                            ? '⟳'
                            : '○'}
                        </span>
                        <span className={s.status === 'done' ? 'text-muted-foreground' : 'text-foreground'}>
                          {s.title}
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              </div>
            )}
            {sending && streamText && (
              // Streaming incremental render (unclosed code blocks optimistically closed)
              <div className="flex justify-start">
                <div className="max-w-[85%] rounded-xl px-3 py-2 text-[13px] leading-relaxed bg-accent/60 text-foreground">
                  <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_h1]:text-[15px] [&_h2]:text-[14px] [&_h3]:text-[13px]">
                    <ReactMarkdown>{safeStreamMarkdown(streamText)}</ReactMarkdown>
                  </div>
                </div>
              </div>
            )}
            {sending && !streamText && (
              <div className="flex justify-start">
                <div className="bg-accent/60 rounded-xl px-3 py-2 text-[13px] text-muted-foreground flex items-center gap-2">
                  <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                  {streamTool || 'Thinking...'}
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {/* Input */}
          <div className="flex items-center gap-2 px-4 py-3 border-t border-border/40">
            <input
              type="text"
              className="flex-1 h-9 px-3 rounded-lg bg-accent/40 text-[13px] text-foreground placeholder:text-muted-foreground outline-none focus:ring-1 focus:ring-primary/30"
              placeholder="Type a question..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  handleSend()
                }
              }}
              disabled={sending}
            />
            <button
              className="h-9 w-9 rounded-lg bg-primary text-primary-foreground flex items-center justify-center hover:bg-primary/90 transition-colors disabled:opacity-50"
              onClick={() => handleSend()}
              disabled={sending || !input.trim()}
              aria-label="Send message"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </>
      )}
    </div>
  )
}
