"""Running a group: renaming, adding and removing people, roles, muting, leaving,
deleting, the history setting, and invite links.

Every action asks access.py first, with the TARGET where it matters (an admin
can remove a member, not another admin; only the owner changes roles), leaves
a system line in the chat ("Ana added Ben"), and tells the right tabs live:

    conversation.updated    the chat's name changed (everyone in it)
    conversation.new        you were added (you)
    conversation.removed    you were removed, you left, or it was deleted (you)
    membership.updated      your role or mute changed (you): your page redraws

A removed person stops receiving the chat's events at once: events go to the
chat's CURRENT members only (chats.member_ids), and every event they send is
checked against their membership again (access.load).
"""
import secrets
from datetime import timedelta

from sqlalchemy import func, select

from . import access, chats, outbox
from .models import InviteLink, Membership, User
from .problems import Conflict, Forbidden, Invalid, NotFound

MUTE_CHOICES = {"60": ("1 hour", timedelta(hours=1)), "480": ("8 hours", timedelta(hours=8)),
                "1440": ("24 hours", timedelta(hours=24))}
INVITE_EXPIRY = {"1": timedelta(days=1), "7": timedelta(days=7), "never": None}
INVITE_USES = {"1": 1, "10": 10, "unlimited": None}
MAX_ACTIVE_INVITES = 10


def is_muted(membership, moment):
    return membership is not None and membership.muted_until is not None and membership.muted_until > moment


def mute_flags(s, conversation, membership, me, moment):
    """chats.speaking_checks: a muted member can't send or react until their time is up."""
    return {"muted": is_muted(membership, moment)}


def load_group(s, me, chat_id):
    conversation, membership = access.load(s, me, chat_id)
    if not conversation.is_group:
        raise NotFound()
    return conversation, membership


def target_membership(s, conversation, user_id):
    target = s.scalar(select(Membership).where(Membership.conversation_id == conversation.id,
                                               Membership.user_id == user_id, Membership.removed_at.is_(None)))
    if target is None:
        raise NotFound("That person isn't in this group.")
    return target


def names(people):
    names_ = [person.first_name for person in people]
    if len(names_) <= 2:
        return " and ".join(names_)
    return ", ".join(names_[:-1]) + f" and {names_[-1]}"


def tell_member(s, user_id, conversation):
    outbox.emit(s, {user_id}, "membership.updated", {"conversation_id": conversation.id})


# ---------- Name and settings ----------

def rename(s, me, chat_id, title, moment):
    conversation, membership = load_group(s, me, chat_id)
    access.require("rename", conversation, membership)
    title = chats.clean_title(title)
    if title == conversation.title:
        return conversation
    conversation.title = title
    chats.post_system(s, conversation, f"{me.first_name} renamed the group to {title}", moment)
    outbox.emit(s, chats.member_ids(s, conversation), "conversation.updated",
                {"conversation_id": conversation.id, "title": title})
    return conversation


def set_history(s, me, chat_id, visible, moment):
    conversation, membership = load_group(s, me, chat_id)
    access.require("change_settings", conversation, membership)
    if conversation.history_visible == visible:
        return conversation
    conversation.history_visible = visible
    chats.post_system(s, conversation, f"{me.first_name} turned {'on' if visible else 'off'} "
                                       "\"New members see earlier messages\"", moment)
    return conversation


# ---------- People ----------

def add_people(s, me, chat_id, people, moment):
    """Add people (or bring back people who left). Returns those actually added."""
    from . import blocks
    conversation, membership = load_group(s, me, chat_id)
    access.require("add_people", conversation, membership)
    active = chats.member_ids(s, conversation)
    added = []
    for person in people:
        if person.id in active:
            continue
        if not access.same_world(me, person):
            raise NotFound("We couldn't find one of those people.")
        if blocks.between(s, me.id, person.id):
            raise Forbidden(f"You can't add {person.name}.")
        added.append(person)
    if len(active) + len(added) > chats.GROUP_MAX_PEOPLE:
        raise Invalid(f"A group can have at most {chats.GROUP_MAX_PEOPLE} people.")
    for person in added:
        join_as_member(s, conversation, person, moment)
    if added:
        chats.post_system(s, conversation, f"{me.first_name} added {names(added)}", moment)
        chats.announce_new_chat(s, conversation, {person.id for person in added})
    return added


def join_as_member(s, conversation, person, moment):
    """A new or returning member. They start "read up to now", so the chat isn't a wall of
    unread messages, and joined_seq marks where the history rule starts for them."""
    existing = s.scalar(select(Membership).where(Membership.conversation_id == conversation.id,
                                                 Membership.user_id == person.id))
    if existing is None:
        existing = Membership(conversation_id=conversation.id, user_id=person.id)
        s.add(existing)
    existing.role = access.MEMBER
    existing.removed_at = None
    existing.muted_until = None
    existing.joined_at = moment
    existing.joined_seq = conversation.last_seq
    existing.last_read_seq = conversation.last_seq
    s.flush()
    return existing


def remove_person(s, me, chat_id, user_id, moment):
    conversation, membership = load_group(s, me, chat_id)
    target = target_membership(s, conversation, user_id)
    access.require("remove_people", conversation, membership, target=target)
    target.removed_at = moment
    s.flush()
    removed = s.get(User, user_id)
    chats.post_system(s, conversation, f"{me.first_name} removed {removed.first_name}", moment)
    # Their tabs drop the chat at once; from now on no event of this chat reaches them.
    outbox.emit(s, {user_id}, "conversation.removed", {"conversation_id": conversation.id, "why": "removed"})


def set_role(s, me, chat_id, user_id, role, moment):
    if role not in (access.ADMIN, access.MEMBER):
        raise Invalid("Choose Admin or Member.")
    conversation, membership = load_group(s, me, chat_id)
    target = target_membership(s, conversation, user_id)
    access.require("change_roles", conversation, membership, target=target)
    if target.role == role:
        return
    target.role = role
    person = s.get(User, user_id)
    words = f"made {person.first_name} an admin" if role == access.ADMIN else f"made {person.first_name} a member"
    chats.post_system(s, conversation, f"{me.first_name} {words}", moment)
    tell_member(s, user_id, conversation)


def hand_over(s, me, chat_id, user_id, moment):
    """The owner makes someone else the owner (and becomes an admin)."""
    conversation, membership = load_group(s, me, chat_id)
    if membership.role != access.OWNER:
        raise Forbidden("Only the group's owner can hand it over.")
    target = target_membership(s, conversation, user_id)
    if target.user_id == me.id:
        raise Invalid("You already own this group.")
    membership.role, target.role = access.ADMIN, access.OWNER
    person = s.get(User, user_id)
    chats.post_system(s, conversation, f"{me.first_name} made {person.first_name} the owner", moment)
    tell_member(s, user_id, conversation)
    tell_member(s, me.id, conversation)


def mute(s, me, chat_id, user_id, choice, moment):
    """choice: "60" / "480" / "1440" minutes, or "off" to unmute."""
    conversation, membership = load_group(s, me, chat_id)
    target = target_membership(s, conversation, user_id)
    access.require("mute_member", conversation, membership, target=target)
    person = s.get(User, user_id)
    if choice == "off":
        if target.muted_until is None:
            return
        target.muted_until = None
        chats.post_system(s, conversation, f"{me.first_name} unmuted {person.first_name}", moment)
    else:
        if choice not in MUTE_CHOICES:
            raise Invalid("Choose how long to mute them.")
        words, length = MUTE_CHOICES[choice]
        target.muted_until = moment + length
        chats.post_system(s, conversation, f"{me.first_name} muted {person.first_name} for {words}", moment)
    tell_member(s, user_id, conversation)


def leave(s, me, chat_id, moment):
    """Leave a group. An owner hands it to the longest-serving admin (or member) first; the last
    person out deletes it."""
    conversation, membership = load_group(s, me, chat_id)
    access.require("leave", conversation, membership)
    others = s.scalars(select(Membership).where(Membership.conversation_id == conversation.id,
                                                Membership.removed_at.is_(None), Membership.user_id != me.id)
                       .order_by(Membership.joined_at, Membership.id)).all()
    if not others:
        delete_group(s, me, chat_id, moment, alone=True)
        return None
    heir = None
    if membership.role == access.OWNER:
        heir = next((m for m in others if m.role == access.ADMIN), others[0])
        heir.role = access.OWNER
    membership.removed_at = moment
    membership.role = access.MEMBER
    s.flush()
    chats.post_system(s, conversation, f"{me.first_name} left", moment)
    if heir is not None:
        heir_user = s.get(User, heir.user_id)
        chats.post_system(s, conversation, f"{heir_user.first_name} is the owner now", moment)
        tell_member(s, heir.user_id, conversation)
    outbox.emit(s, {me.id}, "conversation.removed", {"conversation_id": conversation.id, "why": "left"})
    return heir


def delete_group(s, me, chat_id, moment, alone=False):
    conversation, membership = load_group(s, me, chat_id)
    if not alone:
        access.require("delete_group", conversation, membership)
    everyone = chats.member_ids(s, conversation)
    conversation.deleted_at = moment
    chats.bump(s, conversation, moment)
    outbox.emit(s, everyone, "conversation.removed", {"conversation_id": conversation.id, "why": "deleted"})


# ---------- Invite links ----------

def active_invites(s, conversation, moment):
    links = s.scalars(select(InviteLink).where(InviteLink.conversation_id == conversation.id,
                                               InviteLink.revoked_at.is_(None))
                      .order_by(InviteLink.created_at.desc())).all()
    return [link for link in links if invite_problem(link, moment) is None]


def create_invite(s, me, chat_id, expiry, uses, moment):
    conversation, membership = load_group(s, me, chat_id)
    access.require("invite_links", conversation, membership)
    if expiry not in INVITE_EXPIRY or uses not in INVITE_USES:
        raise Invalid("Choose how long the link works and how many people may use it.")
    if len(active_invites(s, conversation, moment)) >= MAX_ACTIVE_INVITES:
        raise Conflict(f"A group can have at most {MAX_ACTIVE_INVITES} working invite links. Turn one off first.")
    length = INVITE_EXPIRY[expiry]
    link = InviteLink(conversation_id=conversation.id, code=secrets.token_urlsafe(16), created_by_id=me.id,
                      created_at=moment, expires_at=moment + length if length else None, max_uses=INVITE_USES[uses])
    s.add(link)
    s.flush()
    return link


def revoke_invite(s, me, chat_id, invite_id, moment):
    conversation, membership = load_group(s, me, chat_id)
    access.require("invite_links", conversation, membership)
    link = s.get(InviteLink, invite_id)
    if link is None or link.conversation_id != conversation.id:
        raise NotFound()
    link.revoked_at = link.revoked_at or moment


def invite_problem(link, moment):
    """Why a link can't be used now, or None."""
    if link is None or link.revoked_at is not None:
        return "This invite link has been turned off."
    if link.expires_at is not None and link.expires_at <= moment:
        return "This invite link has expired."
    if link.max_uses is not None and link.uses >= link.max_uses:
        return "This invite link has been used up."
    if link.conversation.deleted_at is not None:
        return "This group has been deleted."
    return None


def find_invite(s, code):
    return s.scalar(select(InviteLink).where(InviteLink.code == (code or "")[:40]))


def join(s, me, code, moment):
    """Join a group with its invite link. Returns the chat. Joining twice is fine."""
    link = find_invite(s, code)
    problem = invite_problem(link, moment) if link else "We couldn't find that invite link."
    if problem:
        raise NotFound(problem)
    conversation = link.conversation
    # A demo's link only works inside that demo, and a real group's only for real people.
    if conversation.demo_team_id != me.demo_team_id:
        raise NotFound("We couldn't find that invite link.")
    current = s.scalar(select(Membership).where(Membership.conversation_id == conversation.id,
                                                Membership.user_id == me.id, Membership.removed_at.is_(None)))
    if current is not None:
        return conversation
    if len(chats.member_ids(s, conversation)) >= chats.GROUP_MAX_PEOPLE:
        raise Conflict("This group is full.")
    # The UPDATE counts the use only while the link still has uses left: two people can't
    # both take the last place.
    used = s.execute(
        InviteLink.__table__.update().where(InviteLink.id == link.id)
        .where((InviteLink.max_uses.is_(None)) | (InviteLink.uses < InviteLink.max_uses))
        .values(uses=InviteLink.uses + 1)).rowcount
    if not used:
        raise NotFound("This invite link has been used up.")
    join_as_member(s, conversation, me, moment)
    chats.post_system(s, conversation, f"{me.first_name} joined with an invite link", moment)
    chats.announce_new_chat(s, conversation, {me.id})
    return conversation


def member_count(s, conversation):
    return s.scalar(select(func.count(Membership.id)).where(Membership.conversation_id == conversation.id,
                                                             Membership.removed_at.is_(None))) or 0


def init_app(app):
    if mute_flags not in chats.speaking_checks:
        chats.speaking_checks.append(mute_flags)

