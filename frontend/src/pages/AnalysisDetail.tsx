import { useEffect, useState, type ReactNode } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { inferMarketFromSymbol } from '@/lib/markets'
import remarkGfm from 'remark-gfm'
import {
  ArrowLeft,
  FileDown,
  ImageDown,
  List,
  ChevronDown,
  Target,
  TrendingUp,
  MessageSquare,
  Newspaper,
  BarChart3,
  Scale,
  ShieldAlert,
  History,
  FileSearch,
  Inbox,
  Sparkles,
  type LucideIcon,
} from 'lucide-react'
import {
  tradingAgentsApi,
  type DeepAnalysisResult,
  type HistoryComparisonResponse,
} from '@panwatch/api'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { EmptyState } from '@panwatch/base-ui/components/ui/empty-state'
import { buildAnalysisSections } from '@panwatch/biz-ui/analysis-sections'
import ShareCardModal from '../components/ShareCardModal'

/** Decision headline colour: emphasis is weight and size, not hue - the
 *  agent's recommendation is not a price move, so it never borrows the
 *  stock-up/stock-down direction colours. */
const DECISION_COLOR: Record<string, string> = {
  buy: 'text-foreground',
  hold: 'text-muted-foreground',
  sell: 'text-foreground',
}

/** 报告来源 agent 的展示名(用于页头的 AI 生成标记) */
const AGENT_LABELS: Record<string, string> = {
  tradingagents: 'TradingAgents',
}

/** 各 section 配图标(决策/技术/情绪/新闻/基本面/辩论/风控),与 buildAnalysisSections 的 id 对齐 */
const SECTION_ICON: Record<string, LucideIcon> = {
  decision: Target,
  market: TrendingUp,
  social: MessageSquare,
  news: Newspaper,
  fundamentals: BarChart3,
  debate: Scale,
  risk: ShieldAlert,
}

/** 二级目录显示开关的 localStorage 键(记住用户选择) */
const TOC_SUB_KEY = 'panwatch_toc_show_sub'

function pctClass(v: number | null | undefined): string {
  if (v == null) return 'text-muted-foreground'
  return v > 0 ? 'text-stock-up' : v < 0 ? 'text-stock-down' : 'text-muted-foreground'
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '-'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

/** 标题 → 锚点 slug(去掉 markdown 强调/井号/emoji,空白转连字符)。
 *  解析目录与渲染标题两侧用同一份逻辑,保证 id 一致、点击可跳。 */
function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[*_`#~]/g, '')
    .replace(/\s+/g, '-')
    .replace(/[^\w一-龥-]/g, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

/** 从 ReactMarkdown 标题节点的 children 里递归取纯文本(用于算锚点 id)。 */
function nodeText(children: ReactNode): string {
  if (typeof children === 'string') return children
  if (typeof children === 'number') return String(children)
  if (Array.isArray(children)) return children.map(nodeText).join('')
  if (children && typeof children === 'object' && 'props' in children) {
    return nodeText((children as { props?: { children?: ReactNode } }).props?.children)
  }
  return ''
}

/** 从一段 markdown 里抽出 2~4 级标题(用于二级目录)。 */
function parseHeadings(markdown: string): { text: string; slug: string }[] {
  const out: { text: string; slug: string }[] = []
  for (const raw of markdown.split('\n')) {
    const m = /^(#{2,4})\s+(.+?)\s*#*$/.exec(raw)
    if (!m) continue
    const text = m[2].replace(/[*_`]/g, '').trim()
    if (text) out.push({ text, slug: slugify(m[2]) })
  }
  return out
}

export default function AnalysisDetailPage() {
  const { symbol = '', date = '' } = useParams()
  const navigate = useNavigate()
  const [result, setResult] = useState<DeepAnalysisResult | null>(null)
  const [history, setHistory] = useState<HistoryComparisonResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeId, setActiveId] = useState('')
  const [tocOpen, setTocOpen] = useState(false)
  const [showSub, setShowSub] = useState(() => {
    try {
      return localStorage.getItem(TOC_SUB_KEY) !== '0'
    } catch {
      return true
    }
  })
  const [pdfBusy, setPdfBusy] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)

  const handleExportPdf = async () => {
    if (pdfBusy) return
    setPdfBusy(true)
    try {
      await tradingAgentsApi.downloadAnalysisPdf(symbol, date)
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setPdfBusy(false)
    }
  }

  useEffect(() => {
    setLoading(true)
    tradingAgentsApi
      .getAnalysisByDate(symbol, date)
      .then(setResult)
      .catch(() => setResult(null))
      .finally(() => setLoading(false))
    tradingAgentsApi
      .getHistoryComparison(symbol, inferMarketFromSymbol(symbol), 90)
      .then(setHistory)
      .catch(() => setHistory(null))
  }, [symbol, date])

  // 记住二级目录开关
  useEffect(() => {
    try {
      localStorage.setItem(TOC_SUB_KEY, showSub ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [showSub])

  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion
  const sections = buildAnalysisSections(rawData)
  const stats = history?.stats
  const items = history?.items || []

  // 完整目录:每个 section(一级) + 其 markdown 内 2~4 级标题(二级) + 历史决策对比
  const fullToc: { id: string; title: string; level: 0 | 1 }[] = []
  for (const s of sections) {
    fullToc.push({ id: `sec-${s.id}`, title: s.title, level: 0 })
    for (const h of parseHeadings(s.markdown)) {
      fullToc.push({ id: `h-${s.id}-${h.slug}`, title: h.text, level: 1 })
    }
  }
  fullToc.push({ id: 'sec-history', title: 'Historical Decision Comparison', level: 0 })
  // 开关决定是否展示/联动二级目录
  const toc = showSub ? fullToc : fullToc.filter((t) => t.level === 0)

  // 滚动联动:正文滚动时自动高亮当前段(取视口内最靠上、避开顶部导航的标题)
  useEffect(() => {
    if (!result) return
    const els = toc
      .map((t) => document.getElementById(t.id))
      .filter((el): el is HTMLElement => !!el)
    if (!els.length) return
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (visible[0]) setActiveId(visible[0].target.id)
      },
      { rootMargin: '-100px 0px -55% 0px', threshold: 0 },
    )
    els.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, toc.length])

  if (loading) {
    return <div className="p-12 text-center text-muted-foreground">Loading...</div>
  }
  if (!result) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center px-4">
        <EmptyState
          size="md"
          icon={FileSearch}
          title={`No analysis found for ${symbol} on ${date}`}
          description="This deep-analysis run may not have completed, or the record doesn't exist yet. Trigger a new deep analysis for this stock to see it here."
          action={
            <Button variant="secondary" size="sm" onClick={() => navigate(-1)}>
              <ArrowLeft className="w-3.5 h-3.5" />
              Back
            </Button>
          }
        />
      </div>
    )
  }

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  }

  // 当前所在段标题(移动端折叠条上显示,让用户知道读到哪了)
  const currentTitle = toc.find((t) => t.id === activeId)?.title || ''

  // markdown 标题渲染:挂上与目录一致的锚点 id + 顶部留白(避开吸顶导航)
  const headingComponents = (sectionId: string) => {
    const make = (Tag: 'h2' | 'h3' | 'h4') =>
      function Heading({ children }: { children?: ReactNode }) {
        const id = `h-${sectionId}-${slugify(nodeText(children))}`
        return (
          <Tag id={id} className="scroll-mt-24">
            {children}
          </Tag>
        )
      }
    return { h2: make('h2'), h3: make('h3'), h4: make('h4') }
  }

  // 目录头(标题 + 二级目录开关),桌面右栏 / 移动下拉共用
  const tocHeader = (
    <div className="flex items-center justify-between gap-2 mb-2 px-2">
      <span className="helper-text font-medium">Contents</span>
      <div className="flex items-center gap-1.5 helper-text">
        <span className="cursor-pointer select-none" onClick={() => setShowSub((v) => !v)}>
          Subheadings
        </span>
        <Switch checked={showSub} onCheckedChange={setShowSub} />
      </div>
    </div>
  )

  // 目录列表(桌面右栏 / 移动下拉共用);onAfter 用于移动端选完自动收起
  const tocNav = (onAfter?: () => void) => (
    <nav className="space-y-0.5 text-[13px]">
      {toc.map((t) => (
        <button
          key={t.id}
          onClick={() => {
            scrollTo(t.id)
            onAfter?.()
          }}
          className={`row-interactive block w-full text-left py-1 rounded-md truncate ${
            t.level === 1 ? 'pl-5 pr-2 text-[12px]' : 'px-2'
          } ${
            activeId === t.id
              ? 'bg-muted text-foreground font-medium'
              : 'text-muted-foreground'
          }`}
        >
          {t.title}
        </button>
      ))}
    </nav>
  )

  return (
    <div className="min-h-screen">
      <div className="max-w-5xl mx-auto px-4 pb-12 flex gap-8">
        {/* 左列:标题栏 + 正文(标题栏只占左列宽度,不压到右侧目录) */}
        <div className="flex-1 min-w-0 max-w-3xl">
          {/* 顶部栏 */}
          <div className="border-b border-border pb-3 mb-4">
            <div className="flex items-center gap-3">
              <button
                onClick={() => navigate(-1)}
                className="w-8 h-8 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted transition-colors duration-150 shrink-0"
                aria-label="Back"
              >
                <ArrowLeft className="w-4 h-4" />
              </button>
              <h1 className="page-title truncate min-w-0">{result.title || `${symbol} Deep Analysis`}</h1>
              <span className="helper-text shrink-0">{date}</span>
              <button
                onClick={() => setShareOpen(true)}
                className="btn-mini ml-auto shrink-0"
                title="Generate a shareable conclusion card image"
              >
                <ImageDown className="w-3.5 h-3.5" />
                Share image
              </button>
              <button
                onClick={handleExportPdf}
                disabled={pdfBusy}
                className="btn-mini shrink-0 disabled:opacity-50"
                title="Export as PDF"
              >
                <FileDown className="w-3.5 h-3.5" />
                {pdfBusy ? 'Exporting...' : 'Export PDF'}
              </button>
            </div>
            {/* Machine-voice marker: this whole page is AI-generated output, so it gets a neutral chip marker for the page (which agent, when). */}
            <div className="mt-2 pl-11">
              <span className="chip-neutral">
                <Sparkles className="w-3 h-3" aria-hidden />
                AI-generated by {AGENT_LABELS[result.agent_name] || result.agent_name} · {date}
              </span>
            </div>
          </div>

          {/* Body */}
          <article>
          {/* Decision summary (mobile: top of body; desktop: moved to the right-side TOC area, see aside below) */}
          {sug && (
            <div className="lg:hidden rounded-xl bg-accent/30 p-4 mb-6 flex items-center gap-3 flex-wrap">
              <span className={`text-[24px] font-black tracking-[-0.02em] tabular-nums ${DECISION_COLOR[sug.action] || ''}`}>
                {sug.action_label}
              </span>
              <span className="helper-text">
                Confidence {sug.confidence?.toFixed(1) ?? '-'} / 10
              </span>
              <span className="helper-text ml-auto">
                Cost ${rawData.cost_usd?.toFixed(4) ?? '-'}
              </span>
            </div>
          )}

          {/* Mobile TOC: sticky collapsed bar showing the current section, expands as a dropdown (overlay), collapses on select/outside click (hidden on desktop) */}
          <div className="lg:hidden sticky top-16 z-30 mb-6">
            <div className="relative">
              <button
                onClick={() => setTocOpen((o) => !o)}
                className="card w-full flex items-center gap-2 px-3.5 py-2.5 text-[13px] font-medium"
              >
                <List className="w-4 h-4 shrink-0" />
                <span className="truncate">{currentTitle || 'Contents'}</span>
                <ChevronDown
                  className={`w-4 h-4 ml-auto shrink-0 transition-transform duration-150 ${tocOpen ? 'rotate-180' : ''}`}
                />
              </button>
              {tocOpen && (
                <>
                  <div className="fixed inset-0 z-0" onClick={() => setTocOpen(false)} />
                  <div className="card absolute left-0 right-0 top-full mt-1 z-10 max-h-[60vh] overflow-y-auto scrollbar p-2">
                    {tocHeader}
                    {tocNav(() => setTocOpen(false))}
                  </div>
                </>
              )}
            </div>
          </div>

          {/* 各部分长文 */}
          {sections.map((s) => {
            const Icon = SECTION_ICON[s.id]
            return (
              <section key={s.id} id={`sec-${s.id}`} className="mb-12 scroll-mt-24">
                <h2 className="section-title !text-[18px] flex items-center gap-2 mb-4 pb-2 border-b border-border">
                  {Icon && <Icon className="w-[18px] h-[18px] text-muted-foreground shrink-0" />}
                  {s.title}
                </h2>
                <div className="prose prose-base dark:prose-invert max-w-none leading-relaxed prose-headings:mt-6 prose-headings:mb-2 prose-h2:text-[16px] prose-h3:text-[15px] prose-h4:text-[14px] prose-h2:font-semibold prose-h3:font-semibold prose-p:my-3 prose-p:text-foreground/90 prose-li:my-1 prose-table:my-4 prose-th:px-3 prose-th:py-2 prose-td:px-3 prose-td:py-2 prose-strong:text-foreground">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={headingComponents(s.id)}>
                    {s.markdown}
                  </ReactMarkdown>
                </div>
              </section>
            )
          })}

          {/* Historical decision comparison */}
          <section id="sec-history" className="mb-10 scroll-mt-24">
            <h2 className="section-title !text-[18px] flex items-center gap-2 mb-4 pb-2 border-b border-border">
              <History className="w-[18px] h-[18px] text-muted-foreground shrink-0" />
              Historical Decisions vs. Actual Returns
            </h2>
            {stats && (
              <div className="mb-4 grid grid-cols-2 divide-x divide-y divide-border rounded-xl bg-accent/30 md:grid-cols-4 md:divide-y-0">
                <div className="p-3">
                  <div className="stat-label mb-1">Overall Hit Rate</div>
                  <div className="stat-value">{stats.overall_hit_rate != null ? `${(stats.overall_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="p-3">
                  <div className="stat-label mb-1">Buy Hit Rate</div>
                  <div className="stat-value">{stats.buy_hit_rate != null ? `${(stats.buy_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="p-3">
                  <div className="stat-label mb-1">Sell Hit Rate</div>
                  <div className="stat-value">{stats.sell_hit_rate != null ? `${(stats.sell_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="p-3">
                  <div className="stat-label mb-1">Avg 20-Day Return</div>
                  <div className={`stat-value ${pctClass(stats.avg_return_20d_pct)}`}>{fmtPct(stats.avg_return_20d_pct)}</div>
                </div>
              </div>
            )}
            {items.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="col-head text-left py-2 pr-3">Date</th>
                      <th className="col-head text-left py-2 px-2">Decision</th>
                      <th className="col-head text-right py-2 px-2">Price at Analysis</th>
                      <th className="col-head text-right py-2 px-2">1d</th>
                      <th className="col-head text-right py-2 px-2">5d</th>
                      <th className="col-head text-right py-2 px-2">20d</th>
                      <th className="col-head text-right py-2 pl-2">Hit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((it, i) => (
                      <tr key={i} className="row-hover border-b border-border">
                        <td className="py-2 pr-3">{it.analysis_date}</td>
                        <td className="py-2 px-2">{it.action_label}{it.confidence != null ? ` (${it.confidence.toFixed(1)})` : ''}</td>
                        <td className="text-right py-2 px-2 tabular-nums">{it.price_at_analysis ?? '-'}</td>
                        <td className={`text-right py-2 px-2 tabular-nums ${pctClass(it.return_1d_pct)}`}>{fmtPct(it.return_1d_pct)}</td>
                        <td className={`text-right py-2 px-2 tabular-nums ${pctClass(it.return_5d_pct)}`}>{fmtPct(it.return_5d_pct)}</td>
                        <td className={`text-right py-2 px-2 tabular-nums ${pctClass(it.return_20d_pct)}`}>{fmtPct(it.return_20d_pct)}</td>
                        <td className="text-right py-2 pl-2">{it.hit_20d == null ? '-' : it.hit_20d ? '✓' : '✗'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState
                size="sm"
                icon={Inbox}
                title="No historical decisions yet"
                description="Once this stock has prior AI decisions with enough elapsed time, their actual returns will appear here."
              />
            )}
          </section>

          {/* Disclaimer */}
          <div className="helper-text italic border-t border-border pt-4">
            This analysis is generated by an AI multi-agent framework, for learning and research reference only, and does not constitute investment advice. Investing carries risk; make decisions independently.
          </div>
          </article>
        </div>

        {/* Right column: final decision + TOC merged into one card (starts level with the title, not overlapped by it; theme tokens adapt to light/dark) */}
        <aside className="hidden lg:block w-52 shrink-0">
          <div className="sticky top-24 card overflow-hidden">
            {/* Final decision summary */}
            {sug && (
              <div className="p-3.5 border-b border-border">
                <div className="flex items-baseline justify-between gap-2">
                  <span className={`text-[22px] font-black tracking-[-0.02em] leading-none tabular-nums ${DECISION_COLOR[sug.action] || ''}`}>
                    {sug.action_label}
                  </span>
                  <span className="helper-text shrink-0">
                    ${rawData.cost_usd?.toFixed(4) ?? '-'}
                  </span>
                </div>
                {sug.confidence != null && (
                  <div className="mt-2.5">
                    <div className="flex items-center justify-between helper-text mb-1">
                      <span>Confidence</span>
                      <span className="font-medium text-foreground tabular-nums">{sug.confidence.toFixed(1)} / 10</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                      <div
                        className="h-full rounded-full bg-foreground"
                        style={{ width: `${Math.max(0, Math.min(100, sug.confidence * 10))}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
            {/* 目录 */}
            <div className="p-2">
              {tocHeader}
              <div className="max-h-[calc(100vh-19rem)] overflow-y-auto scrollbar">{tocNav()}</div>
            </div>
          </div>
        </aside>
      </div>

      {/* 分享卡片(导出 PNG) */}
      <ShareCardModal
        open={shareOpen}
        onClose={() => setShareOpen(false)}
        result={result}
        symbol={symbol}
        date={date}
      />
    </div>
  )
}
