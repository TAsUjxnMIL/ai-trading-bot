#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE=./.env.demo API_PORT=8000 docker compose -p botdemo up -d --force-recreate --build