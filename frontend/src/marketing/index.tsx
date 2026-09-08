/**
 * tickerkeep-core stub for the excluded marketing site. Never rendered, because
 * isMarketingPath() (./routes) is always false; it exists so that
 * `import MarketingRoutes from './marketing'` (src/main.tsx) and
 * `lazy(() => import('@/marketing'))` (src/App.tsx) keep compiling.
 */
export default function MarketingRoutes() {
  return null
}
