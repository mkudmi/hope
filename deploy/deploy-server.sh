#!/usr/bin/env bash
set -euo pipefail
umask 077
sha="${1:?Commit SHA required}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
root=/opt/hope
release="$root/releases/$sha"
export HOPE_IMAGE="hope-cz:$sha"
export HOPE_ENV_FILE="$root/shared/.env"
cd "$release"
compose=(docker compose -p hope-cz --env-file "$HOPE_ENV_FILE" -f deploy/compose.server.yaml --profile telegram)

python3 - "$HOPE_ENV_FILE" <<'PY'
import os, sys
from pathlib import Path
p = Path(sys.argv[1])
assert p.stat().st_mode & 0o077 == 0, 'Server .env must be private'
values = dict(line.split('=',1) for line in p.read_text().splitlines() if '=' in line and not line.startswith('#'))
assert values['DEBUG'].lower() == 'false'
assert values['PANEL_WRITES_ENABLED'].lower() == 'false'
assert values['PANEL_MODE'] == 'real'
assert values['SITE_URL'].startswith('https://')
PY
"${compose[@]}" config --quiet
# Build before touching running HOPE services. Never restart panel/proxy/daemon.
docker build --tag "$HOPE_IMAGE" .

if [[ -L "$root/current" ]]; then
    previous=$(readlink -f "$root/current")
    previous_sha=$(basename "$previous")
    HOPE_IMAGE="hope-cz:$previous_sha" docker compose -p hope-cz --env-file "$HOPE_ENV_FILE" \
      -f "$previous/deploy/compose.server.yaml" exec -T web python manage.py backup_hope \
      --output "/app/data/before-$(date -u +%Y%m%dT%H%M%S)-$sha.enc"
fi

# The owner requested standby after one successful bot startup. Always stop,
# including on a failed probe. Worker stays stopped to avoid background delivery.
cleanup() { "${compose[@]}" stop bot worker >/dev/null 2>&1 || true; }
trap cleanup EXIT
"${compose[@]}" stop bot worker
"${compose[@]}" up -d --wait --wait-timeout 180 db web
"${compose[@]}" exec -T web python manage.py check
if [[ "${2:-standby}" == probe ]]; then
"${compose[@]}" up -d --no-deps bot
sleep 30
container=$("${compose[@]}" ps -q bot)
[[ -n "$container" ]]
[[ "$(docker inspect --format '{{.State.Status}}:{{.RestartCount}}' "$container")" == running:0 ]]
# Inspect errors without printing real updates, identities or API tokens.
if docker logs --since 35s "$container" 2>&1 | grep -Eq 'Traceback|Error|Conflict|Unauthorized|failed'; then
    echo 'Bot startup error detected; inspect private server logs' >&2
    exit 1
fi
"${compose[@]}" exec -T bot python - <<'PY'
import asyncio, os
from aiogram import Bot
async def check():
    bot = Bot(os.environ['BOT_TOKEN'])
    try:
        me = await bot.get_me()
        assert me.username.lower() == os.environ['BOT_USERNAME'].lower()
        assert not (await bot.get_webhook_info()).url
        assert len(await bot.get_my_commands()) == 10
        print('Telegram identity, webhook and commands verified')
    finally:
        await bot.session.close()
asyncio.run(check())
PY
fi
cleanup
ln -sfn "$release" "$root/current"
printf '%s\n' "$sha" > "$root/current-sha"
echo "Release $sha verified; server bot and worker stopped (standby)."
