# Приёмник Telegram

Сообщение боту штаба → задача с меткой `входящее` в этом репо. Бот отвечает «#N принято» со ссылкой; ответ на это сообщение — комментарий к #N. Принимает только от владельца; пересланное — «Доверие: третье лицо»; вложения не переносит.

- `src/inbox.js` — что делать с сообщением (чистые функции), `src/worker.js` — сеть, `test/` — `node --test`.
- Выкладка — `.github/workflows/telegram.yml` на пуш в `main`.

## Настройка — владелец, один раз

Значения ключей в беседу с Claude не отправлять: только туда, куда сказано, и в менеджер паролей.

0. **Репо.** В `wrangler.toml` впиши `REPO` — свой логин и репо штаба.
1. **Бот.** В Telegram — `@BotFather` → `/newbot` → имя «Штаб», адрес — любой свободный на `_bot`. Он выдаст ключ бота.
2. **Свой id.** Написать своему боту что угодно, затем открыть в браузере `https://api.telegram.org/bot<ключ бота>/getUpdates` — число после `"from":{"id":`. Работает до шага 8 (привязки).
3. **Cloudflare.** Завести аккаунт на dash.cloudflare.com (бесплатный план). Открыть «Workers & Pages» — при первом входе выбрать поддомен `workers.dev`. Там же справа — **Account ID**, скопировать.
4. **Ключ Cloudflare.** Профиль → «API Tokens» → «Create Token» → шаблон **«Edit Cloudflare Workers»** → в «Account Resources» — свой аккаунт, в «Zone Resources» — «All zones» → «Create Token» → скопировать.
5. **Секреты репо штаба.** github.com/<логин>/<репо штаба> → Settings → Secrets and variables → Actions → «New repository secret»: `CLOUDFLARE_API_TOKEN` (п. 4) и `CLOUDFLARE_ACCOUNT_ID` (п. 3). Затем Actions → «Приёмник Telegram» → «Run workflow» (или попросить сессию) — Worker `shtab-telegram` появится в Cloudflare.
6. **Ключ GitHub для приёмника.** github.com/settings/personal-access-tokens/new → имя `shtab-telegram`, срок — год, «Only select repositories» → репо штаба, «Permissions» → **Issues: Read and write** → «Generate token» → скопировать.
7. **Секреты Worker'а.** Cloudflare → «Workers & Pages» → `shtab-telegram` → Settings → «Variables and Secrets» → «Add», тип **Secret**, четыре штуки:
   - `TELEGRAM_TOKEN` — ключ бота (п. 1);
   - `GITHUB_TOKEN` — ключ GitHub (п. 6);
   - `WEBHOOK_SECRET` — придумать: 30+ знаков, только латиница, цифры, `_` и `-`;
   - `OWNER_ID` — число из п. 2.

   Сохранить («Deploy»).
8. **Привязать бота.** Открыть в браузере `https://shtab-telegram.<поддомен>.workers.dev/setup?key=<WEBHOOK_SECRET>` → «Вебхук привязан».
9. **Проба.** Написать боту `/start` — подсказка; потом любую мысль — «#N принято».

Сроки и замена ключей — `docs/токены.md`; если пропал Cloudflare или бот — `docs/07-Аварийный план.md`.
