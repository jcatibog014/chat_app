"""Blocking someone. A block works both ways for talking, so a blocked person can't
simply wait it out or go around it:

- in your DM, NEITHER of you can send or react (access.can(..., blocked=True));
  you can both still read what was said before;
- neither of you can start a new DM with the other;
- neither of you can add the other to a group (you may still meet in a group
  someone else runs: blocking doesn't throw people out of shared groups);
- neither sees the other online (presence.audience_filters).

The person you block isn't told. Unblocking puts everything back.
"""
from sqlalchemy import delete, or_, select

from . import chats, outbox, presence
from .models import Block, Conversation, Membership, User
from .problems import Forbidden, NotFound


def between(s, a_id, b_id):
    """Is there a block either way between these two people?"""
    return s.scalar(select(Block.id).where(or_(
        (Block.blocker_id == a_id) & (Block.blocked_id == b_id),
        (Block.blocker_id == b_id) & (Block.blocked_id == a_id))).limit(1)) is not None


def i_blocked(s, me_id, other_id):
    return s.scalar(select(Block.id).where(Block.blocker_id == me_id, Block.blocked_id == other_id)) is not None


def blocked_people(s, me):
    return s.scalars(select(User).join(Block, Block.blocked_id == User.id)
                     .where(Block.blocker_id == me.id).order_by(User.name)).all()


def block(s, me, other, moment):
    if other is None or other.id == me.id or other.demo_team_id != me.demo_team_id:
        raise NotFound("We couldn't find that person.")
    if not i_blocked(s, me.id, other.id):
        s.add(Block(blocker_id=me.id, blocked_id=other.id, created_at=moment))
        s.flush()
    from . import activity
    activity.forget_between(s, me.id, other.id)  # no Notifications lines between us
    # Our DM (if any) closes for both of us at once: tell both people's open tabs to redraw it.
    dm = s.scalar(select(Conversation).where(Conversation.dm_key == chats.dm_key(me.id, other.id)))
    if dm is not None:
        outbox.emit(s, {me.id, other.id}, "membership.updated", {"conversation_id": dm.id})
    # And we disappear from each other's presence: both look offline to the other.
    outbox.emit(s, {other.id}, "presence", {"user_id": me.id, "status": "offline", "last_seen": None})
    outbox.emit(s, {me.id}, "presence", {"user_id": other.id, "status": "offline", "last_seen": None})


def unblock(s, me, other, moment):
    if other is None:
        raise NotFound("We couldn't find that person.")
    s.execute(delete(Block).where(Block.blocker_id == me.id, Block.blocked_id == other.id))
    dm = s.scalar(select(Conversation).where(Conversation.dm_key == chats.dm_key(me.id, other.id)))
    if dm is not None:
        outbox.emit(s, {me.id, other.id}, "membership.updated", {"conversation_id": dm.id})


# ---------- The hooks into the rest of the app ----------

def dm_flags(s, conversation, membership, me, moment):
    """chats.speaking_checks: a DM with a block between its two people can't be spoken in."""
    if not conversation.is_dm:
        return {}
    other = s.scalar(select(Membership.user_id).where(Membership.conversation_id == conversation.id,
                                                      Membership.user_id != me.id))
    return {"blocked": other is not None and between(s, me.id, other)}


def check_can_start(s, me, other):
    if between(s, me.id, other.id):
        raise Forbidden("You can't start a chat with this person.")


def hidden_ids(s, user_id):
    """Everyone with a block either way with this person (they don't see each other online)."""
    rows = s.execute(select(Block.blocker_id, Block.blocked_id).where(
        or_(Block.blocker_id == user_id, Block.blocked_id == user_id)))
    return {blocked if blocker == user_id else blocker for blocker, blocked in rows}


def hide_from_presence(s, user_id, audience):
    """presence.audience_filters: nobody sees someone they blocked, or who blocked them, online."""
    if not audience:
        return audience
    rows = s.execute(select(Block.blocker_id, Block.blocked_id).where(or_(
        (Block.blocker_id == user_id) & Block.blocked_id.in_(audience),
        (Block.blocked_id == user_id) & Block.blocker_id.in_(audience))))
    hidden = {blocked if blocker == user_id else blocker for blocker, blocked in rows}
    return audience - hidden


def init_app(app):
    if dm_flags not in chats.speaking_checks:
        chats.speaking_checks.append(dm_flags)
    if hide_from_presence not in presence.audience_filters:
        presence.audience_filters.append(hide_from_presence)
