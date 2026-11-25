"""The data model: every table, as a Python class (SQLAlchemy 2.0).

Times are naive UTC (dates.py). Each table that a demo fills hangs off a
demo team, so deleting the team deletes everything in it (ON DELETE CASCADE).
Every foreign key to a parent that is deleted in bulk has an index that
STARTS with that column: without one, the cascade reads the whole table.
"""
from datetime import datetime

from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, false,
                        true)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .dates import DEFAULT_ZONE, utcnow
from .db import Base

AVATAR_TONES = 8  # avatars take one of 8 colours (style.css .tone-1 .. .tone-8)


class DemoTeam(Base):
    """One visitor's Try Demo: their made-up teammates and chats, deleted after 24 hours."""
    __tablename__ = "demo_teams"
    # sqlite_autoincrement: SQLite would otherwise give a deleted team's id to the next
    # team, and an old cookie could walk into somebody else's demo (ShiroMoto's lesson).
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    people: Mapped[list["User"]] = relationship(back_populates="demo_team", passive_deletes=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    # Real people log in with their username (small letters, numbers and _). A demo's
    # people get "d<team>.<name>" so they never clash with real ones; the dot can't be
    # typed into a real username.
    username: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    # The one-time recovery code is kept only as a hash, like a password.
    recovery_hash: Mapped[str | None] = mapped_column(String(255))
    # Goes up when the password changes or "Log out other devices" is used: every older
    # cookie, and every open WebSocket made with one, stops working.
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    time_zone: Mapped[str] = mapped_column(String(64), default=DEFAULT_ZONE)
    show_presence: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # The colour the app is drawn in for this person (Settings, Appearance): pink (the logo's,
    # the default), purple, red or blue. Saved on the account, so it follows them to any device.
    accent: Mapped[str] = mapped_column(String(10), default="pink", server_default="pink")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    demo_team_id: Mapped[int | None] = mapped_column(ForeignKey("demo_teams.id", ondelete="CASCADE"), index=True)
    # A demo teammate the crew plays (crew.py). The visitor is the one demo person who isn't.
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    demo_team: Mapped[DemoTeam | None] = relationship(back_populates="people")

    @property
    def is_demo(self):
        return self.demo_team_id is not None

    @property
    def handle(self):
        """The name after the @: a demo person's "d17.ana" shows as "ana"."""
        return self.username.split(".", 1)[1] if self.is_demo and "." in self.username else self.username

    @property
    def initial(self):
        return (self.name.strip()[:1] or "?").upper()

    @property
    def first_name(self):
        return self.name.split()[0] if self.name.split() else self.name

    @property
    def tone(self):
        """The avatar's colour: the same for a person everywhere."""
        return self.id % AVATAR_TONES + 1


class RateLimit(Base):
    """A counter for limits.py: tries per key in a time window."""
    __tablename__ = "rate_limits"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime, index=True)


class Conversation(Base):
    """A chat: a DM between two people, or a group.

    last_seq numbers the messages: each new one takes the next number inside the
    same transaction, so every member sees the same order, and unread counts are
    last_seq minus the member's last_read_seq (no counting query).
    version goes up on EVERY change in the chat (a new message, an edit, a member
    leaving...). A browser that reconnects says which version it has, and gets
    only what changed since (chats.changes_since).
    """
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # "dm" or "group"
    title: Mapped[str | None] = mapped_column(String(80))  # groups only; a DM is named after the other person
    # "4:9" for the DM between users 4 and 9 (smaller id first): one DM per pair, even if
    # both start it at the same moment (the UNIQUE rule catches the second).
    dm_key: Mapped[str | None] = mapped_column(String(40), unique=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Groups: may a new member read what was said before they joined? (stage 7)
    history_visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    # A demo's chats go with the demo.
    demo_team_id: Mapped[int | None] = mapped_column(ForeignKey("demo_teams.id", ondelete="CASCADE"), index=True)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="conversation", passive_deletes=True)

    @property
    def is_dm(self):
        return self.kind == "dm"

    @property
    def is_group(self):
        return self.kind == "group"


class Membership(Base):
    """A person in a chat, with their role and how far they have read."""
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("conversation_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="member")  # owner / admin / member (access.py)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # The chat's last_seq when they joined: with history_visible off they see only later messages.
    joined_seq: Mapped[int] = mapped_column(Integer, default=0)
    last_read_seq: Mapped[int] = mapped_column(Integer, default=0)
    # Set when they leave or are removed. The row stays (their old messages keep a sender),
    # but access.py treats them as a stranger to the chat.
    removed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # A group's owner or admin muted them: they can read, but not send or react, until then.
    muted_until: Mapped[datetime | None] = mapped_column(DateTime)
    # THEY muted the chat (stage 9): no notifications and a grey badge until then. Not the same
    # as muted_until above, which an admin sets to stop someone talking.
    notify_off_until: Mapped[datetime | None] = mapped_column(DateTime)

    conversation: Mapped[Conversation] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship()

    @property
    def active(self):
        return self.removed_at is None


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq"),
        # A message sent twice (a reconnect, a double click, a reloaded form) has the same
        # client_id, and is saved once. It also serves as the index for the sender's cascade.
        UniqueConstraint("sender_id", "client_id"),
        Index("ix_messages_conversation_version", "conversation_id", "version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    # None for a system line ("Ana added Ben").
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_id: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(10), default="text")  # text / system
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    # The conversation's version when this message last changed (catch-up after a reconnect).
    version: Mapped[int] = mapped_column(Integer, default=0)
    # The message this one answers (stage 6). Always in the same chat (tools.reply_target).
    reply_to_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), index=True)
    # A private reply to someone's story (stories.reply): the story it answers, while it lasts.
    story_id: Mapped[int | None] = mapped_column(ForeignKey("stories.id", ondelete="SET NULL"), index=True)
    # Stays True after the story is gone (story_id becomes NULL), so the chat can say "Story expired".
    is_story_reply: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    sender: Mapped[User | None] = relationship()
    conversation: Mapped[Conversation] = relationship()
    reply_to: Mapped["Message | None"] = relationship(remote_side=[id])


class InviteLink(Base):
    """A link that lets anyone with it join a group (groups.join). It can expire, have a use
    limit, and be turned off. The code is random (not signed), so turning it off really ends it."""
    __tablename__ = "invite_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)

    conversation: Mapped[Conversation] = relationship()
    created_by: Mapped[User | None] = relationship()


class Block(Base):
    """blocker doesn't want to hear from blocked: no messages in their DM, no new DM, no adding
    them to groups, and neither sees the other online (blocks.py)."""
    __tablename__ = "blocks"
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    blocker_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    blocked_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    blocked: Mapped[User] = relationship(foreign_keys=[blocked_id])


class Attachment(Base):
    """A picture or file sent with a message (files.py). Kept in the database: small (5 MB at
    most), and served only to people who may see the message it belongs to."""
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # "image" (re-saved as WebP by us) or "file"
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)   # not loaded unless asked for
    thumb: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LinkPreview(Base):
    """The title and description of the first web address in a message (previews.py)."""
    __tablename__ = "link_previews"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), unique=True)
    url: Mapped[str] = mapped_column(String(2000))
    site_name: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(300))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Mention(Base):
    """@someone in a group message: they get a red @ on the chat and a notification."""
    __tablename__ = "mentions"
    __table_args__ = (UniqueConstraint("message_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)


class PushSubscription(Base):
    """A browser or phone that asked for notifications when no tab is open (web push)."""
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(1000), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Story(Base):
    """A photo or a text card that lasts 24 hours (stories.py)."""
    __tablename__ = "stories"
    __table_args__ = (Index("ix_stories_user_expires", "user_id", "expires_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))           # "photo" or "text"
    text: Mapped[str] = mapped_column(String(250), default="")  # a photo's caption, or the card's words
    background: Mapped[str] = mapped_column(String(10), default="pink")  # a text card's colour
    audience: Mapped[str] = mapped_column(String(10), default="everyone")  # "everyone" or "close"
    image: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    thumb: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    user: Mapped[User] = relationship()


class StoryView(Base):
    """Who has seen a story (the first time). Only the story's owner sees this list."""
    __tablename__ = "story_views"
    __table_args__ = (UniqueConstraint("story_id", "viewer_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"))
    viewer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    viewed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    viewer: Mapped[User] = relationship()


class StoryReaction(Base):
    """One emoji per viewer per story: another one replaces it, the same one takes it back."""
    __tablename__ = "story_reactions"
    __table_args__ = (UniqueConstraint("story_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    emoji: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CloseFriend(Base):
    """owner's Close Friends list: who sees their Close Friends stories."""
    __tablename__ = "close_friends"
    __table_args__ = (UniqueConstraint("owner_id", "friend_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    friend_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class StoryMute(Base):
    """user doesn't want to see muted's stories (they stay friends; nobody is told)."""
    __tablename__ = "story_mutes"
    __table_args__ = (UniqueConstraint("user_id", "muted_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    muted_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class Activity(Base):
    """One line on someone's Notifications page (activity.py): who did what that concerns them.
    kind: mention, reply, reaction, story_new, story_reaction, story_reply, group_added,
    group_admin, group_removed. The chat, message and story it points to are optional, and
    deleting them takes the line away (or, for a story, leaves it saying the story ended)."""
    __tablename__ = "activities"
    __table_args__ = (Index("ix_activities_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("stories.id", ondelete="SET NULL"), index=True)
    emoji: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)

    actor: Mapped[User] = relationship(foreign_keys=[actor_id])


class Reaction(Base):
    """One person's emoji on one message. Pressing it again takes it back."""
    __tablename__ = "reactions"
    __table_args__ = (UniqueConstraint("message_id", "user_id", "emoji"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    emoji: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
