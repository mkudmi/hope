#!/usr/bin/env bash
set -euo pipefail
umask 077
sha="${1:?Commit SHA required}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
mode="${2:-production}"
[[ "$mode" == production || "$mode" == standby ]] || { echo 'Unsupported deployment mode' >&2; exit 2; }
root=/opt/hope
release="$root/releases/$sha"
export HOPE_IMAGE="hope-cz:$sha"
export HOPE_ENV_FILE="$root/shared/.env"
cd "$release"
compose=(docker compose -p hope-cz --env-file "$HOPE_ENV_FILE" -f deploy/compose.server.yaml --profile telegram)

python3 - "$HOPE_ENV_FILE" <<'PY'
import os, re, sys, tempfile
from pathlib import Path
p = Path(sys.argv[1])
assert p.stat().st_mode & 0o077 == 0, 'Server .env must be private'
content = p.read_text()
values = dict(line.split('=',1) for line in content.splitlines() if '=' in line and not line.startswith('#'))
assert values['DEBUG'].lower() == 'false'
assert values['PANEL_WRITES_ENABLED'].lower() in {'false', 'true'}
assert values['PANEL_MODE'] == 'real'
assert values['SITE_URL'].startswith('https://')
content, replaced = re.subn(r'(?m)^PANEL_WRITES_ENABLED=(?:false|true)$', 'PANEL_WRITES_ENABLED=true', content)
assert replaced == 1, 'Expected one PANEL_WRITES_ENABLED setting'
with tempfile.NamedTemporaryFile(dir=p.parent, prefix='.hope-env-', delete=False) as tmp:
    tmp_path = Path(tmp.name)
    os.fchmod(tmp.fileno(), 0o600)
    tmp.write(content.encode())
os.replace(tmp_path, p)
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

# Keep production services running after a successful deployment. If the new
# release fails, stop its worker/bot and attempt to restore the previous release.
deploy_ok=0
restore_previous() {
    [[ -L "$root/current" ]] || return 0
    local prior prior_sha
    prior=$(readlink -f "$root/current")
    prior_sha=$(basename "$prior")
    HOPE_IMAGE="hope-cz:$prior_sha" docker compose -p hope-cz --env-file "$HOPE_ENV_FILE" \
      -f "$prior/deploy/compose.server.yaml" --profile telegram up -d --wait --wait-timeout 180 db web bot worker \
      >/dev/null 2>&1 || true
}
cleanup_failed() {
    if [[ "$deploy_ok" != 1 ]]; then
        "${compose[@]}" stop bot worker >/dev/null 2>&1 || true
        restore_previous
    fi
}
trap cleanup_failed EXIT
"${compose[@]}" stop bot worker
"${compose[@]}" up -d --wait --wait-timeout 180 db web
"${compose[@]}" exec -T web python manage.py check
"${compose[@]}" exec -T web python manage.py shell -c 'from django.conf import settings
assert settings.PANEL_MODE == "real" and settings.PANEL_WRITES_ENABLED, "Production panel mode is not enabled"'
# Never let activating the worker replay an unreviewed panel operation.
"${compose[@]}" exec -T web python manage.py shell -c 'from core.models import Operation
if Operation.objects.filter(status__in=("queued", "applying")).exists():
    raise SystemExit("Queued panel operations exist; activation stopped")'
"${compose[@]}" up -d --no-deps bot
"${compose[@]}" up -d --no-deps worker
sleep 10
for service in bot worker; do
container=$("${compose[@]}" ps -q "$service")
[[ -n "$container" ]]
[[ "$(docker inspect --format '{{.State.Status}}:{{.RestartCount}}' "$container")" == running:0 ]]
done
# Inspect errors without printing real updates, identities or API tokens.
bot_container=$("${compose[@]}" ps -q bot)
if docker logs --since 15s "$bot_container" 2>&1 | grep -Eq 'TelegramConflictError|Failed to fetch updates|CommandError:|Unauthorized|TelegramNetworkError'; then
    echo 'Bot startup error detected; inspect private server logs' >&2
    exit 1
fi
"${compose[@]}" exec -T bot python - <<'PY'
import json, os, urllib.request
try:
    def call(method):
        url = 'https://api.telegram.org/bot' + os.environ['BOT_TOKEN'] + '/' + method
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.load(response)
        assert payload['ok']
        return payload['result']
    assert call('getMe')['username'].lower() == os.environ['BOT_USERNAME'].lower()
    assert not call('getWebhookInfo')['url']
    assert len(call('getMyCommands')) == 10
    print('Telegram identity, webhook and commands verified')
except Exception:
    raise SystemExit('Telegram startup verification failed; inspect private server settings') from None

PY
ln -sfn "$release" "$root/current"
printf '%s\n' "$sha" > "$root/current-sha"
deploy_ok=1
trap - EXIT
echo "Release $sha verified; production web, bot and worker are running."
