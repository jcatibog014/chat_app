# Paper Plane: Plan

## Context

The owner wants a real-time chat app to practise **WebSockets, event handling and authorization**: direct and group messages, unread counts and online presence. Decisions were made in the planning chat (see "What the Owner Chose"). The owner made the repo **github.com/jcatibog014/chat_app** (only an "Initial commit" with a 10-byte README) and asked for a new folder under Workspace. Outcome: the whole app, built in 10 stages, in one PR with a commit per stage.

**Paper Plane** ("Send it flying.") is a real-time chat app: direct messages, group chats, unread counts and online presence, with messages arriving the moment they are sent.

- Repo: `github.com/jcatibog014/chat_app`, cloned to **`C:\Users\USER\Documents\Workspace\chat_app`**.
- Package `paperplane`, local port **5015**, cookie `paperplane_session`.
- Branch `build-paper-plane`; one PR, a commit per stage, no stops.

## Getting Started (first steps when the plan is approved)

1. `git clone https://github.com/jcatibog014/chat_app` into `C:\Users\USER\Documents\Workspace` (makes `Workspace\chat_app`).
2. `git checkout -b build-paper-plane`.
3. `python -m venv .venv` with Python 3.13 (`%LOCALAPPDATA%\Programs\Python\Python313\python.exe`), install `requirements.txt`.
4. Copy this plan into `docs/PLAN.md`; write `AGENTS.md` (+ `CLAUDE.md` pointing to it), following Pulse Check's AGENTS.md layout.
5. Update the project memory (folder and repo are `chat_app`, not `real_time_chat_app`).

## What the Owner Chose

| Question | Choice |
| --- | --- |
| WebSockets | Plain WebSockets: `flask-sock` on the server, the browser's own `WebSocket`. We write the event messages, acks, heartbeats and reconnect ourselves. No Socket.IO. |
| Accounts | Open sign-up + a live Try Demo (a sample team whose people come online, type and reply). |
| Extras | All four: Message tools, Group admin, Files and pictures, Find and notify. |
| Other apps | Not connected (decided against). |
| Look | "Paper planes": sky-blue sidebar, white paper-like bubbles, sent messages glide in. Dark mode is a night sky. |
| PRs | One PR, a commit per stage. |

## Reused From Earlier Projects

Ported from `Workspace\API_uptime_monitor\pulsecheck` (and adapted; package names changed):

- `db.py` (one engine per app, session per request, SQLite WAL + busy timeout, `PlainNumberLoader` for PostgreSQL numerics), `migrations/` + `upgrade-db` (foreign keys off during SQLite runs), `tests/test_migrations.py`.
- `auth.py` (login by hand, `session_version`, lockout), `limits.py` (rate-limit windows), `tokens.py` (signed links: invites, demo friend link).
- `targets.py` (private-network guard, DNS checked at connect time) for link previews.
- `seed.py` / `views/demo.py` patterns (per-visitor demo, random `session_version`, `.invalid` emails, purge after 24 h, caps), `tick.py` + `.github/workflows/tick.yml`.
- `_toasts.html`, `_icons.html`, `_macros.html` (`page_head`), `theme.js`, the themed `ask()` confirm dialog in `app.js`, `sw.js` + web push (`pywebpush`, `make-vapid-keys`), `docs/take_screenshots.py`, `docs/make_icons.py`, `tests/test_readme.py`, `tests/test_look.py`, `tests/test_demo.py::test_every_page_of_the_demo_shows_an_example`.
- Hire Wire's recovery code (instead of email reset).
- Fixed lessons carried over: own cookie name with a test; `sqlite_autoincrement` on `users`/demo tables; every FK to a bulk-deleted parent has an index starting with that column; new NOT NULL columns need `server_default`; booleans default `sa.false()`.

## Stack

- Flask 3, SQLAlchemy 2.0 ORM + Alembic, SQLite locally and in tests, **Neon** PostgreSQL live (a chat app is idle most of the time, and Neon sleeps when idle).
- **`flask-sock`** (uses `simple-websocket`) for `/ws`. Works with `flask run` locally and with gunicorn threads on Render.
- Login by hand, Werkzeug hashes. Pillow (pictures), `pywebpush` (phone notifications). `pytest`; SQLAlchemy warnings fail tests.
- Render free plan + Neon (Singapore) + a GitHub Actions timer (demo clean-up).
- Every page works without JavaScript (sending is a normal form POST); `app.js` and `socket.js` make it live. Strict CSP: no inline scripts or styles.

### Why one server process

The live part keeps an in-memory **hub** (who is connected, which tabs). That only works when every WebSocket lands on the same process, so Render runs ONE gunicorn process with many threads (each open WebSocket holds a thread; 64 threads, about 60 people at once). The hub sits behind a small `Broker` interface; the README explains how a second server would need PostgreSQL `LISTEN/NOTIFY` (or Redis). Explained, not built.

## The Event Protocol (the main lesson)

Every WebSocket frame is one JSON object:

- Client to server: `{"type": "message.send", "ref": "c17", "data": {...}}`.
- Server reply: `{"type": "ack", "ref": "c17", "data": {...}}` or `{"type": "error", "ref": "c17", "code": "forbidden", "message": "..."}`.
- Server events: `message.new`, `message.updated`, `presence`, `typing`, `read`, `conversation.updated`, `conversation.removed`, ...

Rules:

- **One router** (`events.py`): `@on("message.send", schema=...)`. The router checks the shape; the handler checks permission, works in one transaction, returns an ack plus events to send. Handlers never touch sockets, so they are tested without a network.
- **Idempotent sending:** each message has a browser-made `client_id`; `UNIQUE(sender_id, client_id)` saves a re-send once and the ack returns the first copy.
- **Order and unread:** `conversations.last_seq` is bumped in the same transaction (`UPDATE ... SET last_seq = last_seq + 1 RETURNING`); `memberships.last_read_seq`; unread = difference (minus own messages).
- **Catch-up:** every change bumps `conversations.version` and stamps the row; on reconnect the browser sends `sync` with its versions and gets only what changed (or "reload").
- **Heartbeats and reconnect:** ping every 25 s; server closes after 60 s of silence; backoff + jitter 1 s to 30 s; "Reconnecting..." after 2 s; unsent messages wait and go out on reconnect.
- **Limits:** frames up to 16 KB, 20 messages / 10 s per person, 5 connections per person.

## Authorization (the other main lesson)

- **Handshake checked like a page:** session cookie, `session_version` (password change / "Log out other devices" closes sockets at once), and the **`Origin` header** (cross-site WebSocket hijacking; tested).
- **One place for rules:** `access.py` `can(user, action, conversation)` with a permission table; pages and events both call it; a test walks the whole table for every role.
- **Membership checked on every event.** Removal sends `conversation.removed`; open tabs stop getting that chat.
- **Events only to allowed people:** messages to current members; presence only to people sharing a chat (and never when turned off); typing never to yourself.
- **History rule:** group setting "New members see earlier messages" (on by default); when off, only messages after `joined_seq`, in pages, search, files and catch-up alike.
- **Files** only for members who can see their message. **Blocking** stops DMs, adds and presence.
- Anything you can't see is 404 / `not_found`, never "forbidden".

## Data (main tables)

`users`, `conversations` (kind `dm`/`group`, title, `dm_key` UNIQUE, last_seq, version, history_visible), `memberships` (role, last_read_seq, joined_seq, muted_until, notify level, removed_at), `messages` (seq UNIQUE per conversation, client_id, sender, body, reply_to, edited_at, deleted_at, kind, version), `reactions`, `attachments`, `link_previews`, `invite_links`, `blocks`, `push_subscriptions`, `demo_teams`.

## Stages (one commit each, with tests and demo sample data)

1. **Setup, Accounts and Look:** app factory, db, Alembic, cookie, CSP, AGENTS.md, docs/PLAN.md; sign-up with recovery code, login, lockout, rate limits; Settings (name, password, theme, Log out, Log out other devices); Paper planes look (CSS clouds, folded-corner bubbles, drawn SVG paper-plane logo, self-hosted fonts, 4.5:1 checked); Try Demo skeleton.
2. **Chats over HTTP + Rules:** DM by username (one per pair), groups, chat list with unread badges, chat page (latest 50, "Load earlier"), form POST sending, `access.py` + permission-table test, opening marks read.
3. **WebSocket Core:** `/ws` handshake checks, `hub.py`, `events.py`, acks/errors, `message.send`, fan-out, `read` to own tabs, `sync`, heartbeats, limits; `socket.js` (reconnect, outbox, live list/badges, tab-title count). End-to-end tests with a real server on a free 127.0.0.1 port and real WebSocket clients.
4. **Presence and Typing:** Online / Away (all tabs hidden 5 min) / offline after 10 s grace / Last seen; dots in list and header; "Show when I'm online" setting; typing throttled 3 s, shown 5 s, never stored.
5. **The Live Demo Crew:** a background thread drives the demo team only while the visitor is connected (online/away, typing, replies, group chatter); "Invite a friend to your demo" signed link (bot stops playing that person); sample DMs, groups, unread and muted chats.
6. **Message Tools:** replies, edit ("edited"), delete for everyone, reactions, "Seen by"; all live.
7. **Group Admin and Blocking:** owner/admin/member, group info page (rename, add/remove, make admin, mute a member, leave with hand-over, delete), invite links (expiry, use limit, off), system lines, history setting, block/unblock.
8. **Files and Pictures:** HTTP upload then a message pointing to it; Pillow WebP + EXIF removed + thumbnail + viewer; other files as attachments with `nosniff`; 5 MB a file, per-person storage limit; link previews in the background with the ported private-network guard, then `message.updated`.
9. **Find and Notify:** search in your own chats (history rule), @mentions + badge, mute a chat (1 h / 8 h / until turned back on), browser notifications when hidden, PWA + web push when no tab is open.
10. **Go Live and README:** `render.yaml` (service `paper-plane`, one process, threads), `docs/DEPLOY.md`, README in simple English with screenshots of every page from Try Demo.

## Not Built (and Why)

- Connections to the other apps (the owner decided against it).
- More than one server process (explained in the README).
- End-to-end encryption, voice and video calls.

## Verification

- `.venv\Scripts\python -m pytest` after every stage: unit tests (router, access table, presence with an injected clock), end-to-end WebSocket tests (two users chatting, reconnect + catch-up, hijack refused by Origin, removed member stops receiving), migrations match models, demo pages all show examples, README pictures match.
- Run `.venv\Scripts\flask --app paperplane run --debug --port 5015`, open two browsers (normal + private window via the demo friend link) and check live messages, unread badges, presence, typing, and a reconnect after stopping/starting the server.
- Screenshots and look checks with Playwright borrowed from `expense_tracker\.venv`.
- Push the branch with visible output, confirm `origin/build-paper-plane` has every commit, then open the PR with `--body-file`.

## Things to Watch

- Ask the owner to run the tests on an empty Neon branch (never run on PostgreSQL in past projects).
- Render's free plan sleeps after 15 minutes; the socket reconnects by itself after waking.
- Real phones (web push) and Render's handling of long-lived sockets are only tried when live.
