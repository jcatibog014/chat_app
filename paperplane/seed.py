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


# ---------- Sample chats (stage 2) ----------

def add_chat(s, team, people, moment, kind, handles, title=None, owner=None):
    """A chat made in the past, straight into the tables (no live events: nobody is watching yet)."""
    from . import access, chats
    from .models import Conversation, Membership
    members = [people[handle] for handle in handles]
    conversation = Conversation(kind=kind, title=title, created_at=moment, demo_team_id=team.id,
                                created_by_id=people[owner or handles[0]].id,
                                dm_key=chats.dm_key(members[0].id, members[1].id) if kind == "dm" else None)
    s.add(conversation)
    s.flush()
    boss = people[owner or handles[0]]
    for person in members:
        role = access.OWNER if kind == "group" and person is boss else access.MEMBER
        s.add(Membership(conversation_id=conversation.id, user_id=person.id, role=role, joined_at=moment))
    s.flush()
    return conversation


def say(s, conversation, person, text, moment):
    """A past message, numbered like a real one (chats.bump)."""
    from . import chats
    from .models import Message
    seq, version = chats.bump(s, conversation, moment, new_message=True)
    message = Message(conversation_id=conversation.id, seq=seq, sender_id=person.id, kind="text", body=text,
                      created_at=moment, version=version, client_id=chats.new_client_id())
    s.add(message)
    s.flush()
    return message


def read_up_to(s, conversation, person, seq=None):
    from .models import Membership
    membership = s.scalar(select(Membership).where(Membership.conversation_id == conversation.id,
                                                   Membership.user_id == person.id))
    membership.last_read_seq = conversation.last_seq if seq is None else seq


def ago(moment, days=0, hours=0, minutes=0):
    return moment - timedelta(days=days, hours=hours, minutes=minutes)


def sample_chats(s, team, people, moment):
    """Papel Studio's chats: a few days of history, and some messages Mika hasn't read yet."""
    ana, ben, carlo, dana, mika = (people[handle] for handle in ("ana", "ben", "carlo", "dana", "mika"))

    studio = add_chat(s, team, people, ago(moment, days=30), "group", ["ana", "ben", "carlo", "dana", "mika"],
                      title="Papel Studio", owner="ana")
    say(s, studio, ana, "Good morning, team! The client review for the Kalye Coffee logo is on Thursday.", ago(moment, days=2, hours=3))
    say(s, studio, ben, "I'll bring the printed mockups.", ago(moment, days=2, hours=2, minutes=55))
    say(s, studio, carlo, "Can someone check the new colour palette? It's in the shared folder.", ago(moment, days=2, hours=2, minutes=40))
    say(s, studio, mika, "Looks great, Carlo. That blue is perfect for the cups.", ago(moment, days=2, hours=2, minutes=31))
    say(s, studio, dana, "Reminder: lunch is on me today. Pancit from the corner shop!", ago(moment, days=1, hours=5))
    say(s, studio, ana, "Dana, you are the best.", ago(moment, days=1, hours=4, minutes=58))
    read_up_to(s, studio, mika)
    say(s, studio, ben, "The printer is jammed again...", ago(moment, minutes=52))
    say(s, studio, carlo, "Turn it off and on again. Works every time.", ago(moment, minutes=49))
    say(s, studio, ana, "Thursday's review moved to 3 PM. Is everyone okay with that?", ago(moment, minutes=12))

    with_ana = add_chat(s, team, people, ago(moment, days=20), "dm", ["ana", "mika"])
    say(s, with_ana, ana, "Mika, can you send me the final logo files later?", ago(moment, days=1, hours=2))
    say(s, with_ana, mika, "Sure! I'll send them before 5.", ago(moment, days=1, hours=1, minutes=56))
    say(s, with_ana, ana, "Thank you!", ago(moment, days=1, hours=1, minutes=55))
    read_up_to(s, with_ana, mika)

    with_ben = add_chat(s, team, people, ago(moment, days=15), "dm", ["ben", "mika"])
    say(s, with_ben, mika, "Do you still have the hiking map from last time?", ago(moment, days=3, hours=6))
    say(s, with_ben, ben, "Yes, I'll bring it.", ago(moment, days=3, hours=5))
    read_up_to(s, with_ben, mika)
    say(s, with_ben, ben, "Are you coming to the hike on Saturday?", ago(moment, minutes=31))
    say(s, with_ben, ben, "We leave at 5 AM. Bring a jacket, it gets cold up there.", ago(moment, minutes=30))

    with_carlo = add_chat(s, team, people, ago(moment, days=10), "dm", ["carlo", "mika"])
    say(s, with_carlo, mika, "Nice work on the palette.", ago(moment, days=3, hours=1))
    say(s, with_carlo, carlo, "Thanks! It took me all night.", ago(moment, days=3, minutes=40))
    read_up_to(s, with_carlo, mika)

    hike = add_chat(s, team, people, ago(moment, days=6), "group", ["ben", "ana", "dana", "mika"],
                    title="Weekend Hike", owner="ben")
    say(s, hike, ben, "Mt. Batulao this Saturday?", ago(moment, days=1, hours=8))
    say(s, hike, ana, "I'm in.", ago(moment, days=1, hours=7, minutes=50))
    say(s, hike, dana, "In! I'll bring snacks.", ago(moment, days=1, hours=7, minutes=41))
    say(s, hike, mika, "Count me in too.", ago(moment, days=1, hours=7, minutes=30))
    read_up_to(s, hike, mika)

    launch = add_chat(s, team, people, ago(moment, days=8), "group", ["mika", "carlo", "dana"],
                      title="Launch Plans", owner="mika")
    say(s, launch, mika, "Let's list everything we need for the website launch.", ago(moment, days=4, hours=2))
    say(s, launch, carlo, "Domain, hosting, and the photo shoot.", ago(moment, days=4, hours=1, minutes=50))
    say(s, launch, dana, "I can book the photographer.", ago(moment, days=4, hours=1, minutes=45))
    read_up_to(s, launch, mika)

    # Message tools (stage 6): reactions, a reply, an edited and a deleted message.
    from .models import Message, Reaction
    by_text = {m.body: m for m in s.scalars(select(Message).where(Message.conversation_id.in_(
        [studio.id, launch.id, hike.id])))}
    lunch = by_text["Reminder: lunch is on me today. Pancit from the corner shop!"]
    for person, emoji in ((ana, "❤️"), (ben, "❤️"), (mika, "❤️"), (carlo, "🎉")):
        s.add(Reaction(message_id=lunch.id, user_id=person.id, emoji=emoji, created_at=lunch.created_at))
    s.add(Reaction(message_id=by_text["Turn it off and on again. Works every time."].id, user_id=ben.id,
                   emoji="😂", created_at=moment))
    s.add(Reaction(message_id=by_text["Count me in too."].id, user_id=ben.id, emoji="👍", created_at=moment))
    by_text["Looks great, Carlo. That blue is perfect for the cups."].reply_to_id = \
        by_text["Can someone check the new colour palette? It's in the shared folder."].id
    review = by_text["Thursday's review moved to 3 PM. Is everyone okay with that?"]
    review.edited_at = review.created_at + timedelta(minutes=1)
    oops = say(s, launch, carlo, "(sent to the wrong chat)", ago(moment, days=4, hours=1, minutes=40))
    oops.body, oops.deleted_at = "", oops.created_at + timedelta(minutes=1)
    say(s, launch, mika, "Thanks, both! I'll draft the launch checklist.", ago(moment, days=4, hours=1, minutes=30))
    read_up_to(s, launch, mika)

    # Dana was here two hours ago: her chats say "Last seen 2 hours ago" until the crew brings her in.
    dana.last_seen_at = ago(moment, hours=2)
    for person in (ana, ben, carlo):
        person.last_seen_at = ago(moment, minutes=20)

    # The others have read their own chats (so "Seen by" has something to show later).
    for conversation in (studio, with_ana, with_ben, with_carlo, hike, launch):
        for person in (ana, ben, carlo, dana):
            from .models import Membership
            membership = s.scalar(select(Membership).where(Membership.conversation_id == conversation.id,
                                                           Membership.user_id == person.id))
            if membership is not None:
                membership.last_read_seq = conversation.last_seq


fillers.append(sample_chats)


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
