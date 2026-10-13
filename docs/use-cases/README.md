# Use cases кнопок HOPE VPN

Каждое действие имеет отдельный Markdown-файл со стабильным UC-ID, участником, триггером, предусловиями, шагами, результатом, ошибками и критериями приёмки. Сверено с кодом 2026-10-12; это описание реализации, а не обещание production-готовности.

Повторяющаяся кнопка одинакового действия использует один UC, её подписи перечислены в нём. Для разных тарифов, устройств, приложений и состояний переключателей созданы отдельные файлы. Включены ссылки-кнопки, кликабельные карточки, навигация и раскрываемые блоки. Ввод полей и внешние кнопки ОС/аутентификаторов — шаги сценария, не кнопки приложения. Команды /start и /recover сами по себе не кнопки; их переходы описаны в связанных сценариях привязки/восстановления. Универсальный шаблон form.html раскрывается на конкретные действия, отдельного действующего UC для запасной подписи «Сохранить» нет.

[Карта всех элементов интерфейса и маршрутов кнопок](INVENTORY.md) связывает каждое вхождение в шаблоне с UC.

Проверки безопасности и общие требования: [SPEC](../sdd/SPEC.md). Политика обслуживания: [operations](../operations.md). Изменение документации: [SDD 0008](../sdd/changes/0008-button-use-cases.md).

Важные ограничения текущей реализации зафиксированы прямо в UC: повторный ответ на обращение не имеет reply_id, повтор переключателя меняет состояние обратно, повторная обработка stage=prepared может выполнить первую разрешённую запись, массовая отправка ограничена 500 сохранёнными ID. Это наблюдения по коду; исправления в задачу документации не входят.

## Telegram-бот

| UC | Кнопка / действие | Сценарий |
|---|---|---|
| UC-B001 | ← Главное меню; menu; 👋 С возвращением! | [Главное меню](bot/UC-B001-menu.md) |
| UC-B002 | 💳 Тарифы и цены; Выбрать тариф; ← Другой срок; prices | [Тарифы и цены](bot/UC-B002-prices.md) |
| UC-B003 | 🚀 Хочу подключиться; new | [Хочу подключиться](bot/UC-B003-new.md) |
| UC-B004 | 🔄 Продлить подписку; renew | [Продлить подписку](bot/UC-B004-renew.md) |
| UC-B005 | 7 дней · 99 ₽; plan:week | [7 дней — выбор тарифа](bot/UC-B005-plan-week.md) |
| UC-B006 | Оформить заказ; buy:week:<подпись предложения> | [7 дней — оформить заказ](bot/UC-B006-buy-week.md) |
| UC-B007 | 1 месяц · 299 ₽; plan:month | [1 месяц — выбор тарифа](bot/UC-B007-plan-month.md) |
| UC-B008 | Оформить заказ; buy:month:<подпись предложения> | [1 месяц — оформить заказ](bot/UC-B008-buy-month.md) |
| UC-B009 | 3 месяца · 799 ₽; plan:quarter | [3 месяца — выбор тарифа](bot/UC-B009-plan-quarter.md) |
| UC-B010 | Оформить заказ; buy:quarter:<подпись предложения> | [3 месяца — оформить заказ](bot/UC-B010-buy-quarter.md) |
| UC-B011 | Временно скрытый тариф; callback `plan:halfyear` отклоняется | [6 месяцев — выбор тарифа](bot/UC-B011-plan-halfyear.md) |
| UC-B012 | Временно недоступный срок; `buy:halfyear` не создаёт новый заказ | [6 месяцев — оформить заказ](bot/UC-B012-buy-halfyear.md) |
| UC-B013 | Временно скрытый тариф; callback `plan:year` отклоняется | [12 месяцев — выбор тарифа](bot/UC-B013-plan-year.md) |
| UC-B014 | Временно недоступный срок; `buy:year` не создаёт новый заказ | [12 месяцев — оформить заказ](bot/UC-B014-buy-year.md) |
| UC-B015 | 📦 Мои заказы; orders | [Мои заказы](bot/UC-B015-orders.md) |
| UC-B016 | №<номер> · <статус>; Обновить статус; order:<UUID> | [Открыть или обновить заказ](bot/UC-B016-order-status.md) |
| UC-B017 | Я оплатил; paid:<UUID> | [Я оплатил](bot/UC-B017-paid.md) |
| UC-B018 | Отменить заказ; cancel-order:<UUID> | [Отменить заказ](bot/UC-B018-cancel-order.md) |
| UC-B019 | 🔗 У меня уже есть подписка; Существующая подписка; Проверить привязку; existing | [Существующая подписка и ожидание](bot/UC-B019-existing.md) |
| UC-B020 | Написать администратору; support | [Написать администратору](bot/UC-B020-support.md) |
| UC-B021 | Отменить ввод; support-cancel | [Отменить ввод обращения](bot/UC-B021-support-cancel.md) |
| UC-B022 | 💬 Помощь; help | [Помощь](bot/UC-B022-help.md) |
| UC-B023 | 🌐 Браузерный кабинет; portal; web-setup | [Браузерный кабинет](bot/UC-B023-portal.md) |
| UC-B024 | 🔐 Моя подписка; status | [Моя подписка](bot/UC-B024-status.md) |
| UC-B025 | 🚀 Подключить VPN; ← Устройства; connect | [Подключить VPN и вернуться к устройствам](bot/UC-B025-connect.md) |
| UC-B026 | Android; ← Выбрать приложение (для Android); device:android | [Выбрать Android](bot/UC-B026-device-android.md) |
| UC-B027 | INCY; client:incy:android | [INCY для Android](bot/UC-B027-client-incy-android.md) |
| UC-B028 | Скачать INCY (экран Android) | [Установить INCY на Android](bot/UC-B028-install-incy-android.md) |
| UC-B029 | Happ; client:happ:android | [Happ для Android](bot/UC-B029-client-happ-android.md) |
| UC-B030 | Скачать Happ (экран Android) | [Установить Happ на Android](bot/UC-B030-install-happ-android.md) |
| UC-B031 | iPhone / iPad; ← Выбрать приложение (для iPhone / iPad); device:ios | [Выбрать iPhone / iPad](bot/UC-B031-device-ios.md) |
| UC-B032 | INCY; client:incy:ios | [INCY для iPhone / iPad](bot/UC-B032-client-incy-ios.md) |
| UC-B033 | Скачать INCY (экран iPhone / iPad) | [Установить INCY на iPhone / iPad](bot/UC-B033-install-incy-ios.md) |
| UC-B034 | Happ; client:happ:ios | [Happ для iPhone / iPad](bot/UC-B034-client-happ-ios.md) |
| UC-B035 | Скачать Happ (экран iPhone / iPad) | [Установить Happ на iPhone / iPad](bot/UC-B035-install-happ-ios.md) |
| UC-B036 | Windows; ← Выбрать приложение (для Windows); device:windows | [Выбрать Windows](bot/UC-B036-device-windows.md) |
| UC-B037 | INCY; client:incy:windows | [INCY для Windows](bot/UC-B037-client-incy-windows.md) |
| UC-B038 | Скачать INCY (экран Windows) | [Установить INCY на Windows](bot/UC-B038-install-incy-windows.md) |
| UC-B039 | Happ; client:happ:windows | [Happ для Windows](bot/UC-B039-client-happ-windows.md) |
| UC-B040 | Скачать Happ (экран Windows) | [Установить Happ на Windows](bot/UC-B040-install-happ-windows.md) |
| UC-B041 | macOS; ← Выбрать приложение (для macOS); device:macos | [Выбрать macOS](bot/UC-B041-device-macos.md) |
| UC-B042 | INCY; client:incy:macos | [INCY для macOS](bot/UC-B042-client-incy-macos.md) |
| UC-B043 | Скачать INCY (экран macOS) | [Установить INCY на macOS](bot/UC-B043-install-incy-macos.md) |
| UC-B044 | Happ; client:happ:macos | [Happ для macOS](bot/UC-B044-client-happ-macos.md) |
| UC-B045 | Скачать Happ (экран macOS) | [Установить Happ на macOS](bot/UC-B045-install-happ-macos.md) |
| UC-B046 | Скопировать ссылку; copy_text | [Скопировать ссылку подписки](bot/UC-B046-copy-link.md) |
| UC-B047 | Это мой аккаунт; bind:<UUID> | [Это мой аккаунт](bot/UC-B047-bind.md) |
| UC-B048 | 🌐 Веб версия | [Открыть веб-версию](bot/UC-B048-web-version.md) |

## Веб-кабинет и публичные страницы

| UC | Кнопка / действие | Сценарий |
|---|---|---|
| UC-W001 | HOPE VPN (логотип) | [Открыть главную](web/UC-W001-home.md) |
| UC-W002 | Войти (навигация); Войти в кабинет ↗ | [Открыть вход](web/UC-W002-open-login.md) |
| UC-W003 | Войти (форма) | [Войти — отправить форму](web/UC-W003-login.md) |
| UC-W004 | Выйти | [Выйти](web/UC-W004-logout.md) |
| UC-W005 | Не помню пароль | [Не помню пароль](web/UC-W005-open-recovery.md) |
| UC-W006 | Задать новый пароль | [Задать новый пароль](web/UC-W006-recover.md) |
| UC-W007 | Создать аккаунт | [Создать аккаунт по приглашению](web/UC-W007-activate.md) |
| UC-W008 | Создать логин и пароль | [Создать логин и пароль для Telegram-аккаунта](web/UC-W008-onboard.md) |
| UC-W009 | Скопировать все | [Скопировать все коды](web/UC-W009-copy-codes.md) |
| UC-W010 | Коды сохранены → | [Коды сохранены](web/UC-W010-codes-saved.md) |
| UC-W011 | Подписка | [Открыть подписку](web/UC-W011-dashboard.md) |
| UC-W012 | Аккаунт; Настроить аккаунт → | [Открыть аккаунт](web/UC-W012-account.md) |
| UC-W013 | Помощь; Открыть помощь →; Если не подключается → | [Открыть помощь](web/UC-W013-help.md) |
| UC-W014 | Связаться с поддержкой; Написать в поддержку; Новое обращение; Хочу подключиться | [Открыть поддержку](web/UC-W014-support-form.md) |
| UC-W015 | Продление | [Открыть продление](web/UC-W015-renewal-form.md) |
| UC-W016 | Подключить VPN ↗ | [Открыть подключение](web/UC-W016-connect.md) |
| UC-W017 | Изменить пароль (ссылка) | [Открыть смену пароля](web/UC-W017-password-form.md) |
| UC-W018 | Подключить Telegram ↗ | [Открыть подключение Telegram](web/UC-W018-telegram-page.md) |
| UC-W019 | Отправить обращение (поддержка) | [Отправить обращение поддержки](web/UC-W019-support-submit.md) |
| UC-W020 | Отправить обращение (продление) | [Отправить заявку на продление](web/UC-W020-renewal-submit.md) |
| UC-W021 | Экстренный доступ на час | [Экстренный доступ на час](web/UC-W021-emergency.md) |
| UC-W022 | Изменить пароль (форма) | [Изменить пароль — сохранить](web/UC-W022-password-submit.md) |
| UC-W023 | Новые коды | [Новые коды](web/UC-W023-new-codes.md) |
| UC-W024 | Отвязать Telegram | [Отвязать Telegram](web/UC-W024-unlink.md) |
| UC-W025 | Включить напоминания | [Включить напоминания](web/UC-W025-reminders-true.md) |
| UC-W026 | Выключить напоминания | [Выключить напоминания](web/UC-W026-reminders-false.md) |
| UC-W027 | Создать одноразовую ссылку | [Создать одноразовую ссылку](web/UC-W027-tg-create.md) |
| UC-W028 | Открыть в Telegram app ↗ / HTTPS fallback | [Открыть бот для привязки](web/UC-W028-tg-open.md) |
| UC-W029 | Я подтвердил в боте — обновить | [Обновить подтверждение бота](web/UC-W029-tg-refresh.md) |
| UC-W030 | Это мой Telegram — завершить | [Это мой Telegram — завершить](web/UC-W030-tg-confirm.md) |
| UC-W031 | INCY · App Store ↗ / INCY ↗ | [INCY · App Store ↗ / INCY ↗](web/UC-W031-download-incy.md) |
| UC-W032 | Скачать Happ ↗ | [Скачать Happ ↗](web/UC-W032-download-happ.md) |
| UC-W033 | Другие версии INCY ↗ | [Другие версии INCY ↗](web/UC-W033-download-incy-other.md) |
| UC-W034 | Добавить в INCY ↗ | [Добавить в INCY](web/UC-W034-import-incy.md) |
| UC-W035 | Добавить в Happ ↗ | [Добавить в Happ](web/UC-W035-import-happ.md) |
| UC-W036 | Скопировать (подписка) | [Скопировать персональную ссылку](web/UC-W036-copy-subscription.md) |
| UC-W037 | QR-код для другого устройства | [Раскрыть QR подписки](web/UC-W037-subscription-qr.md) |

## Административный интерфейс

| UC | Кнопка / действие | Сценарий |
|---|---|---|
| UC-A001 | Пользователи; ← Пользователи; Найти подписку → | [Пользователи](admin/UC-A001-users.md) |
| UC-A002 | Обращения; <число> открытых обращений | [Обращения](admin/UC-A002-requests.md) |
| UC-A003 | Операции; <число> операций в очереди | [Операции](admin/UC-A003-events.md) |
| UC-A004 | Заказы из Telegram; ← Заказы | [Заказы из Telegram](admin/UC-A004-orders.md) |
| UC-A005 | Уведомить пользователей | [Уведомить пользователей](admin/UC-A005-broadcast.md) |
| UC-A006 | <метка клиента>; <имя подписки в истории>; Карточка доступа → | [Открыть карточку клиента](admin/UC-A006-detail.md) |
| UC-A007 | №<номер> · <клиент>; ← Вернуться к заказу | [Открыть заказ](admin/UC-A007-order.md) |
| UC-A008 | + Новый клиент | [Новый клиент](admin/UC-A008-new-page.md) |
| UC-A009 | Изменить доступ; Проверить перевод и продлить вручную →; Вернуться к выбору | [Изменить доступ](admin/UC-A009-operation-page.md) |
| UC-A010 | Подключить защиту и войти | [Подключить защиту и войти](admin/UC-A010-mfa-setup.md) |
| UC-A011 | Подтвердить вход | [Подтвердить вход](admin/UC-A011-mfa-confirm.md) |
| UC-A012 | Не можете сканировать? Ввести ключ вручную | [Ввести TOTP-ключ вручную](admin/UC-A012-mfa-manual.md) |
| UC-A013 | Скопировать ключ | [Скопировать ключ TOTP](admin/UC-A013-mfa-copy.md) |
| UC-A014 | Найти | [Найти пользователя](admin/UC-A014-search.md) |
| UC-A015 | Все | [Клиенты: Все](admin/UC-A015-filter-all.md) |
| UC-A016 | Без Telegram | [Клиенты: Без Telegram](admin/UC-A016-filter-unlinked.md) |
| UC-A017 | Скоро истекают | [Клиенты: Скоро истекают](admin/UC-A017-filter-soon.md) |
| UC-A018 | Истекли | [Клиенты: Истекли](admin/UC-A018-filter-expired.md) |
| UC-A019 | Ошибки; <число> результатов требуют внимания | [Клиенты: Ошибки](admin/UC-A019-filter-errors.md) |
| UC-A020 | Импортировать из панели | [Импортировать из панели](admin/UC-A020-import.md) |
| UC-A021 | Создать черновик | [Создать черновик клиента](admin/UC-A021-draft.md) |
| UC-A022 | Выдать приглашение | [Выдать приглашение](admin/UC-A022-invite.md) |
| UC-A023 | Скопировать (персональная ссылка) | [Скопировать приглашение или восстановление](admin/UC-A023-copy-private-link.md) |
| UC-A024 | Принять под управление | [Принять под управление](admin/UC-A024-adopt.md) |
| UC-A025 | Принять текущее состояние панели | [Принять текущее состояние панели](admin/UC-A025-refresh.md) |
| UC-A026 | Ссылка восстановления | [Ссылка восстановления](admin/UC-A026-recovery-link.md) |
| UC-A027 | Отвязать потерянный Telegram | [Отвязать потерянный Telegram](admin/UC-A027-unlink-lost.md) |
| UC-A028 | Заблокировать вход в кабинет | [Заблокировать вход в кабинет](admin/UC-A028-lock.md) |
| UC-A029 | Разблокировать вход в кабинет | [Разблокировать вход в кабинет](admin/UC-A029-unlock.md) |
| UC-A030 | Исключить служебную запись | [Исключить служебную запись](admin/UC-A030-exclude.md) |
| UC-A031 | Вернуть в список | [Вернуть в список](admin/UC-A031-include.md) |
| UC-A032 | Предпросмотр | [Предпросмотр изменения доступа](admin/UC-A032-operation-preview.md) |
| UC-A033 | Подтвердить изменение | [Подтвердить изменение доступа](admin/UC-A033-operation-confirm.md) |
| UC-A034 | Повторить проверку результата | [Повторить проверку результата](admin/UC-A034-retry.md) |
| UC-A035 | Все | [Заказы: Все](admin/UC-A035-orders-filter-all.md) |
| UC-A036 | Проверить перевод | [Заказы: Проверить перевод](admin/UC-A036-orders-filter-payment.md) |
| UC-A037 | Выдать доступ | [Заказы: Выдать доступ](admin/UC-A037-orders-filter-paid.md) |
| UC-A038 | Применение | [Заказы: Применение](admin/UC-A038-orders-filter-provisioning.md) |
| UC-A039 | Подтвердить оплату | [Подтвердить оплату](admin/UC-A039-order-confirm-payment.md) |
| UC-A040 | Перевод не подтверждён | [Перевод не подтверждён](admin/UC-A040-order-reject-payment.md) |
| UC-A041 | Предпросмотр выдачи | [Предпросмотр выдачи](admin/UC-A041-order-preview.md) |
| UC-A042 | Подтвердить выдачу доступа | [Подтвердить выдачу доступа](admin/UC-A042-order-provision.md) |
| UC-A043 | Отправить через очередь бота | [Отправить через очередь бота](admin/UC-A043-order-reply.md) |
| UC-A044 | Открыть Telegram клиента ↗ | [Открыть Telegram клиента](admin/UC-A044-telegram-contact.md) |
| UC-A045 | Сохранить ответ и закрыть | [Сохранить ответ и закрыть](admin/UC-A045-request-reply.md) |
| UC-A046 | Предпросмотр и получатели | [Предпросмотр и получатели](admin/UC-A046-broadcast-preview.md) |
| UC-A047 | Проверить получателей | [Проверить получателей](admin/UC-A047-broadcast-recipients.md) |
| UC-A048 | Подтвердить отправку | [Подтвердить отправку](admin/UC-A048-broadcast-confirm.md) |
| UC-A049 | Отменить | [Отменить предпросмотр рассылки](admin/UC-A049-broadcast-cancel.md) |

Всего: **134 отдельных UC**. При изменении кнопки обновить её UC и индекс в той же задаче; новый UC получает следующий номер внутри B/W/A, существующие ID не переиспользуются.

Callback-навигация бота редактирует текущее сообщение и клавиатуру; команды,
ответы на текст и outbox остаются отдельными сообщениями. URL и copy_text
выполняются клиентом Telegram. См. [SDD 0011](../sdd/changes/0011-bot-message-navigation.md).

Возврат «Главное меню» использует стартовый экран /start по текущей привязке;
детали — [UC-B001](bot/UC-B001-menu.md) и [SDD 0012](../sdd/changes/0012-bot-home-screen.md).
