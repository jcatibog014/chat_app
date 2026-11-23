"""The database: one SQLAlchemy engine per app and one session per request.

Locally and in tests it is a SQLite file in instance/ (git ignores it).
On the live site DATABASE_URL points at PostgreSQL (Neon). The models and
queries are the same for both: SQLAlchemy writes the right SQL for each.

Use it like this in a view:

    s = db.session()
    chat = s.get(Conversation, 7)
    chat.title = "Weekend Hike"
    s.commit()          # nothing is saved without commit()

Every session knows its app (session.info["app"]). outbox.py uses that to
send live events only AFTER a commit: a message that was rolled back must
never reach anyone's screen.

Code outside a request (a WebSocket event, the demo crew) makes its own
session with new_session() and closes it itself.
"""
from pathlib import Path

import click
from alembic import command
from alembic.config import Config
from flask import current_app, g
from flask.cli import with_appcontext
from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session

MIGRATIONS = Path(__file__).parent / "migrations"
ENGINE = "paperplane_engine"

# Every constraint gets a predictable name (uq_users_username, ...).
# Alembic needs names to change or drop a constraint in a later migration.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """The parent class of every model in models.py."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def database_url(config):
    """The SQLAlchemy URL: Neon's DATABASE_URL if set, else the SQLite file."""
    url = config.get("DATABASE_URL")
    if not url:
        return "sqlite:///" + config["DATABASE"]
    # Neon gives "postgresql://..." (some hosts "postgres://..."). SQLAlchemy
    # also needs the driver's name, and ours is psycopg (version 3).
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url):
    if url.startswith("sqlite"):
        # timeout: when another thread is writing, wait up to 30 s for it instead of
        # failing at once with "database is locked" (SQLite lets one writer in at a time).
        # check_same_thread: every WebSocket runs in its own thread, and the pool hands
        # connections between them (one thread uses a connection at a time).
        engine = create_engine(url, connect_args={"timeout": 30, "check_same_thread": False})

        @event.listens_for(engine, "connect")
        def sqlite_settings(dbapi_connection, _record):
            # SQLite ignores FOREIGN KEY rules unless asked, per connection.
            dbapi_connection.execute("PRAGMA foreign_keys = ON")
            # WAL: people can read pages while someone else's message is being saved.
            dbapi_connection.execute("PRAGMA journal_mode = WAL")
            dbapi_connection.execute("PRAGMA synchronous = NORMAL")
        return engine
    # pool_pre_ping: Neon closes idle connections (it sleeps), so check one before using it.
    # pool_size: pages and WebSocket events share these connections; an event holds one
    # only while it is being handled, never while a socket waits.
    engine = create_engine(url, pool_pre_ping=True, pool_size=8, max_overflow=8)

    @event.listens_for(engine, "connect")
    def plain_numbers(dbapi_connection, _record):
        # PostgreSQL's SUM() gives "numeric", which psycopg turns into Decimal. Read it the
        # way SQLite gives it, an int or a float (Tally Ho!'s lesson).
        dbapi_connection.adapters.register_loader("numeric", PlainNumberLoader)
    return engine


try:
    from psycopg.adapt import Loader
except ImportError:  # psycopg is only needed for PostgreSQL
    Loader = object


class PlainNumberLoader(Loader):
    """A PostgreSQL numeric as an int when it is whole ("123"), else a float ("12.5")."""
    def load(self, data):
        text = bytes(data).decode()
        try:
            return int(text)
        except ValueError:
            return float(text)


def engine(app=None):
    return (app or current_app).extensions[ENGINE]


def is_postgres(s):
    return s.get_bind().dialect.name == "postgresql"


def new_session(app=None) -> Session:
    """A session of its own (a WebSocket event, the demo crew). The caller closes it."""
    app = app or current_app._get_current_object()
    # expire_on_commit=False: objects keep their values after commit(),
    # so code can still read message.body after saving it.
    return Session(app.extensions[ENGINE], expire_on_commit=False, info={"app": app})


def session() -> Session:
    """This request's session (made on first use)."""
    if "db" not in g:
        g.db = new_session()
    return g.db


def close_session(_error=None):
    s = g.pop("db", None)
    if s is not None:
        s.close()


# ---------- Making and upgrading the tables ----------

def create_tables():
    """Make every table straight from the models (tests use this; it's fast)."""
    from . import models  # noqa: F401  (registers the tables on Base.metadata)
    Base.metadata.create_all(engine())


def drop_tables():
    from . import models  # noqa: F401
    Base.metadata.drop_all(engine())


def alembic_config():
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    return config


def upgrade_db(revision="head"):
    """Run the Alembic migrations that this database hasn't had yet.

    It keeps all data, and does nothing when the tables are up to date.
    Render runs it before every start (render.yaml).
    """
    config = alembic_config()
    with engine().connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            # SQLite changes a table by copying it and DROPPING the old one. With
            # foreign keys on, that drop deletes every row pointing at the old table
            # (ON DELETE CASCADE): changing "users" would empty "messages".
            # The switch only works outside a transaction, so commit straight after.
            connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
            connection.commit()
        try:
            with connection.begin():
                config.attributes["connection"] = connection  # migrations/env.py uses it
                command.upgrade(config, revision)
            if sqlite:
                broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise RuntimeError(f"The upgrade left {len(broken)} broken links between tables: {broken[:5]}")
        finally:
            if sqlite:
                connection.rollback()
                connection.exec_driver_sql("PRAGMA foreign_keys = ON")
                connection.commit()


@click.command("upgrade-db")
@with_appcontext
def upgrade_db_command():
    """Make or update the tables."""
    upgrade_db()
    click.echo("The database is up to date.")


def init_app(app):
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    app.extensions[ENGINE] = make_engine(database_url(app.config))
    app.teardown_appcontext(close_session)
    app.cli.add_command(upgrade_db_command)
