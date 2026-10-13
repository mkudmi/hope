# GitHub → сервер Чехии

Production deploy запускается вручную из `.github/workflows/deploy-cz.yml` на
`main`. Workflow сначала проверяет Linux/Python 3.12/PostgreSQL 17 (lint, Django
check, collectstatic, pytest, migration check), затем отправляет архив точного
SHA через SSH. Обычный push не выкатывает production.

Перед запуском остановить локальные bot и worker: они используют тот же Telegram
polling. Workflow использует существующий GitHub environment `czech-standby`, в
котором находятся `CZ_DEPLOY_HOST`, `CZ_DEPLOY_KEY` и `CZ_DEPLOY_KNOWN_HOSTS`.
Host key закреплён. SSH deploy key допускает только forced command
`/opt/hope/bin/receive-release.py`, без интерактивного shell.

Приёмник разрешает `deploy <40-символьный SHA>`; старый установленный receiver
передаёт скрипту дополнительный аргумент `standby`, принятый как совместимое
имя production-перехода. Обновление самого receiver требует отдельной доверенной
операции; этот workflow его не заменяет. Сборка доверяет коду `main`, поэтому
доступ к репозиторию является правом выполнить код HOPE на сервере.

Сервер: `/opt/hope/releases/<SHA>`, `/opt/hope/current`, серверная
`/opt/hope/shared/.env` с правами 0600. Compose project `hope-cz` использует свою
PostgreSQL 17 и volumes `hope-cz_hope-db` / `hope-cz_hope-data`. Данные панели
не находятся в этой БД. Web слушает `127.0.0.1:18000`; Caddy, 3x-ui, VPN и их
БД workflow не перезапускает.

На production deploy серверная `.env` переводится в `PANEL_WRITES_ENABLED=true`;
Compose принудительно задаёт `PANEL_MODE=real` и write-on. До запуска worker
команда проверяет, что нет queued/applying операций. Deploy сам не создаёт
панельных операций. Перед заменой релиза создаётся encrypted backup HOPE,
который хранится в data volume и не перезаписывается.

После миграции и Django check запускаются web, bot и worker; startup проверяет
состояние контейнеров, ошибки polling, Telegram identity, webhook и команды.
Успешный deploy оставляет их работающими. При ошибке новый bot/worker
останавливаются, выполняется попытка вернуть прежний релиз; автоматического
отката миграций нет. Проверить pending/partial/conflict вручную до продолжения
записи.

Публичный `/hope/` не включается этим workflow. Пока reverse proxy не настроен,
кабинет доступен только на loopback 18000. Production owner также должен быть
создан отдельно.

Локально можно проверить синтаксис Compose, не меняя производственные значения:

```sh
HOPE_IMAGE=hope-cz:local HOPE_ENV_FILE=.env docker compose --env-file .env -f deploy/compose.server.yaml config --quiet
```

История первоначальной standby-раскатки: [SDD 0013](../docs/sdd/changes/0013-github-cz-deployment.md).
Production policy: [SDD 0017](../docs/sdd/changes/0017-czech-production.md).
