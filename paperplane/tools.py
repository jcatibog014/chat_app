"""Message tools: reply, edit, delete for everyone, emoji reactions, and "Seen by".

Each change follows the same pattern as sending: check the rules (access.py),
take the chat's next version (chats.bump) and stamp the message with it, then
emit "message.updated" with the message drawn again. Tabs replace the old
drawing; a tab that was away gets it from "sync" (the message's version is
newer than theirs). Nothing is ever really deleted in place: a deleted message
keeps its row and seq (so unread counts and replies stay right) but loses its
words and reactions, and shows "Message deleted".
"""
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, select

from . import access, chats, outbox
from .models import Membership, Message, Reaction, User
from .problems import Forbidden, Invalid, NotFound

EMOJI = ("👍", "❤️", "😂", "😮", "😢", "🎉")
EMOJI_NAMES = {"👍": "Thumbs up", "❤️": "Heart", "😂": "Laughing", "😮": "Surprised", "😢": "Sad", "🎉": "Party"}
EDIT_WINDOW = timedelta(hours=24)
SNIPPET_LENGTH = 80


def load_message(s, me, message_id):
    """(message, chat, my membership) if I can see the message, else NotFound."""
    try:
        message = s.get(Message, int(message_id))
    except (TypeError, ValueError):
        message = None
    if message is None:
        raise NotFound()
    conversation, membership = access.load(s, me, message.conversation_id)
    if message.seq <= chats.visible_from(conversation, membership):
        raise NotFound()  # from before I joined a group that hides its history
    return message, conversation, membership


def changed(s, conversation, message, moment):
    """Stamp a changed message with the chat's next version and tell every member."""
    _seq, version = chats.bump(s, conversation, moment)
    message.version = version
    s.flush()
    outbox.emit(s, chats.member_ids(s, conversation), "message.updated", chats.message_data(message))


# ---------- Replies ----------

def reply_target(s, me, conversation, membership, reply_to_id):
    """The message being answered: in the same chat, and visible to me. None if not given."""
    if not reply_to_id:
        return None
    target = s.get(Message, reply_to_id)
    if (target is None or target.conversation_id != conversation.id
            or target.seq <= chats.visible_from(conversation, membership)):
        raise NotFound("We couldn't find the message you're answering.")
    return target


# ---------- Edit ----------

def edit_message(s, me, message_id, body, moment):
    message, conversation, membership = load_message(s, me, message_id)
    if message.sender_id != me.id or message.kind != "text":
        raise Forbidden("You can only edit your own messages.")
    access.require("edit_own", conversation, membership)
    if message.deleted_at is not None:
        raise Invalid("A deleted message can't be edited.")
    if moment - message.created_at > EDIT_WINDOW:
        raise Forbidden("Messages can be edited for 24 hours after they're sent.")
    body = chats.clean_body(body)
    if body == message.body:
        return message
    message.body = body
    message.edited_at = moment
    changed(s, conversation, message, moment)
    return message


# ---------- Delete for everyone ----------

def can_delete(conversation, membership, message, me_id):
    if message.kind != "text" or message.deleted_at is not None:
        return False
    if message.sender_id == me_id:
        return access.can("delete_own", conversation, membership)
    return access.can("delete_any", conversation, membership)


def delete_message(s, me, message_id, moment):
    message, conversation, membership = load_message(s, me, message_id)
    if message.deleted_at is not None:
        return message  # deleting twice is fine (a double click, a re-sent request)
    if not can_delete(conversation, membership, message, me.id):
        action = "delete_own" if message.sender_id == me.id else "delete_any"
        access.require(action, conversation, membership)
        raise Forbidden("You can't delete that message.")
    # The words are really gone: not hidden, erased. The row stays so the numbering does.
    message.body = ""
    message.deleted_at = moment
    s.execute(delete(Reaction).where(Reaction.message_id == message.id))
    changed(s, conversation, message, moment)
    return message


# ---------- Reactions ----------

def react(s, me, message_id, emoji, moment):
    """Add my emoji to a message, or take it back if it's already there. Returns True if added."""
    if emoji not in EMOJI:
        raise Invalid("Choose one of the reactions.")
    message, conversation, membership = load_message(s, me, message_id)
    if message.kind != "text" or message.deleted_at is not None:
        raise Invalid("You can't react to that message.")
    chats.require_speaking("react", s, conversation, membership, me, moment)
    existing = s.scalar(select(Reaction).where(Reaction.message_id == message.id, Reaction.user_id == me.id,
                                               Reaction.emoji == emoji))
    if existing is not None:
        s.delete(existing)
        added = False
    else:
        s.add(Reaction(message_id=message.id, user_id=me.id, emoji=emoji, created_at=moment))
        added = True
    s.flush()
    changed(s, conversation, message, moment)
    return added


# ---------- Getting messages ready to draw ----------

def prepare(s, messages):
    """Put what the template needs on each message: its reactions (grouped, with who), and
    the message it answers (who wrote it, a short piece of it). Two queries for a whole page."""
    ids = [m.id for m in messages]
    groups = defaultdict(lambda: defaultdict(list))
    if ids:
        rows = s.execute(select(Reaction.message_id, Reaction.emoji, User.id, User.name)
                         .join(User, User.id == Reaction.user_id)
                         .where(Reaction.message_id.in_(ids)).order_by(Reaction.id))
        for message_id, emoji, user_id, name in rows:
            groups[message_id][emoji].append((user_id, first_word(name)))
    reply_ids = {m.reply_to_id for m in messages if m.reply_to_id}
    replies = {}
    if reply_ids:
        for target in s.scalars(select(Message).where(Message.id.in_(reply_ids))):
            replies[target.id] = target
    for m in messages:
        m.reaction_list = [
            {"emoji": emoji, "count": len(people), "users": [user_id for user_id, _ in people],
             "names": ", ".join(name for _, name in people), "label": EMOJI_NAMES[emoji]}
            for emoji, people in sorted(groups[m.id].items(), key=lambda item: EMOJI.index(item[0]))
        ]
        target = replies.get(m.reply_to_id)
        m.reply_info = None
        if target is not None:
            text = " ".join(target.body.split())
            m.reply_info = {
                "id": target.id,
                "name": target.sender.first_name if target.sender else "Paper Plane",
                "snippet": "Message deleted" if target.deleted_at else
                           text[:SNIPPET_LENGTH] + ("..." if len(text) > SNIPPET_LENGTH else ""),
            }
    return messages


# ---------- Seen by ----------

def readers(s, conversation):
    """{user id: (first name, last_read_seq)} for everyone in the chat (the browser keeps "Seen by" live)."""
    rows = s.execute(select(Membership.user_id, User.name, Membership.last_read_seq)
                     .join(User, User.id == Membership.user_id)
                     .where(Membership.conversation_id == conversation.id, Membership.removed_at.is_(None)))
    return {user_id: (first_word(name), seq) for user_id, name, seq in rows}


def first_word(name):
    return name.split()[0] if name and name.split() else name


def seen_words(conversation, readers_by_id, message, me_id):
    """"Seen" (a DM), "Seen by Ana and Ben", "Seen by everyone", or "" under my last message."""
    if message is None or message.sender_id != me_id or message.kind != "text":
        return ""
    others = {user_id: data for user_id, data in readers_by_id.items() if user_id != me_id}
    seen = [name for _, (name, seq) in sorted(others.items()) if seq >= message.seq]
    if not seen:
        return ""
    if conversation.is_dm:
        return "Seen"
    if len(seen) == len(others) and len(others) > 1:
        return "Seen by everyone"
    if len(seen) == 1:
        return f"Seen by {seen[0]}"
    return "Seen by " + ", ".join(seen[:-1]) + f" and {seen[-1]}"
