"""Find and notify: search, @mentions, muting a chat, and notifications.

WHO GETS TOLD about a new message is decided here, on the server, once
(the rules in one place again): every member except the sender, unless they
muted the chat. Then HOW:
- someone with a tab open gets a "notify" event; their browser shows a
  notification if that tab is hidden or on another chat (chat.js);
- someone with NO tab open gets a web push on each device that turned
  notifications on (pywebpush, VAPID keys), sent after the commit in a small
  thread pool, so the sender never waits for a push service. A device the
  push service says is gone (404/410) is forgotten.

MENTIONS: "@ana" in a group message, for a member called ana, saves a
Mention (the chat shows a red @ until Ana reads past it) and her
notification says she was mentioned.

SEARCH looks only in your own chats, and only at what you may read there
(not deleted, and not before you joined a group that hides its history).
"""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import click
from flask.cli import with_appcontext
from markupsafe import Markup, escape
from sqlalchemy import and_, delete, func, or_, select

from . import access, chats, db, outbox
from .dates import utcnow
from .models import Conversation, Membership, Mention, Message, PushSubscription, User
from .problems import Invalid

log = logging.getLogger(__name__)

FOREVER = datetime(9999, 1, 1)
MUTE_CHAT_CHOICES = {"60": ("1 hour", timedelta(hours=1)), "480": ("8 hours", timedelta(hours=8)),
                     "forever": ("Until I turn it back on", None)}
MENTION = re.compile(r"(?<![\w@.])@([a-z][a-z0-9_]{2,19})\b", re.IGNORECASE)
SEARCH_LIMIT = 50
PUSH_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="web-push")


# ---------- Muting a chat ----------

def is_muted(membership, moment=None):
    return (membership is not None and membership.notify_off_until is not None
            and membership.notify_off_until > (moment or utcnow()))


def mute_chat(s, me, chat_id, choice, moment):
    """choice: "60", "480", "forever", or "off"."""
    conversation, membership = access.load(s, me, chat_id)
    if choice == "off":
        membership.notify_off_until = None
    elif choice in MUTE_CHAT_CHOICES:
        length = MUTE_CHAT_CHOICES[choice][1]
        membership.notify_off_until = FOREVER if length is None else moment + length
    else:
        raise Invalid("Choose how long to mute the chat.")
    s.flush()
    outbox.emit(s, {me.id}, "chat.muted", {"conversation_id": conversation.id, "muted": is_muted(membership, moment)})
    outbox.emit(s, {me.id}, "unread.total", {"total": chats.total_unread(s, me)})
    return membership


def muted_words(membership):
    if not is_muted(membership):
        return None
    if membership.notify_off_until >= FOREVER - timedelta(days=1):
        return "Muted"
    from .formatting import clock, short_when
    until = membership.notify_off_until
    when = clock(until) if short_when(until) == clock(until) else short_when(until)
    return f"Muted until {when}"


# ---------- Mentions ----------

def handles_in(text):
    return {match.group(1).lower() for match in MENTION.finditer(text or "")}


def record_mentions(s, conversation, message, moment):
    """chats.after_send_hooks: save who a group message mentions. Returns their user ids."""
    if not conversation.is_group or message.kind != "text":
        return set()
    wanted = handles_in(message.body)
    if not wanted:
        return set()
    found = set()
    for membership in chats.members(s, conversation):
        person = membership.user
        if person.id != message.sender_id and person.handle.lower() in wanted:
            s.add(Mention(message_id=message.id, user_id=person.id, conversation_id=conversation.id, seq=message.seq))
            found.add(person.id)
    s.flush()
    if found and message.sender is not None:
        from . import activity
        for user_id in found:
            activity.add(s, user_id, "mention", message.sender, message.created_at,
                         conversation_id=conversation.id, message_id=message.id)
    return found


def mention_counts(s, viewer, rows):
    """Put .mentions (unread mentions of the viewer) on each chat-list row."""
    if not rows:
        return rows
    counts = dict(s.execute(
        select(Mention.conversation_id, func.count(Mention.id))
        .join(Membership, and_(Membership.conversation_id == Mention.conversation_id,
                               Membership.user_id == viewer.id))
        .where(Mention.user_id == viewer.id, Mention.seq > Membership.last_read_seq)
        .group_by(Mention.conversation_id)).all())
    for row in rows:
        row.mentions = counts.get(row.conversation.id, 0)
    return rows


# ---------- Notifications ----------

def after_message(s, conversation, message, moment):
    """chats.after_send_hooks: mentions, then tell each member who should hear about it."""
    mentioned = record_mentions(s, conversation, message, moment)
    sender = message.sender or s.get(User, message.sender_id)
    if sender is None:
        return
    app = s.info.get("app")
    hub = app.extensions.get("paperplane_hub") if app else None
    text = chats.preview_of(message) or "Sent a message"
    from . import restrict
    for membership in chats.members(s, conversation):
        user_id = membership.user_id
        if user_id == message.sender_id or is_muted(membership, moment) or restrict.has(s, user_id, message.sender_id):
            continue
        if conversation.is_dm:
            title = sender.name
        else:
            title = f"{sender.first_name} in {conversation.title}"
        body = f"Mentioned you: {text}" if user_id in mentioned else (
            text if conversation.is_dm else f"{sender.first_name}: {text}")
        payload = {"conversation_id": conversation.id, "message_id": message.id, "title": title,
                   "body": body[:180], "url": f"/chats/{conversation.id}", "mention": user_id in mentioned}
        if user_id in mentioned:
            outbox.emit(s, {user_id}, "mention", {"conversation_id": conversation.id, "message_id": message.id})
        if hub is not None and hub.is_connected(user_id):
            outbox.emit(s, {user_id}, "notify", payload)
        elif app is not None and app.config.get("VAPID_PRIVATE_KEY"):
            outbox.after_commit(s, lambda user_id=user_id, payload=payload: PUSH_POOL.submit(push, app, user_id, payload))


def push(app, user_id, payload, sender=None):
    """Send a web push to each of a person's devices (in the push pool). Returns how many got it."""
    import pywebpush
    sender = sender or pywebpush.webpush
    delivered = 0
    with app.app_context():
        s = db.new_session(app)
        try:
            for device in s.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)).all():
                try:
                    sender(subscription_info={"endpoint": device.endpoint,
                                              "keys": {"p256dh": device.p256dh, "auth": device.auth}},
                           data=json.dumps(payload), vapid_private_key=app.config["VAPID_PRIVATE_KEY"],
                           vapid_claims={"sub": app.config.get("VAPID_SUBJECT") or "mailto:notifications@paperplane.invalid"},
                           timeout=10)
                    delivered += 1
                except pywebpush.WebPushException as error:
                    status = getattr(getattr(error, "response", None), "status_code", None)
                    if status in (404, 410):
                        s.delete(device)  # the browser threw this subscription away
                    else:
                        log.warning("Push to user %s failed: %s", user_id, status or error)
            s.commit()
        finally:
            s.close()
    return delivered


def save_device(s, me, data):
    keys = data.get("keys") or {}
    endpoint, p256dh, auth_key = data.get("endpoint", ""), keys.get("p256dh", ""), keys.get("auth", "")
    if (not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 1000
            or not isinstance(p256dh, str) or not isinstance(auth_key, str) or not p256dh or not auth_key
            or len(p256dh) > 200 or len(auth_key) > 100):
        raise Invalid("That isn't a push subscription.")
    device = s.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if device is None:
        device = PushSubscription(endpoint=endpoint)
        s.add(device)
    device.user_id, device.p256dh, device.auth = me.id, p256dh, auth_key  # a shared computer: the last person wins
    s.flush()
    return device


def forget_devices(s, me):
    return s.execute(delete(PushSubscription).where(PushSubscription.user_id == me.id)).rowcount


def device_count(s, me):
    return s.scalar(select(func.count(PushSubscription.id)).where(PushSubscription.user_id == me.id)) or 0


@click.command("make-vapid-keys")
def make_vapid_keys_command():
    """Make the two keys for phone notifications. Put them in Render's environment."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid, b64urlencode
    keys = Vapid()
    keys.generate_keys()
    private = b64urlencode(keys.private_key.private_bytes(serialization.Encoding.DER,
                                                          serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    public = b64urlencode(keys.public_key.public_bytes(serialization.Encoding.X962,
                                                       serialization.PublicFormat.UncompressedPoint))
    click.echo(f"VAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY={private}")


# ---------- Search ----------

def like_pattern(text):
    """%text% with the LIKE wildcards in the text made plain (searching for "50%" finds "50%")."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search(s, me, query, chat_id=None, limit=SEARCH_LIMIT):
    """Messages I may read whose words contain the query, newest first: [(message, chat)]."""
    query = " ".join((query or "").split())
    if len(query) < 2:
        return []
    statement = (
        select(Message, Conversation)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(Membership, and_(Membership.conversation_id == Conversation.id, Membership.user_id == me.id))
        .where(Membership.removed_at.is_(None), Conversation.deleted_at.is_(None),
               Message.deleted_at.is_(None), Message.kind == "text",
               # The history rule: a group that hides it shows a member only what came after they joined.
               or_(Conversation.history_visible.is_(True), Conversation.kind == "dm", Message.seq > Membership.joined_seq),
               Message.seq > Membership.cleared_seq,  # a chat they deleted for themselves
               func.lower(Message.body).like(like_pattern(query.lower()), escape="\\"))
        .order_by(Message.created_at.desc(), Message.id.desc()).limit(limit))
    if chat_id:
        statement = statement.where(Conversation.id == chat_id)
    return s.execute(statement).all()


def search_chat(s, conversation, membership, query, sender_id=None, before_seq=None, limit=None):
    """Search ONE chat (its Search in this chat page): messages whose words contain the query,
    and/or that this person sent, newest first. ([messages], more?). The chat's own rules: the
    caller loaded it with access.load (members only), the history rule, deleted ones left out.
    With no words but a person, it lists everything that person said."""
    limit = limit or SEARCH_LIMIT
    query = " ".join((query or "").split())
    if len(query) < 2 and sender_id is None:
        return [], False
    statement = select(Message).where(
        Message.conversation_id == conversation.id, Message.deleted_at.is_(None), Message.kind == "text",
        Message.body != "", Message.seq > chats.visible_from(conversation, membership))
    if len(query) >= 2:
        statement = statement.where(func.lower(Message.body).like(like_pattern(query.lower()), escape="\\"))
    if sender_id is not None:
        statement = statement.where(Message.sender_id == sender_id)
    if before_seq is not None:
        statement = statement.where(Message.seq < before_seq)
    found = s.scalars(statement.order_by(Message.seq.desc()).limit(limit + 1)).all()
    return found[:limit], len(found) > limit


def snippet(body, query, width=70):
    """The words around the first match, with the match marked (escaped, safe HTML)."""
    lowered, needle = body.lower(), " ".join(query.split()).lower()
    at = lowered.find(needle)
    if at < 0:
        return escape(body[: width * 2])
    start, end = max(0, at - width), min(len(body), at + len(needle) + width)
    return Markup("{}{}<mark>{}</mark>{}{}").format(
        "..." if start else "", body[start:at], body[at:at + len(needle)], body[at + len(needle):end],
        "..." if end < len(body) else "")


def init_app(app):
    if after_message not in chats.after_send_hooks:
        chats.after_send_hooks.append(after_message)
    app.cli.add_command(make_vapid_keys_command)
