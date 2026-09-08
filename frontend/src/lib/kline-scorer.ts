import type { KlineSummaryData } from '@tickerkeep/biz-ui/components/kline-summary-dialog'

export type Action = 'buy' | 'add' | 'reduce' | 'sell' | 'hold' | 'watch' | 'avoid'

export interface KlineEvidenceItem {
  text: string
  details?: string
  delta: number
  tag?: string
}

export interface KlineScoreSuggestion {
  action: Action
  action_label: string
  signal: string
  score: number
  evidence: KlineEvidenceItem[]
  tags: string[]
}

export function buildKlineSuggestion(s: KlineSummaryData, holding?: boolean): KlineScoreSuggestion {
  let score = 0
  const items: KlineEvidenceItem[] = []
  const tags: string[] = []

  const fmt = (n?: number | null, digits: number = 2): string => {
    if (n == null || Number.isNaN(n)) return '--'
    return Number(n).toFixed(digits)
  }

  const tf = s.timeframe || '1d'
  const asof = s.asof ? `as of ${s.asof}` : ''

  const addItem = (text: string, delta: number = 0, tag?: string, details?: string) => {
    items.push({ text, delta, tag, details })
    score += delta
    if (tag) tags.push(tag)
  }

  // Trend
  if (s.trend?.includes('Bullish')) {
    addItem('Bullish MA alignment, trend leaning strong', 2, 'Bullish', `${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  } else if (s.trend?.includes('Bearish')) {
    addItem('Bearish MA alignment, trend leaning weak', -2, 'Bearish', `${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  } else if (s.trend?.includes('Crossed')) {
    addItem('MAs crossed, trend unclear', 0, undefined, `${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  }

  // MACD
  if (s.macd_status?.includes('Golden Cross')) {
    addItem('MACD golden cross, short-term momentum strengthening', 2, 'MACD Golden Cross', `${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
  }
  if (s.macd_status?.includes('Death Cross')) {
    addItem('MACD death cross, short-term momentum weakening', -2, 'MACD Death Cross', `${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
  }
  if (s.macd_hist != null) {
    if (s.macd_hist > 0.0) {
      addItem('MACD histogram positive (momentum leaning bullish)', 1, undefined, `${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
    } else if (s.macd_hist < 0.0) {
      addItem('MACD histogram negative (momentum leaning bearish)', -1, undefined, `${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
    }
  }

  // RSI
  if (s.rsi_status?.includes('Oversold')) {
    addItem('RSI oversold, a bounce may be possible', 1, 'RSI Oversold', `${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)} (threshold <20)`)
  } else if (s.rsi_status?.includes('Strong')) {
    addItem('RSI strong, buyers in control', 1, 'RSI Strong', `${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)} (threshold 70-80)`)
  } else if (s.rsi_status?.includes('Overbought')) {
    addItem('RSI overbought, watch for pullback risk', -1, 'RSI Overbought', `${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)} (threshold >80)`)
  } else if (s.rsi_status?.includes('Weak')) {
    addItem('RSI weak, short-term pressure', -1, 'RSI Weak', `${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)} (threshold <30)`)
  } else if (s.rsi_status?.includes('Neutral')) {
    addItem('RSI neutral', 0, undefined, `${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}`)
  }

  // KDJ
  if (s.kdj_status?.includes('Golden Cross')) {
    addItem('KDJ golden cross, turning stronger short-term', 1, 'KDJ Golden Cross', `${tf} ${asof} · K/D/J: ${fmt(s.kdj_k, 1)}/${fmt(s.kdj_d, 1)}/${fmt(s.kdj_j, 1)}`)
  }
  if (s.kdj_status?.includes('Death Cross')) {
    addItem('KDJ death cross, turning weaker short-term', -1, 'KDJ Death Cross', `${tf} ${asof} · K/D/J: ${fmt(s.kdj_k, 1)}/${fmt(s.kdj_d, 1)}/${fmt(s.kdj_j, 1)}`)
  }

  // BOLL
  if (s.boll_status?.includes('Broke Above Upper Band')) {
    addItem('Broke above the Bollinger upper band, strong trend', 1, 'Broke Above Upper Band', `${tf} ${asof} · close: ${fmt(s.last_close)} · upper: ${fmt(s.boll_upper)}`)
  } else if (s.boll_status?.includes('Broke Below Lower Band')) {
    addItem('Broke below the Bollinger lower band, weakening', -1, 'Broke Below Lower Band', `${tf} ${asof} · close: ${fmt(s.last_close)} · lower: ${fmt(s.boll_lower)}`)
  }

  // Volume
  if (s.volume_trend?.includes('High Volume')) {
    addItem('High volume confirmation, capital participation rising', 1, 'High Volume', `${tf} ${asof} · volume ratio: ${fmt(s.volume_ratio, 1)}x`)
  } else if (s.volume_trend?.includes('Low Volume')) {
    addItem('Low volume, momentum lacking', -1, 'Low Volume', `${tf} ${asof} · volume ratio: ${fmt(s.volume_ratio, 1)}x`)
  }

  // Support / Resistance proximity
  if (s.last_close != null && s.support != null && s.support > 0) {
    if (s.last_close <= s.support * 1.02) {
      const dist = (s.last_close - s.support) / s.support * 100
      addItem('Price near support, chance of a bounce rising', 1, 'Near Support', `${tf} ${asof} · close: ${fmt(s.last_close)} · support: ${fmt(s.support)} · distance: ${dist >= 0 ? '+' : ''}${dist.toFixed(1)}% (threshold <=+2%)`)
    }
  }
  if (s.last_close != null && s.resistance != null && s.resistance > 0) {
    if (s.last_close >= s.resistance * 0.98) {
      const dist = (s.last_close - s.resistance) / s.resistance * 100
      addItem('Price near resistance, upside limited', -1, 'Near Resistance', `${tf} ${asof} · close: ${fmt(s.last_close)} · resistance: ${fmt(s.resistance)} · distance: ${dist >= 0 ? '+' : ''}${dist.toFixed(1)}% (threshold >=-2%)`)
    }
  }

  const holdingFlag = holding === true
  let action: Action
  if (holdingFlag) {
    if (score >= 3) action = 'add'
    else if (score >= 1) action = 'hold'
    else if (score <= -3) action = 'sell'
    else if (score <= -1) action = 'reduce'
    else action = 'watch'
  } else {
    if (score >= 3) action = 'buy'
    else if (score <= -2) action = 'avoid'
    else action = 'watch'
  }

  const uniqTags = Array.from(new Set(tags))
  const signal = uniqTags.length > 0 ? uniqTags.join(' / ') : 'Neutral technicals'

  const actionLabel = (a: Action): string => {
    switch (a) {
      case 'buy': return 'Buy'
      case 'add': return 'Add'
      case 'reduce': return 'Reduce'
      case 'sell': return 'Sell'
      case 'hold': return 'Hold'
      case 'watch': return 'Watch'
      case 'avoid': return 'Avoid'
      default: return 'Watch'
    }
  }

  return {
    action,
    action_label: actionLabel(action),
    signal,
    score,
    evidence: items,
    tags: uniqTags,
  }
}
