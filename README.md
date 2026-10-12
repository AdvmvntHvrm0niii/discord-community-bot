# Discord Community Bot

A modular Discord bot for community servers, built with **Python 3.14**, **discord.py 2.x** and **SQLite**. It provides a support-ticket system, giveaways, moderation, welcome messages with auto-roles, and server statistics, all through slash commands.

> Portfolio project. It uses only `discord.py`, `python-dotenv` and the Python standard library.

## Features

| Area | What it does |
|------|--------------|
| 🎫 **Tickets** | Admin-configured category and support role. Users open private ticket channels; one open ticket per user; a persistent **Close Ticket** button. |
| 🎉 **Giveaways** | Start, end and list giveaways. A **Join** button prevents duplicate entries. Giveaways end automatically and pick random winners, including ones that expired while the bot was offline. |
| 🛡️ **Moderation** | Message-rate anti-spam and a per-server banned-word filter. Offending messages are deleted and a warning is stored. No automatic bans, kicks or timeouts. |
| 👋 **Welcoming** | Welcome message in a chosen channel with a customisable template, plus an automatic role for new members. |
| 📊 **Stats** | Member count, ticket totals, warnings, active giveaways and the top 10 most active members. |

## Project structure

```
.
├── bot.py              # Entry point: intents, logging, DB init, cog loading, command sync, error handler
├── database.py         # SQLite layer (standard-library sqlite3, parameterized queries)
├── cogs/
│   ├── __init__.py
│   ├── tickets.py      # /setup, /ticket, Close Ticket button
│   ├── giveaways.py    # /giveaway start|end|list, Join button, auto-ending task
│   ├── moderation.py   # anti-spam, banned words, /warnings, /banword
│   ├── welcoming.py    # on_member_join, /welcome set|show
│   └── stats.py        # /stats, message-activity tracking
├── requirements.txt
├── .env.example        # configuration template (no real secrets)
└── README.md
```

## Setup

### 1. Requirements
- Python 3.10 or newer (developed on 3.14)
- A Discord account and a server where you can manage the bot

### 2. Install

```bash
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Create the Discord application
1. Open the [Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. Open the **Bot** tab and click **Reset Token**. Copy the token and keep it secret.
3. On the same tab, under **Privileged Gateway Intents**, enable:
   - **Server Members Intent** (welcome messages and auto-role)
   - **Message Content Intent** (anti-spam and the banned-word filter)
4. Open **OAuth2 → URL Generator**, select the scopes `bot` and `applications.commands`, then select these bot permissions:
   - View Channels, Send Messages, Embed Links, Read Message History
   - Manage Channels (ticket channels)
   - Manage Messages (moderation)
   - Manage Roles (auto-role)
5. Open the generated URL to invite the bot to your server.
6. In **Server Settings → Roles**, drag the bot's role **above** any role it should assign.

### 4. Configure

```bash
# Windows (PowerShell)
Copy-Item .env.example .env
# macOS / Linux
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | yes | Bot token from the Developer Portal |
| `OWNER_ID` | no | Your Discord user ID |
| `DATABASE_PATH` | no | SQLite file location (default `data/bot.db`) |
| `GUILD_ID` | no | Test server ID: commands sync to it instantly. Without it they sync globally, which can take up to an hour |

`.env` and the `data/` folder are git-ignored. The database file and tables are created automatically on first run.

### 5. Run locally

```bash
python bot.py
```

You should see `Loaded cog: ...` lines, then `Logged in as ...`. Stop it with `Ctrl+C`.

## First-time server configuration

Run these once as an administrator:

1. `/setup category:#Tickets support_role:@Support` enables tickets.
2. `/welcome set channel:#welcome role:@Member` enables welcome messages and the auto-role.
3. `/banword add word:...` builds the banned-word list.

## Command reference

| Command | Who can use it | Description |
|---------|----------------|-------------|
| `/setup category support_role` | Administrators | Choose the ticket category and the support role |
| `/ticket [subject]` | Everyone | Open a private ticket (one open ticket per user, 30 s cooldown) |
| `/stats` | Everyone | Server statistics embed |
| `/giveaway start prize duration [winner_count]` | Administrators | Start a giveaway. Duration like `30m`, `2h`, `1d` (10 s to 30 d), 1 to 20 winners |
| `/giveaway end giveaway_id` | Administrators | End a giveaway now and draw winners |
| `/giveaway list` | Everyone | Show running giveaways with their IDs |
| `/warnings member` | Moderators (Moderate Members) | Show a member's stored warnings |
| `/banword add\|remove\|list` | Administrators | Manage the banned-word list |
| `/welcome set [channel] [role] [message]` | Administrators | Configure welcome channel, auto-role and message. `{user}` and `{server}` are placeholders |
| `/welcome show` | Administrators | Show the current welcome settings |

Buttons: **Close Ticket** (ticket author, support role or users with Manage Channels) and **Join Giveaway** (everyone).

## Moderation behaviour

- **Banned words:** matched as whole words, case-insensitive (`ass` does not match `class`).
- **Anti-spam:** more than 5 messages within 8 seconds. Extra messages are deleted and the user gets **one** warning per 30 seconds, so a burst does not stack warnings.
- **Exempt:** bots, administrators and members with Manage Messages.
- **No automatic punishment.** Warnings are recorded for human moderators to review with `/warnings`.

## Architecture

- **Entry point (`bot.py`):** a `commands.Bot` subclass that owns the shared `Database` instance. `setup_hook()` loads every `cogs/*.py` file and syncs the slash-command tree. A single global app-command error handler logs unexpected errors with a traceback and shows users a short, friendly message.
- **Cogs:** each feature is an isolated extension with its own `setup()` function. Adding a feature means adding one file to `cogs/`; nothing else needs to change.
- **Database layer (`database.py`):** one small class around the standard-library `sqlite3`, with narrow helper methods and parameterized queries only. Tables: `tickets`, `warnings`, `giveaways`, `giveaway_entries`, `member_activity`, `guild_settings`. `guild_settings` is a per-server key/value table shared by the ticket, welcome and moderation cogs.
- **Permissions:** admin commands use both `default_permissions` (hidden from other users in Discord's UI) and a runtime `has_permissions` check (enforced even if server admins change command visibility).
- **Persistent views:** the Close Ticket and Join Giveaway buttons use fixed `custom_id`s and are re-registered on startup, so they keep working after restarts.
- **Reliability details:**
  - Giveaway ending is an atomic `UPDATE ... WHERE status = 'active'`, so a giveaway can never be announced twice, even if the timer and `/giveaway end` race.
  - Giveaways that expire while the bot is offline are finished on the next start.
  - A failed database write after creating a channel or message removes the orphaned channel or message.
  - Missing channels, roles or permissions are logged and skipped instead of crashing handlers.

### Known limitations
- The bot uses a single SQLite connection with synchronous calls. This is fine for small and medium servers; a very busy server would want `aiosqlite` or a different database.
- Closed tickets are deleted without a transcript.
- Anti-spam state is kept in memory and resets when the bot restarts.

## Deployment (Railway / Render)

The bot is a long-running worker: it needs no web port.

**Important: SQLite lives in a file.** On both platforms the container filesystem is wiped on every deploy, so attach a **persistent volume/disk** and point `DATABASE_PATH` at it. Without one you will lose all tickets, warnings, giveaways and settings on every redeploy.

### Railway
1. Push the repository to GitHub and create a **New Project → Deploy from GitHub repo**.
2. Set the start command to `python bot.py` (Settings → Deploy).
3. Under **Variables**, add `DISCORD_TOKEN` (and optionally `OWNER_ID`).
4. Add a **Volume** mounted at `/data` and set `DATABASE_PATH=/data/bot.db`.
5. Deploy and check the logs for `Logged in as ...`.

### Render
1. Create a **Background Worker** (not a Web Service) from your GitHub repository.
2. Build command: `pip install -r requirements.txt`. Start command: `python bot.py`.
3. Add the environment variable `DISCORD_TOKEN` (and optionally `OWNER_ID`).
4. Add a **Disk** (paid instances only) mounted at `/data` and set `DATABASE_PATH=/data/bot.db`.
5. Deploy and check the logs.

Notes: do not set `GUILD_ID` in production. Never commit `.env`. If a token ever leaks, reset it in the Developer Portal immediately.

## Testing checklist

- [ ] Bot starts and logs `Loaded cog:` for all 5 cogs and `Logged in as ...`
- [ ] `/setup` is hidden from, and refused for, non-admins
- [ ] `/ticket` before `/setup` shows a friendly "not set up" message
- [ ] `/ticket` creates a private channel; a second `/ticket` is refused
- [ ] Close Ticket deletes the channel after 5 s; a new ticket can then be opened
- [ ] `/giveaway start` with `1m`, join with two accounts, a double-click is refused, it ends and announces winners
- [ ] `/giveaway end` ends one early; `/giveaway list` shows only running giveaways
- [ ] A banned word is deleted and warned; `/warnings` shows it
- [ ] Sending 6+ messages quickly triggers anti-spam (use a non-admin account)
- [ ] A new member gets the role and the welcome message
- [ ] `/stats` works on a fresh server (no data) and after some chatting

## License

MIT. Free to use as a learning or portfolio reference.
