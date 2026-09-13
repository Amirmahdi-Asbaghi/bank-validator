#!/usr/bin/env bash
# First-time setup: prepare .env and bring the stack up.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

cp .env docker/.env

echo "Starting full stack..."
make up

echo "Applying migrations..."
make migrate

echo "Seeding reference data..."
make seed

echo ""
echo "Ready. Open http://localhost:8000/api/schema/swagger/"