"""Who is online: worked out from the hub's open sockets, and told to the right people.

    Online   at least one open tab, and someone is looking at one (or looked less
             than 5 minutes ago)
    Away     tabs are open, but all of them have been hidden for 5 minutes
    Offline  no open tab. We wait 10 seconds first (GRACE): reloading a page closes
             its socket and opens a new one a moment later, and friends shouldn't
             see you flicker offline and back.

The browser says when its tab is hidden or shown ({"type": "presence",
"data": {"visible": false}}). A ticker thread looks every 2 seconds for
people whose grace or away time is up (tick(), which tests call by hand).

WHO hears it (authorization again): only people who share a chat with you
(contacts()), never the whole site; never anyone if you turned off "Show
when I'm online" in Settings, and never someone you blocked (stage 7).
Turning it off looks exactly like being offline, with no "last seen".
"""
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import aliased

from . import db
from .dates import iso, utcnow
from .models import Conversation, Membership, User

GRACE = 10          # seconds before a closed last tab counts as offline
AWAY_AFTER = 300    # seconds with every tab hidden before "away"
TICK_EVERY = 2
KEY = "paperplane_presence"
ONLINE, AWAY, OFFLINE = "online", "away", "offline"


def contacts(s, user_id):
    """Everyone who shares an active chat with this person (they may see each other's status)."""
    mine, theirs = aliased(Membership), aliased(Membership)
    query = (select(theirs.user_id).distinct()
             .join(mine, mine.conversation_id == theirs.conversation_id)
             .join(Conversation, Conversation.id == theirs.conversation_id)
             .where(mine.user_id == user_id, mine.removed_at.is_(None), theirs.removed_at.is_(None),
                    Conversation.deleted_at.is_(None), theirs.user_id != user_id))
    ids = set(s.scalars(query))
    for hide in audience_filters:
        ids = hide(s, user_id, ids)
    return ids


# Later stages take people out of someone's audience (blocking): function(s, user_id, ids) -> ids.
audience_filters = []


class Presence:
    def __init__(self, app, hub, clock=time.monotonic):
        self.app = app
        self.hub = hub
        self.clock = clock
        self.lock = threading.RLock()
        self.status = {}        # user_id -> ONLINE / AWAY (anyone missing is OFFLINE)
        self.leaving = {}       # user_id -> when they count as offline (their last tab closed)
        self.hidden_since = {}  # connection id -> when its tab was hidden
        hub.on_connect.append(self._connected)
        hub.on_disconnect.append(self._disconnected)

    # ---------- What the hub and the browser tell us ----------

    def _connected(self, _hub, connection):
        with self.lock:
            self.leaving.pop(connection.user_id, None)
        self.refresh(connection.user_id)

    def _disconnected(self, hub, connection):
        with self.lock:
            self.hidden_since.pop(connection.id, None)
            if not hub.is_connected(connection.user_id):
                # Not offline yet: a reload comes back within the grace time.
                self.leaving[connection.user_id] = self.clock() + GRACE
                return
        self.refresh(connection.user_id)

    def set_visible(self, connection, visible):
        with self.lock:
            connection.visible = visible
            if visible:
                self.hidden_since.pop(connection.id, None)
            else:
                self.hidden_since.setdefault(connection.id, self.clock())
        self.refresh(connection.user_id)

    # ---------- Working it out ----------

    def compute(self, user_id, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            tabs = self.hub.connections(user_id)
            if not tabs:
                deadline = self.leaving.get(user_id)
                if deadline is not None and now < deadline:
                    return self.status.get(user_id, OFFLINE)  # still in the grace time
                return OFFLINE
            looking = any(tab.visible or now - self.hidden_since.get(tab.id, now) < AWAY_AFTER for tab in tabs)
            return ONLINE if looking else AWAY

    def of(self, user_id):
        """The status pages show (hub memory only: no database)."""
        with self.lock:
            return self.status.get(user_id, OFFLINE)

    def refresh(self, user_id, now=None):
        """Work out the person's status; if it changed, tell their contacts."""
        new = self.compute(user_id, now)
        with self.lock:
            old = self.status.get(user_id, OFFLINE)
            if new == old:
                return False
            if new == OFFLINE:
                self.status.pop(user_id, None)
                self.leaving.pop(user_id, None)
            else:
                self.status[user_id] = new
        self.announce(user_id, new)
        return True

    def tick(self, now=None):
        """Grace and away times that are up. The ticker thread calls this every 2 seconds."""
        now = self.clock() if now is None else now
        with self.lock:
            due = {user_id for user_id, deadline in self.leaving.items() if deadline <= now}
            hidden_users = {tab.user_id for user_id in list(self.status)
                            for tab in self.hub.connections(user_id) if tab.id in self.hidden_since}
        for user_id in due | hidden_users:
            self.refresh(user_id, now)

    # ---------- Telling people ----------

    def announce(self, user_id, status):
        """Save "last seen" when someone goes offline, and tell their contacts (if they share it)."""
        with self.app.app_context():
            s = db.new_session(self.app)
            try:
                user = s.get(User, user_id)
                if user is None:
                    return
                if status == OFFLINE:
                    user.last_seen_at = utcnow()
                    s.commit()
                if not user.show_presence:
                    return
                audience = contacts(s, user_id)
                last_seen = iso(user.last_seen_at) if status == OFFLINE else None
            finally:
                s.close()
        self.hub.publish(audience, {"type": "presence",
                                    "data": {"user_id": user_id, "status": status, "last_seen": last_seen}})

    def hide(self, s, user):
        """"Show when I'm online" was turned off: to everyone, I go offline (no last seen)."""
        self.hub.publish(contacts(s, user.id), {"type": "presence",
                                                "data": {"user_id": user.id, "status": OFFLINE, "last_seen": None}})

    def show(self, s, user):
        """It was turned back on: tell my contacts my real status."""
        status = self.of(user.id)
        self.hub.publish(contacts(s, user.id), {"type": "presence", "data": {
            "user_id": user.id, "status": status,
            "last_seen": iso(user.last_seen_at) if status == OFFLINE else None}})

    def snapshot_for(self, s, user_id):
        """The statuses of my contacts who share theirs (sent when my socket opens)."""
        people = contacts(s, user_id)
        if not people:
            return {}
        sharing = set(s.scalars(select(User.id).where(User.id.in_(people), User.show_presence.is_(True))))
        return {person: self.of(person) for person in sharing}

    # ---------- The ticker ----------

    def start_ticker(self):
        def run():
            while True:
                time.sleep(TICK_EVERY)
                try:
                    self.tick()
                except Exception:  # never let the ticker die
                    pass
        threading.Thread(target=run, name="presence-ticker", daemon=True).start()


def get(app):
    return app.extensions[KEY]


def status_for_page(viewer, person):
    """The dot a page draws for `person`: their status if they share it, else offline."""
    from flask import current_app
    if person is None or not person.show_presence:
        return OFFLINE
    tracker = current_app.extensions.get(KEY)
    return tracker.of(person.id) if tracker else OFFLINE


def send_snapshot(app, connection):
    """Right after "hello": my contacts' statuses, so the dots are right even if someone
    changed between the page loading and the socket opening."""
    with app.app_context():
        s = db.new_session(app)
        try:
            statuses = get(app).snapshot_for(s, connection.user_id)
        finally:
            s.close()
    connection.send_json({"type": "presence.snapshot",
                          "data": {"statuses": {str(person): status for person, status in statuses.items()}}})


# ---------- Settings: "Show when I'm online" ----------

def save_show_presence(s, user, form):
    user.show_presence = form.get("show_presence") == "1"
    tracker = get(s.info["app"])
    if user.show_presence:
        tracker.show(s, user)
        return {}, "People you chat with can see when you're online again."
    tracker.hide(s, user)
    return {}, "Nobody can see when you're online now."


def words_for(person):
    """The line under a DM's name: Online / Away / Last seen 5 min ago / @handle."""
    from .formatting import ago
    status = status_for_page(None, person)
    if status == ONLINE:
        return "Online"
    if status == AWAY:
        return "Away"
    if person.show_presence and person.last_seen_at:
        return f"Last seen {ago(person.last_seen_at)}"
    return f"@{person.handle}"


def init_app(app):
    from . import live
    from .hub import HUB
    from .views import settings
    tracker = Presence(app, app.extensions[HUB])
    app.extensions[KEY] = tracker
    if not app.testing:
        tracker.start_ticker()
    if send_snapshot not in live.hello_hooks:
        live.hello_hooks.append(send_snapshot)
    settings.actions["show_presence"] = save_show_presence
    if settings_section not in settings.extras:
        settings.extras.append(settings_section)
    app.jinja_env.globals["presence_of"] = lambda person: status_for_page(None, person)
    app.jinja_env.globals["presence_words"] = words_for


def settings_section(s, user):
    return {"settings_sections": ["_settings_presence.html"]}
