"""The data model: every table, as a Python class (SQLAlchemy 2.0).

Times are naive UTC (dates.py). Each table that a demo fills hangs off a
demo team, so deleting the team deletes everything in it (ON DELETE CASCADE).
Every foreign key to a parent that is deleted in bulk has an index that
STARTS with that column: without one, the cascade reads the whole table.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false, true
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


class Reaction(Base):
    """One person's emoji on one message. Pressing it again takes it back."""
    __tablename__ = "reactions"
    __table_args__ = (UniqueConstraint("message_id", "user_id", "emoji"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    emoji: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
