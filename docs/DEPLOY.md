# Put Paper Plane on the Internet

Paper Plane runs on **Render** (the web server) and **Neon** (the PostgreSQL database). Both have free plans. You need about 20 minutes.

Never paste a password or a connection string into a chat, an issue or a commit. Keep them only in Render and on your PC.

## Part 1: The Database (Neon)

1. Go to https://neon.tech and log in.
2. Click **New Project**. Name: `paper-plane`. Region: **AWS Asia Pacific (Singapore)** (close to Render's Singapore servers).
3. Open **Connection Details**. Choose **Pooled connection**. Copy the connection string. It starts with `postgresql://`.
4. Keep it secret. You paste it into Render in Part 2.

## Part 2: The Web Server (Render)

1. Go to https://render.com and log in with GitHub.
2. Click **New** and then **Blueprint**.
3. Choose the repo **jcatibog014/chat_app**. Render reads `render.yaml`.
4. Render asks for the values marked "sync: false":
   - `DATABASE_URL`: paste Neon's connection string from Part 1.
   - `BASE_URL`: leave empty for now.
   - `VAPID_...`: leave empty for now (Part 4 is optional).
5. Click **Apply**. Render builds the app. It makes the tables by itself (`upgrade-db`).
6. When it says **Live**, copy the site's address (like `https://paper-plane-xxxx.onrender.com`).
7. In Render, open the service, then **Environment**. Set `BASE_URL` to that address. Save. Render restarts the app.

## Part 3: Try It

1. Open the address. You see the login page.
2. Click **Try the demo**. Mika's chats open. Wait about 15 seconds: Ana, Ben and Carlo come online and answer.
3. Click **Create an account** to make your real account. **Save the recovery code.** It is the only way back in if you forget your password.

The free plan sleeps after 15 minutes without visitors. The first visit after that takes about a minute. The chat connects again by itself.

## Part 4 (Optional): Phone Notifications

Phone notifications need two keys (VAPID keys). Make them on your PC. Press Enter after each line:

```
cd C:\Users\USER\Documents\Workspace\chat_app
```

```
.venv\Scripts\flask --app paperplane make-vapid-keys
```

It prints two lines: `VAPID_PUBLIC_KEY=...` and `VAPID_PRIVATE_KEY=...`.

1. In Render, open **Environment**.
2. Add `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` with the values after the `=`.
3. Add `VAPID_SUBJECT` with `mailto:` and your email, like `mailto:you@example.com`.
4. Save. In the app, open **Settings** and click **Turn on notifications**.

## Run the Tests on Neon (Recommended Once)

The tests normally use SQLite. Run them once on PostgreSQL too:

1. In Neon, make a **branch** called `test` (Branches, then New Branch). It must be empty: in the branch, open the SQL Editor and run `DROP SCHEMA public CASCADE; CREATE SCHEMA public;`.
2. Copy the test branch's connection string.
3. On your PC, press Enter after each line:

```
cd C:\Users\USER\Documents\Workspace\chat_app
```

```
$env:TEST_DATABASE_URL = "paste the TEST branch's connection string here"
```

```
.venv\Scripts\python -m pytest
```

Never use the live branch for tests: every test deletes all the tables.

## If Something Goes Wrong

- **"Set the DATABASE_URL environment variable"** in Render's log: Part 2, step 4 was skipped.
- **"password authentication failed"**: the connection string is old (you reset the password in Neon). Copy it again and update `DATABASE_URL` in Render.
- **The chat says "Reconnecting..." for a long time**: the site was asleep. Reload the page after a minute.
- **Old demos**: they are deleted when someone starts a new demo. To delete them now, run `purge-demos` against Neon from your PC (set `DATABASE_URL` first, like `TEST_DATABASE_URL` above).
