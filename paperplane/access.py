"""Who may do what: every rule about chats in ONE place (the authorization lesson).

Pages and WebSocket events both ask here, so a rule can't be right on one
path and wrong on the other. Two questions, always in this order:

1. Can you SEE the chat at all?  load() answers with the chat and your
   membership, or raises NotFound. A chat you aren't in (or were removed
   from, or that was deleted) is "not found", never "forbidden": a stranger
   learns nothing, not even that chat 41 exists.
2. May you do THIS in it?  can() / require() look the action up in the
   permission table for your role. Some actions also depend on WHO you do
   them to (an admin can remove a member, not the owner): `target`.

tests/test_access.py walks the whole table for every role.
"""
from sqlalchemy import select

from .models import Conversation, Membership
from .problems import Forbidden, NotFound

OWNER, ADMIN, MEMBER = "owner", "admin", "member"
ROLES = (OWNER, ADMIN, MEMBER)
RANK = {OWNER: 3, ADMIN: 2, MEMBER: 1}
ROLE_NAMES = {OWNER: "Owner", ADMIN: "Admin", MEMBER: "Member"}

EVERYONE = frozenset(ROLES)
MANAGERS = frozenset({OWNER, ADMIN})

# What each role may do in a GROUP.
GROUP_RULES = {
    "read": EVERYONE,
    "send": EVERYONE,
    "react": EVERYONE,
    "edit_own": EVERYONE,
    "delete_own": EVERYONE,
    "leave": EVERYONE,
    "delete_any": MANAGERS,       # take down anyone's message
    "rename": MANAGERS,
    "add_people": MANAGERS,
    "remove_people": MANAGERS,    # only people of a lower role (see TARGETED)
    "mute_member": MANAGERS,      # only people of a lower role
    "invite_links": MANAGERS,
    "change_settings": MANAGERS,  # "New members see earlier messages"
    "change_roles": frozenset({OWNER}),
    "delete_group": frozenset({OWNER}),
}
ACTIONS = tuple(GROUP_RULES)

# In a DM both people are equals, and the chat's shape can't change: no renaming,
# no adding a third person, no leaving (you can block instead).
DM_RULES = frozenset({"read", "send", "react", "edit_own", "delete_own"})

# Actions done TO someone: the actor must rank above the target, and never act on themselves.
TARGETED = frozenset({"remove_people", "mute_member", "change_roles"})

# Actions that a muted member (stage 7) or a DM with a block between the two people loses.
SPEAKING = frozenset({"send", "react"})


def same_world(a, b):
    """Real people only meet real people; a demo person only meets their own team.
    So no real account can ever be messaged by, or added to a chat with, a demo."""
    return a.demo_team_id == b.demo_team_id


def load(s, user, conversation_id, lock=False):
    """(chat, your membership) if you can see the chat, else NotFound."""
    try:
        conversation_id = int(conversation_id)
    except (TypeError, ValueError):
        raise NotFound() from None
    query = (select(Conversation, Membership)
             .join(Membership, Membership.conversation_id == Conversation.id)
             .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None),
                    Membership.user_id == user.id, Membership.removed_at.is_(None)))
    if lock:
        query = query.with_for_update(of=Conversation)
    row = s.execute(query).first()
    if row is None:
        raise NotFound()
    return row[0], row[1]


def can(action, conversation, membership, target=None, muted=False, blocked=False):
    """May the holder of `membership` do `action` in `conversation` (to `target`, a Membership)?

    muted: the actor is muted in this group right now. blocked: in a DM, one of the two
    has blocked the other. Both only take away SPEAKING actions."""
    if membership is None or not membership.active or conversation.deleted_at is not None:
        return False
    if action not in GROUP_RULES:
        raise ValueError(f"Unknown action: {action}")
    if (muted or blocked) and action in SPEAKING:
        return False
    if conversation.is_dm:
        return action in DM_RULES
    if membership.role not in GROUP_RULES[action]:
        return False
    if action in TARGETED:
        if target is None or not target.active or target.user_id == membership.user_id:
            return False
        if action == "change_roles":
            return target.role != OWNER  # the owner changes the others; handing over is its own step
        return RANK[membership.role] > RANK[target.role]
    return True


def require(action, conversation, membership, target=None, muted=False, blocked=False, message=None):
    if not can(action, conversation, membership, target, muted, blocked):
        raise Forbidden(message or FORBIDDEN_WORDS.get(action, "You can't do that in this chat."))


FORBIDDEN_WORDS = {
    "send": "You can't send messages here right now.",
    "react": "You can't react here right now.",
    "delete_any": "Only the group's owner and admins can delete other people's messages.",
    "rename": "Only the group's owner and admins can rename it.",
    "add_people": "Only the group's owner and admins can add people.",
    "remove_people": "You can only remove people with a lower role than yours.",
    "mute_member": "You can only mute people with a lower role than yours.",
    "invite_links": "Only the group's owner and admins can make invite links.",
    "change_settings": "Only the group's owner and admins can change its settings.",
    "change_roles": "Only the group's owner can change roles.",
    "delete_group": "Only the group's owner can delete it.",
    "leave": "You can't leave a direct message. You can block the person instead.",
}
