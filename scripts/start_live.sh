#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

grep -q '^IG_SERVICE_ACC_TYPE=LIVE' ./.env.live || { echo "ERROR: .env.live is not LIVE"; exit 1; }

echo "⚠️  Starting LIVE stack (botlive) on port 8001."
read -r -p 'Type LIVE to continue: ' ans
[[ "$ans" == "LIVE" ]] || { echo "Aborted."; exit 1; }

ENV_FILE=./.env.live API_PORT=8001 docker compose -p botlive up -d --force-recreate --build