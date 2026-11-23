"""Paper Plane, a real-time chat app. "Send it flying."

Direct messages and group chats, unread counts and online presence, with
every message arriving the moment it is sent. The live part is plain
WebSockets (flask-sock on the server, the browser's own WebSocket), with an
event protocol written by hand: see events.py, hub.py and live.py.

Flask builds the app inside create_app() (the "app factory" pattern).
Tests call it with their own settings, so each test gets a fresh app and database.
"""
import mimetypes
import os
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

# Python doesn't know this one, and browsers want the right type for the app manifest.
mimetypes.add_type("application/manifest+json", ".webmanifest")

APP_NAME = "Paper Plane"
TAGLINE = "Send it flying."
DEV_SECRET_KEY = "dev-only-not-secret"
LOCAL_PORT = 5015


def content_security_policy():
    """What the browser may load and talk to. Scripts, styles and fonts come only
    from this site, so even if someone sneaked a <script> into a message, the
    browser would refuse to run it.

    connect-src names the WebSocket address too: older browsers don't count
    ws:// and wss:// as 'self'."""
    host = request.host
    return "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "font-src 'self'",
        "img-src 'self' data: blob:",
        f"connect-src 'self' ws://{host} wss://{host}",
        "worker-src 'self'",  # the service worker, /sw.js
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


def env_flag(name, default):
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    on_render = bool(os.environ.get("RENDER"))  # Render sets RENDER=true on its servers
    app.config.from_mapping(
        # Signs the login cookie, invite links and the demo's friend links.
        SECRET_KEY=os.environ.get("SECRET_KEY", DEV_SECRET_KEY),
        # Local SQLite file, in the instance/ folder (git ignores it).
        DATABASE=str(Path(app.instance_path) / "paperplane.sqlite"),
        # Set on the live site: then we use PostgreSQL (Neon) instead of SQLite.
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        ON_RENDER=on_render,
        # The site's own address, for links that leave the site (phone notifications).
        BASE_URL=os.environ.get("BASE_URL", f"http://127.0.0.1:{LOCAL_PORT}").rstrip("/"),
        # The login cookie has its own name. Browsers keep cookies per HOST, not per port,
        # and every Flask app calls its cookie "session": the owner's other apps on
        # 127.0.0.1 (ports 5000-5014) would overwrite Paper Plane's login.
        SESSION_COOKIE_NAME="paperplane_session",
        # Cookie safety: not readable by JavaScript (Flask's default), not sent
        # with forms from other sites, and only over HTTPS on the live site.
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=on_render,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        # Pictures and files are sent with a normal form (a file is at most 5 MB, files.py).
        MAX_CONTENT_LENGTH=6 * 1000 * 1000,
        # Pages the WebSocket handshake may come from (live.py checks the Origin header).
        # Empty: only this site's own address.
        ALLOWED_ORIGINS=[origin.strip() for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",") if origin.strip()],
        # flask-sock passes these to simple-websocket: the biggest frame we accept, and
        # a WebSocket ping every 25 s, which finds connections that died without a goodbye.
        SOCK_SERVER_OPTIONS={"max_message_size": 16 * 1024, "ping_interval": 25},
        # The secret the GitHub Actions timer sends to POST /tasks/tick (tick.py).
        TICK_KEY=os.environ.get("TICK_KEY"),
        # The demo's made-up people (crew.py) talk by themselves while a visitor is connected.
        RUN_CREW=env_flag("RUN_CREW", True),
        # Link previews fetch other sites (previews.py). PREVIEWS=0 turns them off.
        PREVIEWS=env_flag("PREVIEWS", True),
        ALLOW_PRIVATE_TARGETS=env_flag("ALLOW_PRIVATE_TARGETS", False),
        # Phone notifications (web push): flask --app paperplane make-vapid-keys makes the two keys.
        VAPID_PUBLIC_KEY=os.environ.get("VAPID_PUBLIC_KEY", ""),
        VAPID_PRIVATE_KEY=os.environ.get("VAPID_PRIVATE_KEY", ""),
        VAPID_SUBJECT=os.environ.get("VAPID_SUBJECT", ""),
    )
    if test_config:
        app.config.update(test_config)
    if app.testing:
        # Tests drive the demo crew and link previews by hand, never in threads of their own,
        # and never call the internet.
        app.config["RUN_CREW"] = bool(test_config and test_config.get("RUN_CREW"))
        app.config["PREVIEWS"] = bool(test_config and test_config.get("PREVIEWS"))
    if on_render:
        # Render's proxy talks HTTPS to the visitor and HTTP to us. This makes Flask
        # believe the X-Forwarded-* headers: links start with https://, and the rate
        # limits see the visitor's address instead of the proxy's.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        if app.config["ALLOW_PRIVATE_TARGETS"]:
            raise RuntimeError("ALLOW_PRIVATE_TARGETS is for your own PC only. Remove it on Render.")
    if on_render and app.config["SECRET_KEY"] == DEV_SECRET_KEY:
        raise RuntimeError("Set the SECRET_KEY environment variable on Render.")
    if on_render and not app.config["DATABASE_URL"]:
        # Without it we'd use a SQLite file, and Render forgets files on every restart.
        raise RuntimeError("Set the DATABASE_URL environment variable on Render (Neon's connection string).")

    from . import auth, blocks, crew, db, formatting, groups, live, notify, presence, previews, seed, stories
    from .views import chats as chat_views
    from .views import demo, settings
    from .views import messages as message_views
    from .views import groups as group_views
    from .views import files as file_views
    from .views import notify as notify_views
    from .views import stories as story_views

    db.init_app(app)
    auth.init_app(app)
    formatting.init_app(app)
    seed.init_app(app)
    settings.init_app(app)
    chat_views.init_app(app)
    message_views.init_app(app)
    group_views.init_app(app)
    groups.init_app(app)
    blocks.init_app(app)
    previews.init_app(app)
    file_views.init_app(app)
    notify.init_app(app)
    notify_views.init_app(app)
    stories.init_app(app)
    story_views.init_app(app)
    # auth comes first: its before_request (load_user) must run before the others'.
    for blueprint in (auth.bp, chat_views.bp, message_views.bp, group_views.bp, file_views.bp, notify_views.bp, story_views.bp, settings.bp, demo.bp):
        app.register_blueprint(blueprint)
    # After the blueprints: the WebSocket handshake check needs auth.load_user to have run first.
    live.init_app(app)
    presence.init_app(app)
    crew.init_app(app)

    from .dates import utcnow
    app.jinja_env.globals.update(APP_NAME=APP_NAME, TAGLINE=TAGLINE, now=utcnow,
                                 has_page=lambda endpoint: endpoint in app.view_functions)

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("icons/favicon.ico")

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", content_security_policy())
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if g.get("user") is not None:
            # Pages with someone's messages on them are never kept by the browser or a proxy.
            response.headers.setdefault("Cache-Control", "no-store")
        if on_render:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    def error_page(code, message):
        return render_template("error.html", code=code, message=message), code

    @app.errorhandler(400)
    def bad_request(error):
        return error_page(400, error.description or "Something in that request was wrong.")

    @app.errorhandler(403)
    def forbidden(error):
        return error_page(403, error.description or "You can't do that.")

    @app.errorhandler(404)
    def not_found(error):
        return error_page(404, "We couldn't find that. It may have been deleted, or it isn't yours to see.")

    @app.errorhandler(409)
    def conflict(error):
        return error_page(409, error.description or "That can't be done right now.")

    @app.errorhandler(413)
    def too_big(error):
        return error_page(413, "That is too big to send. A file can be at most 5 MB.")

    @app.errorhandler(429)
    def too_many(error):
        return error_page(429, error.description or "Too many tries. Please wait a little and try again.")

    return app
