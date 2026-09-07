// Cross-layer parity harness: feeds a REAL captured server response (the
// actual JSON body of `src.web.api.accounts.get_portfolio_summary`) into the
// REAL client merge function (`mergePortfolioQuotes` in
// frontend/src/lib/portfolio-valuation.ts) and asserts server vs. client
// agreement on the same numbers, using the SAME quote inputs.
//
// This is deliberately NOT a fixture test. scripts/tests/test_portfolio_valuation_merge.mjs
// (read-only reference for this file — never edited here) already covers the
// client module in isolation with hand-built fixtures; that proves the client
// is internally consistent, but it can never catch a case where the client
// and the real server payload shape/semantics have quietly diverged, because
// both the fixture's shape AND its expected numbers were typed by hand to
// agree with each other. This harness instead takes a JSON file that is the
// server's OWN serialized output (accounts/total/exchange_rates/quotes, all
// captured verbatim, never re-typed) and re-derives the client's independent
// arithmetic from the SAME raw inputs (cost_price/quantity/quotes/FX) baked
// into that same file — if the two layers disagree on the contract, this is
// the only one of the two harnesses that can catch it.
//
// ---------------------------------------------------------------------------
// HOW TO GENERATE INPUT JSON (disposable fixture DB, no live server/network):
// ---------------------------------------------------------------------------
//   1. Work in a disposable SOURCE-ONLY copy with no .env, installation data,
//      credentials or production mounts, and enforce network isolation. Imports
//      construct the configured database engine and may load Settings before a
//      test can monkeypatch them. DATA_DIR alone is NOT an isolation guarantee.
//      Use a fresh in-memory sqlite session, exactly like
//      tests/test_portfolio_valuation.py's `db_session` fixture / helpers),
//      build a small portfolio with `_make_account` / `_make_stock` /
//      `_make_position`, then monkeypatch the SAME seams the pytest suites
//      in this repo already use:
//        - `src.web.api.accounts._fetch_quotes_for_stocks` -> a stub
//          returning a dict keyed by (market, symbol) tuples (see
//          `tests/test_portfolio_valuation.py::_quote_stub`).
//        - the CAD/USD resolution seams:
//          `_cad_usd_rate_cache` / `_fetch_cad_usd_boc` /
//          `_fetch_cad_usd_macro` (to exercise cold/expired/last_known/
//          unknown transitions) — same hooks as
//          tests/test_codex_fx_transitions.py.
//   2. Call the REAL function directly, passing the fixture session explicitly.
//      This bypasses FastAPI's DI, NOT module import-time initialization; the
//      source-only sandbox in step 1 is still mandatory:
//          result = accounts_api.get_portfolio_summary(
//              account_id=None, include_quotes=True, db=db_session,
//          )
//   3. Serialize verbatim and write to a file:
//          import json
//          with open(path, "w") as f:
//              json.dump(result, f)
//   4. Run this script with that path:
//          node --experimental-strip-types scripts/tests/test_cross_layer_parity.mjs /path/to/summary.json
//      or:
//          PANWATCH_SUMMARY_JSON=/path/to/summary.json node --experimental-strip-types scripts/tests/test_cross_layer_parity.mjs
//
//   Prefer a portfolio that mixes US + CA positions, at least one unpriced
//   position, and at least one position with a genuine zero price or zero
//   gain, so the comparison below actually exercises the contract's sharp
//   edges rather than only the easy happy path. Nothing about this script
//   assumes any particular portfolio shape — it only requires the standard
//   `/portfolio/summary` response shape (accounts/total/exchange_rates/quotes).
//
// ---------------------------------------------------------------------------
// WHAT THIS SCRIPT ACTUALLY DOES (no separate fixture is ever built):
// ---------------------------------------------------------------------------
//   - Reads the captured JSON as-is: `serverSummary`.
//   - Reads its own `quotes` field (the exact (market:symbol) -> quote map
//     the server itself fetched to produce this response) as the SAME quote
//     input handed to `mergePortfolioQuotes`.
//   - Calls `mergePortfolioQuotes(serverSummary, serverSummary.quotes)` — this
//     re-derives EVERY per-position and aggregate field from scratch off the
//     raw cost_price/quantity/market/symbol fields plus that quote map plus
//     the payload's own exchange_rates/fx_status/fx_as_of/fx_source, using
//     ONLY the real client module. Nothing here is hand-typed to match.
//   - Compares the re-derived (`clientSummary`) result against the original
//     (`serverSummary`) field-by-field (round 7 / contract 2+3 widened this
//     beyond the original 6 grand-total + 4 per-position fields — see the
//     TOTAL_FIELDS / ACCOUNT_FIELDS / POSITION_FIELDS / FX_FIELDS lists
//     below): grand-total AND per-account P&L/coverage fields
//     (total_pnl/total_pnl_pct/priced_positions/valuation_complete/
//     pnl_basis/total_assets), daily P&L coverage (total_daily_pnl/
//     daily_pnl_positions/daily_pnl_complete) at both levels, per-position
//     fields (valuation_status/price_status/cost_usd/pnl/daily_pnl/
//     prev_close), and FX provenance (exchange_rates.CAD_USD/fx_status/
//     fx_as_of/fx_source) — and fails loudly — explicit message, non-zero
//     exit — the moment a field is MISSING on either side, rather than
//     silently skipping it. A fixture captured before the server started
//     serializing `prev_close` (contract 2) will correctly FAIL this harness
//     with explicit MISSING-field errors rather than silently passing —
//     that is the intended behavior, not a bug in the harness.
//
//   CAVEAT — what the 4 FX PROVENANCE comparisons do and do not prove (see
//   the FX block below): they verify that the CLIENT (mergePortfolioQuotes)
//   propagates fx_source/fx_as_of/exchange_rates.CAD_USD faithfully from the
//   payload it is handed — a dropped, mangled, or shape-regressed client
//   read (e.g. losing the dual nested-vs-flat acceptance) WILL be caught.
//   They CANNOT detect a WRONG value the server itself emitted (a
//   fabricated `fx_source`, a bogus `fx_as_of`), because the client
//   recomputation derives its own value from that exact same server payload
//   — for a pure passthrough field both sides move together, so a bad
//   server value reproduces itself on the client side and the comparison
//   agrees. `fx_status` is the one FX field whose parity check DOES catch a
//   server error indirectly: an incorrect fx_status changes what the client
//   VALUES (cascades into cost_usd/pnl/daily figures), not because the
//   fx_status line itself is independently verified. Independent
//   verification that the server's provenance values are themselves
//   correct belongs in the backend test suite, not here.

import assert from 'node:assert/strict'
import fs from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const modulePath = path.resolve(here, '..', '..', 'frontend', 'src', 'lib', 'portfolio-valuation.ts')
const moduleUrl = pathToFileURL(modulePath).href

const summaryPath = process.argv[2] || process.env.PANWATCH_SUMMARY_JSON

if (!summaryPath) {
  console.error('FATAL: no summary JSON supplied.')
  console.error('')
  console.error('Usage:')
  console.error('  node --experimental-strip-types scripts/tests/test_cross_layer_parity.mjs <path-to-summary.json>')
  console.error('  PANWATCH_SUMMARY_JSON=<path> node --experimental-strip-types scripts/tests/test_cross_layer_parity.mjs')
  console.error('')
  console.error('That file must be the REAL, verbatim JSON body returned by')
  console.error('src.web.api.accounts.get_portfolio_summary(account_id=None, include_quotes=True, db=<in-memory session>)')
  console.error('(see the comment block at the top of this file for the exact generation recipe —')
  console.error('mock _fetch_quotes_for_stocks + the CAD/USD seams, call the function directly, json.dump the result).')
  process.exit(1)
}

let raw
try {
  raw = fs.readFileSync(summaryPath, 'utf8')
} catch (err) {
  console.error(`FATAL: could not read summary JSON at ${summaryPath}: ${err.message}`)
  process.exit(1)
}

let serverSummary
try {
  serverSummary = JSON.parse(raw)
} catch (err) {
  console.error(`FATAL: summary JSON at ${summaryPath} is not valid JSON: ${err.message}`)
  process.exit(1)
}

if (!serverSummary || typeof serverSummary !== 'object' || !Array.isArray(serverSummary.accounts) || !serverSummary.total) {
  console.error(`FATAL: summary JSON at ${summaryPath} does not look like a /portfolio/summary response`)
  console.error('(expected top-level "accounts" array and "total" object).')
  process.exit(1)
}

const { mergePortfolioQuotes } = await import(moduleUrl)

const quotes = serverSummary.quotes || {}
if (!serverSummary.quotes) {
  console.error(`WARNING: summary JSON at ${summaryPath} has no "quotes" field — comparing with an empty quote map.`)
  console.error('Every position will be re-derived as last_known/unavailable rather than fresh; this is')
  console.error('almost certainly not what you intended. Regenerate with include_quotes=True.')
}

const clientSummary = mergePortfolioQuotes(serverSummary, quotes)

// ---------------------------------------------------------------------------
// Comparison plumbing. A MISSING field (key absent on either side) is a
// harder failure than a MISMATCHED field (both present, different values) —
// both end the run with a non-zero exit, but they are reported separately so
// the parent can tell "the contract fields aren't even there yet" apart from
// "the two layers computed different numbers".
// ---------------------------------------------------------------------------

const hasOwn = (obj, key) => obj != null && Object.prototype.hasOwnProperty.call(obj, key)

const missing = []
const mismatches = []
let checks = 0

function numbersAgree(a, b, eps = 0.01) {
  if (typeof a === 'number' && typeof b === 'number') {
    if (Number.isNaN(a) || Number.isNaN(b)) return false // NaN must never appear in either layer's output
    return Math.abs(a - b) <= eps
  }
  return Object.is(a, b) || a === b
}

function compareField(serverObj, clientObj, field, context) {
  const serverHas = hasOwn(serverObj, field)
  const clientHas = hasOwn(clientObj, field)
  if (!serverHas || !clientHas) {
    missing.push(
      `${context}.${field}: ${serverHas ? '' : 'MISSING on server response; '}${clientHas ? '' : 'MISSING on client-recomputed response'}`.trim(),
    )
    return
  }
  checks += 1
  const a = serverObj[field]
  const b = clientObj[field]
  if (!numbersAgree(a, b)) {
    mismatches.push(`${context}.${field}: server=${JSON.stringify(a)} client(recomputed)=${JSON.stringify(b)}`)
  }
}

// Like compareField, but for a value that lives at a DIFFERENT field name
// and/or nesting level on each side — needed for FX provenance, where the
// server nests it under `exchange_rates.{fx_status,fx_as_of,fx_source}`
// (see `exchange_rates_payload` in src/web/api/accounts.py) while
// mergePortfolioQuotes carries the resolved provenance at the TOP level
// (`clientSummary.fx_status`/`fx_as_of`/`fx_source` — see
// portfolio-valuation.ts). Never used to paper over a genuine field-name
// drift within one layer; only for this documented cross-layer shape
// difference.
function compareValueAt(serverObj, serverField, clientObj, clientField, label) {
  const serverHas = hasOwn(serverObj, serverField)
  const clientHas = hasOwn(clientObj, clientField)
  if (!serverHas || !clientHas) {
    missing.push(
      `${label}: ${serverHas ? '' : 'MISSING on server response; '}${clientHas ? '' : 'MISSING on client-recomputed response'}`.trim(),
    )
    return
  }
  checks += 1
  const a = serverObj[serverField]
  const b = clientObj[clientField]
  if (!numbersAgree(a, b)) {
    mismatches.push(`${label}: server=${JSON.stringify(a)} client(recomputed)=${JSON.stringify(b)}`)
  }
}

// ---------------------------------------------------------------------------
// 1) Grand-total parity: P&L/coverage fields, PLUS daily-P&L value, count,
// and completeness (contract 2) — a partial daily figure must never compare
// equal to a complete one just because both sides happened to omit the
// check.
// ---------------------------------------------------------------------------

const TOTAL_FIELDS = [
  'total_pnl', 'total_pnl_pct', 'priced_positions', 'valuation_complete', 'pnl_basis', 'total_assets',
  'total_daily_pnl', 'daily_pnl_positions', 'daily_pnl_complete',
]
for (const field of TOTAL_FIELDS) {
  compareField(serverSummary.total, clientSummary.total, field, 'total')
}

// ---------------------------------------------------------------------------
// 1b) Account-level parity — the SAME field list, per account, not just the
// grand total. The grand total can agree by coincidence (e.g. two accounts'
// errors cancelling out) while an individual account's figures have
// silently diverged, so this is a distinct, necessary check, not a
// restatement of (1).
// ---------------------------------------------------------------------------

const ACCOUNT_FIELDS = TOTAL_FIELDS

// ---------------------------------------------------------------------------
// 2) Per-position parity, account-by-account, matched by array index (both
// `.accounts.map` in mergePortfolioQuotes and the server's own account loop
// preserve position order) with an id cross-check so a silent reordering
// can never masquerade as agreement. Includes per-position daily P&L
// (contract 2) and `prev_close` (the canonical daily-P&L INPUT the server
// now serializes per position and in `quotes` — comparing it here catches a
// server payload that regresses to omitting it, per the harness's mandate
// to never silently pass when a required field is missing).
// ---------------------------------------------------------------------------

const POSITION_FIELDS = ['valuation_status', 'price_status', 'cost_usd', 'pnl', 'daily_pnl', 'prev_close']

if (serverSummary.accounts.length !== clientSummary.accounts.length) {
  missing.push(
    `accounts: server has ${serverSummary.accounts.length} account(s), client-recomputed has ${clientSummary.accounts.length} — cannot align positions for comparison`,
  )
} else {
  for (let ai = 0; ai < serverSummary.accounts.length; ai += 1) {
    const serverAcc = serverSummary.accounts[ai]
    const clientAcc = clientSummary.accounts[ai]
    const serverPositions = serverAcc.positions || []
    const clientPositions = clientAcc.positions || []
    const accLabel = `accounts[${ai}] (${serverAcc.name ?? 'unnamed'})`

    for (const field of ACCOUNT_FIELDS) {
      compareField(serverAcc, clientAcc, field, accLabel)
    }

    if (serverPositions.length !== clientPositions.length) {
      missing.push(
        `${accLabel}: server has ${serverPositions.length} position(s), client-recomputed has ${clientPositions.length}`,
      )
      continue
    }

    for (let pi = 0; pi < serverPositions.length; pi += 1) {
      const sp = serverPositions[pi]
      const cp = clientPositions[pi]
      const posLabel = `${accLabel}.positions[${pi}] (${sp.market ?? '?'}:${sp.symbol ?? '?'})`

      if (sp.id !== cp.id) {
        missing.push(`${posLabel}: id mismatch (server id=${sp.id}, client id=${cp.id}) — position ordering diverged`)
        continue
      }

      for (const field of POSITION_FIELDS) {
        compareField(sp, cp, field, posLabel)
      }
    }
  }
}

// ---------------------------------------------------------------------------
// 3) FX provenance parity (contract 2/3). The server nests this under
// `exchange_rates.{fx_status,fx_as_of,fx_source}.CAD_USD`
// (`exchange_rates_payload` in src/web/api/accounts.py); the client's
// recomputation carries the resolved provenance at the TOP level
// (`clientSummary.fx_status`/`fx_as_of`/`fx_source`.CAD_USD — see
// `resolveCadUsd`/`mergePortfolioQuotes` in portfolio-valuation.ts). This is
// a documented shape difference between the two layers, not a bug, so
// `compareValueAt` is used instead of `compareField`.
//
// SCOPE OF THESE 4 CHECKS: they prove the client PROPAGATES fx_source/
// fx_as_of/exchange_rates.CAD_USD faithfully out of the payload it was
// handed (catches a dropped, mangled, or shape-regressed client read). They
// do NOT independently verify the server's own provenance values are
// correct — the client recomputation derives its value from this exact same
// serverSummary, so a wrong `fx_source`/`fx_as_of` on the server reproduces
// itself on the client side and this comparison agrees regardless (a
// passthrough field can't disagree with its own source). `fx_status` is the
// exception: an incorrect fx_status changes what the client actually VALUES
// (cascades into cost_usd/pnl/daily figures elsewhere in this run), so its
// check can fail on a server error — but that's the valuation cascade
// catching it, not this line. Server-side provenance correctness is the
// backend test suite's job, not this harness's.
// ---------------------------------------------------------------------------

const serverFx = serverSummary.exchange_rates ?? {}
compareValueAt(serverFx, 'CAD_USD', clientSummary.exchange_rates ?? {}, 'CAD_USD', 'exchange_rates.CAD_USD')
compareValueAt(serverFx.fx_status ?? {}, 'CAD_USD', clientSummary.fx_status ?? {}, 'CAD_USD', 'fx_status.CAD_USD')
compareValueAt(serverFx.fx_as_of ?? {}, 'CAD_USD', clientSummary.fx_as_of ?? {}, 'CAD_USD', 'fx_as_of.CAD_USD')
compareValueAt(serverFx.fx_source ?? {}, 'CAD_USD', clientSummary.fx_source ?? {}, 'CAD_USD', 'fx_source.CAD_USD')

// ---------------------------------------------------------------------------
// Verdict.
// ---------------------------------------------------------------------------

if (missing.length > 0 || mismatches.length > 0) {
  if (missing.length > 0) {
    console.error(`FATAL: ${missing.length} field(s) missing on server and/or client side (cannot be compared):`)
    for (const m of missing) console.error(`  - ${m}`)
  }
  if (mismatches.length > 0) {
    console.error(`FATAL: ${mismatches.length} field(s) disagree between server and client-recomputed output:`)
    for (const m of mismatches) console.error(`  - ${m}`)
  }
  console.error(`\nCross-layer parity FAILED (${checks} field comparisons attempted, source: ${summaryPath})`)
  process.exit(1)
}

assert.ok(checks > 0, 'no fields were actually compared — the summary JSON is probably empty/malformed')
console.log(
  `PASS: cross-layer parity — ${checks} field comparisons agree between the real server summary and the real ` +
    `client mergePortfolioQuotes recomputation (source: ${summaryPath}).`,
)
