"""Try Demo: every visitor gets their own sample team, "Papel Studio".

The visitor is Mika Santos, a designer. Four made-up teammates (Ana, Ben,
Carlo and Dana) are played by the demo crew (crew.py): they come online, type
and answer. Nobody sees anyone else's demo: a demo person can only find and
talk to people in the same team (access.same_world), and the team is deleted
with everything in it 24 hours later.

Every page a demo can open must show an example (tests/test_demo.py), so each
new feature adds its sample data here.
"""
import secrets
from datetime import timedelta

import click
from flask.cli import with_appcontext
from sqlalchemy import delete, func, select
from werkzeug.security import generate_password_hash

from . import db
from .dates import utcnow
from .models import DemoTeam, User

MAX_DEMOS = 20
LIFETIME = timedelta(hours=24)
TEAM_NAME = "Papel Studio"

# (handle, name). The first is the visitor.
PEOPLE = [
    ("mika", "Mika Santos"),
    ("ana", "Ana Reyes"),
    ("ben", "Ben Cruz"),
    ("carlo", "Carlo Diaz"),
    ("dana", "Dana Lim"),
]

# Later stages add sample chats: function(s, team, people, moment), people by handle.
fillers = []


def count(s):
    return s.scalar(select(func.count(DemoTeam.id))) or 0


def build(s, moment):
    """A new demo team with its people (and, from later stages, its chats). Returns the visitor."""
    team = DemoTeam(created_at=moment, expires_at=moment + LIFETIME)
    s.add(team)
    s.flush()
    # Nobody can log in to a demo person with a password: the hash is of a secret
    # nobody knows. Try Demo and the friend link log people in instead.
    unusable = generate_password_hash(secrets.token_urlsafe(24))
    people = {}
    for index, (handle, name) in enumerate(PEOPLE):
        people[handle] = User(username=f"d{team.id}.{handle}", name=name, password_hash=unusable,
                              demo_team_id=team.id, is_bot=index > 0, created_at=moment - timedelta(days=60),
                              # A random start, so a cookie from a deleted demo can never match.
                              session_version=secrets.randbelow(1_000_000) + 2)
        s.add(people[handle])
    s.flush()
    for fill in fillers:
        fill(s, team, people, moment)
    return people["mika"]


def visitor(s, team_id):
    return s.scalar(select(User).where(User.demo_team_id == team_id, User.is_bot.is_(False)))


def purge(s, moment):
    """Delete demo teams whose 24 hours are over (and, by cascade, all they hold)."""
    expired = s.scalars(select(DemoTeam.id).where(DemoTeam.expires_at <= moment)).all()
    if expired:
        s.execute(delete(DemoTeam).where(DemoTeam.id.in_(expired)))
    return len(expired)


@click.command("purge-demos")
@with_appcontext
def purge_demos_command():
    """Delete demos whose 24 hours are over."""
    s = db.session()
    removed = purge(s, utcnow())
    s.commit()
    click.echo(f"Deleted {removed} demo{'s' if removed != 1 else ''}.")


def init_app(app):
    app.cli.add_command(purge_demos_command)
