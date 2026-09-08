import type { DeepAnalysisResult } from '@tickerkeep/api'

export interface AnalysisSection {
  id: string
  title: string
  markdown: string
}

/**
 * Assembles the sections (decision body / four analysts / bull-bear debate /
 * risk control debate) from the deep-analysis raw_data.
 * Both the modal tabs and the detail page's long-form view share this same
 * assembly logic to avoid the two renderings drifting apart.
 * The order matches top-to-bottom on the detail page and left-to-right for
 * the modal tabs. Only sections with content are returned.
 */
export function buildAnalysisSections(
  rawData: Partial<DeepAnalysisResult['raw_data']>,
): AnalysisSection[] {
  const reports = rawData.analyst_reports || { market: '', social: '', news: '', fundamentals: '' }
  const debate = rawData.debate_history
  const riskDebate = rawData.risk_debate
  const sections: AnalysisSection[] = []

  // Decision body: section title is directly "PM Final Decision" (dropping the
  // redundant leading "Final Decision" heading that used to precede it);
  // the trader's execution plan is kept as a subheading (set apart from the decision body).
  const decisionBody = [
    rawData.final_decision || '',
    rawData.trader_plan && `### 💼 Trader Execution Plan\n\n${rawData.trader_plan}`,
  ]
    .filter(Boolean)
    .join('\n\n')
  if (decisionBody) sections.push({ id: 'decision', title: 'PM Final Decision', markdown: decisionBody })

  // The four analysts
  const analysts: [string, string][] = [
    ['market', 'Technical Analyst'],
    ['social', 'Sentiment Analyst'],
    ['news', 'News Analyst'],
    ['fundamentals', 'Fundamentals Analyst'],
  ]
  for (const [k, title] of analysts) {
    const text = (reports as unknown as Record<string, string>)[k] || ''
    if (text) sections.push({ id: k, title, markdown: text })
  }

  // Bull-bear debate (research team: debate history + research manager's ruling)
  if (debate?.history) {
    let dc = debate.history
    if (debate.judge_decision) dc += `\n\n### ⚖️ Research Manager's Ruling\n\n${debate.judge_decision}`
    sections.push({ id: 'debate', title: 'Bull-Bear Debate', markdown: dc })
  }

  // Risk control debate (risk team: aggressive/neutral/conservative debate + risk ruling)
  if (riskDebate?.history) {
    let rc = riskDebate.history
    if (riskDebate.judge_decision) rc += `\n\n### 🛡️ Risk Control Ruling\n\n${riskDebate.judge_decision}`
    sections.push({ id: 'risk', title: 'Risk Control Debate', markdown: rc })
  }

  return sections
}
