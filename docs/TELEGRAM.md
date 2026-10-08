# Telegram setup

PlateWatch can post every plate (or only watchlisted plates) to Telegram chats and groups with a photo of the vehicle,
and the same bot answers commands such as `/last`, `/plate ABC123` and `/snapshot`.

You need a Telegram account to create the bot. Nobody else can do that step for you.

## 1. Create the bot (2 minutes)

1. In Telegram, open **@BotFather** and send `/newbot`.
2. Choose a display name and a username ending in `bot`.
3. BotFather replies with a **token** like `123456789:AAH...`. Treat it like a password: anyone with it controls the bot.
   If it leaks, send `/revoke` to BotFather to get a new one.
4. Put it in an environment variable, **not** in the config file or chat:
   ```bash
   export TELEGRAM_BOT_TOKEN='123456789:AAH...'
   ```

## 2. Add the bot to your group and find the chat id

1. Create a group (or use an existing one) and **add the bot as a member**. It does not need to be an admin.
2. Run:
   ```bash
   platewatch telegram-setup --send-test
   ```
   It checks the token, then waits 60 s. Send any message in the group (for example `/id`). It prints each chat it sees
   and a config snippet to paste. Group ids are **negative** (`-100…` for supergroups); private chat ids are positive.
3. To use the bot in a private chat too, message it and note your own user id from `/id`; add it to `bot.admins`.

## 3. Configure

```yaml
sinks:
  - type: telegram
    token: ${TELEGRAM_BOT_TOKEN}
    chat_ids: [-1001234567890]
    send_photo: true          # false = text only
    only_watchlist: false     # true = only plates added with /watch (alert channel)
    # cameras: [gate-in]      # only these cameras post here
    # message_thread_id: 12   # a topic inside a forum-style group
    # min_interval_seconds: 5 # throttle per chat during bursts

bot:
  enabled: true
  allowed_chats: [-1001234567890]   # who may use commands
  admins: []                        # user ids allowed to command the bot in private chat
```

Check it end to end without any camera: `platewatch test-sinks -c config.yaml` sends a fake plate to every sink.

## Bot commands

| Command | What it does |
|---|---|
| `/id` | Show this chat's id and your user id (works in any chat) |
| `/status` | Camera online/offline, last plate, today's totals |
| `/last [n]` | Most recent plates |
| `/plate ABC123` | Sightings of a plate |
| `/snapshot [camera]` | Live picture from a camera |
| `/watch ABC123 [note]` / `/unwatch` / `/watchlist` | Watchlist; a hit is posted with a 🚨 banner |
| `/subscribe` / `/unsubscribe` | Make this chat receive every read, without editing the config |

Only chats in `bot.allowed_chats` (or admins in a private chat) can use commands; everyone else only gets `/id` and `/start`.
This matters because anyone can add a public bot to their own group.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `chat not found` (400) | The bot isn't in that chat, or the id is wrong (group ids are negative) |
| `bot was kicked` / 403 | Bot was removed or blocked. Subscribed chats are dropped automatically; fix `chat_ids` yourself |
| Group id changed | Telegram changes the id when a group is upgraded to a supergroup; run `telegram-setup` again |
| Bot ignores commands in the group | Chat isn't in `bot.allowed_chats`; the `/start` reply shows the exact id to add |
| `409 Conflict` in the log | Two programs are polling the same token (a second PlateWatch, or another bot script). Run one |
| Slow / skipped messages | Telegram limits a group to about 20 messages a minute; PlateWatch backs off on 429. Use `min_interval_seconds`, `only_watchlist`, or the `cameras` filter |
