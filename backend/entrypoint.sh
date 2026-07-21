#!/bin/sh
set -e
# Prod-Migrationen: nur wenn ausdrücklich aktiviert (MIGRATE=1) und create_all aus.
if [ "${MIGRATE:-0}" = "1" ] && [ "${DEV_CREATE_ALL:-true}" != "true" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head
fi
exec "$@"
