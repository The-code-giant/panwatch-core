// Map Python module logger names to concise English display names
export const LOGGER_MAPPING: Record<string, string> = {
  // Agents
  'src.agents.daily_report': 'Daily Report',
  'src.agents.premarket_outlook': 'Premarket Outlook',
  'src.agents.intraday_monitor': 'Intraday Monitor',
  'src.agents.base': 'Agent Execution Chain',
  'src.agents.news_digest': 'News Digest',
  'src.agents.chart_analyst': 'Chart Analyst',
  'src.agents.tradingagents': 'Deep Analysis',
  'src.agents.tradingagents.agent': 'Deep Analysis - Main Flow',
  'src.agents.tradingagents.progress': 'Deep Analysis - Progress',
  'src.agents.tradingagents.portfolio_context': 'Deep Analysis - Portfolio Context',
  'src.agents.tradingagents.toolkit_adapter': 'Deep Analysis - Data Adapter',
  'src.agents.tradingagents.paper_trading_bridge': 'Deep Analysis - Paper Trading',
  'src.agents.tradingagents.cost_tracker': 'Deep Analysis - Cost',
  'src.agents.tradingagents.llm_adapter': 'Deep Analysis - LLM',
  'src.agents.tradingagents.langchain_compat': 'Deep Analysis - Compat Layer',
  'src.agents.tradingagents.backfill': 'Deep Analysis - Backfill',
  'src.agents.tradingagents.result_mapper': 'Deep Analysis - Result Mapper',
  'tradingagents': 'Deep Analysis (Upstream)',

  // Core
  'src.core.scheduler': 'Scheduler',
  'src.core.ai_client': 'AI Client',
  'src.core.notifier': 'Notifier',
  'src.core.analysis_history': 'Analysis History',
  'src.core.suggestion_pool': 'Suggestion Pool',
  'src.core.data_collector': 'Data Collector',

  // Collectors
  'src.collectors.akshare_collector': 'Market Data Collector',
  'src.collectors.kline_collector': 'Kline Collector',
  'src.collectors.events_collector': 'Events Collector',
  'src.collectors.news_collector': 'News Collector',
  'src.collectors.chart_renderer': 'Chart Renderer',

  // Web/API
  'src.web.api': 'API',
  'src.web.app': 'Web App',
  'src.web.database': 'Database',
  'src.web.stock_list': 'Stock List',
  'api': 'API',

  // Entry
  'server': 'Server',

  // Third-party & infra
  'httpx': 'HTTP Client',
  'httpcore': 'HTTP Core',
  'urllib3': 'HTTP Library',
  'requests': 'HTTP Client',
  'uvicorn.access': 'Access Log',
  'uvicorn.error': 'Uvicorn Error',
  'uvicorn': 'Uvicorn',
  'fastapi': 'FastAPI',
  'starlette': 'Starlette',
  'sqlalchemy.engine': 'DB Engine',
  'sqlalchemy': 'SQLAlchemy',
  'apscheduler': 'APScheduler',
  'playwright': 'Browser',
  'openai': 'AI SDK',
  'tenacity': 'Retry Library',
}

export function mapLoggerName(moduleName?: string): string {
  if (!moduleName) return ''
  let bestKey = ''
  for (const key of Object.keys(LOGGER_MAPPING)) {
    if (moduleName === key || moduleName.startsWith(key)) {
      if (key.length > bestKey.length) bestKey = key
    }
  }
  return LOGGER_MAPPING[bestKey] || moduleName
}

export function loggerOptions(): { key: string, label: string }[] {
  return Object.entries(LOGGER_MAPPING).map(([key, label]) => ({ key, label }))
}
