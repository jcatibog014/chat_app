"""Stories: a photo or a text card that lasts 24 hours.

WHO MAY SEE A STORY is decided here, in one place (like access.py for chats):

    can_view(s, viewer, story, now)
      - the story hasn't expired, and
      - the viewer IS its owner, or
      - the viewer is one of the owner's contacts (presence.contacts: people who
        share an active chat with them; blocks in either direction are already
        taken out), and, for a Close Friends story, on the owner's Close Friends list.

Anything else is "not found", never "forbidden". Demo and real people never
meet (contacts are always in the same world).

Only the OWNER sees who viewed and who reacted. A private reply goes through
the normal chat code (chats.start_dm + send_message), so blocks and every chat
rule still apply. Muting someone's stories only hides them from your circles
and Stories page; it changes nothing about who may view.

Expired stories never show (every query asks expires_at > now) and are
deleted by purge(): on each new story, on Try Demo, and by `flask purge-stories`.
"""
from datetime import timedelta

import click
from flask.cli import with_appcontext
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from . import chats, db, files, outbox, presence, tools
from .dates import utcnow
from .models import CloseFriend, Story, StoryMute, StoryReaction, StoryView, User
from .problems import Invalid, NotFound, TooMany

LIFETIME = timedelta(hours=24)
MAX_LIVE = 10
TEXT_MAX = 250
# A text card's colours (style.css .story-bg-*): white words on each, 4.5:1 or more.
BACKGROUNDS = [("pink", "Pink"), ("purple", "Purple"), ("blue", "Blue"), ("teal", "Teal"), ("orange", "Orange"),
               ("night", "Night")]
BACKGROUND_NAMES = [name for name, _ in BACKGROUNDS]
AUDIENCES = ("everyone", "close")

# Later parts react to a new story (the demo crew): function(s, story, now).
after_post_hooks = []


# ---------- Lists ----------

def close_friend_ids(s, owner_id):
    return set(s.scalars(select(CloseFriend.friend_id).where(CloseFriend.owner_id == owner_id)))


def muted_ids(s, user_id):
    return set(s.scalars(select(StoryMute.muted_id).where(StoryMute.user_id == user_id)))


def audience_of(s, story):
    """Everyone (besides the owner) who may see this story right now."""
    people = presence.contacts(s, story.user_id)
    if story.audience == "close":
        people &= close_friend_ids(s, story.user_id)
    return people


# ---------- Who may see ----------

def can_view(s, viewer, story, now):
    if story is None or story.expires_at <= now:
        return False
    if story.user_id == viewer.id:
        return True
    if viewer.id not in presence.contacts(s, story.user_id):
        return False
    return story.audience != "close" or viewer.id in close_friend_ids(s, story.user_id)


def load_visible(s, viewer, story_id, now):
    """The story if this person may see it, else NotFound."""
    try:
        story = s.get(Story, int(story_id))
    except (TypeError, ValueError):
        story = None
    if not can_view(s, viewer, story, now):
        raise NotFound()
    return story


def live_stories_of(s, owner_id, viewer, now):
    """A person's stories that this viewer may see, oldest first."""
    found = s.scalars(select(Story).where(Story.user_id == owner_id, Story.expires_at > now)
                      .order_by(Story.created_at, Story.id)).all()
    if owner_id == viewer.id:
        return found
    if viewer.id not in presence.contacts(s, owner_id):
        return []
    close = viewer.id in close_friend_ids(s, owner_id)
    return [story for story in found if story.audience != "close" or close]


# ---------- Posting and deleting ----------

def clean_text(text, required):
    text = " ".join((text or "").split())
    if required and not text:
        raise Invalid("Write something for your card.")
    if len(text) > TEXT_MAX:
        raise Invalid(f"Use at most {TEXT_MAX} characters.")
    return text


def post(s, me, kind, text, background, audience, now, upload_name=None, raw=None):
    """A new story. kind "photo" (upload_name + raw bytes) or "text"."""
    purge(s, now)
    if audience not in AUDIENCES:
        raise Invalid("Choose who can see it.")
    live = s.scalar(select(func.count(Story.id)).where(Story.user_id == me.id, Story.expires_at > now)) or 0
    if live >= MAX_LIVE:
        raise TooMany(f"You can have {MAX_LIVE} stories at a time. Delete one, or wait for one to end.")
    story = Story(user_id=me.id, kind=kind, audience=audience, created_at=now, expires_at=now + LIFETIME)
    if kind == "photo":
        if not raw:
            raise Invalid("Choose a photo.")
        prepared = files.prepare(upload_name or "photo.jpg", raw)
        if prepared.kind != "image":
            raise Invalid("A story photo must be a picture (JPEG, PNG, GIF or WebP).")
        story.image, story.thumb = prepared.data, prepared.thumb
        story.width, story.height = prepared.width, prepared.height
        story.text = clean_text(text, required=False)
    elif kind == "text":
        if background not in BACKGROUND_NAMES:
            raise Invalid("Choose a colour for your card.")
        story.text = clean_text(text, required=True)
        story.background = background
    else:
        raise Invalid("Choose a photo or a text card.")
    s.add(story)
    s.flush()
    outbox.emit(s, audience_of(s, story) | {me.id}, "story.new", {"story_id": story.id, "user_id": me.id})
    for hook in after_post_hooks:
        hook(s, story, now)
    return story


def delete_story(s, me, story_id):
    story = s.get(Story, story_id)
    if story is None or story.user_id != me.id:
        raise NotFound()
    people = audience_of(s, story) | {me.id}
    s.delete(story)
    outbox.emit(s, people, "story.gone", {"story_id": story_id, "user_id": me.id})


def purge(s, now):
    """Delete stories whose 24 hours are over (their views and reactions go with them)."""
    return s.execute(delete(Story).where(Story.expires_at <= now)).rowcount


# ---------- Seen by, reactions, replies ----------

def mark_seen(s, viewer, story, now):
    """The viewer saw it (counted once). The owner's tabs update their Seen by number."""
    if story.user_id == viewer.id:
        return False
    if s.scalar(select(StoryView.id).where(StoryView.story_id == story.id, StoryView.viewer_id == viewer.id)):
        return False
    try:
        with s.begin_nested():  # the same person in two tabs at once: saved once
            s.add(StoryView(story_id=story.id, viewer_id=viewer.id, viewed_at=now))
            s.flush()
    except IntegrityError:
        return False
    outbox.emit(s, {story.user_id}, "story.seen", {"story_id": story.id, "count": seen_count(s, story.id)})
    return True


def seen_ids(s, viewer_id, story_ids):
    """Which of these stories the viewer has seen."""
    if not story_ids:
        return set()
    return set(s.scalars(select(StoryView.story_id).where(StoryView.viewer_id == viewer_id,
                                                          StoryView.story_id.in_(story_ids))))


def seen_count(s, story_id):
    return s.scalar(select(func.count(StoryView.id)).where(StoryView.story_id == story_id)) or 0


def react(s, viewer, story_id, emoji, now):
    """React to someone's story. The same emoji again takes it back; another one replaces it.
    Returns the emoji now on it, or None."""
    if emoji not in tools.EMOJI:
        raise Invalid("Choose one of the reactions.")
    story = load_visible(s, viewer, story_id, now)
    if story.user_id == viewer.id:
        raise Invalid("You can't react to your own story.")
    mark_seen(s, viewer, story, now)
    existing = s.scalar(select(StoryReaction).where(StoryReaction.story_id == story.id,
                                                    StoryReaction.user_id == viewer.id))
    if existing is not None and existing.emoji == emoji:
        s.delete(existing)
        now_on = None
    elif existing is not None:
        existing.emoji, existing.created_at = emoji, now
        now_on = emoji
    else:
        s.add(StoryReaction(story_id=story.id, user_id=viewer.id, emoji=emoji, created_at=now))
        now_on = emoji
    s.flush()
    outbox.emit(s, {story.user_id}, "story.reacted", {"story_id": story.id, "user_id": viewer.id,
                                                       "name": viewer.first_name, "emoji": now_on})
    return now_on


def reply(s, viewer, story_id, body, now):
    """A private reply: a message in the viewer's DM with the owner, quoting the story."""
    story = load_visible(s, viewer, story_id, now)
    if story.user_id == viewer.id:
        raise Invalid("That's your own story.")
    owner = s.get(User, story.user_id)
    conversation, _made = chats.start_dm(s, viewer, owner, now)
    message, _dup = chats.send_message(s, viewer, conversation.id, body, None, now, story_id=story.id)
    mark_seen(s, viewer, story, now)
    return message


def viewers_of(s, story):
    """[(person, when they saw it, their emoji or None)], newest first. For the owner only."""
    emojis = dict(s.execute(select(StoryReaction.user_id, StoryReaction.emoji)
                            .where(StoryReaction.story_id == story.id)).all())
    rows = s.execute(select(User, StoryView.viewed_at).join(StoryView, StoryView.viewer_id == User.id)
                     .where(StoryView.story_id == story.id).order_by(StoryView.viewed_at.desc())).all()
    return [(person, when, emojis.get(person.id)) for person, when in rows]


def my_reaction(s, viewer, story):
    return s.scalar(select(StoryReaction.emoji).where(StoryReaction.story_id == story.id,
                                                      StoryReaction.user_id == viewer.id))


# ---------- The circles and the Stories page ----------

class Ring:
    """One person in the circles: their live stories (that I may see) and whether I've seen them all."""

    def __init__(self, person, stories, seen_ids):
        self.person = person
        self.stories = stories
        self.unseen = [story for story in stories if story.id not in seen_ids]
        self.latest = stories[-1].created_at if stories else None

    @property
    def has_unseen(self):
        return bool(self.unseen)

    @property
    def first_story(self):
        """Where viewing starts: the first story not seen yet, or the first one."""
        return (self.unseen or self.stories)[0]


def tray(s, viewer, now, include_muted=False):
    """(my Ring or None, [Rings of the people I chat with]): unseen first, then the newest."""
    people = presence.contacts(s, viewer.id)
    if not include_muted:
        people -= muted_ids(s, viewer.id)
    mine = s.scalars(select(Story).where(Story.user_id == viewer.id, Story.expires_at > now)
                     .order_by(Story.created_at, Story.id)).all()
    others = []
    if people:
        found = s.scalars(select(Story).where(Story.user_id.in_(people), Story.expires_at > now)
                          .order_by(Story.created_at, Story.id)).all()
        close_to = set(s.scalars(select(CloseFriend.owner_id).where(CloseFriend.friend_id == viewer.id)))
        by_person = {}
        for story in found:
            if story.audience == "close" and story.user_id not in close_to:
                continue
            by_person.setdefault(story.user_id, []).append(story)
        seen = set(s.scalars(select(StoryView.story_id).where(StoryView.viewer_id == viewer.id)))
        users = {user.id: user for user in s.scalars(select(User).where(User.id.in_(by_person)))}
        others = [Ring(users[user_id], stories, seen) for user_id, stories in by_person.items()]
        others.sort(key=lambda ring: (not ring.has_unseen, -ring.latest.timestamp()))
    return (Ring(viewer, mine, set(s.scalars(select(Story.id).where(Story.user_id == viewer.id)))) if mine else None,
            others)


def stats_for(s, story_ids):
    """{story id: (views, reactions)} for the owner's own stories."""
    if not story_ids:
        return {}
    views = dict(s.execute(select(StoryView.story_id, func.count(StoryView.id))
                           .where(StoryView.story_id.in_(story_ids)).group_by(StoryView.story_id)).all())
    reactions = dict(s.execute(select(StoryReaction.story_id, func.count(StoryReaction.id))
                               .where(StoryReaction.story_id.in_(story_ids)).group_by(StoryReaction.story_id)).all())
    return {story_id: (views.get(story_id, 0), reactions.get(story_id, 0)) for story_id in story_ids}


# ---------- Close Friends and muting ----------

def set_close_friends(s, me, friend_ids):
    """Replace my Close Friends list with these people (only people I chat with)."""
    allowed = presence.contacts(s, me.id)
    wanted = {friend_id for friend_id in friend_ids if friend_id in allowed}
    s.execute(delete(CloseFriend).where(CloseFriend.owner_id == me.id))
    for friend_id in wanted:
        s.add(CloseFriend(owner_id=me.id, friend_id=friend_id))
    s.flush()
    return wanted


def mute(s, me, person_id):
    # Only people I chat with (anyone else is "not found", so ids can't be probed).
    if person_id == me.id or person_id not in presence.contacts(s, me.id):
        raise NotFound()
    if person_id not in muted_ids(s, me.id):
        s.add(StoryMute(user_id=me.id, muted_id=person_id))
        s.flush()


def unmute(s, me, person_id):
    s.execute(delete(StoryMute).where(StoryMute.user_id == me.id, StoryMute.muted_id == person_id))


def muted_people(s, me):
    return s.scalars(select(User).join(StoryMute, StoryMute.muted_id == User.id)
                     .where(StoryMute.user_id == me.id).order_by(User.name)).all()


# ---------- A story quoted in a chat (a private reply) ----------

def quote_info(s, message, now):
    """What a reply's quote shows: the story while it lasts, else "Story expired"."""
    if not message.is_story_reply:
        return None
    story = s.get(Story, message.story_id) if message.story_id else None
    live = story is not None and story.expires_at > now
    owner = s.get(User, story.user_id) if story is not None else None
    return {"id": story.id if live else None, "live": live, "kind": story.kind if live else None,
            "text": story.text if live else "", "background": story.background if live else None,
            "owner": owner.first_name if owner else None, "owner_id": owner.id if owner else None}


@click.command("purge-stories")
@with_appcontext
def purge_stories_command():
    """Delete stories whose 24 hours are over."""
    s = db.session()
    removed = purge(s, utcnow())
    s.commit()
    click.echo(f"Deleted {removed} stor{'ies' if removed != 1 else 'y'}.")


def init_app(app):
    app.cli.add_command(purge_stories_command)
    app.jinja_env.globals.update(STORY_BACKGROUNDS=BACKGROUNDS)
