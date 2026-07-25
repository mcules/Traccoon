#!/bin/sh
# Backend-Tests im Backend-Image gegen den Arbeitsbaum (nicht gegen das gebaute Image).
# Test-Deps werden pro Lauf nachinstalliert, damit das Prod-Image schlank bleibt.
set -e
cd "$(dirname "$0")/.."
exec docker run --rm -v "$PWD/backend:/app" -w /app --entrypoint sh traccoon-backend -c \
  "pip install -q -r requirements.txt >/dev/null 2>&1 || true; python -m pytest tests/ $*"
