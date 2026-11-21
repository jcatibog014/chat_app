"""The demo crew: Ana, Ben, Carlo and Dana come alive while a visitor is here.

A demo is no fun alone: nobody comes online, nobody types, nobody answers.
So while the visitor (Mika) has a tab open, this crew plays the made-up
teammates through the REAL code paths, exactly like people would:

- they come online by opening a pretend socket in the hub (BotConnection),
  so presence.py announces them like anyone else, and they can look away;
- they read (chats.mark_read), type (the same "typing" event) and send
  (chats.send_message), so every live event goes out through the outbox;
- they answer the visitor's messages after a short, human-looking wait, and
  say a few scripted lines on their own.

Nothing here touches the database or sockets in a special way: if the crew
works, the app works.

A real person can take a teammate over with a friend link (views/demo.py:
open it in a private window or on a phone). The crew stops playing anyone who
has a real socket open, and only reacts to messages from real people.

When the last real tab of a demo closes, the crew waits a minute, then the
teammates go offline and the demo goes quiet: no work, no database calls.
The crew thread wakes twice a second; tests call step(now) by hand.
"""
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from sqlalchemy import select

from . import chats, db, outbox
from .dates import utcnow
from .hub import Connection
from .models import Conversation, Membership, User

KEY = "paperplane_crew"
STEP_EVERY = 0.5
QUIET_AFTER = 60   # seconds with no real tab before the crew stops playing a demo


class BotSocket:
    """A pretend socket: the crew doesn't read what the team is sent."""

    def send(self, text):
        pass

    def close(self, *args):
        pass


class BotConnection(Connection):
    """A teammate's pretend tab. Presence counts it like a real one."""
    played = True


# What the teammates say back, by what the message is about. The first match wins, so a
# topic ("Hi! Is the logo ready?") beats a plain greeting.
REPLIES = [
    (("thank", "salamat", "thanks"),
     ["You're welcome!", "Anytime!", "No problem at all."]),
    (("hike", "saturday", "batulao", "trail"),
     ["Can't wait for Saturday!", "I'll bring extra water.", "Don't forget your jacket!"]),
    (("logo", "design", "palette", "colour", "color", "mockup"),
     ["Send it over, I'd love to see it.", "The blue looks great on screen.", "Let's show it at the review."]),
    (("lunch", "coffee", "eat", "pancit", "kape"),
     ["Yes please, I'm hungry.", "Coffee sounds perfect right now.", "Count me in!"]),
    (("hello", "hi", "hey", "kumusta", "good morning", "morning"),
     ["Hi Mika!", "Hey! What's up?", "Hello! How's your day going?"]),
    (("?",),
     ["Good question. Let me check and get back to you.", "Hmm, I think so!", "Not sure yet. I'll ask Ana."]),
]
FALLBACK = ["Nice!", "Got it.", "Haha, true.", "Sounds good to me.", "Okay, noted!", "I'll take a look later."]

# What happens by itself once a visitor arrives: (seconds after, action, who, chat title, words).
SCRIPT = [
    (0.5, "online", "ana", None, None),
    (1.0, "online", "ben", None, None),
    (14, "online", "carlo", None, None),
    (22, "say", "carlo", "Papel Studio", "The printer works again, by the way."),
    (40, "hide", "ben", None, None),   # Ben looks away: "Away" five minutes later
    (75, "say", "ana", "Papel Studio", "Mika, are you free for a quick call at 4?"),
    (120, "online", "dana", None, None),
    (128, "say", "dana", "Weekend Hike", "I bought trail mix for everyone!"),
]


def reply_for(text, rng):
    lowered = (text or "").lower()
    words = set(lowered.replace("?", " ? ").replace("!", " ").replace(".", " ").replace(",", " ").split())
    for keys, choices in REPLIES:
        if any(key in words or (len(key) > 3 and key in lowered) for key in keys):
            return rng.choice(choices)
    return rng.choice(FALLBACK)


@dataclass(order=True)
class Job:
    due: float
    order: int
    action: str = field(compare=False)
    who: int = field(compare=False)            # the teammate's user id
    chat_id: int | None = field(compare=False, default=None)
    words: str | None = field(compare=False, default=None)


class TeamPlay:
    """One demo being played: its teammates, their pretend tabs, and what is due."""

    def __init__(self, team_id, now, rng):
        self.team_id = team_id
        self.rng = rng
        self.started = now
        self.last_human = now
        self.jobs = []
        self.tabs = {}           # teammate id -> BotConnection
        self.replying = set()    # chats with a reply on its way (one at a time per chat)
        self.order = 0

    def add(self, due, action, who, chat_id=None, words=None):
        self.order += 1
        self.jobs.append(Job(due, self.order, action, who, chat_id, words))


class Crew:
    def __init__(self, app, hub, clock=time.monotonic, seed=None):
        self.app = app
        self.hub = hub
        self.clock = clock
        self.seed = seed
        self.lock = threading.RLock()
        self.teams = {}       # team id -> TeamPlay
        self.team_of = {}     # user id -> demo team id (or None): people don't change teams
        self.heard_queue = deque()

    # ---------- What happened ----------

    def heard(self, team_id, chat_id, sender_id, text):
        """A real person sent a message in a demo (chats.after_send_hooks)."""
        self.heard_queue.append((team_id, chat_id, sender_id, text))

    # ---------- Who is here ----------

    def is_real(self, connection):
        return not getattr(connection, "played", False)

    def has_real_tab(self, user_id):
        return any(self.is_real(tab) for tab in self.hub.connections(user_id))

    def teams_with_people(self, s):
        """Demo teams that have at least one real tab open right now."""
        users = {user_id for user_id in self.hub.connected_users() if self.has_real_tab(user_id)}
        unknown = [user_id for user_id in users if user_id not in self.team_of]
        if unknown:
            for user_id, team_id in s.execute(select(User.id, User.demo_team_id).where(User.id.in_(unknown))):
                self.team_of[user_id] = team_id
        return {self.team_of.get(user_id) for user_id in users} - {None}

    # ---------- The loop ----------

    def step(self, now=None):
        """Start and stop demos, answer what was heard, and do what is due."""
        now = self.clock() if now is None else now
        with self.lock, self.app.app_context():
            s = db.new_session(self.app)
            try:
                live = self.teams_with_people(s)
                for team_id in live:
                    if team_id not in self.teams:
                        self.start(s, team_id, now)
                    self.teams[team_id].last_human = now
                for team_id, play in list(self.teams.items()):
                    if team_id not in live and now - play.last_human > QUIET_AFTER:
                        self.stop(team_id)
                while self.heard_queue:
                    self.plan_reply(s, *self.heard_queue.popleft(), now)
            finally:
                s.close()
            for play in list(self.teams.values()):
                play.jobs.sort()
                while play.jobs and play.jobs[0].due <= now:
                    self.run(play, play.jobs.pop(0))

    def start(self, s, team_id, now):
        rng = random.Random(f"{self.seed}:{team_id}" if self.seed is not None else None)
        play = TeamPlay(team_id, now, rng)
        mates = {user.handle: user for user in s.scalars(select(User).where(User.demo_team_id == team_id,
                                                                                 User.is_bot.is_(True)))}
        titles = {c.title: c.id for c in s.scalars(select(Conversation).where(
            Conversation.demo_team_id == team_id, Conversation.kind == "group", Conversation.deleted_at.is_(None)))}
        for after, action, handle, title, words in SCRIPT:
            mate = mates.get(handle)
            if mate is None or (title and title not in titles):
                continue
            chat_id = titles.get(title)
            if action == "say":
                play.add(now + after - 3, "typing", mate.id, chat_id)
            play.add(now + after, action, mate.id, chat_id, words)
        self.teams[team_id] = play

    def stop(self, team_id):
        play = self.teams.pop(team_id, None)
        if play:
            for tab in play.tabs.values():
                self.hub.unregister(tab)

    def plan_reply(self, s, team_id, chat_id, sender_id, text, now):
        play = self.teams.get(team_id)
        if play is None or chat_id in play.replying:
            return
        mates = s.scalars(select(User).join(Membership, Membership.user_id == User.id).where(
            Membership.conversation_id == chat_id, Membership.removed_at.is_(None),
            User.is_bot.is_(True), User.id != sender_id)).all()
        mates = [mate for mate in mates if not self.has_real_tab(mate.id)]  # a friend plays them
        if not mates:
            return
        online = [mate for mate in mates if mate.id in play.tabs]
        mate = play.rng.choice(online or mates)
        wait = play.rng.uniform(5.0, 7.5)
        play.replying.add(chat_id)
        if mate.id not in play.tabs:
            play.add(now + 0.8, "online", mate.id)
        play.add(now + 1.2, "read", mate.id, chat_id)
        play.add(now + 1.8, "typing", mate.id, chat_id)
        play.add(now + wait - 2.5, "typing", mate.id, chat_id)
        play.add(now + wait, "reply", mate.id, chat_id, reply_for(text, play.rng))

    # ---------- Doing things, through the real code ----------

    def run(self, play, job):
        if job.action == "reply":
            play.replying.discard(job.chat_id)  # the chat may get a new reply after this one
        if job.action != "online" and self.has_real_tab(job.who):
            return  # a friend is playing this teammate now
        try:
            if job.action == "online":
                self.online(play, job.who)
            elif job.action == "hide":
                tab = play.tabs.get(job.who)
                if tab:
                    from . import presence
                    presence.get(self.app).set_visible(tab, False)
            elif job.action == "typing":
                self.with_session(lambda s: self.typing(s, job.who, job.chat_id))
            elif job.action == "read":
                self.with_session(lambda s: chats.mark_read(s, s.get(User, job.who), job.chat_id))
            elif job.action in ("say", "reply"):
                self.with_session(lambda s: self.say(s, job.who, job.chat_id, job.words))
        except Exception:
            # A teammate left a chat, the demo was deleted under us...: skip this line.
            pass

    def online(self, play, user_id):
        if user_id in play.tabs or self.has_real_tab(user_id):
            return
        with self.app.app_context():
            s = db.new_session(self.app)
            try:
                user = s.get(User, user_id)
                if user is None:
                    return
                tab = BotConnection(BotSocket(), user.id, user.session_version)
            finally:
                s.close()
        if self.hub.register(tab):
            play.tabs[user_id] = tab

    def with_session(self, work):
        s = db.new_session(self.app)
        s.info["crew"] = True  # so the crew never "hears" its own messages
        try:
            work(s)
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def typing(self, s, user_id, chat_id):
        user = s.get(User, user_id)
        conversation = s.get(Conversation, chat_id)
        others = chats.member_ids(s, conversation) - {user_id}
        outbox.emit(s, others, "typing", {"conversation_id": chat_id, "user_id": user_id, "name": user.first_name})

    def say(self, s, user_id, chat_id, words):
        user = s.get(User, user_id)
        chats.mark_read(s, user, chat_id)
        chats.send_message(s, user, chat_id, words, None, utcnow())

    # ---------- The thread ----------

    def start_thread(self):
        def run():
            while True:
                time.sleep(STEP_EVERY)
                try:
                    self.step()
                except Exception:  # never let the crew die
                    pass
        threading.Thread(target=run, name="demo-crew", daemon=True).start()


def notice(s, conversation, message, moment):
    """chats.after_send_hooks: a real person spoke in a demo chat; the crew may answer."""
    if s.info.get("crew") or conversation.demo_team_id is None or message.kind != "text":
        return
    app = s.info.get("app")
    crew = app.extensions.get(KEY) if app else None
    if crew is not None:
        crew.heard(conversation.demo_team_id, conversation.id, message.sender_id, message.body)


def get(app):
    return app.extensions[KEY]


def init_app(app):
    from .hub import HUB
    crew = Crew(app, app.extensions[HUB])
    app.extensions[KEY] = crew
    if notice not in chats.after_send_hooks:
        chats.after_send_hooks.append(notice)
    if app.config.get("RUN_CREW"):
        crew.start_thread()
