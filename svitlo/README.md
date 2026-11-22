# Світло 2.2

Telegram-бот: найсвіжіший графік відключень для черги 2.2 (Київщина) + чи є світло вдома (розетка Smart Life).

## Звідки графік
* **Сайт ДТЕК КРЕМ** (dtek-krem.com.ua/ua/shutdowns) — через відкрите дзеркало
  [outage-data-ua](https://github.com/Baskerville42/outage-data-ua), перевірка щохвилини.
* **Ваші чати й канали** — через ваш Telegram-акаунт (секрет `SVITLO_SESSION`): нові пости миттєво,
  плюс пошук по всіх чатах кожні 15 хв. Пости з «Київщина» беруться одразу; місцеві чати без назви
  регіону бот спершу пропонує підтвердити кнопками.
* Перемагає графік, **опублікований найпізніше**.

## Секрети (Settings → Secrets and variables → Actions)
| Секрет | Що це |
|---|---|
| `SVITLO_BOT_TOKEN` | токен нового бота від @BotFather |
| `SVITLO_SESSION` | рядок з `make_session.command` (окремий від `TG_SESSION` EStation) |

## Команди
`/grafik`, `/svitlo`, `/dzherela`, `/rozetka КОД` (код: Smart Life → Я → ⚙️ → Обліковий запис і безпека → Код користувача).

## Як працює безперервно
Workflow `svitlo.yml`: запуск живе ~5 год 45 хв, через 2 хв після старту ставить наступний у чергу
(concurrency `svitlo`). Стан — зашифровано в гілці `svitlo-state`. Якщо зупинився: Actions → Svitlo 2.2 → Run workflow.
