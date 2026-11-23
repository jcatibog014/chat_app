import html
import os
import re

import pytest
from sqlalchemy import select

from paperplane import auth, create_app, db
from paperplane.models import User

# Tests use a fresh SQLite file per test. To check everything on PostgreSQL
# too, set TEST_DATABASE_URL to an EMPTY Neon branch (its tables are dropped
# and made again for every test, so never point it at the live one).
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

CSRF = "test-csrf-token"
PASSWORD = "correct horse"


@pytest.fixture(autouse=True)
def fast_passwords(monkeypatch):
    """The real password hash is slow on purpose (to slow down guessing). Tests make
    many users, so they use a quick setting of the same kind of hash."""
    from functools import partial

    from werkzeug.security import generate_password_hash
    quick = partial(generate_password_hash, method="pbkdf2:sha256:1000")
    monkeypatch.setattr(auth, "generate_password_hash", quick)
    from paperplane import seed
    monkeypatch.setattr(seed, "generate_password_hash", quick)


def make_app(tmp_path, **config):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DATABASE": str(tmp_path / "test.sqlite"),
        "DATABASE_URL": TEST_DATABASE_URL,
        "SERVER_NAME": "localhost",
        "BASE_URL": "http://localhost",
        **config,
    })
    with app.app_context():
        if TEST_DATABASE_URL:
            db.drop_tables()
        db.create_tables()
    return app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


@pytest.fixture
def ctx(app):
    """An app context, for tests that use db.session() directly."""
    with app.app_context():
        yield db.session()


@pytest.fixture
def client(app):
    client = app.test_client()
    # Every POST needs the session's CSRF token. Put a known one in the session.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client


def post(client, url, data=None, **kwargs):
    """POST a form with the right CSRF token, like a real page would."""
    # Logging in or out starts a new session (and a new token), so set it every time.
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return client.post(url, data={"csrf_token": CSRF, **(data or {})}, **kwargs)


def make_user(app, username="maria", name="Maria Santos", password=PASSWORD, **fields):
    """A user straight into the database. Returns the id."""
    with app.app_context():
        s = db.session()
        user, _code = auth.create_user(s, username, name, password, **fields)
        s.commit()
        return user.id


def log_in_as(client, user_id):
    """Skip the login form: put the user straight into the session."""
    with client.application.app_context():
        version = db.session().get(User, user_id).session_version
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["session_version"] = version
        session["csrf"] = CSRF
    return client


def browser(app, user_id=None):
    """A fresh client, logged in as this user if given."""
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = CSRF
    return log_in_as(client, user_id) if user_id else client


@pytest.fixture
def maria(app):
    """A logged-in user, Maria. Returns (client, user id)."""
    user_id = make_user(app)
    return browser(app, user_id), user_id


def is_logged_in(client):
    client.get("/settings")
    with client.session_transaction() as session:
        return "user_id" in session


def get_user(app, username):
    with app.app_context():
        return db.session().scalar(select(User).where(User.username == username))


def text(response):
    """A page's words the way a person reads them (&#39; back to ', &amp; to &)."""
    return html.unescape(response.get_data(as_text=True))


def words(response):
    """A page as plain words: no tags, single spaces (for sentences split by <strong> and such)."""
    return " ".join(re.sub(r"<[^>]+>", " ", text(response)).split())
