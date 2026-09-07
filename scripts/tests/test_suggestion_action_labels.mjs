// Isolated out-of-band test for frontend/packages/biz-ui/src/components/suggestion-action.ts.
// Run with:
//   node --experimental-strip-types scripts/tests/test_suggestion_action_labels.mjs
//
// This exercises the REAL module (imported by absolute file:// URL, resolved from
// import.meta.url — a bare relative specifier fails under --experimental-strip-types
// run from an arbitrary cwd). No new dependency: assert/strict + node:url only.
//
// Regression under test: resolveSuggestionLabel's legacy fallback echoes the raw
// stored label (e.g. a Chinese action_label with no known mapping) straight into the
// ACTIVE UI. resolveActiveActionLabel must instead fall back to the safe English
// 'Review' placeholder and must NEVER surface a CJK character for ANY input.

import assert from 'node:assert/strict'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const modulePath = path.resolve(
  here,
  '..',
  '..',
  'frontend',
  'packages',
  'biz-ui',
  'src',
  'components',
  'suggestion-action.ts',
)
const moduleUrl = pathToFileURL(modulePath).href

const {
  resolveActiveActionLabel,
  resolveSuggestionLabel,
  suggestionActionLabels,
} = await import(moduleUrl)

const CJK_RE = /[一-鿿]/

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

// ---------------------------------------------------------------------------
// resolveActiveActionLabel: canonical English label for every canonical action.
// ---------------------------------------------------------------------------

const canonicalActions = [
  ['buy', 'Buy'],
  ['add', 'Add'],
  ['reduce', 'Reduce'],
  ['sell', 'Sell'],
  ['hold', 'Hold'],
  ['watch', 'Watch'],
  ['alert', 'Alert'],
  ['avoid', 'Avoid'],
]

for (const [action, expectedLabel] of canonicalActions) {
  check(`resolveActiveActionLabel('${action}') === '${expectedLabel}'`, () => {
    assert.equal(resolveActiveActionLabel(action), expectedLabel)
    // Also confirm this matches the shared label table used elsewhere in the UI.
    assert.equal(suggestionActionLabels[action], expectedLabel)
  })
}

// ---------------------------------------------------------------------------
// Stored Chinese labels map to the canonical English label.
// ---------------------------------------------------------------------------

const chineseLabelMap = [
  ['观望', 'Watch'],
  ['买入', 'Buy'],
  ['加仓', 'Add'],
  ['减仓', 'Reduce'],
  ['清仓', 'Sell'],
  ['持有', 'Hold'],
]

for (const [zh, expectedLabel] of chineseLabelMap) {
  check(`resolveActiveActionLabel(undefined, '${zh}') === '${expectedLabel}'`, () => {
    assert.equal(resolveActiveActionLabel(undefined, zh), expectedLabel)
  })
}

// ---------------------------------------------------------------------------
// Safe unknown handling: an unmapped label must return 'Review', never the raw
// stored string. This is the actual regression — the old resolveSuggestionLabel
// echoes it back.
// ---------------------------------------------------------------------------

check("resolveActiveActionLabel(undefined, '某个未知标签') === 'Review' (not echoed)", () => {
  const result = resolveActiveActionLabel(undefined, '某个未知标签')
  assert.equal(result, 'Review')
  assert.notEqual(result, '某个未知标签')
})

check("resolveActiveActionLabel() with no action/label at all === 'Review'", () => {
  assert.equal(resolveActiveActionLabel(), 'Review')
})

check(
  "resolveSuggestionLabel(undefined, '某个未知标签') echoes the raw stored label (old behaviour, unchanged)",
  () => {
    // This documents the pre-existing (non-ACTIVE-presentation) behaviour of
    // resolveSuggestionLabel, which resolveActiveActionLabel exists to NOT repeat.
    assert.equal(resolveSuggestionLabel(undefined, '某个未知标签'), '某个未知标签')
  },
)

// ---------------------------------------------------------------------------
// The output of resolveActiveActionLabel must never contain a CJK character,
// for every input in the table above (canonical actions + Chinese stored labels
// + the unknown-label case).
// ---------------------------------------------------------------------------

const allInputsForCjkCheck = [
  ...canonicalActions.map(([action]) => [action, undefined]),
  ...chineseLabelMap.map(([zh]) => [undefined, zh]),
  [undefined, '某个未知标签'],
  [undefined, undefined],
]

for (const [action, label] of allInputsForCjkCheck) {
  check(
    `resolveActiveActionLabel(${JSON.stringify(action)}, ${JSON.stringify(label)}) contains no CJK`,
    () => {
      const result = resolveActiveActionLabel(action, label)
      assert.equal(CJK_RE.test(result), false, `unexpected CJK in "${result}"`)
    },
  )
}

// ---------------------------------------------------------------------------
// Existing resolveSuggestionLabel behaviour is unchanged for its mapped inputs.
// ---------------------------------------------------------------------------

for (const [action, expectedLabel] of canonicalActions) {
  check(`resolveSuggestionLabel('${action}') === '${expectedLabel}' (unchanged)`, () => {
    assert.equal(resolveSuggestionLabel(action), expectedLabel)
  })
}

for (const [zh, expectedLabel] of chineseLabelMap) {
  check(`resolveSuggestionLabel(undefined, '${zh}') === '${expectedLabel}' (unchanged)`, () => {
    assert.equal(resolveSuggestionLabel(undefined, zh), expectedLabel)
  })
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

if (failures > 0) {
  console.error(`FAILED ${failures}/${checks} checks`)
  process.exit(1)
} else {
  console.log(`PASS: ${checks}/${checks} checks passed (suggestion-action label mapping)`)
}
