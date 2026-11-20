"""The data model: every table, as a Python class (SQLAlchemy 2.0).

Times are naive UTC (dates.py). Each table that a demo fills hangs off a
demo team, so deleting the team deletes everything in it (ON DELETE CASCADE).
Every foreign key to a parent that is deleted in bulk has an index that
STARTS with that column: without one, the cascade reads the whole table.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, false, true
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
