#!/usr/bin/env bash
# Build the demo house from scratch and fill it: stack, fictional data, English UI, map plugin.
#
#   bash docs/demo/refresh.sh          # http://localhost:8089, login ada@example.org / demo-demo-demo
set -euo pipefail
cd "$(dirname "$0")/../.."
API=http://localhost:8089/api

docker compose -f docs/demo/compose.yml down -v >/dev/null 2>&1 || true
docker compose -f docs/demo/compose.yml up -d --build >/dev/null
for _ in $(seq 40); do
  curl -sf "$API/health" >/dev/null && break
  sleep 2
done

python3 docs/demo/seed.py
docker compose -f docs/demo/compose.yml exec -T backend python - < docs/demo/seed_metrics.py
docker compose -f docs/demo/compose.yml exec -T backend python - < docs/demo/seed_assistant.py
python3 docs/demo/seed_mail.py

TOKEN=$(curl -s -X POST "$API/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.org","password":"demo-demo-demo"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s -X PUT "$API/me/locale" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"value":"en"}' >/dev/null
docker compose -f docs/demo/compose.yml exec -T backend python -c "
import asyncio
from app.db import SessionLocal
from sqlalchemy import select
from app.models.user import User
async def main():
    async with SessionLocal() as db:
        u = (await db.execute(select(User).where(User.username=='ada'))).scalars().first()
        u.display_name = 'Ada Lovelace'
        await db.commit()
asyncio.run(main())" >/dev/null

# The mailbox of the demo: GreenMail next door, an account with a house rule and a release
# per tool — the same shape a real one has.
curl -s -X POST "$API/mailbox/accounts" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "name":"Private","imap_host":"mail","imap_port":3143,"imap_ssl":false,
    "imap_user":"ada@example.org","imap_password":"demo",
    "smtp_host":"mail","smtp_port":3025,"smtp_security":"none",
    "smtp_user":"ada@example.org","smtp_password":"demo",
    "folder_sent":"Sent","folder_drafts":"Drafts","folder_trash":"Trash",
    "folder_junk":"Junk","folder_archive":"Archive",
    "archive_mode":"pattern","archive_pattern":"Archive/{jahr}/{monat}","mcp_enabled":true,
    "mcp_tools":["imap_list_folders","imap_search","imap_get_message","imap_flag"],
    "mcp_instructions":"Send nothing without asking. Invoices belong in the archive, not in the trash."}' >/dev/null

# The map plugin, if the sibling repository is checked out next door.
PLUGIN=../traccoon-plugins/build/map.zip
if [ -f "$PLUGIN" ]; then
  curl -s -X POST "$API/plugins" -H "Authorization: Bearer $TOKEN" -F "file=@$PLUGIN" >/dev/null
  curl -s -X PUT "$API/plugins/map/rights" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"reads_granted":["series:location"],"enabled":true}' >/dev/null
  echo "map plugin installed"
fi

echo "$TOKEN" > /tmp/demo_token.txt
echo "demo ready: http://localhost:8089 (token in /tmp/demo_token.txt)"
