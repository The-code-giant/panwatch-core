# PanWatch Core

PanWatch is a self-hosted stock research assistant with a React interface, FastAPI
backend, portfolio tracking, configurable AI agents, alerts, and paper trading.
The product focuses on US and Canadian equities; crypto and gold are watchlist-only
extras. Provider availability and credentials determine which research features
can run. Paper trading is simulated.

This development export contains the product application. Hosted subscriptions,
billing, multi-tenant isolation, and the public marketing site are not implemented
here. It is not a published release.

## Fresh installation

Use Docker Compose with support for optional `env_file` entries. Review the
Dockerfile and dependency manifests before building. Builds download dependencies.

```sh
cp .env.example .env
docker compose -p panwatch-core-demo build
docker compose -p panwatch-core-demo up -d
```

Open `http://127.0.0.1:18080` and complete first-run authentication setup. This
example binds only to loopback and creates the Compose-managed
`panwatch-core-demo_panwatch_data` volume. It does not reuse an external volume or
fixed container name. Pick a new project name and `PANWATCH_PORT` for each separate
installation. Ordinary `docker compose down` preserves records; avoid `down -v`
when retaining them.

Schedulers and update polling are disabled in this sample. Configure providers,
models, agents and notification channels before changing `DISABLE_SCHEDULERS`
to `0`. Manual actions can still contact configured providers. Do not use a
personal installation for testing. Application defaults outside this sample remain
unchanged, including reading `.env` from the current directory.
`PANWATCH_ENV_FILE` selects an alternative configuration file; `DATA_DIR` selects
the data directory (Docker uses `/app/data`).

## Source and frontend

`server.py` starts the backend; `src/` contains agents, collectors, core services
and API routes. `prompts/` contains agent templates. `packages/marketdata` is a
separate companion Python distribution. `frontend/` includes the product and
its API/UI workspace packages.

The frontend uses Node 24 and pnpm 9.15.9. In a disposable source-only build copy:

```sh
cd frontend
pnpm install --frozen-lockfile --ignore-scripts
pnpm build
```

The build includes TypeScript, Vite and the product prerender entry. It emits
`dist/index.html`, `app-shell.html`, `404.html` and gzip variants, with no
marketing pages or sitemap. Product routes cover Today, Portfolio (watchlist,
paper and alerts), Discover, Agents (reports), Settings (data sources), login and
report details. Existing legacy redirects remain. Chart code and Satoshi fonts
load from public CDNs at runtime; they are not bundled for offline use.

## Python wheel scope

`panwatch-core` uses the non-release version `0.0.0.dev0`. The wheel includes the
existing `src` import namespace, `server` module and prompt resources. It excludes
the compiled frontend, database and deployment configuration. Use the source
Docker build for the complete application. Install in its own environment:
the legacy `src` namespace is not a namespaced SDK. There is no published
compatibility promise or cloud release pin.

Build both local distributions in disposable copies with prepared build tools:

```sh
python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/wheelhouse ./packages/marketdata
python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/wheelhouse .
```

The declared `marketdata==0.1.0` dependency means this repository's companion
wheel. Do not substitute an unrelated public package with that generic name.
Install using `--no-index --find-links /tmp/wheelhouse` with a prepared offline
dependency wheelhouse. TradingAgents is optional in wheel metadata through the
`deep-analysis` extra; source image requirements retain the pinned upstream
integration. No PanWatch release or tag has been invented.

## Tests and attribution

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests require disposable source copies,
Docker with networking denied, disabled schedulers and mocked providers. Shared
test configuration overrides installation paths before imports and rejects real
notifications; this does not replace container isolation or provider mocks.

The inherited MIT license and `Copyright (c) 2026 sunxiao0721` are retained in
[LICENSE](LICENSE). See [NOTICE](NOTICE) for third-party components and unresolved
license review. Excluded assets with uncertain provenance are not cleared for
redistribution merely because they were untracked. This export makes no upstream
donation, release, or hosted-service claim.
