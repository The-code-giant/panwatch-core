#!/usr/bin/env bash
#
# Rebuild the local TickerKeep image from the working tree and restart the
# container. This is the only supported way to look at the app locally:
# the Vite dev server is not used for review.
#
#   bash scripts/redeploy-local.sh          # build + restart
#   bash scripts/redeploy-local.sh --logs   # build + restart, then follow logs
#
# The frontend is built inside the image (Dockerfile stage 1), so a type
# error fails the build here rather than shipping a broken bundle.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env at repo root. docker-compose.yml declares it as env_file." >&2
  exit 1
fi

echo "==> Building tickerkeep:en-local from the working tree"
docker compose build tickerkeep

echo "==> Restarting the container"
docker compose up -d tickerkeep

echo "==> Waiting for the app to answer on :8000"
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:8000/api/health 2>/dev/null; then
    echo "Ready: http://localhost:8000"
    [ "${1:-}" = "--logs" ] && exec docker compose logs -f tickerkeep
    exit 0
  fi
  sleep 2
done

echo "The app did not answer within 120s. Recent logs:" >&2
docker compose logs --tail 60 tickerkeep >&2
exit 1
