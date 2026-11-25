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


def chats_bump(s, conversation, moment):
    from . import chats
    return chats.bump(s, conversation, moment, new_message=True)


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

    # Group admin (stage 7): Mika runs Launch Plans: Carlo is an admin, and there's an invite link.
    from . import access
    from .models import InviteLink, Membership
    seq, version = chats_bump(s, launch, ago(moment, days=4, hours=1, minutes=20))
    s.add(Message(conversation_id=launch.id, seq=seq, kind="system", body="Mika made Carlo an admin",
                  created_at=ago(moment, days=4, hours=1, minutes=20), version=version))
    s.scalar(select(Membership).where(Membership.conversation_id == launch.id,
                                      Membership.user_id == carlo.id)).role = access.ADMIN
    s.add(InviteLink(conversation_id=launch.id, code=f"demo{team.id}-{secrets.token_urlsafe(12)}",
                     created_by_id=mika.id, created_at=ago(moment, days=2), expires_at=moment + timedelta(days=5),
                     max_uses=10, uses=2))
    read_up_to(s, launch, mika)

    # Pictures, files and a link preview (stage 8), made here so no file has to ship with the app.
    from . import files
    from .models import LinkPreview
    palette = by_text["Can someone check the new colour palette? It's in the shared folder."]
    files.attach(s, palette, carlo, files.prepare("kalye-palette.png", palette_picture()))
    checklist = s.scalar(select(Message).where(Message.conversation_id == launch.id,
                                               Message.body.like("Thanks, both!%")))
    files.attach(s, checklist, mika, files.prepare("launch-checklist.txt", CHECKLIST.encode()))
    trail = say(s, hike, dana, "Trail info for Saturday: https://en.wikipedia.org/wiki/Mount_Batulao",
                ago(moment, days=1, hours=7, minutes=20))
    s.add(LinkPreview(message_id=trail.id, url="https://en.wikipedia.org/wiki/Mount_Batulao", site_name="Wikipedia",
                      title="Mount Batulao", fetched_at=trail.created_at,
                      description="Mount Batulao is a mountain in Batangas, Philippines, popular with hikers "
                                  "for its rolling peaks and wide views."))
    read_up_to(s, hike, mika)

    # Find and notify (stage 9): Ana mentions Mika (a red @ on Papel Studio), and Mika muted
    # Weekend Hike, which has a new message (a grey badge, left out of the menu's number).
    from . import notify
    ask = say(s, studio, ana, "@mika can you bring the printed mockups on Thursday?", ago(moment, minutes=8))
    notify.record_mentions(s, studio, ask, moment)
    say(s, hike, ben, "The bus leaves at 5 AM sharp. Don't be late!", ago(moment, hours=3))
    s.scalar(select(Membership).where(Membership.conversation_id == hike.id,
                                      Membership.user_id == mika.id)).notify_off_until = notify.FOREVER

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

# ---------- Sample stories ----------

def sample_stories(s, team, people, moment):
    """Stories from today: a ring of each kind above Mika's chats, and Mika's own with Seen by."""
    from . import files
    from .models import CloseFriend, Conversation, Message, Story, StoryMute, StoryReaction, StoryView
    ana, ben, carlo, dana, mika = (people[handle] for handle in ("ana", "ben", "carlo", "dana", "mika"))

    def story(person, hours, kind="text", text=None, background=None, audience="everyone", picture=None):
        made = ago(moment, hours=hours)
        item = Story(user_id=person.id, kind=kind, text=text, background=background, audience=audience,
                     created_at=made, expires_at=made + timedelta(hours=24))
        if picture is not None:
            prepared = files.prepare("story.png", picture)
            item.image, item.thumb = prepared.data, prepared.thumb
            item.width, item.height = prepared.width, prepared.height
        s.add(item)
        s.flush()
        return item

    # Ana: a photo Mika hasn't seen (a coloured ring). Ben: a card Mika has seen (a grey ring).
    story(ana, 5, "photo", "Sunrise on the way to work.", picture=sunrise_picture())
    bens = story(ben, 7, text="Who else is ready for Saturday? Mt. Batulao, here we come!", background="teal")
    s.add(StoryView(story_id=bens.id, viewer_id=mika.id, viewed_at=ago(moment, hours=6)))
    # Carlo: for his Close Friends only, and Mika is on his list.
    s.add(CloseFriend(owner_id=carlo.id, friend_id=mika.id))
    story(carlo, 2, text="Close friends only: the new Kalye Coffee cups arrive tomorrow!", background="orange",
          audience="close")
    # Dana: Mika muted her stories (Settings > Privacy > Muted Stories shows her).
    story(dana, 4, text="Snacks for the hike: done.", background="blue")
    s.add(StoryMute(user_id=mika.id, muted_id=dana.id))

    # Mika's own: seen by four, Ben's heart, and Carlo's private reply in their chat.
    mine = story(mika, 3, text="Sketching the Kalye Coffee cups today.", background="purple")
    for minutes, person in ((170, ana), (150, ben), (125, carlo), (60, dana)):
        s.add(StoryView(story_id=mine.id, viewer_id=person.id, viewed_at=ago(moment, minutes=minutes)))
    s.add(StoryReaction(story_id=mine.id, user_id=ben.id, emoji="❤️", created_at=ago(moment, minutes=149)))
    with_carlo = s.scalar(select(Conversation).where(Conversation.demo_team_id == team.id, Conversation.kind == "dm",
                                                     Conversation.title.is_(None), Conversation.created_by_id == carlo.id))
    answer = say(s, with_carlo, carlo, "Those cups look great! Can I see the blue one?", ago(moment, minutes=124))
    answer.story_id, answer.is_story_reply = mine.id, True
    read_up_to(s, with_carlo, mika)
    read_up_to(s, with_carlo, carlo)
    # Mika's Close Friends: Ana and Ben.
    for person in (ana, ben):
        s.add(CloseFriend(owner_id=mika.id, friend_id=person.id))
    s.flush()


fillers.append(sample_stories)


# ---------- Photos, Files and Links: something in every chat's history ----------

def sample_media(s, team, people, moment):
    """More pictures and a file, on messages the sample chats already have, so each chat's
    Photos, Files and Links page shows something (Weekend Hike has its link, Launch Plans its file)."""
    from . import files
    from .models import Conversation, Message

    def message(chat_title, created_by, body):
        query = select(Message).join(Conversation, Conversation.id == Message.conversation_id).where(
            Conversation.demo_team_id == team.id, Message.body == body)
        if chat_title:
            query = query.where(Conversation.title == chat_title)
        else:
            query = query.where(Conversation.kind == "dm", Conversation.created_by_id == people[created_by].id)
        return s.scalar(query)

    for chat_title, created_by, body, sender, name, data in (
            ("Papel Studio", None, "I'll bring the printed mockups.", "ben", "cup-mockup.png", cup_picture()),
            (None, "ana", "Sure! I'll send them before 5.", "mika", "kalye-logo-final.png", logo_picture()),
            (None, "ben", "Yes, I'll bring it.", "ben", "batulao-trail-map.png", map_picture()),
            (None, "carlo", "Thanks! It took me all night.", "carlo", "kalye-palette-notes.txt", PALETTE_NOTES.encode())):
        files.attach(s, message(chat_title, created_by, body), people[sender], files.prepare(name, data))


fillers.append(sample_media)


# ---------- Notifications: a few days of things that concern Mika ----------

def sample_activity(s, team, people, moment):
    """Mika's Notifications page: new ones (stories, a reaction and a reply to her story; the
    @mention is made by notify.record_mentions itself) and earlier, read ones."""
    from .models import Activity, Conversation, Message, Story
    ana, ben, carlo, mika = (people[handle] for handle in ("ana", "ben", "carlo", "mika"))
    chat = {c.title: c for c in s.scalars(select(Conversation).where(
        Conversation.demo_team_id == team.id, Conversation.kind == "group"))}

    def said(title, body):
        return s.scalar(select(Message).where(Message.conversation_id == chat[title].id, Message.body == body))

    def line(actor, kind, when, read=True, **where):
        s.add(Activity(user_id=mika.id, actor_id=actor.id, kind=kind, created_at=when,
                       read_at=moment if read else None, **where))

    # Earlier (read): added to the groups, a reaction, a reply.
    line(ana, "group_added", ago(moment, days=30), conversation_id=chat["Papel Studio"].id)
    line(ben, "group_added", ago(moment, days=6), conversation_id=chat["Weekend Hike"].id)
    count_me_in = said("Weekend Hike", "Count me in too.")
    line(ben, "reaction", ago(moment, days=1, hours=7), conversation_id=chat["Weekend Hike"].id,
         message_id=count_me_in.id, emoji="👍")
    plan = said("Launch Plans", "Let's list everything we need for the website launch.")
    answer = said("Launch Plans", "Domain, hosting, and the photo shoot.")
    answer.reply_to_id = plan.id
    line(carlo, "reply", answer.created_at, conversation_id=chat["Launch Plans"].id, message_id=answer.id)

    # New: Ana's and Carlo's stories, and Ben's heart and Carlo's reply on Mika's own.
    story_of = {story.user_id: story for story in s.scalars(select(Story).where(
        Story.user_id.in_([ana.id, carlo.id, mika.id])))}
    line(ana, "story_new", story_of[ana.id].created_at, read=False, story_id=story_of[ana.id].id)
    line(carlo, "story_new", story_of[carlo.id].created_at, read=False, story_id=story_of[carlo.id].id)
    mine = story_of[mika.id]
    line(ben, "story_reaction", ago(moment, minutes=149), read=False, story_id=mine.id, emoji="❤️")
    reply = s.scalar(select(Message).where(Message.story_id == mine.id))
    line(carlo, "story_reply", reply.created_at, read=False, conversation_id=reply.conversation_id,
         message_id=reply.id, story_id=mine.id)
    s.flush()


fillers.append(sample_activity)

PALETTE_NOTES = """Kalye Coffee palette (Carlo)

Harbour blue  #1D4E6B  cups, signs
Sky blue      #2F80A8  menu headings
Sunny yellow  #F2C14E  stickers, sleeves
Clay orange   #E07A3F  sale tags
Cream         #F6EFE3  backgrounds
"""


CHECKLIST = """Website launch checklist (Papel Studio)

[x] Buy the domain
[x] Choose hosting
[ ] Photo shoot (Dana is booking the photographer)
[ ] Write the About page
[ ] Test on phones
[ ] Launch!
"""


def palette_picture():
    """Carlo's colour palette for Kalye Coffee: five swatches, drawn with Pillow (PNG bytes)."""
    import io

    from PIL import Image, ImageDraw
    colours = ["#1d4e6b", "#2f80a8", "#f2c14e", "#e07a3f", "#f6efe3"]
    picture = Image.new("RGB", (800, 420), "#ffffff")
    draw = ImageDraw.Draw(picture)
    width = 800 // len(colours)
    for index, colour in enumerate(colours):
        draw.rectangle([index * width, 0, (index + 1) * width, 330], fill=colour)
        draw.text((index * width + 18, 360), colour.upper(), fill="#13263b")
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def _png(picture):
    import io
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def cup_picture():
    """Ben's printed mockup: a Kalye Coffee cup in Carlo's blue and yellow."""
    from PIL import Image, ImageDraw
    picture = Image.new("RGB", (800, 800), "#f6efe3")
    draw = ImageDraw.Draw(picture)
    draw.ellipse([170, 640, 630, 720], fill="#e3d6c2")                       # the shadow
    draw.polygon([(230, 230), (570, 230), (530, 680), (270, 680)], fill="#1d4e6b")  # the cup
    draw.rectangle([215, 200, 585, 250], fill="#13384f")                     # the lid
    draw.rounded_rectangle([250, 380, 550, 520], radius=12, fill="#f2c14e")  # the sleeve
    draw.ellipse([355, 405, 445, 495], fill="#1d4e6b")                       # a coffee bean
    draw.line([(400, 412), (400, 488)], fill="#f2c14e", width=8)
    return _png(picture)


def logo_picture():
    """Mika's final logo for Kalye Coffee: a bean in a circle, with the shop's name."""
    from PIL import Image, ImageDraw, ImageFont
    picture = Image.new("RGB", (900, 600), "#ffffff")
    draw = ImageDraw.Draw(picture)
    draw.ellipse([330, 60, 570, 300], fill="#1d4e6b")
    draw.ellipse([395, 110, 505, 250], fill="#f2c14e")
    draw.arc([420, 110, 480, 250], 250, 110, fill="#1d4e6b", width=10)
    font = ImageFont.load_default(size=72)
    draw.text((450, 400), "KALYE COFFEE", fill="#13384f", font=font, anchor="mm")
    draw.text((450, 480), "since 2026", fill="#2f80a8", font=ImageFont.load_default(size=34), anchor="mm")
    return _png(picture)


def map_picture():
    """The hiking map Ben brings: hills, a dashed trail and the peak."""
    from PIL import Image, ImageDraw
    picture = Image.new("RGB", (900, 640), "#e4efd9")
    draw = ImageDraw.Draw(picture)
    for ring in range(6):  # contour lines around the peak
        grow = ring * 55
        draw.ellipse([520 - grow, 170 - grow * 0.6, 720 + grow, 330 + grow * 0.6], outline="#9fbf88", width=3)
    trail = [(90, 560), (200, 500), (260, 420), (380, 400), (450, 320), (560, 280), (620, 250)]
    for start, end in zip(trail, trail[1:]):
        steps = 6
        for step in range(0, steps, 2):
            a = (start[0] + (end[0] - start[0]) * step / steps, start[1] + (end[1] - start[1]) * step / steps)
            b = (start[0] + (end[0] - start[0]) * (step + 1) / steps, start[1] + (end[1] - start[1]) * (step + 1) / steps)
            draw.line([a, b], fill="#c4411a", width=7)
    draw.polygon([(620, 205), (650, 255), (590, 255)], fill="#5b4636")       # the peak
    draw.ellipse([75, 545, 105, 575], fill="#1d4e6b")                         # the start
    return _png(picture)


def sunrise_picture():
    """Ana's story photo: a sunrise over hills, drawn with Pillow (PNG bytes, 9:16)."""
    import io

    from PIL import Image, ImageDraw
    width, height = 540, 960
    picture = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(picture)
    top, bottom = (255, 183, 120), (233, 98, 120)
    for y in range(height):
        mix = y / height
        draw.line([(0, y), (width, y)], fill=tuple(round(a + (b - a) * mix) for a, b in zip(top, bottom)))
    draw.ellipse([170, 420, 370, 620], fill="#fff1c9")
    draw.polygon([(0, 640), (150, 520), (300, 610), (430, 500), (540, 580), (540, 960), (0, 960)], fill="#7d3c6b")
    draw.polygon([(0, 760), (200, 650), (380, 740), (540, 680), (540, 960), (0, 960)], fill="#4a2350")
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


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
