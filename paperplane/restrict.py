"""Restricting someone: a quieter step than blocking. They can still message you, and you can
still read it, but:
- you get no notifications from them (no push, no in-page note, nothing on your Notifications
  page: notify.after_message and activity.add ask `has`);
- your chat with them stays out of the unread number in the menu (like a muted chat: the row
  keeps a grey badge and says Restricted);
- neither of you sees the other online (presence.audience_filters, and pages' dots);
- they aren't told.
Undo it from the chat's menu, or Settings > Preferences > Restricted People.
"""
from sqlalchemy import delete, or_, select

from . import access, presence
from .models import Restriction, User
from .problems import NotFound


def has(s, restrictor_id, restricted_id):
    """Did restrictor_id restrict restricted_id?"""
    return s.scalar(select(Restriction.id).where(Restriction.restrictor_id == restrictor_id,
                                                 Restriction.restricted_id == restricted_id)) is not None


def restricted_ids(s, me_id):
    """The people I restricted."""
    return set(s.scalars(select(Restriction.restricted_id).where(Restriction.restrictor_id == me_id)))


def pair_ids(s, user_id):
    """Everyone with a restriction either way with this person (they don't see each other online)."""
    rows = s.execute(select(Restriction.restrictor_id, Restriction.restricted_id).where(
        or_(Restriction.restrictor_id == user_id, Restriction.restricted_id == user_id)))
    return {b if a == user_id else a for a, b in rows}


def restrict(s, me, other):
    if other is None or other.id == me.id or not access.same_world(me, other):
        raise NotFound("We couldn't find that person.")
    if not has(s, me.id, other.id):
        s.add(Restriction(restrictor_id=me.id, restricted_id=other.id))
        s.flush()
    _hide_from_each_other(s, me, other)


def unrestrict(s, me, other):
    if other is None:
        raise NotFound("We couldn't find that person.")
    s.execute(delete(Restriction).where(Restriction.restrictor_id == me.id, Restriction.restricted_id == other.id))


def restricted_people(s, me):
    return s.scalars(select(User).join(Restriction, Restriction.restricted_id == User.id)
                     .where(Restriction.restrictor_id == me.id).order_by(User.name)).all()


def _hide_from_each_other(s, me, other):
    from . import outbox
    outbox.emit(s, {other.id}, "presence", {"user_id": me.id, "status": "offline", "last_seen": None})
    outbox.emit(s, {me.id}, "presence", {"user_id": other.id, "status": "offline", "last_seen": None})


def hide_from_presence(s, user_id, audience):
    """presence.audience_filters: a restriction either way hides the two from each other."""
    if not audience:
        return audience
    return audience - pair_ids(s, user_id)


def init_app(app):
    if hide_from_presence not in presence.audience_filters:
        presence.audience_filters.append(hide_from_presence)
