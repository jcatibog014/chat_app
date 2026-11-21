"""Chats: starting them, sending messages, reading, and the list of chats.

Every function here takes the session, the person doing it and the moment,
checks the rules (access.py), changes the database and emit()s what the
others must see live (outbox.py). The caller commits. Pages (views/chats.py)
and WebSocket events (live_events.py) both call these, so they behave the same.
"""
import re
import uuid
from dataclasses import dataclass

from flask import current_app, has_request_context, render_template
from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError

from . import access, outbox
from .dates import iso
from .models import Conversation, Membership, Message, User
from .problems import Invalid, NotFound

BODY_MAX_LENGTH = 4000
TITLE_MAX_LENGTH = 80
GROUP_MAX_PEOPLE = 50
PAGE_SIZE = 50
# A browser that comes back after more than this many changes reloads the chat instead.
CATCH_UP_LIMIT = 200
CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{8,40}")


# ---------- Small helpers ----------

def clean_body(text):
    """Trim the ends, keep the lines people typed, and refuse an empty or huge message."""
    body = (text or "").replace("\r\n", "\n").strip()
    if not body:
        raise Invalid("Type a message first.")
    if len(body) > BODY_MAX_LENGTH:
        raise Invalid(f"A message can be at most {BODY_MAX_LENGTH} characters.")
    return body


def clean_title(text):
    title = " ".join((text or "").split())
    if not title:
        raise Invalid("Give the group a name.")
    if len(title) > TITLE_MAX_LENGTH:
        raise Invalid(f"Use at most {TITLE_MAX_LENGTH} characters.")
    return title


def clean_client_id(text):
    """The browser's own id for a message (a UUID). Forms without JavaScript get one from the page."""
    text = (text or "").strip()
    return text if CLIENT_ID.fullmatch(text) else None


def new_client_id():
    return uuid.uuid4().hex


def dm_key(a_id, b_id):
    low, high = sorted((a_id, b_id))
    return f"{low}:{high}"


def members(s, conversation, active_only=True):
    query = select(Membership).where(Membership.conversation_id == conversation.id)
    if active_only:
        query = query.where(Membership.removed_at.is_(None))
    return s.scalars(query.order_by(Membership.id)).all()


def member_ids(s, conversation):
    return set(s.scalars(select(Membership.user_id).where(
        Membership.conversation_id == conversation.id, Membership.removed_at.is_(None))))


def other_person(s, conversation, viewer_id):
    """In a DM: the other person (even if their membership was removed)."""
    return s.scalar(select(User).join(Membership, Membership.user_id == User.id)
                    .where(Membership.conversation_id == conversation.id, User.id != viewer_id).limit(1))


def title_for(s, conversation, viewer_id):
    if conversation.is_group:
        return conversation.title
    other = other_person(s, conversation, viewer_id)
    return other.name if other else "Direct message"


def visible_from(conversation, membership):
    """The seq after which this member may read: all of it, unless the group hides earlier history."""
    return 0 if conversation.history_visible or conversation.is_dm else membership.joined_seq


def bump(s, conversation, moment, new_message=False):
    """Take the chat's next version (and seq, for a new message) inside this transaction.

    One UPDATE ... RETURNING: the database adds 1 and hands back the new numbers, so
    two messages sent at the same moment can never get the same seq. On PostgreSQL the
    row stays locked until the commit; SQLite lets one writer in at a time anyway."""
    values = {"version": Conversation.version + 1}
    if new_message:
        values.update(last_seq=Conversation.last_seq + 1, last_message_at=moment)
    seq, version = s.execute(
        update(Conversation).where(Conversation.id == conversation.id).values(**values)
        .returning(Conversation.last_seq, Conversation.version)
        .execution_options(synchronize_session=False)).one()
    # Keep the object in this session in step with the row.
    conversation.last_seq, conversation.version = seq, version
    if new_message:
        conversation.last_message_at = moment
    return seq, version


# ---------- How a message travels: data and HTML ----------

def render_fragment(template, **context):
    """Render a template outside a page (a WebSocket event, the demo crew's thread)."""
    if has_request_context():
        return render_template(template, **context)
    app = current_app._get_current_object()
    with app.test_request_context(base_url=app.config["BASE_URL"]):
        return render_template(template, **context)


def message_data(m):
    """A message as every member's browser gets it: the facts, and its HTML drawn once on
    the server (the same template as the page, so live and reloaded messages look the same;
    the browser marks your own ones). The HTML is escaped by Jinja: nothing anyone types
    becomes markup."""
    from . import tools
    from sqlalchemy.orm import object_session
    session = object_session(m)
    if session is not None and not hasattr(m, "reaction_list"):
        tools.prepare(session, [m])
    sender = m.sender
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "seq": m.seq,
        "version": m.version,
        "kind": m.kind,
        "sender_id": m.sender_id,
        "sender_name": sender.name if sender else None,
        "sender_first": sender.first_name if sender else None,
        # The sender's tab matches this to the bubble it drew while sending. (A random id: harmless to share.)
        "client_id": m.client_id,
        "preview": preview_of(m),
        "body": None if m.deleted_at else m.body,
        "created_at": iso(m.created_at),
        "edited": m.edited_at is not None,
        "deleted": m.deleted_at is not None,
        "reply_to_id": m.reply_to_id,
        "reactions": getattr(m, "reaction_list", []),
        "html": render_fragment("_message_live.html", m=m),
    }


FOLLOW_GAP_SECONDS = 300


def thread(messages, zone_name):
    """Messages ready to draw: (message, follows, new_day). `follows`: the same person
    wrote the one before, within 5 minutes, on the same day, so their name and avatar
    aren't drawn again. `new_day`: a day line goes before it."""
    from .dates import to_zone
    previous = None
    for m in messages:
        day = to_zone(m.created_at, zone_name).date()
        new_day = previous is None or to_zone(previous.created_at, zone_name).date() != day
        follows = (previous is not None and not new_day and m.kind == "text" and previous.kind == "text"
                   and previous.sender_id == m.sender_id
                   and (m.created_at - previous.created_at).total_seconds() <= FOLLOW_GAP_SECONDS)
        yield m, follows, new_day
        previous = m


def preview_of(m):
    """The short line under a chat's name in the list."""
    if m is None:
        return ""
    if m.deleted_at:
        return "Message deleted"
    text = " ".join(m.body.split())
    return text[:90] + ("..." if len(text) > 90 else "")


# ---------- Starting chats ----------

def start_dm(s, me, other, moment):
    """The DM between me and other: the one that exists, or a new one. Returns (chat, made_now)."""
    if other is None or other.id == me.id or not access.same_world(me, other):
        raise NotFound("We couldn't find anyone with that username.")
    key = dm_key(me.id, other.id)
    existing = s.scalar(select(Conversation).where(Conversation.dm_key == key))
    if existing is not None:
        mine = s.scalar(select(Membership).where(Membership.conversation_id == existing.id,
                                                 Membership.user_id == me.id))
        if mine is not None and mine.removed_at is not None:
            mine.removed_at = None
        return existing, False
    conversation = Conversation(kind="dm", dm_key=key, created_by_id=me.id, created_at=moment,
                                demo_team_id=me.demo_team_id)
    try:
        with s.begin_nested():  # both people starting it at once: the UNIQUE dm_key keeps one
            s.add(conversation)
            s.flush()
    except IntegrityError:
        return s.scalar(select(Conversation).where(Conversation.dm_key == key)), False
    for person in (me, other):
        s.add(Membership(conversation_id=conversation.id, user_id=person.id, role=access.MEMBER, joined_at=moment))
    s.flush()
    announce_new_chat(s, conversation, {me.id, other.id})
    return conversation, True


def create_group(s, me, title, others, moment):
    """A new group with me as its owner and these people as members."""
    title = clean_title(title)
    people = {person.id: person for person in others if person.id != me.id}
    for person in people.values():
        if not access.same_world(me, person):
            raise NotFound("We couldn't find one of those people.")
    if len(people) + 1 > GROUP_MAX_PEOPLE:
        raise Invalid(f"A group can have at most {GROUP_MAX_PEOPLE} people.")
    conversation = Conversation(kind="group", title=title, created_by_id=me.id, created_at=moment,
                                demo_team_id=me.demo_team_id)
    s.add(conversation)
    s.flush()
    s.add(Membership(conversation_id=conversation.id, user_id=me.id, role=access.OWNER, joined_at=moment))
    for person in people.values():
        s.add(Membership(conversation_id=conversation.id, user_id=person.id, role=access.MEMBER, joined_at=moment))
    s.flush()
    announce_new_chat(s, conversation, {me.id, *people})
    return conversation


def announce_new_chat(s, conversation, user_ids):
    """Each person's open tabs get the new chat in their list (its name differs per person in a DM)."""
    def row_for(user_id):
        viewer = s.get(User, user_id)
        return {"conversation_id": conversation.id,
                "html": render_fragment("_chat_row_live.html", row=chat_row(s, viewer, conversation))}
    outbox.emit_each(s, user_ids, "conversation.new", row_for)


# ---------- Sending ----------

def send_message(s, me, conversation_id, body, client_id, moment, reply_to_id=None):
    """Save a message and send it to every member. Returns (message, was_already_sent).

    The same client_id twice (a reconnect re-sending, a double click) gives back the
    first message and sends nothing again: that is what makes re-sending safe."""
    body = clean_body(body)
    client_id = clean_client_id(client_id) or new_client_id()
    earlier = s.scalar(select(Message).where(Message.sender_id == me.id, Message.client_id == client_id))
    if earlier is not None:
        return earlier, True
    conversation, membership = access.load(s, me, conversation_id, lock=True)
    require_speaking("send", s, conversation, membership, me, moment)
    from . import tools
    target = tools.reply_target(s, me, conversation, membership, reply_to_id)
    seq, version = bump(s, conversation, moment, new_message=True)
    message = Message(conversation_id=conversation.id, seq=seq, sender_id=me.id, client_id=client_id,
                      kind="text", body=body, created_at=moment, version=version,
                      reply_to_id=target.id if target else None)
    try:
        with s.begin_nested():
            s.add(message)
            s.flush()
    except IntegrityError:
        # The same message raced in from another tab a moment ago: keep that one. The seq we
        # took is skipped, which is fine: unread counts only compare positions.
        earlier = s.scalar(select(Message).where(Message.sender_id == me.id, Message.client_id == client_id))
        if earlier is None:
            raise
        return earlier, True
    message.sender = me
    # You have read everything up to your own message.
    membership.last_read_seq = seq
    after_send(s, conversation, message, moment)
    outbox.emit(s, member_ids(s, conversation), "message.new", message_data(message))
    return message, False


# Later stages add work after a message is saved (mentions, link previews, notifications):
# function(s, conversation, message, moment).
after_send_hooks = []
# ...and reasons someone may not speak right now (muted in a group, a block in a DM):
# function(s, conversation, membership, me, moment) -> dict of access.can() flags.
speaking_checks = []


def require_speaking(action, s, conversation, membership, me, moment):
    """access.require for "send" and "react", with the flags later stages add."""
    flags = {}
    for check in speaking_checks:
        for flag, value in check(s, conversation, membership, me, moment).items():
            flags[flag] = flags.get(flag, False) or value
    access.require(action, conversation, membership, **flags)


def after_send(s, conversation, message, moment):
    for hook in after_send_hooks:
        hook(s, conversation, message, moment)


def post_system(s, conversation, text, moment):
    """A line from the app itself in a chat ("Ana added Ben"). It takes a seq like a message."""
    seq, version = bump(s, conversation, moment, new_message=True)
    message = Message(conversation_id=conversation.id, seq=seq, sender_id=None, kind="system", body=text,
                      created_at=moment, version=version)
    s.add(message)
    s.flush()
    outbox.emit(s, member_ids(s, conversation), "message.new", message_data(message))
    return message


# ---------- Reading ----------

def mark_read(s, me, conversation_id, seq=None):
    """Remember that I've read up to seq (or everything). It never goes backwards.
    My other tabs clear the badge too, and members see it for "Seen by" (stage 6)."""
    conversation, membership = access.load(s, me, conversation_id)
    target = conversation.last_seq if seq is None else max(0, min(int(seq), conversation.last_seq))
    if target <= membership.last_read_seq:
        return membership, False
    membership.last_read_seq = target
    outbox.emit(s, member_ids(s, conversation), "read",
                {"conversation_id": conversation.id, "user_id": me.id, "seq": target})
    # My tabs on other pages can't work the menu's number out themselves: tell them.
    s.flush()
    outbox.emit(s, {me.id}, "unread.total", {"total": total_unread(s, me)})
    return membership, True


def unread_count(conversation, membership):
    return max(0, conversation.last_seq - max(membership.last_read_seq, visible_from(conversation, membership)))


@dataclass
class ChatRow:
    """One line of the chat list."""
    conversation: Conversation
    membership: Membership
    title: str
    other: User | None       # the other person, in a DM
    last: Message | None
    unread: int

    @property
    def when(self):
        return self.conversation.last_message_at or self.conversation.created_at


def chat_row(s, viewer, conversation, membership=None):
    if membership is None:
        membership = s.scalar(select(Membership).where(Membership.conversation_id == conversation.id,
                                                       Membership.user_id == viewer.id))
    last = None
    if conversation.last_seq:
        last = s.scalar(select(Message).where(Message.conversation_id == conversation.id,
                                              Message.seq == conversation.last_seq))
    other = other_person(s, conversation, viewer.id) if conversation.is_dm else None
    return ChatRow(conversation, membership, conversation.title if conversation.is_group else (other.name if other else "Direct message"),
                   other, last, unread_count(conversation, membership))


def chat_list(s, viewer):
    """My chats, newest activity first, with their last message and unread count (few queries)."""
    rows = s.execute(
        select(Conversation, Membership)
        .join(Membership, Membership.conversation_id == Conversation.id)
        .where(Membership.user_id == viewer.id, Membership.removed_at.is_(None), Conversation.deleted_at.is_(None))
        .order_by(func.coalesce(Conversation.last_message_at, Conversation.created_at).desc(), Conversation.id.desc())
    ).all()
    if not rows:
        return []
    ids = [conversation.id for conversation, _ in rows]
    last_messages = {m.conversation_id: m for m in s.scalars(
        select(Message).join(Conversation, and_(Conversation.id == Message.conversation_id,
                                                Message.seq == Conversation.last_seq))
        .where(Message.conversation_id.in_(ids)))}
    dm_ids = [conversation.id for conversation, _ in rows if conversation.is_dm]
    others = {}
    if dm_ids:
        for conversation_id, user in s.execute(
                select(Membership.conversation_id, User).join(User, User.id == Membership.user_id)
                .where(Membership.conversation_id.in_(dm_ids), Membership.user_id != viewer.id)):
            others[conversation_id] = user
    result = []
    for conversation, membership in rows:
        other = others.get(conversation.id)
        title = conversation.title if conversation.is_group else (other.name if other else "Direct message")
        result.append(ChatRow(conversation, membership, title, other, last_messages.get(conversation.id),
                              unread_count(conversation, membership)))
    return result


def total_unread(s, viewer, rows=None):
    """The number on the Chats menu link (stage 9 leaves muted chats out)."""
    return sum(row.unread for row in (chat_list(s, viewer) if rows is None else rows) if counts_in_total(row))


def counts_in_total(row):
    return True


def messages_page(s, conversation, membership, before_seq=None, limit=PAGE_SIZE):
    """The latest `limit` messages (or those before before_seq), oldest first, and whether
    there are earlier ones. Members who joined a group that hides its history see only
    what came after they joined."""
    floor = visible_from(conversation, membership)
    query = select(Message).where(Message.conversation_id == conversation.id, Message.seq > floor)
    if before_seq is not None:
        query = query.where(Message.seq < before_seq)
    page = s.scalars(query.order_by(Message.seq.desc()).limit(limit + 1)).all()
    more = len(page) > limit
    return list(reversed(page[:limit])), more


def changes_since(s, conversation, membership, version):
    """What changed in a chat after `version` (for a browser that reconnects).
    Returns (messages, reload): reload when there's too much to send piece by piece."""
    if version >= conversation.version:
        return [], False
    floor = visible_from(conversation, membership)
    changed = s.scalars(select(Message).where(
        Message.conversation_id == conversation.id, Message.version > version, Message.seq > floor)
        .order_by(Message.seq).limit(CATCH_UP_LIMIT + 1)).all()
    if len(changed) > CATCH_UP_LIMIT:
        return [], True
    return changed, False


def find_person(s, me, username):
    """Someone to message, by username, in my own world (real people, or my demo team)."""
    from .auth import clean_username
    handle = clean_username(username)
    if not handle:
        return None
    username = f"d{me.demo_team_id}.{handle}" if me.is_demo else handle
    person = s.scalar(select(User).where(User.username == username))
    if person is None or person.id == me.id or not access.same_world(me, person):
        return None
    return person


def people_i_know(s, me):
    """Everyone I share an active chat with (and, in a demo, my whole team): the quick picks
    on the New Chat page."""
    shared = (select(Membership.user_id)
              .join(Conversation, Conversation.id == Membership.conversation_id)
              .where(Membership.removed_at.is_(None), Conversation.deleted_at.is_(None),
                     Membership.conversation_id.in_(select(Membership.conversation_id).where(
                         Membership.user_id == me.id, Membership.removed_at.is_(None)))))
    query = select(User).where(User.id != me.id)
    if me.is_demo:
        query = query.where(User.demo_team_id == me.demo_team_id)
    else:
        query = query.where(User.id.in_(shared))
    return s.scalars(query.order_by(User.name)).all()
