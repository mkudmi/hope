# GitHub → сервер Чехии

Workflow `Deploy HOPE to Czech server (standby)` проверяет код на Linux/Python
3.12/отдельной PostgreSQL 17 и передаёт архив точного SHA по SSH. Main и ручной
workflow_dispatch раскатывают HOPE, но оставляют bot/worker остановленными.
Ручной `probe_bot=true` запускает bot на 30 секунд, проверяет отсутствие ошибок,
имя/webhook/команды Telegram и останавливает его даже при ошибке проверки.
Перед probe остановить любой другой polling того же токена, включая локальный.

Среда GitHub: `czech-standby`. Секреты: `CZ_DEPLOY_HOST`, `CZ_DEPLOY_KEY`,
`CZ_DEPLOY_KNOWN_HOSTS`. SSH host key закреплён; deploy key использует restrict
и forced command `/opt/hope/bin/receive-release.py`, без интерактивного shell.
Приёмник разрешает только `deploy <40-символьный SHA>` с необязательным `probe`.
Он отвергает обход каталогов, symlink и секретные файлы в архиве. Ключ доверяет
коду main для сборки Docker; доступ к репозиторию должен быть ограничен владельцем.

Сервер: `/opt/hope/releases/<SHA>`, `/opt/hope/current`,
`/opt/hope/shared/.env` (0600). Compose project `hope-cz`, собственная PostgreSQL
17 и volumes `hope-cz_hope-db` / `hope-cz_hope-data`. БД панели не используется.
Web слушает `127.0.0.1:18000`, БД не публикуется. Reverse proxy не изменяется.
SITE_URL планируется на домене панели `/hope/`, но публичный кабинет требует
отдельной настройки действующего Caddy. До этого ссылки кабинета из бота
нельзя считать рабочими. Локальная .env остаётся localhost.

Параметры deploy: `HOPE_IMAGE` и `HOPE_ENV_FILE` устанавливает deploy-server.sh;
описаны в едином `.env.example`. DEBUG=false, real и write-off обязательны,
Compose дополнительно принудительно выставляет real/write-off.

Перед повторным релизом сохраняется encrypted backup HOPE в volume `/app/data`;
ключ шифрования остаётся в общей серверной .env. Сборка идёт до остановки прежних
HOPE-сервисов. Релизы сериализованы GitHub concurrency и серверным flock.
Завершённый SHA записывается в current-sha только после проверки; после ошибки
миграций текущее состояние необходимо проверить вручную, автоматического отката
схемы нет. Возврат на старый SHA не восстанавливает БД и не снимает write-off.
Volumes и прежние релизы автоматически не удаляются.

Локальная проверка синтаксиса Compose: задать HOPE_IMAGE и HOPE_ENV_FILE на
локальную .env, затем `docker compose --env-file .env -f deploy/compose.server.yaml config --quiet`.
Для серверных команд использовать current/deploy/compose.server.yaml,
`-p hope-cz --env-file /opt/hope/shared/.env --profile telegram` и переменную
HOPE_IMAGE по current-sha. Постоянный запуск bot/worker пока не разрешён.

Связанные документы: [SDD 0013](../docs/sdd/changes/0013-github-cz-deployment.md),
[ADR 0003](../docs/sdd/decisions/0003-github-ssh-releases.md).
