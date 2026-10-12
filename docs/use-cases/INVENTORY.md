# Инвентаризация элементов интерфейса

Сверка исходных шаблонов и callbacks на 2026-10-12. Каждое вхождение кнопки/ссылки/раскрывающего блока связано с UC; динамические подписи перечислены без раскрытия реальных данных. Повторные места используют тот же сценарий.

## [templates/account.html](../../templates/account.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: Изменить пароль | [UC-W017](web/UC-W017-password-form.md) |
| 2 | button: {% if identity.reminders %}Выключить{% else %}Включить{% endif %} напоминания | [UC-W026](web/UC-W026-reminders-false.md), [UC-W025](web/UC-W025-reminders-true.md) |
| 3 | a: Подключить Telegram ↗ | [UC-W018](web/UC-W018-telegram-page.md) |
| 4 | button: Новые коды | [UC-W023](web/UC-W023-new-codes.md) |
| 5 | button: Отвязать Telegram | [UC-W024](web/UC-W024-unlink.md) |
## [templates/base.html](../../templates/base.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: hHOPE VPN | [UC-W001](web/UC-W001-home.md) |
| 2 | a: Пользователи | [UC-A001](admin/UC-A001-users.md) |
| 3 | a: Обращения | [UC-A002](admin/UC-A002-requests.md) |
| 4 | a: Операции | [UC-A003](admin/UC-A003-events.md) |
| 5 | a: Подписка | [UC-W011](web/UC-W011-dashboard.md) |
| 6 | a: Аккаунт | [UC-W012](web/UC-W012-account.md) |
| 7 | button: Выйти | [UC-W004](web/UC-W004-logout.md) |
| 8 | a: Помощь | [UC-W013](web/UC-W013-help.md) |
| 9 | a: Войти | [UC-W002](web/UC-W002-open-login.md) |
| 10 | a: Связаться с поддержкой | [UC-W014](web/UC-W014-support-form.md) |
## [templates/codes.html](../../templates/codes.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | button: Скопировать все | [UC-W009](web/UC-W009-copy-codes.md) |
| 2 | button: Коды сохранены → | [UC-W010](web/UC-W010-codes-saved.md) |
## [templates/connect.html](../../templates/connect.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: INCY · App Store ↗ | [UC-W031](web/UC-W031-download-incy.md) |
| 2 | a: Скачать Happ ↗ | [UC-W032](web/UC-W032-download-happ.md) |
| 3 | a: Другие версии INCY ↗ | [UC-W033](web/UC-W033-download-incy-other.md) |
| 4 | a: Добавить в INCY ↗ | [UC-W034](web/UC-W034-import-incy.md) |
| 5 | a: Добавить в Happ ↗ | [UC-W035](web/UC-W035-import-happ.md) |
| 6 | a: Если не подключается → | [UC-W013](web/UC-W013-help.md) |
| 7 | button: Скопировать | [UC-W036](web/UC-W036-copy-subscription.md) |
| 8 | summary: QR-код для другого устройства | [UC-W037](web/UC-W037-subscription-qr.md) |
## [templates/dashboard.html](../../templates/dashboard.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: Подключить VPN ↗ | [UC-W016](web/UC-W016-connect.md) |
| 2 | a: Продление | [UC-W015](web/UC-W015-renewal-form.md) |
| 3 | button: Экстренный доступ на час | [UC-W021](web/UC-W021-emergency.md) |
| 4 | button: Экстренный доступ на час | [UC-W021](web/UC-W021-emergency.md) |
| 5 | a: Открыть помощь → | [UC-W013](web/UC-W013-help.md) |
| 6 | a: Настроить аккаунт → | [UC-W012](web/UC-W012-account.md) |
## [templates/form.html](../../templates/form.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | button: {{ button&#124;default:'Сохранить' }} | [UC-W003](web/UC-W003-login.md), [UC-W007](web/UC-W007-activate.md), [UC-W006](web/UC-W006-recover.md), [UC-W008](web/UC-W008-onboard.md), [UC-W022](web/UC-W022-password-submit.md), [UC-A021](admin/UC-A021-draft.md), [UC-A032](admin/UC-A032-operation-preview.md) |
| 2 | a: Не помню пароль | [UC-W005](web/UC-W005-open-recovery.md) |
## [templates/help.html](../../templates/help.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: INCY ↗ | [UC-W031](web/UC-W031-download-incy.md) |
| 2 | a: Написать в поддержку | [UC-W014](web/UC-W014-support-form.md) |
## [templates/home.html](../../templates/home.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: Войти в кабинет ↗ | [UC-W002](web/UC-W002-open-login.md) |
| 2 | a: Хочу подключиться | [UC-W014](web/UC-W014-support-form.md) |
## [templates/mfa.html](../../templates/mfa.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | summary: Не можете сканировать? Ввести ключ вручную | [UC-A012](admin/UC-A012-mfa-manual.md) |
| 2 | button: Скопировать ключ | [UC-A013](admin/UC-A013-mfa-copy.md) |
| 3 | button: {% if setup %}Подключить защиту и войти{% else %}Подтвердить вход{% endif %} | [UC-A010](admin/UC-A010-mfa-setup.md), [UC-A011](admin/UC-A011-mfa-confirm.md) |
## [templates/request.html](../../templates/request.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | button: Отправить обращение | [UC-W019](web/UC-W019-support-submit.md), [UC-W020](web/UC-W020-renewal-submit.md) |
## [templates/requests.html](../../templates/requests.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: Новое обращение | [UC-W014](web/UC-W014-support-form.md) |
## [templates/staff/broadcast.html](../../templates/staff/broadcast.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | summary: Проверить получателей | [UC-A047](admin/UC-A047-broadcast-recipients.md) |
| 2 | button: Подтвердить отправку | [UC-A048](admin/UC-A048-broadcast-confirm.md) |
| 3 | a: Отменить | [UC-A049](admin/UC-A049-broadcast-cancel.md) |
| 4 | button: Предпросмотр и получатели | [UC-A046](admin/UC-A046-broadcast-preview.md) |
## [templates/staff/detail.html](../../templates/staff/detail.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: ← Пользователи | [UC-A001](admin/UC-A001-users.md) |
| 2 | button: Скопировать | [UC-A023](admin/UC-A023-copy-private-link.md) |
| 3 | a: Изменить доступ | [UC-A009](admin/UC-A009-operation-page.md) |
| 4 | button: {% if sub.managed %}Принять текущее состояние панели{% else %}Принять под управление{% endif %} | [UC-A024](admin/UC-A024-adopt.md), [UC-A025](admin/UC-A025-refresh.md) |
| 5 | button: Ссылка восстановления | [UC-A026](admin/UC-A026-recovery-link.md) |
| 6 | button: Отвязать потерянный Telegram | [UC-A027](admin/UC-A027-unlink-lost.md) |
| 7 | button: {% if sub.owner.is_active %}Заблокировать{% else %}Разблокировать{% endif %} вход в кабинет | [UC-A028](admin/UC-A028-lock.md), [UC-A029](admin/UC-A029-unlock.md) |
| 8 | button: Выдать приглашение | [UC-A022](admin/UC-A022-invite.md) |
| 9 | button: {% if sub.excluded %}Вернуть в список{% else %}Исключить служебную запись{% endif %} | [UC-A031](admin/UC-A031-include.md), [UC-A030](admin/UC-A030-exclude.md) |
| 10 | button: Повторить проверку результата | [UC-A034](admin/UC-A034-retry.md) |
## [templates/staff/events.html](../../templates/staff/events.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: {{ op.subscription.label }} | [UC-A006](admin/UC-A006-detail.md) |
## [templates/staff/index.html](../../templates/staff/index.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: + Новый клиент | [UC-A008](admin/UC-A008-new-page.md) |
| 2 | a: {{ open_requests }}открытых обращений | [UC-A002](admin/UC-A002-requests.md) |
| 3 | a: {{ queued }}операций в очереди | [UC-A003](admin/UC-A003-events.md) |
| 4 | a: {{ problems }}результатов требуют внимания | [UC-A019](admin/UC-A019-filter-errors.md) |
| 5 | a: Заказы из Telegram | [UC-A004](admin/UC-A004-orders.md) |
| 6 | a: Уведомить пользователей | [UC-A005](admin/UC-A005-broadcast.md) |
| 7 | button: Найти | [UC-A014](admin/UC-A014-search.md) |
| 8 | button: Импортировать из панели | [UC-A020](admin/UC-A020-import.md) |
| 9 | a: Все | [UC-A015](admin/UC-A015-filter-all.md) |
| 10 | a: Без Telegram | [UC-A016](admin/UC-A016-filter-unlinked.md) |
| 11 | a: Скоро истекают | [UC-A017](admin/UC-A017-filter-soon.md) |
| 12 | a: Истекли | [UC-A018](admin/UC-A018-filter-expired.md) |
| 13 | a: Ошибки | [UC-A019](admin/UC-A019-filter-errors.md) |
| 14 | a: {{ sub.label }} | [UC-A006](admin/UC-A006-detail.md) |
## [templates/staff/order-preview.html](../../templates/staff/order-preview.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | button: Подтвердить выдачу доступа | [UC-A042](admin/UC-A042-order-provision.md) |
| 2 | a: ← Вернуться к заказу | [UC-A007](admin/UC-A007-order.md) |
## [templates/staff/order.html](../../templates/staff/order.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: ← Заказы | [UC-A004](admin/UC-A004-orders.md) |
| 2 | a: Открыть Telegram клиента ↗ | [UC-A044](admin/UC-A044-telegram-contact.md) |
| 3 | a: Карточка доступа → | [UC-A006](admin/UC-A006-detail.md) |
| 4 | button: Подтвердить оплату | [UC-A039](admin/UC-A039-order-confirm-payment.md) |
| 5 | button: Перевод не подтверждён | [UC-A040](admin/UC-A040-order-reject-payment.md) |
| 6 | button: Предпросмотр выдачи | [UC-A041](admin/UC-A041-order-preview.md) |
| 7 | button: Отправить через очередь бота | [UC-A043](admin/UC-A043-order-reply.md) |
## [templates/staff/orders.html](../../templates/staff/orders.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: Все | [UC-A035](admin/UC-A035-orders-filter-all.md) |
| 2 | a: Проверить перевод | [UC-A036](admin/UC-A036-orders-filter-payment.md) |
| 3 | a: Выдать доступ | [UC-A037](admin/UC-A037-orders-filter-paid.md) |
| 4 | a: Применение | [UC-A038](admin/UC-A038-orders-filter-provisioning.md) |
| 5 | a: №{{ order.number }} · {{ order.contact.display_name&#124;default:'Клиент Telegram' }}{{ order.plan_title }} · {{ order.price_rub }} ₽{{ order.status_label }}{{ order.created_at&#124;date:'d.m.Y H:i' }} МСК | [UC-A007](admin/UC-A007-order.md) |
## [templates/staff/preview.html](../../templates/staff/preview.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | button: Подтвердить изменение | [UC-A033](admin/UC-A033-operation-confirm.md) |
| 2 | a: Вернуться к выбору | [UC-A009](admin/UC-A009-operation-page.md) |
## [templates/staff/requests.html](../../templates/staff/requests.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: Открыть Telegram клиента ↗ | [UC-A044](admin/UC-A044-telegram-contact.md) |
| 2 | a: Найти подписку → | [UC-A001](admin/UC-A001-users.md) |
| 3 | button: Сохранить ответ и закрыть | [UC-A045](admin/UC-A045-request-reply.md) |
| 4 | a: Проверить перевод и продлить вручную → | [UC-A009](admin/UC-A009-operation-page.md) |
## [templates/telegram.html](../../templates/telegram.html)

| № | Тип / подпись в шаблоне | UC |
|---|---|---|
| 1 | a: Открыть @{{ bot_username }} ↗ | [UC-W028](web/UC-W028-tg-open.md) |
| 2 | button: Это мой Telegram — завершить | [UC-W030](web/UC-W030-tg-confirm.md) |
| 3 | a: Я подтвердил в боте — обновить | [UC-W029](web/UC-W029-tg-refresh.md) |
| 4 | button: Создать одноразовую ссылку | [UC-W027](web/UC-W027-tg-create.md) |

## Telegram: покрытие маршрутов кнопок

| Маршрут | UC |
|---|---|
| menu | [UC-B001-menu](bot/UC-B001-menu.md) |
| prices | [UC-B002-prices](bot/UC-B002-prices.md) |
| new | [UC-B003-new](bot/UC-B003-new.md) |
| renew | [UC-B004-renew](bot/UC-B004-renew.md) |
| orders | [UC-B015-orders](bot/UC-B015-orders.md) |
| order: | [UC-B016-order-status](bot/UC-B016-order-status.md) |
| paid: | [UC-B017-paid](bot/UC-B017-paid.md) |
| cancel-order: | [UC-B018-cancel-order](bot/UC-B018-cancel-order.md) |
| existing | [UC-B019-existing](bot/UC-B019-existing.md) |
| support | [UC-B020-support](bot/UC-B020-support.md) |
| support-cancel | [UC-B021-support-cancel](bot/UC-B021-support-cancel.md) |
| help | [UC-B022-help](bot/UC-B022-help.md) |
| portal | [UC-B023-portal](bot/UC-B023-portal.md) |
| status | [UC-B024-status](bot/UC-B024-status.md) |
| connect | [UC-B025-connect](bot/UC-B025-connect.md) |
| bind: | [UC-B047-bind](bot/UC-B047-bind.md) |
| plan- (все варианты) | [UC-B013-plan-year](bot/UC-B013-plan-year.md), [UC-B009-plan-quarter](bot/UC-B009-plan-quarter.md), [UC-B005-plan-week](bot/UC-B005-plan-week.md), [UC-B007-plan-month](bot/UC-B007-plan-month.md), [UC-B011-plan-halfyear](bot/UC-B011-plan-halfyear.md) |
| buy- (все варианты) | [UC-B012-buy-halfyear](bot/UC-B012-buy-halfyear.md), [UC-B008-buy-month](bot/UC-B008-buy-month.md), [UC-B010-buy-quarter](bot/UC-B010-buy-quarter.md), [UC-B006-buy-week](bot/UC-B006-buy-week.md), [UC-B014-buy-year](bot/UC-B014-buy-year.md) |
| device- (все варианты) | [UC-B041-device-macos](bot/UC-B041-device-macos.md), [UC-B026-device-android](bot/UC-B026-device-android.md), [UC-B031-device-ios](bot/UC-B031-device-ios.md), [UC-B036-device-windows](bot/UC-B036-device-windows.md) |
| client- (все варианты) | [UC-B029-client-happ-android](bot/UC-B029-client-happ-android.md), [UC-B037-client-incy-windows](bot/UC-B037-client-incy-windows.md), [UC-B042-client-incy-macos](bot/UC-B042-client-incy-macos.md), [UC-B032-client-incy-ios](bot/UC-B032-client-incy-ios.md), [UC-B039-client-happ-windows](bot/UC-B039-client-happ-windows.md), [UC-B027-client-incy-android](bot/UC-B027-client-incy-android.md), [UC-B034-client-happ-ios](bot/UC-B034-client-happ-ios.md), [UC-B044-client-happ-macos](bot/UC-B044-client-happ-macos.md) |
| install- (все варианты) | [UC-B038-install-incy-windows](bot/UC-B038-install-incy-windows.md), [UC-B035-install-happ-ios](bot/UC-B035-install-happ-ios.md), [UC-B040-install-happ-windows](bot/UC-B040-install-happ-windows.md), [UC-B028-install-incy-android](bot/UC-B028-install-incy-android.md), [UC-B043-install-incy-macos](bot/UC-B043-install-incy-macos.md), [UC-B045-install-happ-macos](bot/UC-B045-install-happ-macos.md), [UC-B033-install-incy-ios](bot/UC-B033-install-incy-ios.md), [UC-B030-install-happ-android](bot/UC-B030-install-happ-android.md) |
| copy-link (все варианты) | [UC-B046-copy-link](bot/UC-B046-copy-link.md) |

Комбинации client/app/platform имеют собственные UC выбора и ссылки установки. URL-кнопки «Скачать …» не вызывают callback; copy_text работает средствами Telegram. Алиас web-setup обрабатывается тем же UC portal, но отдельной видимой кнопки сейчас нет.
