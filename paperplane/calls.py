"""Audio, video and group calls.

The sound and pictures go straight between the browsers (WebRTC, static/call.js); this server
only rings people, says who is in the call, and passes the browsers' connection notes
(SDP offers/answers and ICE candidates) between them, over the same WebSocket as chat.

    browser -> server   call.start {conversation_id, kind}   ring the chat's other members
                        call.join {call_id}                   answer (or join a group call)
                        call.decline {call_id}                not now (a DM call ends)
                        call.leave {call_id}                  hang up
                        call.signal {call_id, to, data}       a connection note for one person
                        call.media {call_id, audio, video}    my microphone / camera is on or off
    server -> browser   call.ring, call.joined, call.left, call.declined, call.taken,
                        call.ended, call.signal, call.media, call.state

Rules (the same as the chat's): only members, who may speak in it (a block closes a DM, an
admin's mute stops a member), and at most MAX_PEOPLE in a call (everyone connects to everyone).
Nobody rings someone who restricted them. A call ends when the last person leaves, when a DM's
other person declines, or when nobody answers (the caller's tab gives up after RING_SECONDS, and
a tab that closes leaves its call: hub.on_disconnect). Each end writes one line in the chat
("Video call · 4 min", "Missed audio call") and a missed-call line on the Notifications page of
everyone who never joined.

Which tab is in a call is kept in memory (IN_CALL), like the hub itself: one server process.
"""
import json
import threading
from datetime import datetime

from sqlalchemy import select

from . import access, chats, db, outbox
from .events import on
from .models import Call, CallParticipant, Conversation, User
from .problems import Invalid, NotFound, TooMany

KINDS = ("audio", "video")
MAX_PEOPLE = 6
RING_SECONDS = 45
SIGNAL_MAX = 20_000   # characters of SDP/ICE in one note
STUN = "stun:stun.l.google.com:19302"

IN_CALL = {}          # connection -> call id (the tab that joined)
_lock = threading.Lock()

# Hooks: function(s, call, moment) after a call starts (the demo crew answers for its teammates).
after_start_hooks = []


# ---------- Reading ----------

def ice_servers(app):
    """STUN finds each browser's public address; a TURN server (optional, TURN_URL...) relays
    the call when a network won't let two browsers reach each other directly."""
    servers = [{"urls": app.config.get("STUN_URL") or STUN}]
    if app.config.get("TURN_URL"):
        servers.append({"urls": app.config["TURN_URL"], "username": app.config.get("TURN_USERNAME", ""),
                        "credential": app.config.get("TURN_CREDENTIAL", "")})
    return servers


def active_call(s, conversation_id):
    return s.scalar(select(Call).where(Call.conversation_id == conversation_id, Call.ended_at.is_(None))
                    .order_by(Call.id.desc()))


def in_call_ids(s, call):
    """The people in the call right now (joined, not left)."""
    return set(s.scalars(select(CallParticipant.user_id).where(
        CallParticipant.call_id == call.id, CallParticipant.joined_at.is_not(None), CallParticipant.left_at.is_(None))))


def person(user):
    return {"id": user.id, "name": user.name, "first": user.first_name, "tone": user.tone}


def call_data(s, call):
    people = s.scalars(select(User).where(User.id.in_(in_call_ids(s, call)))).all()
    return {"id": call.id, "conversation_id": call.conversation_id, "kind": call.kind,
            "started_by": call.started_by_id, "answered": call.answered_at is not None,
            "people": [person(user) for user in people]}


def participant(s, call, user_id):
    row = s.scalar(select(CallParticipant).where(CallParticipant.call_id == call.id, CallParticipant.user_id == user_id))
    if row is None:
        row = CallParticipant(call_id=call.id, user_id=user_id)
        s.add(row)
    return row


def load_live_call(s, me, call_id):
    """The call (still going) and its chat, if I'm a member of the chat; else not found."""
    call = s.get(Call, call_id)
    if call is None or call.ended_at is not None:
        raise NotFound("That call has ended.")
    conversation, membership = access.load(s, me, call.conversation_id)
    return call, conversation, membership


def tabs_in(call_id):
    with _lock:
        return [connection for connection, current in IN_CALL.items() if current == call_id]


def stale(call):
    """A call nobody's tab is in (the server restarted, or tabs vanished without a goodbye)."""
    return not tabs_in(call.id)


# ---------- What people do ----------

def ring_audience(s, conversation, caller):
    """Who rings: the chat's other members, minus anyone who restricted the caller."""
    from . import restrict
    return {user_id for user_id in chats.member_ids(s, conversation) - {caller.id}
            if not restrict.has(s, user_id, caller.id)}


def state(s, conversation, call=None):
    """Tell every member's tabs whether a call is going on in this chat (the chat's Join button)."""
    data = {"conversation_id": conversation.id, "call_id": None, "kind": None, "count": 0}
    if call is not None and call.ended_at is None:
        data.update(call_id=call.id, kind=call.kind, count=len(in_call_ids(s, call)))
    outbox.emit(s, chats.member_ids(s, conversation), "call.state", data)


def start(s, me, connection, conversation_id, kind, moment):
    """Start a call (or, if one is already going on in this chat, hand it back to join)."""
    if kind not in KINDS:
        raise Invalid("Choose an audio or a video call.")
    conversation, membership = access.load(s, me, conversation_id)
    chats.require_speaking("send", s, conversation, membership, me, moment)
    current = active_call(s, conversation.id)
    if current is not None and stale(current):
        end(s, current, moment)
        current = None
    if current is not None:
        return current, True
    if connection in IN_CALL:
        raise Invalid("You're already in a call.")
    call = Call(conversation_id=conversation.id, started_by_id=me.id, kind=kind, started_at=moment)
    s.add(call)
    s.flush()
    row = participant(s, call, me.id)
    row.joined_at = moment
    s.flush()
    with _lock:
        IN_CALL[connection] = call.id
    title = conversation.title if conversation.is_group else None
    outbox.emit_each(s, ring_audience(s, conversation, me), "call.ring", lambda user_id: {
        "call_id": call.id, "conversation_id": conversation.id, "kind": kind, "is_group": conversation.is_group,
        "from": person(me), "title": title or me.name})
    outbox.emit(s, {me.id}, "call.taken", {"call_id": call.id})  # my other tabs don't ring
    state(s, conversation, call)
    for hook in after_start_hooks:
        hook(s, call, moment)
    return call, False


def join(s, me, connection, call_id, moment):
    call, conversation, membership = load_live_call(s, me, call_id)
    if connection in IN_CALL and IN_CALL[connection] != call.id:
        raise Invalid("You're already in a call.")
    if conversation.is_dm:
        chats.require_speaking("send", s, conversation, membership, me, moment)
    people = in_call_ids(s, call)
    if me.id not in people and len(people) >= MAX_PEOPLE:
        raise TooMany(f"This call is full ({MAX_PEOPLE} people at most).")
    row = participant(s, call, me.id)
    row.joined_at, row.left_at, row.declined_at = row.joined_at or moment, None, None
    if me.id != call.started_by_id and call.answered_at is None:
        call.answered_at = moment
    s.flush()
    with _lock:
        IN_CALL[connection] = call.id
    outbox.emit(s, people - {me.id}, "call.joined", {"call_id": call.id, "user": person(me)})
    outbox.emit(s, {me.id}, "call.taken", {"call_id": call.id})
    state(s, conversation, call)
    return call


def decline(s, me, call_id, moment):
    call, conversation, _membership = load_live_call(s, me, call_id)
    row = participant(s, call, me.id)
    if row.joined_at is None:
        row.declined_at = moment
    s.flush()
    outbox.emit(s, {me.id}, "call.taken", {"call_id": call.id})
    if conversation.is_dm and call.answered_at is None:
        outbox.emit(s, {call.started_by_id}, "call.declined", {"call_id": call.id, "user": person(me)})
        end(s, call, moment)
    return call


def leave(s, me, call_id, moment, connection=None):
    call = s.get(Call, call_id)
    if call is None or call.ended_at is not None:
        return None
    row = s.scalar(select(CallParticipant).where(CallParticipant.call_id == call.id, CallParticipant.user_id == me.id))
    if row is not None and row.joined_at is not None and row.left_at is None:
        row.left_at = moment
    s.flush()
    if connection is not None:
        with _lock:
            IN_CALL.pop(connection, None)
    left_now = in_call_ids(s, call)
    outbox.emit(s, left_now, "call.left", {"call_id": call.id, "user_id": me.id})
    conversation = s.get(Conversation, call.conversation_id)
    # A DM call is over when either person hangs up; any call is over when nobody is left.
    if not left_now or (conversation.is_dm and call.answered_at is not None) or (
            me.id == call.started_by_id and call.answered_at is None):
        end(s, call, moment)
    else:
        state(s, conversation, call)
    return call


def end(s, call, moment):
    """The call is over: one line in the chat, missed-call lines, and every tab is told."""
    from . import activity
    if call.ended_at is not None:
        return
    for row in s.scalars(select(CallParticipant).where(CallParticipant.call_id == call.id,
                                                       CallParticipant.left_at.is_(None),
                                                       CallParticipant.joined_at.is_not(None))):
        row.left_at = moment
    call.ended_at = moment
    s.flush()
    with _lock:
        for connection in [c for c, current in IN_CALL.items() if current == call.id]:
            IN_CALL.pop(connection, None)
    conversation = s.get(Conversation, call.conversation_id)
    caller = s.get(User, call.started_by_id)
    joined = set(s.scalars(select(CallParticipant.user_id).where(
        CallParticipant.call_id == call.id, CallParticipant.joined_at.is_not(None))))
    kind = "video" if call.kind == "video" else "audio"
    group = "Group " if conversation.is_group else ""
    if call.answered_at is not None:
        words = f"{group}{kind} call · {length(call.answered_at, moment)}"
    else:
        words = f"Missed {group.lower()}{kind} call"
    chats.post_system(s, conversation, words[0].upper() + words[1:], moment)
    declined = set(s.scalars(select(CallParticipant.user_id).where(
        CallParticipant.call_id == call.id, CallParticipant.declined_at.is_not(None))))
    for user_id in chats.member_ids(s, conversation) - joined - declined:  # a "missed" call, not a declined one
        activity.add(s, user_id, "call_missed", caller, call.started_at, conversation_id=conversation.id,
                     emoji="video" if call.kind == "video" else "audio")
    outbox.emit(s, chats.member_ids(s, conversation) | joined, "call.ended", {"call_id": call.id,
                                                                            "conversation_id": conversation.id})
    state(s, conversation, None)


def length(start_at: datetime, end_at: datetime):
    seconds = max(0, int((end_at - start_at).total_seconds()))
    if seconds < 60:
        return f"{seconds} sec"
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"


def signal(s, me, call_id, to, data):
    """Pass one connection note to one person in the same call."""
    call, _conversation, _membership = load_live_call(s, me, call_id)
    people = in_call_ids(s, call)
    if me.id not in people or to not in people or to == me.id:
        raise NotFound("They aren't in this call.")
    if len(json.dumps(data)) > SIGNAL_MAX:
        raise Invalid("That note is too big.")
    outbox.emit(s, {to}, "call.signal", {"call_id": call.id, "from": me.id, "data": data})


def media(s, me, call_id, audio, video):
    call, _conversation, _membership = load_live_call(s, me, call_id)
    people = in_call_ids(s, call)
    if me.id in people:
        outbox.emit(s, people - {me.id}, "call.media", {"call_id": call.id, "user_id": me.id,
                                                       "audio": bool(audio), "video": bool(video)})


# ---------- The WebSocket events ----------

@on("call.start", limit=(6, 60), conversation_id=(int, True), kind=(str, True, 10))
def on_start(ctx, data):
    call, existing = start(ctx.s, ctx.user, ctx.connection, data["conversation_id"], data["kind"], ctx.now)
    if existing:
        return {"join": True, "call": call_data(ctx.s, call), "ice_servers": ice_servers(ctx.app)}
    return {"join": False, "call": call_data(ctx.s, call), "ice_servers": ice_servers(ctx.app),
            "ring_seconds": RING_SECONDS}


@on("call.join", limit=(20, 60), call_id=(int, True))
def on_join(ctx, data):
    call = join(ctx.s, ctx.user, ctx.connection, data["call_id"], ctx.now)
    return {"call": call_data(ctx.s, call), "ice_servers": ice_servers(ctx.app)}


@on("call.decline", limit=(20, 60), call_id=(int, True))
def on_decline(ctx, data):
    decline(ctx.s, ctx.user, data["call_id"], ctx.now)
    return {}


@on("call.leave", limit=(20, 60), call_id=(int, True))
def on_leave(ctx, data):
    leave(ctx.s, ctx.user, data["call_id"], ctx.now, ctx.connection)
    return {}


@on("call.signal", limit=(400, 10), call_id=(int, True), to=(int, True), data=(dict, True))
def on_signal(ctx, data):
    signal(ctx.s, ctx.user, data["call_id"], data["to"], data["data"])
    return {}


@on("call.media", limit=(30, 10), call_id=(int, True), audio=(bool, True), video=(bool, True))
def on_media(ctx, data):
    media(ctx.s, ctx.user, data["call_id"], data["audio"], data["video"])
    return {}


# ---------- A tab that closes leaves its call ----------

def init_app(app):
    from .hub import HUB

    def tab_closed(hub, connection):
        with _lock:
            call_id = IN_CALL.pop(connection, None)
        if call_id is None:
            return
        with app.app_context():
            s = db.new_session(app)
            try:
                user = s.get(User, connection.user_id)
                if user is not None:
                    from .dates import utcnow
                    leave(s, user, call_id, utcnow())
                    s.commit()
            except Exception:
                s.rollback()
            finally:
                s.close()

    hub = app.extensions[HUB]
    if tab_closed not in hub.on_disconnect:
        hub.on_disconnect.append(tab_closed)
    app.jinja_env.globals.update(CALL_KINDS=KINDS)
