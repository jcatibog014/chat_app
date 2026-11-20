"""The WebSocket endpoint, /ws (the WebSocket lesson).

1. THE HANDSHAKE is an ordinary HTTP request that asks to "upgrade" to a
   WebSocket. check_handshake() runs before the upgrade, like any page:
   - the login cookie must be valid (g.user, from auth.load_user);
   - the ORIGIN header must be this site. Browsers send cookies with a
     WebSocket handshake even when ANOTHER site opens it, and a CSRF token
     can't be added to it. Without this check, evil.example could open a
     socket to us with your cookie and read your messages (cross-site
     WebSocket hijacking). The Origin header can't be faked by a page.
   A refused handshake gets a normal 401/403 and never becomes a socket.

2. THE LOOP: the socket gets a Connection in the hub, a "hello" frame, and
   then waits for frames. Each frame goes to the router (events.py); the
   answers go back on the same socket. Events for others go out through the
   hub when the work commits (outbox.py).

3. STAYING ALIVE: simple-websocket pings the browser every 25 s (protocol
   pings, SOCK_SERVER_OPTIONS), which finds dead connections on our side.
   The browser also sends {"type": "ping"} every 25 s, because a page can't
   see protocol pings and needs to find a dead connection on ITS side. A
   socket we haven't heard from in 70 s is closed.

The request's database session is closed before the loop: a socket can stay
open for hours, and must not hold one of the pool's few connections all that
time. Each event opens and closes its own session.
"""
import json
import time

from flask import abort, current_app, g, jsonify, request
from flask_sock import ConnectionClosed, Sock

from . import db, events, live_events  # noqa: F401  (live_events registers the handlers)
from .hub import HUB, Connection, Hub

sock = Sock()
RECEIVE_TIMEOUT = 10   # seconds: how often the loop wakes up to check for silence
SILENCE_LIMIT = 70     # seconds without a frame (the browser pings every 25)


def allowed_origins():
    own = f"{request.scheme}://{request.host}"
    return {own, *current_app.config.get("ALLOWED_ORIGINS", [])}


def handshake_problem():
    """Why this handshake is refused, as (status, message), or None if it's fine."""
    if request.headers.get("Upgrade", "").lower() != "websocket":
        return 400, "This address is for WebSocket connections."
    if request.headers.get("Origin") not in allowed_origins():
        return 403, "Connections from other sites aren't allowed."
    if g.get("user") is None:
        return 401, "Log in first."
    return None


def serve(ws):
    app = current_app._get_current_object()
    hub = app.extensions[HUB]
    connection = Connection(ws, g.user.id, g.user.session_version)
    # Let go of the request's database session: this socket may stay open for hours.
    db.close_session()
    if not hub.register(connection):
        connection.send_json(events.error(None, "too_many_connections",
                                          "You have too many tabs open. Close one and reload."))
        connection.close(4008, "too_many_connections")
        return
    try:
        connection.send_json(events.frame("hello", {"user_id": connection.user_id, "heartbeat": 25}))
        for hello in hello_hooks:
            hello(app, connection)
        while not connection.closed:
            text = ws.receive(timeout=RECEIVE_TIMEOUT)
            now = time.monotonic()
            if text is None:
                if now - connection.last_heard > SILENCE_LIMIT:
                    connection.close(4000, "silent")
                continue
            connection.last_heard = now
            if isinstance(text, bytes):
                connection.send_json(events.error(None, "bad_frame", "Send text frames (JSON)."))
                continue
            for reply in events.dispatch(app, connection, text):
                connection.send(json.dumps(reply, separators=(",", ":")))
    except ConnectionClosed:
        pass
    finally:
        hub.unregister(connection)


# Later stages send a little more after "hello" (presence: who is online): function(app, connection).
hello_hooks = []


def init_app(app):
    app.extensions[HUB] = Hub()
    sock.init_app(app)

    @sock.route("/ws")
    def websocket(ws):
        serve(ws)

    @app.before_request
    def check_handshake():
        if request.endpoint == "websocket":
            problem = handshake_problem()
            if problem:
                abort(*problem)

    @app.get("/me.json")
    def me_json():
        """socket.js asks this when it can't connect: logged out, or just offline?"""
        if g.get("user") is None:
            return jsonify(logged_in=False), 401
        return jsonify(logged_in=True, user_id=g.user.id)
