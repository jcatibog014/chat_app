"""The event router: what happens to each frame a browser sends (the event-handling lesson).

Every frame is one JSON object:

    browser -> server   {"type": "message.send", "ref": "c17", "data": {...}}
    server  -> browser  {"type": "ack",   "ref": "c17", "data": {...}}                 it worked
                        {"type": "error", "ref": "c17", "code": "not_found", "message": "..."}
                        {"type": "message.new", "data": {...}}                         news, no ref

`ref` is the browser's own number for its request, so it can match the answer
to the question (many can be in flight at once).

A handler is registered with @on("type", field=(type, required, max_length)):

    @on("message.send", conversation_id=(int, True), body=(str, True, 4000), limit=(20, 10))
    def send(ctx, data): ...

dispatch() does the same steps for every frame, in this order:
  1. read the JSON and the envelope (type, ref, data);
  2. find the handler, and check the data's SHAPE against its fields;
  3. open a database session, and check the person is still logged in with the
     same session_version as when the socket opened (a password change ends it);
  4. check the rate limit;
  5. run the handler, which checks PERMISSION (access.py) and does the work;
  6. commit (the live events go out now, outbox.py) and answer with an ack,
     or roll back and answer with an error.
Handlers never touch sockets: they take data and return data. So they are
tested without any network (tests/test_events.py).
"""
import json
import logging
from dataclasses import dataclass, field

from . import db
from .auth import user_from_session
from .dates import utcnow
from .problems import Problem, TooMany

log = logging.getLogger(__name__)

REF_MAX_LENGTH = 40
HANDLERS = {}


@dataclass
class Spec:
    function: object
    fields: dict
    limit: tuple | None  # (how many, per seconds)


@dataclass
class Context:
    """What a handler gets: the session, the person, their socket, the hub and the moment."""
    app: object
    s: object
    user: object
    connection: object
    hub: object
    now: object
    extra: dict = field(default_factory=dict)


def on(kind, limit=None, **fields):
    """Register a handler. fields: name=(type, required) or (type, required, max_length)."""
    def register(function):
        HANDLERS[kind] = Spec(function, fields, limit)
        return function
    return register


def frame(kind, data=None, ref=None):
    payload = {"type": kind}
    if ref is not None:
        payload["ref"] = ref
    if data is not None:
        payload["data"] = data
    return payload


def error(ref, code, message):
    payload = {"type": "error", "code": code, "message": message}
    if ref is not None:
        payload["ref"] = ref
    return payload


def check_shape(spec, data):
    """The data has the right fields, of the right types and sizes. Returns a cleaned dict,
    or raises Problem. (Permission is checked later, by the handler: shape first, then rights.)"""
    if not isinstance(data, dict):
        raise Problem("The data must be an object.")
    clean = {}
    for name, rule in spec.fields.items():
        kind, required = rule[0], rule[1]
        max_length = rule[2] if len(rule) > 2 else None
        value = data.get(name)
        if value is None:
            if required:
                raise Problem(f"Missing {name}.")
            clean[name] = None
            continue
        if kind is int:
            # JSON has one number type: refuse true/false and 1.5, accept "12" from a form-like client.
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise Problem(f"{name} must be a whole number.")
            try:
                value = int(value)
            except ValueError:
                raise Problem(f"{name} must be a whole number.") from None
        elif kind is str:
            if not isinstance(value, str):
                raise Problem(f"{name} must be text.")
            if max_length is not None and len(value) > max_length:
                raise Problem(f"{name} is too long.")
        elif kind is dict:
            if not isinstance(value, dict):
                raise Problem(f"{name} must be an object.")
        elif kind is bool:
            if not isinstance(value, bool):
                raise Problem(f"{name} must be true or false.")
        clean[name] = value
    return clean


def dispatch(app, connection, text, now=None):
    """Handle one frame from a socket. Returns the frames to send back to THAT socket.
    (Events for other people go out through the outbox when the work commits.)"""
    try:
        message = json.loads(text)
    except (TypeError, ValueError):
        return [error(None, "bad_json", "That frame isn't JSON.")]
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        return [error(None, "bad_frame", "A frame needs a type.")]
    ref = message.get("ref")
    if not isinstance(ref, str) or len(ref) > REF_MAX_LENGTH:
        ref = None
    kind = message["type"]
    if kind == "ping":
        # The browser's heartbeat: no database, just "I'm here".
        return [frame("pong", ref=ref)]
    spec = HANDLERS.get(kind)
    if spec is None:
        return [error(ref, "unknown_type", f"Unknown event: {kind[:40]}.")]
    try:
        data = check_shape(spec, message.get("data") or {})
    except Problem as problem:
        return [error(ref, "bad_request", problem.message)]

    hub = app.extensions["paperplane_hub"]
    moment = now or utcnow()
    with app.app_context():
        s = db.new_session(app)
        try:
            user = user_from_session(s, {"user_id": connection.user_id,
                                         "session_version": connection.session_version}, moment)
            if user is None:
                # Logged out elsewhere (password change, demo over): this socket is done.
                connection.close(4001, "logged_out")
                return [error(ref, "logged_out", "You have been logged out.")]
            if spec.limit and not hub.allow(f"{kind}:{user.id}", *spec.limit):
                raise TooMany("You're sending very fast. Please wait a moment.")
            context = Context(app, s, user, connection, hub, moment)
            result = spec.function(context, data)
            s.commit()  # live events for the others go out here (outbox.py)
            return [frame("ack", result if result is not None else {}, ref=ref)]
        except Problem as problem:
            s.rollback()
            return [error(ref, problem.code, problem.message)]
        except Exception:
            s.rollback()
            log.exception("Event %s failed", kind)
            return [error(ref, "server_error", "Something went wrong. Please try again.")]
        finally:
            s.close()
