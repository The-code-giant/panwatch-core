# TickerKeep Core — Third-Party Licence Notice

Compiled as part of release gate **G2** (third-party licence verification), which
was previously skipped because the audit ran with networking disabled. This
document supersedes the "unverified" caveats in `NOTICE` for the items below;
`NOTICE` and `LICENSE` are unmodified and remain the canonical files for the
project's own MIT terms.

Research date for every claim in this file: **2026-09-07**, unless a different
retrieval date is noted next to a specific citation. All URLs were fetched live
during this review.

## Upstream project licence (unchanged)

TickerKeep Core is MIT-licensed. The upstream MIT copyright notice, reproduced in
`LICENSE` and required to be carried in all copies:

```
MIT License

Copyright (c) 2026 sunxiao0721

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Summary table

| Dependency | Licence | Commercial use permitted | Redistribution permitted | Required notice |
|---|---|---|---|---|
| `tradingagents` (TauricResearch/TradingAgents @ v0.3.0) | Apache License 2.0 | Yes | Yes | Reproduce the Apache-2.0 licence text; no separate NOTICE file exists upstream at this tag (see below) |
| `lightweight-charts@5.2.1` (CDN, TradingView) | Apache License 2.0 | Yes | Yes (as a linked runtime dependency, not vendored source) | **Yes — a visible attribution notice/link to tradingview.com is contractually required by the vendor's stated licence terms** (see below); this is satisfied automatically by the library's default `attributionLogo: true` behaviour, which this codebase does not override |
| Fontshare "Satoshi" (CDN) | ITF Free Font License (FFL) v2.0, 17 Aug 2026 — closed-source, not OSI/OSS | Yes | No (font files may not be redistributed/modified; CDN embedding via `@font-face`/Fontshare API is explicitly permitted) | Attribution optional ("may but are not required to credit ITF/Fontshare") |
| `weasyprint` | BSD (PyPI classifier: "License :: OSI Approved :: BSD License") | Yes | Yes | Standard BSD notice retention |
| `xhtml2pdf` | Apache License 2.0 | Yes | Yes | Standard Apache-2.0 notice retention |
| `matplotlib` | matplotlib/PSF-derived licence (BSD-style, OSI "Python Software Foundation License" classifier) | Yes | Yes | Standard permissive notice retention |
| `exchange_calendars` | Apache License 2.0 (confirmed via upstream `LICENSE` file; PyPI metadata carries no classifier) | Yes | Yes | Standard Apache-2.0 notice retention |
| `apprise` | BSD-2-Clause | Yes | Yes | Standard BSD notice retention |
| `yfinance` | Apache License 2.0 | Yes | Yes | Standard Apache-2.0 notice retention |
| `opentelemetry-sdk`, `opentelemetry-exporter-otlp` (optional, `requirements-otel.txt`) | Apache License 2.0 (opentelemetry-python monorepo `LICENSE`) | Yes | Yes | Standard Apache-2.0 notice retention |
| `fastapi`, `uvicorn`, `sqlalchemy`, `PyJWT`, `pydantic`/`pydantic-settings`, `httpx`, `apscheduler`, `pyyaml`, `certifi`, `tenacity`, `openai`, `pytest` | MIT/BSD/Apache-2.0 (industry-standard permissive; not individually re-verified — see "Checked vs. assumed" below) | Yes | Yes | Standard notice retention |
| Frontend: `react`, `react-dom`, `react-router-dom`, `@radix-ui/*`, `clsx`, `lucide-react`, `tailwindcss`, `tailwind-merge` (MIT, confirmed), `class-variance-authority` (Apache-2.0, confirmed), `cmdk` (MIT, confirmed), `react-markdown`/`remark-gfm` (MIT, confirmed), `html-to-image` (MIT, confirmed), `react-day-picker` (MIT, confirmed), `tailwindcss-animate` (MIT, confirmed), `date-fn` (ISC, confirmed — see note) | MIT / ISC / Apache-2.0 | Yes | Yes | Standard notice retention |
| Higgsfield / Meshy imagery | N/A — confirmed absent | N/A | N/A | `grep -ri "higgsfield\|meshy"` over the full export returned no matches; nothing to license |

**No AGPL, GPL, SSPL, BUSL, CC-BY-NC, or other copyleft/source-available/non-commercial
licence was found anywhere in this dependency set.** Every item above is either
permissive (MIT/BSD/ISC/Apache-2.0) or, for the one non-OSS case (Satoshi), a
free-for-commercial-use closed-source font licence with no copyleft or
redistribution-of-the-work-itself obligation on TickerKeep's own code. **None of
items 1–4 is a release blocker for MIT publication**, provided the two actions
noted under "Action items" below are taken.

---

## Item 1 — `tradingagents` (TauricResearch/TradingAgents @ v0.3.0)

- Pinned in `core/requirements.txt` line 38 and `core/pyproject.toml`
  (`deep-analysis` optional extra); imported by `src/agents/tradingagents/*`.
  It is an optional dependency — only installed/loaded when the user enables
  the "TradingAgents deep analysis" agent.
- Fetched the LICENSE **at tag v0.3.0 specifically** (not `main`):
  `https://raw.githubusercontent.com/TauricResearch/TradingAgents/v0.3.0/LICENSE`
  (HTTP 200, retrieved 2026-09-07). Content is the standard Apache License,
  Version 2.0 text.
- Confirmed via GitHub tags API (`https://api.github.com/repos/TauricResearch/TradingAgents/tags`,
  retrieved 2026-09-07) that `v0.3.0` is a real, distinct tag
  (commit `85946c2f60768ab2dae23a5a36cd927662feef94`), separate from `main`/`v0.4.0`.
  Spot-checked `https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/LICENSE`
  (retrieved 2026-09-07) — also Apache-2.0, so no drift risk between the pinned tag and
  `main` for this project.
- Checked for a `NOTICE` file at the same tag:
  `https://raw.githubusercontent.com/TauricResearch/TradingAgents/v0.3.0/NOTICE` →
  **HTTP 404 (does not exist)**. Under Apache-2.0 §4(d), a NOTICE file only needs
  to be carried forward if the upstream Work ships one; since it does not,
  there is nothing additional to reproduce beyond the licence text itself.

**Verdict: Apache License 2.0.** Commercial use, modification, and redistribution
are explicitly permitted (§2, §4). It is **not** copyleft — Apache-2.0 does not
require derivative works to be released under the same licence, and it is
explicitly compatible with MIT distribution (this is a one-directional
compatibility: an MIT project may depend on/ship Apache-2.0 code; the reverse
is not automatically true, which is not relevant here). Required notice: retain
the Apache-2.0 licence text for this dependency and do not remove copyright/
attribution notices from its source form (§4(c)). No copyleft, no
non-commercial restriction. **NOT A BLOCKER.**

## Item 2 — `lightweight-charts@5.2.1` (CDN, TradingView)

- Loaded at runtime from a CDN (not via `package.json`) —
  `core/frontend/src/ProductApp.tsx` lines 13 and 16, with `unpkg.com` primary
  and `cdn.jsdelivr.net` fallback, both pinned to `5.2.1`.
- Licence confirmed via the tag-pinned upstream repo:
  `https://raw.githubusercontent.com/tradingview/lightweight-charts/v5.2.1/LICENSE`
  (HTTP 200, retrieved 2026-09-07) — Apache License, Version 2.0. Also confirmed
  via `package.json`'s `"license": "Apache-2.0"` field at the same tag.
- **Attribution requirement — confirmed, and this is the important part.**
  The upstream `README.md` at the same tag
  (`https://raw.githubusercontent.com/tradingview/lightweight-charts/v5.2.1/README.md`,
  retrieved 2026-09-07) states, verbatim, beyond the bare Apache-2.0 text:

  > "This license requires specifying TradingView as the product creator. You
  > shall add the "attribution notice" from the NOTICE file and a link to
  > https://www.tradingview.com/ to the page of your website or mobile
  > application that is available to your users. ... You can use the
  > `attributionLogo` chart option for displaying an appropriate link to
  > https://www.tradingview.com/ on the chart itself, which will satisfy the
  > link requirement."

  The referenced NOTICE file
  (`https://raw.githubusercontent.com/tradingview/lightweight-charts/v5.2.1/NOTICE`,
  retrieved 2026-09-07) reads: `TradingView Lightweight Charts™ Copyright (с)
  2025 TradingView, Inc. https://www.tradingview.com/`.
- Checked the library's own type definitions for the default behaviour of this
  option: `https://raw.githubusercontent.com/tradingview/lightweight-charts/v5.2.1/src/model/layout-options.ts`
  (retrieved 2026-09-07) documents `attributionLogo: boolean` with
  `@defaultValue true`, and its doc-comment explicitly says using this logo
  "is sufficient for meeting this linking requirement."
- **Codebase check — is the attribution present?** Read
  `core/frontend/src/ProductApp.tsx` in full (26 lines) and grepped the whole
  frontend tree for `tradingview`/`attribution`/`attributionLogo` (case
  insensitive) — **no matches anywhere.** The only chart-creation call sites
  are in `core/frontend/packages/biz-ui/src/components/InteractiveKline.tsx`
  (three `LW.createChart(...)` calls, lines 310/377/432). None of them sets
  `layout.attributionLogo`, and no CSS rule targets or hides a TradingView
  logo/link element.

  **Conclusion: the codebase does not explicitly add or disable the
  attribution.** Because the option's documented default is `true`, the
  library itself should render its own "TradingView" attribution logo/link
  inside the chart pane by default at runtime — satisfying the vendor's
  stated linking requirement without any code change. This is a reasonable
  inference from the library's documented default, **not a visual
  confirmation**: this review did not run the app (Docker/builds were
  explicitly out of scope), so nothing renders the actual page and screenshots
  it. The chart container has `overflow-hidden` styling
  (`InteractiveKline.tsx`, the `w-full h-[380px] rounded-xl overflow-hidden ...`
  div), which should not clip the logo since it renders inside the chart's own
  canvas area, but this has not been visually verified.

**Verdict: Apache License 2.0**, commercial use and redistribution permitted.
The vendor's linking/attribution requirement is a real, explicit,
contractually-stated term (not a generic Apache clause) and **is very likely
satisfied by default** since the code never disables `attributionLogo`. **NOT
A BLOCKER as currently understood**, but flagged as an **action item**: before
shipping, visually confirm (in a running Docker build, per project convention)
that the TradingView attribution logo/link is actually visible in the chart
UI, and add an explicit `attributionLogo: true` (or an equivalent manual
"Charts by TradingView" credit linking to https://www.tradingview.com/) if it
turns out not to be — this removes any doubt and guards against a future code
change silently disabling it.

## Item 3 — Fontshare "Satoshi" font (CDN)

- Loaded by `core/frontend/index.html` (`<link ... href="https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700,900&display=swap">`,
  with a `<noscript>` fallback and a `crossorigin` preconnect to
  `cdn.fontshare.com`).
- Fontshare's font-license index page states Satoshi is a "Closed Source"
  Fontshare-exclusive font governed by the **ITF Free Font License (FFL)**.
  Full current licence text was read directly from
  `https://www.fontshare.com/licenses/itf-ffl` (rendered via browser, retrieved
  2026-09-07; the page is a client-rendered SPA so a plain HTTP fetch/curl
  returns an empty shell — content was read from the rendered DOM). The page
  is dated **"ITF Free Font License (FFL) Version 2.0 - 17 Aug 2026"** and
  copyrighted "© 2021 — 2026 Indian Type Foundry."
- Key clauses, quoted from that page:
  - §01 Grant of License: "You are hereby granted a non-exclusive,
    non-assignable, non-transferable and terminable license to access,
    download, install, store and use the Font Software for **personal or
    commercial purposes, free of charge and for an unlimited period of
    time**... You may use the Font Software in any media, including... Websites...
    at any scale and in any location worldwide."
  - Also §01: "You may **self-host** the Font Software on your own servers...
    including through standard webfont technologies such as CSS @font-face...
    Use of the Fontshare API is **optional** and is not required for web use."
    — i.e. loading it from Fontshare's CDN (as `index.html` does) is one
    explicitly permitted option among several, not a compliance risk.
  - Also §01, on attribution: "You may, **but are not required to**, identify
    or credit Indian Type Foundry or Fontshare in works created using the Font
    Software." — attribution is optional.
  - §02 Limitations: modification/reverse-engineering of the font files
    themselves and redistribution of the **font files** (e.g. re-hosting them
    on another font marketplace, giving raw font files to third parties) are
    prohibited. This restricts redistributing the *font asset*, not
    TickerKeep's own source code, and does not affect the MIT status of
    TickerKeep Core itself.

**Verdict: ITF Free Font License (FFL) v2.0.** Not an OSI-approved open-source
licence, but explicitly free for commercial use, with CDN embedding via
`@font-face`/Fontshare API explicitly sanctioned and attribution optional.
**NOT A BLOCKER.** (Note: this licence governs the font asset loaded at
runtime from Fontshare's CDN; no font files are vendored in this repo, so
there is nothing to relicense — only a runtime dependency to disclose, which
this file now does.)

## Item 4 — Other dependencies scanned for copyleft/incompatible terms

Read `core/requirements.txt`, `core/requirements-otel.txt`, `core/pyproject.toml`,
and `core/frontend/package.json` in full (all four are short, complete lists —
no additional lockfile-only transitive deps were audited beyond what's
enumerated here, since the task scope is direct dependencies of this export).

**Individually checked (PyPI JSON metadata and/or upstream `LICENSE` files,
all retrieved 2026-09-07):**
- `weasyprint` → BSD (PyPI classifier "License :: OSI Approved :: BSD License")
- `xhtml2pdf` → Apache License 2.0 (PyPI `license` field is the full Apache-2.0 text)
- `matplotlib` → matplotlib/PSF-derived licence, BSD-style
  ("License :: OSI Approved :: Python Software Foundation License" classifier)
- `exchange_calendars` → Apache License 2.0. PyPI metadata carries no license
  classifier, so confirmed instead from the upstream repo's own `LICENSE` file
  at `https://raw.githubusercontent.com/gerrymanoim/exchange_calendars/master/LICENSE`
  (HTTP 200) — matches the `# Apache-2.0` comment already in `requirements.txt`.
- `apprise` → BSD-2-Clause (PyPI `license` field)
- `yfinance` → Apache-2.0 (PyPI `license` field and classifier)
- `opentelemetry-sdk`, `opentelemetry-exporter-otlp` (optional OTel extra) →
  Apache License 2.0, confirmed via `https://raw.githubusercontent.com/open-telemetry/opentelemetry-python/main/LICENSE`
  (the opentelemetry-python monorepo license; PyPI metadata for these two
  packages carries no classifier)
- Frontend, individually checked via the npm registry: `class-variance-authority`
  (Apache-2.0), `html-to-image`, `cmdk`, `react-markdown`, `remark-gfm`,
  `tailwind-merge`, `tailwindcss-animate`, `react-day-picker` (all MIT), and
  `date-fn` (ISC — see note below).

**Taken as standard permissive, not individually re-verified:** `openai`,
`apscheduler`, `httpx`, `pydantic`/`pydantic-settings`, `pyyaml`, `certifi`,
`tenacity`, `fastapi`, `uvicorn`, `sqlalchemy`, `PyJWT`, `pytest` (Python
side); `react`, `react-dom`, `react-router-dom`, all `@radix-ui/*` packages,
`clsx`, `lucide-react`, `tailwindcss`, `typescript`, `vite`,
`@vitejs/plugin-react`, `autoprefixer`, `postcss`, `@tailwindcss/typography`,
`@types/react`, `@types/react-dom` (frontend side). These are widely-known
MIT/BSD-licensed packages in extremely common use (React core and ecosystem,
FastAPI/Starlette/pydantic stack, etc.) and were not individually re-fetched
given the scope of this review — **if a stricter gate is desired, these should
still go through an automated SBOM/license-scanner pass** (e.g. `pip-licenses`,
`license-checker`) before public release, since this review's PyPI/npm spot
checks were manual and sampled rather than exhaustive.

**No copyleft or non-commercial licence was found in any dependency checked.**
No AGPL, GPL, SSPL, BUSL, CC-BY-NC, or "source-available" terms appeared
anywhere in `requirements.txt`, `requirements-otel.txt`, `pyproject.toml`, or
`frontend/package.json`.

**Note — `date-fn` (not `date-fns`):** `frontend/package.json` lists
`"date-fn": "^0.0.2"`. This is a real, separate, ISC-licensed npm package (not
the popular `date-fns` library) — confirmed via
`https://registry.npmjs.org/date-fn/latest` (no `repository` field, version
`0.0.2`). Licence-wise this is fine (ISC is permissive, equivalent in effect to
MIT). It is flagged here only because a near-identical name to a much more
popular package, pinned at version `0.0.2` with no repository metadata, is a
common typosquat pattern — this is a code-hygiene flag for the engineering
team, not a licensing blocker.

## Higgsfield / Meshy imagery

`grep -ri "higgsfield\|meshy" /private/tmp/panwatch-split-oJOJ9h/core` (recursive,
case-insensitive, whole export) returned **no matches**. Confirmed already
removed; no further action and no licence terms researched for either, per
scope.

## Action items before public release

1. **Visually confirm** (via `scripts/redeploy-local.sh` per project
   convention, run by someone with access to that flow — out of scope for
   this read-only research task) that the TradingView Lightweight Charts
   attribution logo/link renders in the chart UI. If it does not, add
   `layout: { attributionLogo: true }` to the three `LW.createChart(...)`
   calls in `frontend/packages/biz-ui/src/components/InteractiveKline.tsx`,
   or add an equivalent visible "Charts by TradingView" credit linking to
   `https://www.tradingview.com/`.
2. Optional hardening: run an automated licence scanner (`pip-licenses`,
   `license-checker`/`npx license-checker`) over the full resolved dependency
   graphs (not just the direct pins reviewed here) to catch any transitive
   copyleft dependency this manual review would miss.
3. Retain `LICENSE`, `NOTICE`, and this file (`NOTICE-THIRD-PARTY.md`)
   unmodified in the public release; both are load-bearing for compliance.
