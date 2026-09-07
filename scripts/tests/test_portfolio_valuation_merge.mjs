// Isolated out-of-band test for frontend/src/lib/portfolio-valuation.ts
// (the extracted `mergePortfolioQuotes` — round 4 fix for REVIEW-03.md P1,
// round 5 scope/quote/FX parity for REVIEW-04.md section B).
//
// Run with:
//   node --experimental-strip-types scripts/tests/test_portfolio_valuation_merge.mjs
//
// Imports the REAL module by absolute file:// URL, resolved from
// import.meta.url — a bare relative specifier fails under
// --experimental-strip-types run from an arbitrary cwd (same idiom as
// scripts/tests/test_suggestion_action_labels.mjs). No new dependency:
// node:assert/strict + node:url only.
//
// Regression under test (mirrors tests/test_portfolio_valuation.py so client
// and server are proven to agree on the SAME arithmetic):
// a position with no live quote must contribute its full native cost to the
// cost basis but NOTHING to market value — the old client-side merge
// (frontend/src/pages/Stocks.tsx ~303/~325, same bug as accounts.py ~518/~551)
// subtracted an unpriced position's full cost from priced gains, turning a
// priced-subset net of +80.88 into a bogus deep loss. A genuine 0.00 P&L must
// also survive (no `if (pnl)`-style truthiness null-out).
//
// Round 5 (REVIEW-04 section B) — the server is the scope authority and the
// merge never invents a price or an FX rate:
//   - `total.market_scope` (else per-position `valuation_status`) decides
//     support; the module has NO hardcoded enabled-market list.
//   - a quote entry PRESENT with current_price null = unavailable (price is
//     dropped); an ABSENT entry = no update (prior price kept as last_known).
//   - CAD_USD is used only when the payload supplies it with fx_status known
//     — a rate supplied with no provenance fails closed. No 0.73 default.
//   - a last_known price/rate never makes valuation_complete true.
//
// quotes keying: mergePortfolioQuotes's `quotes` argument is keyed
// "MARKET:SYMBOL" (see frontend/src/pages/Stocks.tsx's pre-extraction
// `quotes[\`${pos.market}:${pos.symbol}\`]` lookup at ~256) — NOT a bare
// symbol. This is the same same-symbol-cross-market collision the server-side
// `_fetch_quotes_for_stocks` re-keying fixes; the client test for it below
// exercises the identical shape.

import assert from 'node:assert/strict'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const modulePath = path.resolve(here, '..', '..', 'frontend', 'src', 'lib', 'portfolio-valuation.ts')
const moduleUrl = pathToFileURL(modulePath).href

const { mergePortfolioQuotes } = await import(moduleUrl)

let failures = 0
let checks = 0

function check(description, fn) {
  checks += 1
  try {
    fn()
  } catch (err) {
    failures += 1
    console.error(`FAIL: ${description}`)
    console.error(err && err.message ? err.message : err)
  }
}

function approx(actual, expected, eps = 0.01, msg = '') {
  assert.ok(
    typeof actual === 'number' && Math.abs(actual - expected) <= eps,
    `${msg} expected ~${expected}, got ${actual}`,
  )
}

// ---------------------------------------------------------------------------
// Fixture builders: a bare-bones "initial fetch, include_quotes=false" shaped
// position/account/portfolio, matching the pre-merge shape from Stocks.tsx.
// The portfolio carries the round-5 response contract by default
// (`total.market_scope`, `exchange_rates`, `fx_status`); each can be omitted
// with `null` to simulate an older payload.
// ---------------------------------------------------------------------------

let _nextId = 1

const DEFAULT_SCOPE = ['US', 'CA', 'CRYPTO', 'GOLD']

function makePosition({ symbol, market, cost_price, quantity, trading_style = 'swing', ...rest }) {
  return {
    id: _nextId++,
    stock_id: _nextId,
    sort_order: 0,
    symbol,
    name: symbol,
    market,
    cost_price,
    quantity,
    invested_amount: null,
    trading_style,
    // Pre-merge state (as returned by /portfolio/summary?include_quotes=false):
    // nothing priced yet.
    current_price: null,
    current_price_cny: null,
    change_pct: null,
    market_value: null,
    market_value_cny: null,
    pnl: null,
    pnl_pct: null,
    daily_pnl: null,
    daily_pnl_pct: null,
    exchange_rate: null,
    ...rest,
  }
}

function makePortfolio(
  positions,
  { available_funds = 0, cadUsd = 1.0, marketScope = DEFAULT_SCOPE, fxStatus = 'known' } = {},
) {
  const portfolio = {
    accounts: [
      {
        id: 1,
        name: 'Main',
        available_funds,
        total_market_value: 0,
        total_cost: 0,
        total_pnl: 0,
        total_pnl_pct: 0,
        total_daily_pnl: 0,
        total_assets: 0,
        positions,
      },
    ],
    total: {
      total_market_value: 0,
      total_cost: 0,
      total_pnl: 0,
      total_pnl_pct: 0,
      total_daily_pnl: 0,
      available_funds,
      total_assets: 0,
    },
  }
  if (marketScope !== null) portfolio.total.market_scope = marketScope
  if (cadUsd !== null) portfolio.exchange_rates = { CAD_USD: cadUsd }
  if (fxStatus !== null) portfolio.fx_status = { CAD_USD: fxStatus }
  return portfolio
}

const row = (merged, symbol, market) =>
  merged.accounts[0].positions.find(p => p.symbol === symbol && (market == null || p.market === market))

// ---------------------------------------------------------------------------
// 1) THE PINNED REGRESSION: priced-subset total_pnl === +80.88, not -7419.12.
//
// Same numbers as tests/test_portfolio_valuation.py's pinned fixture (see that
// file's docstring for why SHOP.TO uses cost_price=100.00/quantity=28/
// current_price=112.71 rather than REVIEW-03.md's literal "90x30 @106.25" —
// the latter computes to +487.50, not the pinned +355.88; these numbers hit
// +355.88 exactly so AAPL(+1235.00) + NVDA(-1510.00) + MSFT(0.00) +
// SHOP.TO(+355.88) = +80.88). CAD_USD mocked at 1.0 so the CA row's native
// gain converts 1:1.
// ---------------------------------------------------------------------------

{
  const aapl = makePosition({ symbol: 'AAPL', market: 'US', cost_price: 180.0, quantity: 50 })
  const nvda = makePosition({ symbol: 'NVDA', market: 'US', cost_price: 560.0, quantity: 20 })
  const msft = makePosition({ symbol: 'MSFT', market: 'US', cost_price: 410.0, quantity: 10 })
  const shop = makePosition({ symbol: 'SHOP.TO', market: 'CA', cost_price: 100.0, quantity: 28 })
  const cnLegacy = makePosition({ symbol: '600519', market: 'CN', cost_price: 1500.0, quantity: 5 })

  const portfolio = makePortfolio([aapl, nvda, msft, shop, cnLegacy], {
    available_funds: 500,
    cadUsd: 1.0,
  })

  const quotes = {
    'US:AAPL': { current_price: 204.7, change_pct: null },
    'US:NVDA': { current_price: 484.5, change_pct: null },
    'US:MSFT': { current_price: 410.0, change_pct: null },
    'CA:SHOP.TO': { current_price: 112.71, change_pct: null },
    // 600519 deliberately has no quote entry: unpriced.
  }

  const merged = mergePortfolioQuotes(portfolio, quotes)

  check('pinned: total.total_pnl is approx +80.88 (priced-subset)', () => {
    approx(merged.total.total_pnl, 80.88, 0.01, 'total_pnl')
  })

  check('pinned: total.total_pnl is NOT -7419.12', () => {
    assert.ok(Math.abs(merged.total.total_pnl - -7419.12) > 1, 'total_pnl must not equal the bogus full-loss figure')
  })

  check('pinned: total.pnl_basis === "priced_subset"', () => {
    assert.equal(merged.total.pnl_basis, 'priced_subset')
  })

  check('pinned: total.valuation_complete === false', () => {
    assert.equal(merged.total.valuation_complete, false)
  })

  check('pinned: total.unpriced_positions === 1 and unsupported_positions === 1', () => {
    assert.equal(merged.total.unpriced_positions, 1)
    assert.equal(merged.total.unsupported_positions, 1)
  })

  check('pinned: total.priced_positions === 4', () => {
    assert.equal(merged.total.priced_positions, 4)
  })

  // 2) TRUE ZERO SURVIVES: MSFT's row pnl === 0 (not null, not dropped).
  const msftRow = merged.accounts[0].positions.find(p => p.symbol === 'MSFT')
  check("true zero survives: MSFT row pnl === 0 and !== null/undefined", () => {
    assert.equal(msftRow.priced, true)
    assert.equal(msftRow.valuation_status, 'priced')
    assert.equal(msftRow.pnl, 0)
    assert.notEqual(msftRow.pnl, null)
    assert.notEqual(msftRow.pnl, undefined)
  })

  const cnRow = merged.accounts[0].positions.find(p => p.symbol === '600519')
  check('pinned: CN row is unsupported, unpriced, and its native cost survives', () => {
    assert.equal(cnRow.priced, false)
    assert.equal(cnRow.valuation_status, 'unsupported')
    assert.equal(cnRow.pnl, null)
    assert.equal(cnRow.market_value, null)
    approx(cnRow.cost, 7500.0, 0.01, 'cn native cost')
  })
}

// ---------------------------------------------------------------------------
// 3) ALL UNPRICED: no quotes at all -> total_pnl/total_pnl_pct must be null,
// never a substituted 0/0.0.
// ---------------------------------------------------------------------------

{
  const aapl = makePosition({ symbol: 'AAPL', market: 'US', cost_price: 100.0, quantity: 10 })
  const nvda = makePosition({ symbol: 'NVDA', market: 'US', cost_price: 200.0, quantity: 5 })
  const portfolio = makePortfolio([aapl, nvda], { available_funds: 100, cadUsd: 1.0 })

  const merged = mergePortfolioQuotes(portfolio, {})

  check('all-unpriced: total.total_pnl is null (not 0/0.0)', () => {
    assert.equal(merged.total.total_pnl, null)
  })
  check('all-unpriced: total.total_pnl_pct is null (not 0/0.0)', () => {
    assert.equal(merged.total.total_pnl_pct, null)
  })
  check('all-unpriced: total.priced_positions === 0', () => {
    assert.equal(merged.total.priced_positions, 0)
  })
  check('all-unpriced: every position row has priced === false and pnl === null', () => {
    for (const pos of merged.accounts[0].positions) {
      assert.equal(pos.priced, false)
      assert.equal(pos.pnl, null)
      assert.equal(pos.market_value, null)
      assert.notEqual(pos.cost, null) // native cost always present
    }
  })
}

// ---------------------------------------------------------------------------
// 4) SAME-SYMBOL TWO ENABLED MARKETS: quotes keyed "MARKET:SYMBOL" must not
// cross-contaminate a shared symbol across US/CA.
// ---------------------------------------------------------------------------

{
  const usShop = makePosition({ symbol: 'SHOP', market: 'US', cost_price: 50.0, quantity: 10 })
  const caShop = makePosition({ symbol: 'SHOP', market: 'CA', cost_price: 60.0, quantity: 20 })
  const portfolio = makePortfolio([usShop, caShop], { available_funds: 0, cadUsd: 1.0 })

  const quotes = {
    'US:SHOP': { current_price: 55.0, change_pct: null },
    'CA:SHOP': { current_price: 65.0, change_pct: null },
  }

  const merged = mergePortfolioQuotes(portfolio, quotes)
  const usRow = merged.accounts[0].positions.find(p => p.market === 'US')
  const caRow = merged.accounts[0].positions.find(p => p.market === 'CA')

  check('same-symbol markets: US row gets its own 55.00 price', () => {
    approx(usRow.current_price, 55.0, 0.001, 'US current_price')
  })
  check('same-symbol markets: CA row gets its own 65.00 price (not US\'s)', () => {
    approx(caRow.current_price, 65.0, 0.001, 'CA current_price')
  })
  check('same-symbol markets: no cross-contamination', () => {
    assert.notEqual(usRow.current_price, caRow.current_price)
  })
}

// ===========================================================================
// ROUND 5 — REVIEW-04 section B
// ===========================================================================

// ---------------------------------------------------------------------------
// 5) THE REPRODUCED PROBE (REVIEW-04 "Independent verification"): one CA
// position carrying server `valuation_status: 'unsupported'`, current_price
// 120, cost 100, quantity 1, exchange_rate null, NO exchange_rates, NO
// market_scope, NO fx_status, refreshed with an EXPLICIT
// `{current_price: null, change_pct: null}` quote. Before round 5 this came
// back priced @120 with an invented 0.73 rate, pnl 14.60 and
// valuation_complete true.
// ---------------------------------------------------------------------------

{
  const probe = makePosition({
    symbol: 'SHOP.TO', market: 'CA', cost_price: 100, quantity: 1,
    current_price: 120, valuation_status: 'unsupported',
  })
  const portfolio = makePortfolio([probe], { cadUsd: null, marketScope: null, fxStatus: null })
  const merged = mergePortfolioQuotes(portfolio, { 'CA:SHOP.TO': { current_price: null, change_pct: null } })
  const r = row(merged, 'SHOP.TO')

  check('probe: stays unsupported (server status honoured, no hardcoded scope)', () => {
    assert.equal(r.valuation_status, 'unsupported')
    assert.equal(r.priced, false)
  })
  check('probe: explicit-null quote drops the stale 120 price (price_status unavailable)', () => {
    assert.equal(r.current_price, null)
    assert.equal(r.price_status, 'unavailable')
    assert.equal(r.market_value, null)
  })
  check('probe: NO invented FX — exchange_rate/cost_usd/market_value_cny/pnl all null', () => {
    assert.equal(r.exchange_rate, null)
    assert.equal(r.fx_status, 'unknown')
    assert.equal(r.cost_usd, null)
    assert.equal(r.cost_cny, null)
    assert.equal(r.market_value_cny, null)
    assert.equal(r.current_price_cny, null)
    assert.equal(r.pnl, null)
    assert.equal(r.pnl_pct, null)
    assert.equal(r.daily_pnl, null)
    assert.equal(merged.exchange_rates.CAD_USD, null)
    assert.equal(merged.fx_status.CAD_USD, 'unknown')
  })
  check('probe: native cost survives (100.00)', () => {
    approx(r.cost, 100.0, 0.001, 'native cost')
  })
  check('probe: total.valuation_complete false, total_pnl null, unsupported_positions 1', () => {
    assert.equal(merged.total.valuation_complete, false)
    assert.equal(merged.total.total_assets_complete, false)
    assert.equal(merged.total.total_pnl, null)
    assert.equal(merged.total.total_pnl_pct, null)
    assert.equal(merged.total.total_market_value, 0)
    assert.equal(merged.total.unsupported_positions, 1)
    assert.equal(merged.total.priced_positions, 0)
    assert.equal(merged.total.cost_basis_complete, false)
  })

  // Same probe, but the server enables CA — the price is still gone and the
  // FX is still unknown, so it is 'unavailable', never priced.
  const portfolio2 = makePortfolio(
    [makePosition({ symbol: 'SHOP.TO', market: 'CA', cost_price: 100, quantity: 1, current_price: 120, valuation_status: 'unsupported' })],
    { cadUsd: null, marketScope: ['US', 'CA'], fxStatus: null },
  )
  const merged2 = mergePortfolioQuotes(portfolio2, { 'CA:SHOP.TO': { current_price: null, change_pct: null } })
  const r2 = row(merged2, 'SHOP.TO')
  check('probe (CA in scope, no FX): unavailable, price dropped, no FX invented', () => {
    assert.equal(r2.valuation_status, 'unavailable')
    assert.equal(r2.current_price, null)
    assert.equal(r2.price_status, 'unavailable')
    assert.equal(r2.exchange_rate, null)
    assert.equal(r2.cost_usd, null)
    assert.equal(r2.pnl, null)
    assert.equal(merged2.total.valuation_complete, false)
  })
}

// ---------------------------------------------------------------------------
// 6) INITIAL-LOAD vs REFRESH PARITY: the same price reached via the server
// snapshot (no quote round yet) and via a quote refresh (server unpriced)
// must produce the SAME arithmetic. Freshness differs honestly: the snapshot
// path has no quote entry, so its price is last_known and the valuation is
// not complete; the refresh path is fresh and complete.
// ---------------------------------------------------------------------------

{
  const viaServer = makePortfolio([
    makePosition({ symbol: 'AAPL', market: 'US', cost_price: 180, quantity: 50, current_price: 204.7, change_pct: 1.5, valuation_status: 'priced', price_status: 'fresh' }),
    makePosition({ symbol: 'SHOP.TO', market: 'CA', cost_price: 100, quantity: 28, current_price: 112.71, change_pct: -0.5, valuation_status: 'priced', price_status: 'fresh' }),
  ], { available_funds: 500, cadUsd: 0.75 })
  const viaRefresh = makePortfolio([
    makePosition({ symbol: 'AAPL', market: 'US', cost_price: 180, quantity: 50, valuation_status: 'unavailable' }),
    makePosition({ symbol: 'SHOP.TO', market: 'CA', cost_price: 100, quantity: 28, valuation_status: 'unavailable' }),
  ], { available_funds: 500, cadUsd: 0.75 })

  const a = mergePortfolioQuotes(viaServer, {})
  const b = mergePortfolioQuotes(viaRefresh, {
    'US:AAPL': { current_price: 204.7, change_pct: 1.5 },
    'CA:SHOP.TO': { current_price: 112.71, change_pct: -0.5 },
  })

  const numericFields = ['current_price', 'current_price_cny', 'market_value', 'market_value_cny', 'pnl', 'pnl_pct', 'daily_pnl', 'daily_pnl_pct', 'cost', 'cost_usd', 'exchange_rate']
  check('parity: every per-position number is identical on both paths', () => {
    for (const sym of ['AAPL', 'SHOP.TO']) {
      for (const f of numericFields) {
        assert.deepEqual(row(a, sym)[f], row(b, sym)[f], `${sym}.${f}`)
      }
    }
  })
  check('parity: totals (market value, pnl, pnl_pct, daily pnl, assets) identical on both paths', () => {
    for (const f of ['total_market_value', 'total_cost', 'total_pnl', 'total_pnl_pct', 'total_daily_pnl', 'total_assets', 'priced_positions']) {
      assert.deepEqual(a.total[f], b.total[f], `total.${f}`)
    }
    approx(b.total.total_pnl, 1235.0 + (112.71 * 28 - 100 * 28) * 0.75, 0.02, 'total_pnl')
  })
  check('parity: snapshot path is last_known/incomplete, refresh path is fresh/complete', () => {
    assert.equal(row(a, 'AAPL').price_status, 'last_known')
    assert.equal(row(a, 'AAPL').priced, true)
    assert.equal(a.total.valuation_complete, false)
    assert.equal(a.total.pnl_basis, 'last_known')
    assert.equal(a.total.last_known_positions, 2)
    assert.equal(a.total.fresh_positions, 0)
    assert.equal(a.total.unpriced_positions, 0)
    assert.equal(row(b, 'AAPL').price_status, 'fresh')
    assert.equal(b.total.valuation_complete, true)
    assert.equal(b.total.pnl_basis, 'complete')
    assert.equal(b.total.fresh_positions, 2)
    assert.equal(b.total.last_known_positions, 0)
  })
}

// ---------------------------------------------------------------------------
// 7) NON-DEFAULT SCOPE: US-only deployment (CA disabled). The CA row has a
// fresh quote AND a known rate, yet the server says CA is out of scope — it
// must be unsupported with no USD conversion.
// ---------------------------------------------------------------------------

{
  const portfolio = makePortfolio([
    makePosition({ symbol: 'AAPL', market: 'US', cost_price: 100, quantity: 10 }),
    makePosition({ symbol: 'SHOP.TO', market: 'CA', cost_price: 60, quantity: 20 }),
  ], { marketScope: ['US'], cadUsd: 0.75 })
  const merged = mergePortfolioQuotes(portfolio, {
    'US:AAPL': { current_price: 110, change_pct: null },
    'CA:SHOP.TO': { current_price: 65, change_pct: null },
  })
  const ca = row(merged, 'SHOP.TO')
  const us = row(merged, 'AAPL')

  check('US-only scope: CA row unsupported despite quote + rate; native values kept; no USD values', () => {
    assert.equal(ca.valuation_status, 'unsupported')
    assert.equal(ca.priced, false)
    approx(ca.current_price, 65, 0.001, 'native price kept')
    approx(ca.market_value, 1300, 0.01, 'native market value')
    approx(ca.cost, 1200, 0.01, 'native cost')
    assert.equal(ca.exchange_rate, null)
    assert.equal(ca.cost_usd, null)
    assert.equal(ca.market_value_cny, null)
    assert.equal(ca.pnl, null)
  })
  check('US-only scope: US row priced; totals exclude CA', () => {
    assert.equal(us.valuation_status, 'priced')
    approx(merged.total.total_market_value, 1100, 0.01, 'total mv')
    approx(merged.total.total_pnl, 100, 0.01, 'total pnl')
    assert.equal(merged.total.unsupported_positions, 1)
    assert.equal(merged.total.valuation_complete, false)
    assert.equal(merged.total.pnl_basis, 'priced_subset')
  })
  // Scope wins over a stale per-position status in BOTH directions.
  const staleStatus = makePortfolio([
    makePosition({ symbol: 'SHOP.TO', market: 'CA', cost_price: 60, quantity: 20, valuation_status: 'priced' }),
  ], { marketScope: ['US'], cadUsd: 0.75 })
  const m2 = mergePortfolioQuotes(staleStatus, { 'CA:SHOP.TO': { current_price: 65, change_pct: null } })
  check('US-only scope: server scope overrides a per-position "priced" status (CA disabled => unsupported)', () => {
    assert.equal(row(m2, 'SHOP.TO').valuation_status, 'unsupported')
    assert.equal(row(m2, 'SHOP.TO').cost_usd, null)
  })
}

// ---------------------------------------------------------------------------
// 8) SCOPE FALLBACKS: no market_scope -> per-position valuation_status; no
// status either -> fail closed (unsupported). Unknown market codes.
// ---------------------------------------------------------------------------

{
  const noScope = makePortfolio([
    makePosition({ symbol: 'AAPL', market: 'US', cost_price: 100, quantity: 1, valuation_status: 'unavailable' }),
    makePosition({ symbol: 'MSFT', market: 'US', cost_price: 100, quantity: 1, valuation_status: 'priced' }),
    makePosition({ symbol: '600519', market: 'CN', cost_price: 100, quantity: 1, valuation_status: 'unsupported' }),
    makePosition({ symbol: 'ZZZZ', market: 'XX', cost_price: 100, quantity: 1 }), // no status at all
  ], { marketScope: null })
  const merged = mergePortfolioQuotes(noScope, {
    'US:AAPL': { current_price: 110, change_pct: null },
    'US:MSFT': { current_price: 90, change_pct: null },
    'CN:600519': { current_price: 120, change_pct: null },
    'XX:ZZZZ': { current_price: 120, change_pct: null },
  })
  check('no scope: per-position status decides support (unavailable->priced once quoted; unsupported stays)', () => {
    assert.equal(row(merged, 'AAPL').valuation_status, 'priced')
    assert.equal(row(merged, 'MSFT').valuation_status, 'priced')
    assert.equal(row(merged, '600519').valuation_status, 'unsupported')
    assert.equal(row(merged, '600519').cost_usd, null)
  })
  check('no scope and no status: fails closed (unsupported), never valued', () => {
    const r = row(merged, 'ZZZZ')
    assert.equal(r.valuation_status, 'unsupported')
    assert.equal(r.priced, false)
    assert.equal(r.cost_usd, null)
    assert.equal(r.pnl, null)
    approx(r.market_value, 120, 0.001, 'native market value still computed')
  })

  // An unknown market the server DOES enable: supported, but its currency
  // is unknown to us so FX is unknown — unavailable, never a 1:1 guess.
  const oddScope = makePortfolio([
    makePosition({ symbol: 'ZZZZ', market: 'XX', cost_price: 100, quantity: 2 }),
  ], { marketScope: ['US', 'XX'] })
  const m3 = mergePortfolioQuotes(oddScope, { 'XX:ZZZZ': { current_price: 120, change_pct: null } })
  check('unknown market in scope: supported but FX unknown -> unavailable, fx_unknown_positions 1, no USD values', () => {
    const r = row(m3, 'ZZZZ')
    assert.equal(r.valuation_status, 'unavailable')
    assert.equal(r.fx_status, 'unknown')
    assert.equal(r.priced, false)
    assert.equal(r.cost_usd, null)
    assert.equal(r.market_value_cny, null)
    approx(r.market_value, 240, 0.001, 'native mv')
    assert.equal(m3.total.fx_unknown_positions, 1)
    assert.equal(m3.total.unavailable_positions, 1)
    assert.equal(m3.total.valuation_complete, false)
  })
  // Scope with lowercase codes / a position with lowercase market code.
  const lower = makePortfolio([
    makePosition({ symbol: 'AAPL', market: 'us', cost_price: 100, quantity: 1 }),
  ], { marketScope: ['us'] })
  const m4 = mergePortfolioQuotes(lower, { 'us:AAPL': { current_price: 110, change_pct: null } })
  check('scope matching is case-insensitive', () => {
    assert.equal(row(m4, 'AAPL').valuation_status, 'priced')
  })
  // Empty scope: the server enables nothing.
  const empty = makePortfolio([makePosition({ symbol: 'AAPL', market: 'US', cost_price: 100, quantity: 1 })], { marketScope: [] })
  const m5 = mergePortfolioQuotes(empty, { 'US:AAPL': { current_price: 110, change_pct: null } })
  check('empty market_scope: everything unsupported', () => {
    assert.equal(row(m5, 'AAPL').valuation_status, 'unsupported')
    assert.equal(m5.total.unsupported_positions, 1)
  })
}

// ---------------------------------------------------------------------------
// 9) EXPLICIT-NULL QUOTE vs ABSENT ENTRY on a supported US position.
// ---------------------------------------------------------------------------

{
  const mk = () => makePortfolio([
    // prev_close (145.0) is DELIBERATELY NOT the value change_pct would
    // imply (2.0% => a prior close of ~147.06) — round 7 / contract 2
    // computes daily P&L from the carried prev_close ONLY, never by
    // reconstructing a previous close from change_pct, so a regression back
    // to that reconstruction would change the daily_pnl numbers asserted
    // below rather than silently agreeing with them.
    makePosition({ symbol: 'AAPL', market: 'US', cost_price: 100, quantity: 10, current_price: 150, change_pct: 2.0, prev_close: 145.0, valuation_status: 'priced', price_status: 'fresh' }),
    makePosition({ symbol: 'MSFT', market: 'US', cost_price: 400, quantity: 1, valuation_status: 'unavailable' }),
  ], { available_funds: 100 })

  // Explicit null: provider says unavailable -> price dropped.
  const explicitNull = mergePortfolioQuotes(mk(), {
    'US:AAPL': { current_price: null, change_pct: null },
    'US:MSFT': { current_price: 410, change_pct: null },
  })
  check('explicit-null quote: stale 150 price dropped, price_status unavailable, valuation_status unavailable, excluded from totals', () => {
    const r = row(explicitNull, 'AAPL')
    assert.equal(r.current_price, null)
    assert.equal(r.change_pct, null)
    assert.equal(r.price_status, 'unavailable')
    assert.equal(r.valuation_status, 'unavailable')
    assert.equal(r.priced, false)
    assert.equal(r.market_value, null)
    assert.equal(r.pnl, null)
    assert.equal(r.daily_pnl, null)
    approx(r.cost_usd, 1000, 0.01, 'cost_usd still known (US)')
    approx(explicitNull.total.total_market_value, 410, 0.01, 'only MSFT')
    approx(explicitNull.total.total_pnl, 10, 0.01, 'only MSFT')
    assert.equal(explicitNull.total.unavailable_positions, 1)
    assert.equal(explicitNull.total.unpriced_positions, 1)
    approx(explicitNull.total.unpriced_cost, 1000, 0.01, 'unpriced cost is AAPL cost')
    assert.equal(explicitNull.total.valuation_complete, false)
    assert.equal(explicitNull.total.pnl_basis, 'priced_subset')
  })

  // Absent entry: no update this round -> prior price kept as last_known.
  const absent = mergePortfolioQuotes(mk(), {
    'US:MSFT': { current_price: 410, change_pct: null },
  })
  check('absent quote entry: prior 150 price kept as last_known; priced but NOT fresh', () => {
    const r = row(absent, 'AAPL')
    approx(r.current_price, 150, 0.001, 'last-known price')
    assert.equal(r.price_status, 'last_known')
    assert.equal(r.valuation_status, 'priced')
    assert.equal(r.priced, true)
    approx(r.market_value, 1500, 0.01, 'mv')
    approx(r.pnl, 500, 0.01, 'pnl')
    // change_pct is a passthrough quote-provenance field, carried forward
    // independent of daily P&L (round 7 / contract 2: daily P&L is computed
    // from the carried prev_close ONLY, never from change_pct). This
    // assertion used to read `daily_pnl_pct === change_pct`, which was only
    // ever true by coincidence of the old change_pct-based reconstruction —
    // prev_close is set to a DIFFERENT number above (145 vs. the 147.06 that
    // 2% would imply) precisely so that coincidence no longer holds, and
    // these two numbers are asserted separately as what they actually are.
    approx(r.change_pct, 2.0, 0.001, 'change_pct retained as its own passthrough field')
    approx(r.daily_pnl, 50.0, 0.01, 'daily_pnl computed from the carried prev_close (150-145)*10, not change_pct')
    approx(r.daily_pnl_pct, 3.4483, 0.01, 'daily_pnl_pct computed from the carried prev_close ((150-145)/145*100), not change_pct')
  })
  check('absent quote entry: contributes to the priced subset but valuation_complete stays false (pnl_basis last_known)', () => {
    approx(absent.total.total_market_value, 1910, 0.01, 'mv')
    approx(absent.total.total_pnl, 510, 0.01, 'pnl')
    assert.equal(absent.total.priced_positions, 2)
    assert.equal(absent.total.unpriced_positions, 0)
    assert.equal(absent.total.fresh_positions, 1)
    assert.equal(absent.total.last_known_positions, 1)
    assert.equal(absent.total.valuation_complete, false)
    assert.equal(absent.total.total_assets_complete, false)
    assert.equal(absent.total.pnl_basis, 'last_known')
  })
  check('a server "fresh" status is not trusted when no quote entry exists this round', () => {
    // The raw AAPL row declared price_status 'fresh'; without a quote entry
    // the client cannot know that is still true, so it is last_known.
    assert.equal(row(absent, 'AAPL').price_status, 'last_known')
  })
}

// ---------------------------------------------------------------------------
// 10) FX: absent / unknown / last_known / legacy-numeric.
// ---------------------------------------------------------------------------

{
  const caPos = () => makePosition({ symbol: 'SHOP.TO', market: 'CA', cost_price: 60, quantity: 20 })
  const usPos = () => makePosition({ symbol: 'AAPL', market: 'US', cost_price: 100, quantity: 10 })
  const quotes = {
    'CA:SHOP.TO': { current_price: 65, change_pct: 1.0 },
    // prev_close added (round 7 / contract 2) so the absentFx case below
    // still exercises a genuine daily P&L on the (USD-valued, priced) US
    // row — a bare change_pct no longer produces one now that daily P&L is
    // computed from prev_close only.
    'US:AAPL': { current_price: 110, change_pct: 1.0, prev_close: 100 },
  }

  // No exchange_rates block at all.
  const absentFx = mergePortfolioQuotes(makePortfolio([caPos(), usPos()], { cadUsd: null, fxStatus: null }), quotes)
  check('absent FX: CA row has price but no rate -> unavailable, fx unknown, native values kept, USD values null', () => {
    const r = row(absentFx, 'SHOP.TO')
    assert.equal(r.valuation_status, 'unavailable')
    assert.equal(r.price_status, 'fresh')
    assert.equal(r.fx_status, 'unknown')
    assert.equal(r.priced, false)
    assert.equal(r.exchange_rate, null)
    approx(r.market_value, 1300, 0.01, 'native mv')
    approx(r.cost, 1200, 0.01, 'native cost')
    assert.equal(r.cost_usd, null)
    assert.equal(r.market_value_cny, null)
    assert.equal(r.current_price_cny, null)
    assert.equal(r.pnl, null)
    assert.equal(r.pnl_pct, null)
    assert.equal(r.daily_pnl, null)
  })
  check('absent FX: CA row excluded from USD aggregates; US row still counts; coverage flags it', () => {
    approx(absentFx.total.total_market_value, 1100, 0.01, 'US only')
    approx(absentFx.total.total_pnl, 100, 0.01, 'US only')
    approx(absentFx.total.total_cost, 1000, 0.01, 'known USD cost is US only')
    assert.equal(absentFx.total.cost_basis_complete, false)
    assert.equal(absentFx.total.unpriced_cost, null)
    assert.equal(absentFx.total.fx_unknown_positions, 1)
    assert.equal(absentFx.total.unavailable_positions, 1)
    assert.equal(absentFx.total.valuation_complete, false)
    assert.equal(absentFx.total.daily_pnl_complete, false)
    assert.equal(absentFx.total.daily_pnl_positions, 1)
    assert.equal(absentFx.exchange_rates.CAD_USD, null)
    assert.equal(absentFx.fx_status.CAD_USD, 'unknown')
  })

  // Server sends a number but discloses it is unknown (fallback used).
  const declaredUnknown = mergePortfolioQuotes(makePortfolio([caPos()], { cadUsd: 0.73, fxStatus: 'unknown' }), quotes)
  check('fx_status unknown: a supplied number is NOT used (no fallback laundering)', () => {
    const r = row(declaredUnknown, 'SHOP.TO')
    assert.equal(r.exchange_rate, null)
    assert.equal(r.fx_status, 'unknown')
    assert.equal(r.cost_usd, null)
    assert.equal(r.pnl, null)
    assert.equal(declaredUnknown.exchange_rates.CAD_USD, null)
  })

  // Declared last_known: usable, but never fresh.
  const lastKnown = mergePortfolioQuotes(makePortfolio([caPos()], { cadUsd: 0.75, fxStatus: 'last_known' }), quotes)
  check('fx_status last_known: rate applied with provenance retained; priced but valuation not complete', () => {
    const r = row(lastKnown, 'SHOP.TO')
    approx(r.exchange_rate, 0.75, 0.0001, 'rate')
    assert.equal(r.fx_status, 'last_known')
    assert.equal(r.price_status, 'fresh')
    assert.equal(r.priced, true)
    assert.equal(r.valuation_status, 'priced')
    approx(r.cost_usd, 900, 0.01, 'cost_usd')
    approx(r.market_value_cny, 975, 0.01, 'mv usd')
    approx(r.pnl, 75, 0.01, 'pnl')
    assert.equal(lastKnown.total.valuation_complete, false)
    assert.equal(lastKnown.total.pnl_basis, 'last_known')
    assert.equal(lastKnown.total.last_known_positions, 1)
    assert.equal(lastKnown.total.fresh_positions, 0)
    assert.equal(lastKnown.fx_status.CAD_USD, 'last_known')
  })

  // Payload supplying a numeric rate with NO provenance must FAIL CLOSED. This is
  // exactly how the server's constant fallback (0.73) was mistaken for a real rate:
  // the number is present, the status is not, and the merge used to trust it.
  const legacy = mergePortfolioQuotes(makePortfolio([caPos()], { cadUsd: 0.75, fxStatus: null }), quotes)
  check('unlabelled numeric rate (no fx_status) fails closed - never treated as known', () => {
    const r = row(legacy, 'SHOP.TO')
    assert.equal(r.exchange_rate, null, 'rate must not be adopted without provenance')
    assert.equal(r.fx_status, 'unknown')
    assert.equal(r.cost_usd, null)
    assert.equal(r.pnl, null)
    // The position could not be valued, so the portfolio is NOT complete.
    assert.equal(legacy.total.valuation_complete, false)
    assert.equal(legacy.total.pnl_basis, 'priced_subset')
  })

  // Known status is what the server contract emits.
  const known = mergePortfolioQuotes(makePortfolio([caPos()], { cadUsd: 0.75, fxStatus: 'known' }), quotes)
  check('fx_status known: applied and fresh', () => {
    assert.equal(row(known, 'SHOP.TO').fx_status, 'known')
    assert.equal(known.total.valuation_complete, true)
    assert.equal(known.total.pnl_basis, 'complete')
  })

  // Per-position exchange_rate on input is never a rate source.
  const perPos = mergePortfolioQuotes(
    makePortfolio([makePosition({ symbol: 'SHOP.TO', market: 'CA', cost_price: 60, quantity: 20, exchange_rate: 0.8 })], { cadUsd: null, fxStatus: null }),
    quotes,
  )
  check('a per-position exchange_rate on input is not a rate source when the payload has no FX', () => {
    assert.equal(row(perPos, 'SHOP.TO').exchange_rate, null)
    assert.equal(row(perPos, 'SHOP.TO').cost_usd, null)
  })
  // A US row never reports an exchange_rate and never depends on the CAD rate.
  check('US row: exchange_rate null on output, priced regardless of CAD status', () => {
    const r = row(absentFx, 'AAPL')
    assert.equal(r.exchange_rate, null)
    assert.equal(r.fx_status, 'known')
    assert.equal(r.valuation_status, 'priced')
  })
}

// ---------------------------------------------------------------------------
// 11) UNSUPPORTED -> SUPPORTED TRANSITION: a row previously merged under a
// scope that excluded CA (carrying valuation_status 'unsupported') is
// re-merged against a payload whose scope now includes CA.
// ---------------------------------------------------------------------------

{
  const raw = makePosition({ symbol: 'SHOP.TO', market: 'CA', cost_price: 60, quantity: 20 })
  const before = mergePortfolioQuotes(makePortfolio([raw], { marketScope: ['US'], cadUsd: 0.75 }), {
    'CA:SHOP.TO': { current_price: 65, change_pct: null },
  })
  const carried = before.accounts[0].positions[0]
  check('transition: first merge under US-only scope -> unsupported', () => {
    assert.equal(carried.valuation_status, 'unsupported')
    assert.equal(carried.cost_usd, null)
  })
  const after = mergePortfolioQuotes(makePortfolio([carried], { marketScope: ['US', 'CA'], cadUsd: 0.75 }), {
    'CA:SHOP.TO': { current_price: 65, change_pct: null },
  })
  check('transition: re-merge with CA in scope -> priced with the server rate (stale unsupported status discarded)', () => {
    const r = row(after, 'SHOP.TO')
    assert.equal(r.valuation_status, 'priced')
    assert.equal(r.priced, true)
    approx(r.exchange_rate, 0.75, 0.0001, 'rate')
    approx(r.cost_usd, 900, 0.01, 'cost_usd')
    approx(r.pnl, 75, 0.01, 'pnl')
    assert.equal(after.total.unsupported_positions, 0)
    assert.equal(after.total.valuation_complete, true)
  })
  // Without a scope in the new payload, the carried status is all we have —
  // so it stays unsupported (fail closed, no guessing).
  const noScopeAfter = mergePortfolioQuotes(makePortfolio([carried], { marketScope: null, cadUsd: 0.75 }), {
    'CA:SHOP.TO': { current_price: 65, change_pct: null },
  })
  check('transition without a new scope: carried unsupported status is honoured (fail closed)', () => {
    assert.equal(row(noScopeAfter, 'SHOP.TO').valuation_status, 'unsupported')
  })
}

// ---------------------------------------------------------------------------
// 12) NON-FINITE INPUT: NaN / Infinity / negative prices and rates are
// unknown, never propagated into money figures.
// ---------------------------------------------------------------------------

{
  const mk = () => makePortfolio([
    makePosition({ symbol: 'AAPL', market: 'US', cost_price: 100, quantity: 10, current_price: 150 }),
  ])
  for (const bad of [NaN, Infinity, -Infinity, -1, '150', undefined]) {
    const merged = mergePortfolioQuotes(mk(), { 'US:AAPL': { current_price: bad, change_pct: null } })
    check(`non-finite/invalid quote price ${String(bad)}: entry present -> unavailable (stale 150 not retained)`, () => {
      const r = row(merged, 'AAPL')
      assert.equal(r.current_price, null)
      assert.equal(r.price_status, 'unavailable')
      assert.equal(r.priced, false)
      assert.equal(r.market_value, null)
      assert.equal(r.pnl, null)
      assert.equal(merged.total.total_pnl, null)
    })
  }
  const badPrior = mergePortfolioQuotes(
    makePortfolio([makePosition({ symbol: 'AAPL', market: 'US', cost_price: 100, quantity: 10, current_price: NaN })]),
    {},
  )
  check('non-finite prior price with absent quote: not retained as last_known', () => {
    assert.equal(row(badPrior, 'AAPL').current_price, null)
    assert.equal(row(badPrior, 'AAPL').price_status, 'unavailable')
  })
  const badChange = mergePortfolioQuotes(mk(), { 'US:AAPL': { current_price: 150, change_pct: NaN } })
  check('non-finite change_pct: price still fresh, daily_pnl null, no NaN anywhere', () => {
    const r = row(badChange, 'AAPL')
    assert.equal(r.price_status, 'fresh')
    assert.equal(r.change_pct, null)
    assert.equal(r.daily_pnl, null)
    assert.equal(r.daily_pnl_pct, null)
    approx(r.pnl, 500, 0.01, 'pnl')
    assert.equal(badChange.total.total_daily_pnl, null)
    assert.equal(badChange.total.daily_pnl_complete, false)
  })
  for (const badRate of [NaN, Infinity, 0, -0.5, '0.75']) {
    const merged = mergePortfolioQuotes(
      makePortfolio([makePosition({ symbol: 'SHOP.TO', market: 'CA', cost_price: 60, quantity: 20 })], { cadUsd: badRate, fxStatus: 'known' }),
      { 'CA:SHOP.TO': { current_price: 65, change_pct: null } },
    )
    check(`non-finite/invalid CAD rate ${String(badRate)}: FX unknown, no USD values`, () => {
      const r = row(merged, 'SHOP.TO')
      assert.equal(r.exchange_rate, null)
      assert.equal(r.fx_status, 'unknown')
      assert.equal(r.cost_usd, null)
      assert.equal(r.pnl, null)
      assert.equal(r.valuation_status, 'unavailable')
    })
  }
  const nanTotals = mergePortfolioQuotes(mk(), { 'US:AAPL': { current_price: 150, change_pct: 1 } })
  check('no NaN leaks into any total', () => {
    for (const [k, v] of Object.entries(nanTotals.total)) {
      if (typeof v === 'number') assert.ok(Number.isFinite(v), `total.${k} is ${v}`)
    }
  })
}

// ---------------------------------------------------------------------------
// 13) TRUE ZERO and the -100% edge.
// ---------------------------------------------------------------------------

{
  const zero = mergePortfolioQuotes(
    makePortfolio([makePosition({ symbol: 'AAPL', market: 'US', cost_price: 100, quantity: 10 })], { available_funds: 50 }),
    { 'US:AAPL': { current_price: 0, change_pct: -100 } },
  )
  const r = row(zero, 'AAPL')
  check('true zero price: priced (not dropped), market_value 0, pnl -1000, pnl_pct -100', () => {
    assert.equal(r.current_price, 0)
    assert.equal(r.price_status, 'fresh')
    assert.equal(r.priced, true)
    assert.equal(r.valuation_status, 'priced')
    assert.equal(r.market_value, 0)
    assert.equal(r.market_value_cny, 0)
    assert.equal(r.current_price_cny, 0)
    approx(r.pnl, -1000, 0.01, 'pnl')
    approx(r.pnl_pct, -100, 0.01, 'pnl_pct')
  })
  check('change_pct -100 never divides by zero: daily_pnl null, totals finite', () => {
    assert.equal(r.daily_pnl, null)
    assert.equal(r.daily_pnl_pct, null)
    assert.equal(zero.total.total_market_value, 0)
    approx(zero.total.total_pnl, -1000, 0.01, 'total pnl')
    approx(zero.total.total_assets, 50, 0.01, 'assets = 0 mv + funds')
    assert.equal(zero.total.valuation_complete, true)
  })
  const zeroCost = mergePortfolioQuotes(
    makePortfolio([makePosition({ symbol: 'FREE', market: 'US', cost_price: 0, quantity: 10 })]),
    // prev_close === current_price makes the daily_pnl below a GENUINE zero
    // (round 7 / contract 2's "never fabricate a zero" clause cuts both
    // ways — a real 0 must also survive, not just a non-zero).
    { 'US:FREE': { current_price: 5, change_pct: 0, prev_close: 5 } },
  )
  check('zero cost basis: pnl 50, pnl_pct null (no divide by zero), daily_pnl 0 (genuine zero)', () => {
    const f = row(zeroCost, 'FREE')
    assert.equal(f.cost, 0)
    assert.equal(f.cost_usd, 0)
    approx(f.pnl, 50, 0.01, 'pnl')
    assert.equal(f.pnl_pct, null)
    assert.equal(f.daily_pnl, 0)
    assert.equal(f.daily_pnl_pct, 0)
    assert.equal(zeroCost.total.total_pnl_pct, null)
    assert.equal(zeroCost.total.total_daily_pnl, 0)
    assert.equal(zeroCost.total.daily_pnl_complete, true)
  })
  const zeroRate = mergePortfolioQuotes(
    makePortfolio([makePosition({ symbol: 'SHOP.TO', market: 'CA', cost_price: 60, quantity: 20 })], { cadUsd: 0, fxStatus: 'known' }),
    { 'CA:SHOP.TO': { current_price: 65, change_pct: null } },
  )
  check('a zero FX rate is invalid (unknown), not a genuine rate', () => {
    assert.equal(row(zeroRate, 'SHOP.TO').exchange_rate, null)
    assert.equal(row(zeroRate, 'SHOP.TO').fx_status, 'unknown')
  })
}

// ---------------------------------------------------------------------------
// 14) DAILY P&L coverage and empty portfolio.
// ---------------------------------------------------------------------------

{
  const merged = mergePortfolioQuotes(
    makePortfolio([
      makePosition({ symbol: 'AAPL', market: 'US', cost_price: 100, quantity: 10 }),
      makePosition({ symbol: 'MSFT', market: 'US', cost_price: 100, quantity: 10 }),
    ], { available_funds: 100 }),
    {
      'US:AAPL': { current_price: 110, change_pct: 10, prev_close: 100 },  // prev 100 -> +100 daily
      'US:MSFT': { current_price: 110, change_pct: null }, // fresh price but no prev_close -> no daily figure (partial coverage)
    },
  )
  check('daily pnl: summed over positions that have one; coverage says partial', () => {
    approx(merged.total.total_daily_pnl, 100, 0.01, 'daily')
    assert.equal(merged.total.daily_pnl_positions, 1)
    assert.equal(merged.total.daily_pnl_complete, false)
    assert.equal(merged.total.valuation_complete, true) // prices are all fresh
  })
  const empty = mergePortfolioQuotes(makePortfolio([], { available_funds: 250 }), {})
  check('empty portfolio: complete, assets = funds, daily 0, pnl null', () => {
    assert.equal(empty.total.valuation_complete, true)
    assert.equal(empty.total.pnl_basis, 'complete')
    approx(empty.total.total_assets, 250, 0.01, 'assets')
    assert.equal(empty.total.total_daily_pnl, 0)
    assert.equal(empty.total.daily_pnl_complete, true)
    assert.equal(empty.total.total_pnl, null)
  })
  check('market_scope is carried through to the merged total', () => {
    assert.deepEqual(merged.total.market_scope, DEFAULT_SCOPE)
  })
  check('null portfolio passes through as null', () => {
    assert.equal(mergePortfolioQuotes(null, {}), null)
  })
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

if (failures > 0) {
  console.error(`FAILED ${failures}/${checks} checks`)
  process.exit(1)
} else {
  console.log(`PASS: ${checks}/${checks} checks passed (portfolio valuation merge: server/client agreement)`)
}
