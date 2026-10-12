# 0013 — Раскатка через GitHub Actions на сервер Чехии

Статус: verified (раскатка и одноразовый запуск; публичный кабинет отдельно). Создано: 2026-10-12. Обновлено: 2026-10-12.
Связанные требования: R-10, R-11, R-12, R-14, R-17. Решение: ADR 0003.

## Проблема и результат

Пользователь запросил раскатку бота через GitHub на сервер Чехии и подтвердил
чистую серверную базу без переноса локальных данных. Инфраструктурный checkout
на Windows называется vpndev и находится в ../ChatGPT/vpndev; прежнее имя
vpnresearch относится к историческим материалам.

## Объём и требования

- GitHub Actions: проверки, ручной запуск workflow_dispatch и раскатка main.
- Изолированный Compose-проект hope-cz: отдельный PostgreSQL 17, web на loopback
  18000, worker и bot. Не использовать PostgreSQL действующей 3x-ui.
- Серверная .env вне релизов, 0600; реальные значения не публикуются в Git/логах.
- SSH deployment key с forced command, проверка host key, последовательные релизы
  по commit SHA; перед повторной раскаткой encrypted backup HOPE.
- PANEL_MODE=real, PANEL_WRITES_ENABLED=false принудительно в Compose.
- Чистая БД и новые серверные ключи; локальная .env остаётся localhost.
- Только один polling: локальный bot остановить перед серверным probe, затем
  восстановить после его остановки. По последующему указанию пользователя
  выполнить один успешный запуск и выключить bot на сервере. Обычные релизы
  оставляют bot/worker выключенными, probe_bot=true запускается отдельно вручную.
- Публичный reverse proxy /hope/ не менять в этой раскатке; кабинет loopback.
- Не менять и не перезапускать панель, VPN, Caddy и их БД.

## Критерии приёмки

- AC-01: документы указывают Windows vpndev и его фактическое расположение.
- AC-02: workflow main проходит проверки и раскатывает точный commit SHA.
- AC-03: отдельная HOPE БД, migrations/check/health успешны; worker/bot running.
- AC-04: серверная запись панели выключена, polling локально остановлен,
  production-секреты отсутствуют в Git, HTTPS/proxy кабинета не объявляется готовым.
- AC-05: повторный релиз сохраняет БД и создаёт новый encrypted backup;
  неудачная сборка оставляет прежние сервисы доступными.

## Решение и влияние

Workflow .github/workflows/deploy-cz.yml, deploy/compose.server.yaml и receiver.
Секреты GitHub — только SSH deploy key/host key, host/user; приложения — на сервере.
Обновление только HOPE. Первая раскатка создаёт новую БД; автоматического импорта
3x-ui и сообщений для теста нет. Откат на прежний SHA не откатывает миграции;
несовместимость схемы требует отдельного решения, старый dump поверх не применять.

## Проверки и прослеживаемость

| Критерий | Код / документ | Проверка | Фактический результат |
|---|---|---|---|
| AC-01 | AGENTS, product/launch | Проверка пути и ссылок | vpndev найден, Windows-путь и ссылки обновлены |
| AC-02 | workflow, tests/test_deployment.py | GitHub Linux/Python 3.12/PostgreSQL 17 | 163 passed за 23.04 s в первом CI; следующие проверки успешны. Main раскатан по точному SHA |
| AC-03–04 | deploy/compose.server.yaml | Сервер, probe workflow 38291038901 | Успешен. Telegram identity/webhook/10 команд подтверждены; после 30 секунд bot exited, restarts=0. Web/db healthy, health OK; DEBUG=false, writes=false, hope_live, 0 подписок/операций |
| AC-04 | Сервер и локальный Docker | Контроль single polling и чужих служб | Локальный bot остановлен на время probe и затем восстановлен; серверный выключен. Caddy/3xui-postgres StartedAt/restarts и x-ui PID/start timestamp совпадают с исходными |
| AC-05 | backup_hope, shared env, receiver | Повторные релизы, права, отказ SSH-команды id | Encrypted backups созданы, все 0600; env 0600. Restricted key отвергает shell-команду. Volumes сохранены; новая сборка не требует остановки прежних сервисов |

## Итог и ограничения

Сервер доступен: x86_64, около 2 GiB RAM, Docker/Compose установлены,
/opt/hope и loopback 18000 свободны. Действующие caddy и 3xui-postgres найдены;
их состояние сохранено для сравнения. Реальный туннель/группа/публичный кабинет
не входят в доказательства работоспособности раскатки.

Первый probe выявил OOM в cgroup bot при 192 MiB: ядро зафиксировало
убитый Python с RSS около 182 MiB и дополнительной mapped memory. Лимит bot
увеличен до 384 MiB; API-проверка использует лёгкий stdlib вместо второго
aiogram-процесса. Старые callback с истёкшим query ID не являются отказом polling:
они остаются в приватных логах, очередь updates не удаляется. Проверка выявляет
конфликт polling, ошибки подключения/авторизации и перезапуски контейнера.

Первый standby workflow [38289977213](https://github.com/mkudmi/hope/actions/runs/38289977213)
успешен. Первый probe [38290301529](https://github.com/mkudmi/hope/actions/runs/38290301529)
неуспешен из-за cgroup OOM, его результат сохранён. Исправленный релиз
[38290981583](https://github.com/mkudmi/hope/actions/runs/38290981583) и один успешный
probe [38291038901](https://github.com/mkudmi/hope/actions/runs/38291038901) прошли.
На сервере bot/worker выключены; локальный bot восстановлен. Публичный /hope/
не опубликован, секреты и локальные данные не переносились в серверную БД.
Первоначальный владелец production ещё не создан: штатный createsuperuser —
отдельный шаг перед пользованием серверной админкой. PANEL_SUB_URL пока пуст;
импорт/выдача, реальные сообщения/туннель и восстановление бэкапа не проверялись.
Windows-прогон до CI: 149 passed, 4 skipped, 2 failures (POSIX 0600 и отсутствие
native collectstatic). В Linux CI collectstatic выполнен, эти проверки успешны.
