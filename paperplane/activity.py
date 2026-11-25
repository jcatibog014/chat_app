"""The Notifications page: who did something that concerns you.

Lines are written where things happen (the same transaction, so they can't disagree):
- mention: notify.record_mentions (someone wrote @you in a group);
- reply / story_reply: after_message, a chats.after_send_hooks step (a reply to your
  message, or a private reply to your story);
- reaction: tools.react (taking the emoji back takes the line back);
- story_new: story_posted, a stories.after_post_hooks step (to everyone who may see it and
  hasn't muted the person; one line per person, freshened by their next story);
- story_reaction: stories.react;
- group_added / group_admin / group_owner / group_removed: groups.py;
- call_missed: calls.end (a call you never joined; emoji holds "audio" or "video").

A line is read when you click it (or "Mark all as read"); opening the page doesn't read them.
Never about yourself, never from someone you restricted, never between people who blocked each other (blocking also deletes the
lines between them), and a deleted message takes its lines with it, so the number in the
menu always matches what the page shows. Each change
sends "activity.count" {unread} to the person's tabs (activity.js updates the menu's number).
"""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, or_, select, update

from . import outbox
from .models import Activity, Conversation, Membership, Message, Story, StoryMute, User

PAGE = 40
KEEP = timedelta(days=60)


# ---------- Writing ----------

def add(s, user_id, kind, actor, moment, conversation_id=None, message_id=None, story_id=None, emoji=None):
    """A new line on user_id's page, unless it's about themselves or a block stands between."""
    from . import blocks, restrict
    if (user_id is None or user_id == actor.id or blocks.between(s, user_id, actor.id)
            or restrict.has(s, user_id, actor.id)):
        return None
    item = Activity(user_id=user_id, actor_id=actor.id, kind=kind, conversation_id=conversation_id,
                    message_id=message_id, story_id=story_id, emoji=emoji, created_at=moment)
    s.add(item)
    s.flush()
    tell(s, {user_id})
    return item


def take_back(s, user_id, kind, actor_id, message_id=None, story_id=None, emoji=None):
    """Remove a line (a reaction taken back)."""
    query = delete(Activity).where(Activity.user_id == user_id, Activity.kind == kind, Activity.actor_id == actor_id)
    if message_id is not None:
        query = query.where(Activity.message_id == message_id)
    if story_id is not None:
        query = query.where(Activity.story_id == story_id)
    if emoji is not None:
        query = query.where(Activity.emoji == emoji)
    if s.execute(query).rowcount:
        tell(s, {user_id})


def forget_message(s, message_id):
    """A deleted message: its mentions, replies and reactions leave the pages."""
    people = set(s.scalars(select(Activity.user_id).where(Activity.message_id == message_id)))
    if people:
        s.execute(delete(Activity).where(Activity.message_id == message_id))
        tell(s, people)


def forget_between(s, a_id, b_id):
    """A block: the lines between the two people go, both ways."""
    query = or_((Activity.user_id == a_id) & (Activity.actor_id == b_id),
                (Activity.user_id == b_id) & (Activity.actor_id == a_id))
    if s.execute(delete(Activity).where(query)).rowcount:
        tell(s, {a_id, b_id})


def tell(s, user_ids):
    for user_id in user_ids:
        outbox.emit(s, {user_id}, "activity.count", {"unread": unread_count(s, user_id)})


# ---------- Where things happen ----------

def after_message(s, conversation, message, moment):
    """chats.after_send_hooks: a reply to someone's message, or a private reply to their story."""
    if message.kind != "text" or message.sender is None:
        return
    if message.reply_to_id:
        target = s.get(Message, message.reply_to_id)
        if target is not None and target.deleted_at is None:
            add(s, target.sender_id, "reply", message.sender, message.created_at,
                conversation_id=conversation.id, message_id=message.id)
    if message.is_story_reply and message.story_id:
        story = s.get(Story, message.story_id)
        if story is not None:
            add(s, story.user_id, "story_reply", message.sender, message.created_at,
                conversation_id=conversation.id, message_id=message.id, story_id=story.id)


def story_posted(s, story, moment):
    """stories.after_post_hooks: tell everyone who may see it (and hasn't muted the person).
    One line per person: an unread one from earlier is freshened instead of adding another."""
    from . import stories
    owner = s.get(User, story.user_id)
    muting = set(s.scalars(select(StoryMute.user_id).where(StoryMute.muted_id == owner.id)))
    for user_id in stories.audience_of(s, story) - muting:
        waiting = s.scalar(select(Activity).where(Activity.user_id == user_id, Activity.actor_id == owner.id,
                                                  Activity.kind == "story_new", Activity.read_at.is_(None)))
        if waiting is not None:
            waiting.story_id, waiting.created_at = story.id, moment
        else:
            add(s, user_id, "story_new", owner, moment, story_id=story.id)


# ---------- Reading ----------

def unread_count(s, user_id):
    return s.scalar(select(func.count(Activity.id)).where(Activity.user_id == user_id,
                                                          Activity.read_at.is_(None))) or 0


def mark_read(s, user, moment):
    if s.execute(update(Activity).where(Activity.user_id == user.id, Activity.read_at.is_(None))
                 .values(read_at=moment)).rowcount:
        tell(s, {user.id})


def purge(s, user, moment):
    """Lines older than KEEP (60 days) go."""
    s.execute(delete(Activity).where(Activity.user_id == user.id, Activity.created_at < moment - KEEP))


@dataclass
class Line:
    item: Activity
    actor: User
    chat_title: str | None   # the group's name (None in a DM)
    snippet: str | None      # the words it's about
    url: str | None          # where it leads, if you may still go there
    story_live: bool = False
    is_new: bool = False     # unread when the page was asked for (mark_read changes the row after)


def page(s, user, moment, before=None, limit=None):
    """([Line], more?) newest first."""
    limit = limit or PAGE
    query = select(Activity).where(Activity.user_id == user.id)
    if before is not None:
        query = query.where(Activity.id < before)
    items = s.scalars(query.order_by(Activity.created_at.desc(), Activity.id.desc()).limit(limit + 1)).all()
    more = len(items) > limit
    return lines_for(s, user, items[:limit], moment), more


def open_line(s, user, activity_id, moment):
    """Clicking a line: it's read now. Returns the Line (its url is where to go), or None."""
    item = s.get(Activity, activity_id)
    if item is None or item.user_id != user.id:
        return None
    lines = lines_for(s, user, [item], moment)
    if item.read_at is None:
        item.read_at = moment
        s.flush()
        tell(s, {user.id})
    return lines[0] if lines else None


def lines_for(s, user, items, moment):
    """The page's Lines for these rows: who, the words, and where each leads (if you may go)."""
    from flask import url_for

    from . import stories

    def load(model, ids):
        ids = {i for i in ids if i}
        return {row.id: row for row in s.scalars(select(model).where(model.id.in_(ids)))} if ids else {}

    actors = load(User, (i.actor_id for i in items))
    chats_by_id = load(Conversation, (i.conversation_id for i in items))
    messages = load(Message, (i.message_id for i in items))
    stories_by_id = load(Story, (i.story_id for i in items))
    memberships = {m.conversation_id: m for m in s.scalars(select(Membership).where(
        Membership.user_id == user.id, Membership.removed_at.is_(None),
        Membership.conversation_id.in_(list(chats_by_id) or [0])))}

    def can_read(message):
        """You're still in the chat and the message is one you may read: link to it."""
        conversation = chats_by_id.get(message.conversation_id)
        membership = memberships.get(message.conversation_id)
        if conversation is None or membership is None or conversation.deleted_at is not None:
            return False
        from .chats import visible_from
        return message.deleted_at is None and message.seq > visible_from(conversation, membership)

    def message_url(message):
        return (url_for("chats.show", chat_id=message.conversation_id, before=message.seq + 20) + f"#m-{message.id}"
                if can_read(message) else None)

    lines = []
    for item in items:
        actor = actors.get(item.actor_id)
        if actor is None:
            continue
        conversation = chats_by_id.get(item.conversation_id)
        message = messages.get(item.message_id)
        story = stories_by_id.get(item.story_id)
        live = story is not None and story.expires_at > moment
        snippet, url = None, None
        if item.kind in ("mention", "reply", "reaction", "story_reply") and message is not None:
            snippet, url = message.body or None, message_url(message)
        elif item.kind == "story_new":
            snippet = story.text if live else None
            url = url_for("stories.view", person_id=actor.id) if live and stories.live_stories_of(
                s, actor.id, user, moment) else None
        elif item.kind == "story_reaction":
            snippet = story.text if live else None
            url = url_for("stories.view", person_id=user.id, story=story.id) if live else None
        elif item.kind in ("group_added", "group_admin", "group_owner", "call_missed") and conversation is not None:
            url = url_for("chats.show", chat_id=conversation.id) if conversation.id in memberships else None
        lines.append(Line(item=item, actor=actor, chat_title=conversation.title if conversation is not None else None,
                          snippet=snippet, url=url, story_live=live, is_new=item.read_at is None))
    return lines


def init_app(app):
    from . import chats, stories
    if after_message not in chats.after_send_hooks:
        chats.after_send_hooks.append(after_message)
    if story_posted not in stories.after_post_hooks:
        stories.after_post_hooks.append(story_posted)
