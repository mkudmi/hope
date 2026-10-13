# 0017 — Production-режим на сервере Чехии

Статус: verified (production deploy; live panel write/tunnel separately pending). Создано: 2026-10-13. Обновлено: 2026-10-13.
Связанные требования: R-06, R-07, R-08, R-11, R-12, R-17. Решение: [ADR 0004](../decisions/0004-czech-production-runtime.md).

## Проблема и результат

Развёртывание 12.10 оставляло server bot/worker остановленными и запрещало запись
в 3x-ui. Владелец 13.10 прямо указал перевести Czech deployment в production,
разрешил штатную запись в действующую панель, обновление спецификаций, commit и
GitHub deploy. Результат: web, bot и worker постоянно работают с выделенной БД
HOPE; действия 3x-ui проходят через существующий ручной flow и audit.

## Объём и требования

- Сервер: `PANEL_MODE=real`, `PANEL_WRITES_ENABLED=true`, `DEBUG=false`; bot и
  worker запускаются после health/check и остаются работающими.
- Локальное окружение остаётся write-off и на `localhost`.
- GitHub workflow становится ручным, чтобы не раскатывать production при каждом
  push. Текущий secret environment сохраняется под именем `czech-standby`.
- До запуска worker deployment прерывается, если серверная БД содержит queued или
  applying panel operations. Не выполняются диагностические записи и не создаются
  фиктивные клиенты, заказы, платежи или Telegram-сообщения.
- Создание/продление реального доступа остаётся доступно только через штатный
  кабинет, ручное подтверждение владельца, существующие проверки версии,
  идемпотентность, сверку и аудит.
- Отдельная серверная БД сохраняется; локальные данные не импортируются,
  3x-ui/Caddy/VPN не перезапускаются и их БД не затрагивается.
- Вне объёма: публикация `/hope/`, создание production-владельца, приглашение
  пользователей и доказательство рабочего VPN-туннеля.

## Критерии приёмки

- AC-01: эффективный runtime сервера использует real panel + write-on; локальные
  настройки остаются write-off.
- AC-02: успешный deploy оставляет web, bot и worker запущенными; перед cutover
  локальные bot/worker с тем же токеном остановлены.
- AC-03: worker не запускается при queued/applying panel operations; непустая
  очередь останавливает активацию без применения этих операций.
- AC-04: GitHub Actions проверяет код и выполняет ручной deploy точного commit
  SHA через закреплённый SSH host key; сервисы 3x-ui/Caddy/VPN не перезапускаются.
- AC-05: production остаётся отдельной базой HOPE; секреты не выводятся,
  зашифрованный backup выполняется перед заменой работающего релиза.
- AC-06: реальные панельные операции требуют пользовательского действия и
  ручного подтверждения владельца; deploy сам не меняет клиентов 3x-ui.

## Решение и влияние

`deploy/compose.server.yaml` включает write-on только на Czech production.
`deploy/deploy-server.sh` переводит серверный `.env` в тот же режим, валидирует
его privacy и режимы, проверяет DB очередь, запускает сервисы и проверяет bot.
Installed forced-command receiver пока передаёт исторический аргумент `standby`;
скрипт принимает его как совместимый alias production. Workflow вызывает только
`deploy <SHA>` и запускается вручную.

Перед deploy локальные bot/worker необходимо остановить: они используют тот же
Telegram polling и потенциально тот же production API. При неудаче deploy trap
останавливает новый worker/bot и пытается вернуть прежний release. Миграции БД
автоматически не откатываются; при несовместимой миграции потребуется ручная
проверка.

Публичный `/hope/` по-прежнему недоступен через известную конфигурацию loopback,
а production owner не создан. До этих отдельных шагов web/admin flow не считается
полностью доступным владельцу снаружи.

## Проверки и прослеживаемость

| Критерий / требование | Код / документ | Проверка и среда | Фактический результат |
|---|---|---|---|
| AC-01 / R-11 | compose, deploy script, AGENTS | GitHub deploy и Django runtime assertion | Чехия поднята с `PANEL_MODE=real`, writes=true; локальная конфигурация осталась writes=false |
| AC-02 / R-17 | workflow, deploy script | GitHub Actions run `38396346387` | Успех; web healthy, bot/worker running с RestartCount=0 после startup check; Telegram identity/webhook/10 commands подтверждены |
| AC-03 / R-17 | deploy script | server DB preflight перед запуском worker | Проверка прошла: queued/applying операций нет |
| AC-04 / R-17 | workflow, installed receiver | run SHA, workflow logs, server services | SHA `158fdf16a3c80758099cd38d8395bd4080c84258`; workflow success; Caddy/3x-ui/VPN не перезапускались |
| AC-05 / R-17 | receiver, deploy script | backup перед заменой релиза, отдельная DB | Deploy завершился после backup_hope; отдельная PostgreSQL сохраняется, локальные данные не переносились |
| AC-06 / R-06/R-07 | services, jobs, operations | политика и действия deploy | Deploy не создавал панельных операций; реальная запись клиенту ещё не выполнялась |

Повторный CI: `38396346387` — `163 passed, 2 warnings`, ruff/system check/
collectstatic/makemigrations check успешны. Предшествующий CI `38396146426` имел
1 устаревшее строковое ожидание (162 passed); оно исправлено без изменения текста
бота. Изменения кода: `6afe5ca`, `158fdf1`.

## Итог и ограничения

Успешный deploy подтверждает старт приложения и production-конфигурацию, но не
проверяет реальное создание/продление клиента, пользовательский туннель или
внешнюю доступность `/hope/`. Production owner ещё не создан; web пока привязан
к loopback. Не выводить URL/token/персональные записи в следующие отчёты.
