# Contributing to TickerKeep Core

Keep changes focused and describe behavior and verification in your pull request.
New product text is English. Preserve inherited attribution, historical user
content, and runtime defaults unless a change explicitly covers them. Never
include personal records, keys, actual `.env` files, databases, dependencies or
generated build output in an export.

## Architecture

- `src/agents/`: agent logic; register agents and seed disabled configuration in
  `server.py`. Templates in `prompts/` are included in the Python wheel.
- `src/collectors/` and `packages/marketdata/`: collection and provider adapters.
  New providers need a documented source, license and mocked fixtures.
- `src/core/`: scheduling, notifications, analysis and paper trading services.
- `src/web/`: models, persistence and FastAPI routes.
- `frontend/src/` and `frontend/packages/`: product routes, API client and UI.

Use typed Python, four-space indentation and snake_case names. Use PascalCase for
React components and camelCase for TypeScript utilities. Keep core independent of
private cloud modules. Agent context can contain private records; never use it as
shared public research cache input.

## Safe checks

Work in a fresh source-only copy with empty data. Review scripts before running
them. Prepare dependencies separately, then run every test in Docker with
`--network none`, `--no-healthcheck` and `DISABLE_SCHEDULERS=1`. Mount only the
disposable copy; never mount installation data, credentials or a Docker socket.
Do not restart an existing deployment. Providers, models and notifications must
be mocked; live scans and trades are not tests.

With an already prepared local dependency image and `/tmp/core-check` containing
only disposable source, an example is:

```sh
docker run --rm --network none --no-healthcheck \
  -e DISABLE_SCHEDULERS=1 -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  -e PYTHONPATH=/work:/work/packages/marketdata/src \
  --mount type=bind,src=/tmp/core-check,dst=/work \
  -w /work --entrypoint python tickerkeep:en-local \
  -m pytest tests/test_installation_isolation.py -q
```

`tests/conftest.py` unconditionally chooses a new temporary `DATA_DIR`, points
`TICKERKEEP_ENV_FILE` at a nonexistent file, and disables schedulers and update checks
before importing application modules. Early imports fail fast. `--notify` is
rejected. Use synthetic sentinels to prove isolation, never an actual installation.

Run the frontend build (TypeScript, Vite and product prerender) in an isolated
build copy. Install with the frozen lockfile and disabled lifecycle scripts;
review any necessary lifecycle step explicitly. Keep lockfile and package
manifest changes together. Build wheels outside the export and verify metadata,
contents and fresh installation before claiming packaging works. Unavailable
Docker or dependencies are blockers, not successful checks.

Preserve every product route when changing build wiring. Update README and NOTICE
when setup or dependencies change. Do not infer ownership from an untracked file.
