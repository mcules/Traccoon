#!/bin/sh
# Backend-Tests im Backend-Image gegen den Arbeitsbaum (nicht gegen das gebaute Image).
# Test-Deps werden pro Lauf nachinstalliert, damit das Prod-Image schlank bleibt.
set -e
cd "$(dirname "$0")/.."
# A key of its own per run. Without one the encryption falls back to plain text
# on purpose, so every test that stores a secret was checking nothing — and the
# one test that noticed (mail account passwords) was red for that reason alone.
# Generated here rather than written down: it is thrown away with the container,
# and no key belongs in the repository.
exec docker run --rm -v "$PWD/backend:/app" -w /app --entrypoint sh traccoon-backend -c \
  "pip install -q -r requirements.txt >/dev/null 2>&1 || true; \
   SECRET_ENCRYPTION_KEY=\$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())') \
   python -m pytest tests/ $*"
