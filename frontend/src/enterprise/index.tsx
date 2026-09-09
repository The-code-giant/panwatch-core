/**
 * tickerkeep-core stub for the excluded enterprise pages. Never rendered,
 * because isEnterprisePath() (./routes) is always false; it exists so that
 * `lazy(() => import('@/enterprise'))` (src/App.tsx) keeps compiling.
 *
 * Unlike the marketing site, enterprise pages render inside the signed-in
 * dashboard shell (rail, top bar, room shell, session), so App.tsx mounts this
 * component in the route area, not as a replacement for the whole app.
 */
export default function EnterpriseRoutes() {
  return null
}
