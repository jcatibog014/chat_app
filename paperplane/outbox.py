"""Events wait in the session until the transaction COMMITS, then go out live.

The domain code (chats.py, groups.py, ...) says what happened with emit():

    outbox.emit(s, member_ids, "message.new", {...})

The event is only held on the session (s.info["outbox"]). When s.commit()
succeeds, SQLAlchemy's after_commit hook hands every held event to the hub
(hub.py), which sends it to those people's open sockets. If the transaction
rolls back, the events are thrown away: nobody ever sees a message that was
never saved. The same path works for a page's form, a WebSocket event and the
demo crew, because all three commit a session.
"""
from sqlalchemy import event
from sqlalchemy.orm import Session

KEY = "outbox"
HUB = "paperplane_hub"


def emit(s, user_ids, kind, data):
    """Send {"type": kind, "data": data} to these people's open tabs, once s commits."""
    user_ids = {user_id for user_id in user_ids if user_id is not None}
    if user_ids:
        s.info.setdefault(KEY, []).append((frozenset(user_ids), {"type": kind, "data": data}))


def emit_each(s, user_ids, kind, make_data):
    """Like emit, with data made for each person (a chat's name differs per person in a DM)."""
    for user_id in user_ids:
        if user_id is not None:
            emit(s, {user_id}, kind, make_data(user_id))


def pending(s):
    """The events held on this session (tests look at them)."""
    return list(s.info.get(KEY, []))


@event.listens_for(Session, "after_commit")
def _send(s):
    events = s.info.pop(KEY, None)
    app = s.info.get("app")
    if not events or app is None:
        return
    hub = app.extensions.get(HUB)
    if hub is None:
        return
    for user_ids, payload in events:
        hub.publish(user_ids, payload)


@event.listens_for(Session, "after_rollback")
def _forget(s):
    s.info.pop(KEY, None)
