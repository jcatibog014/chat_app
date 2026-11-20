"""The hub: who is connected right now, and sending events to their open tabs.

Every open WebSocket is a Connection. One person can have several (a laptop,
a phone, three tabs), so the hub keeps a set per person:

    hub.by_user = {4: {<tab 1>, <tab 2>}, 9: {<phone>}}

publish(user_ids, payload) turns the event into JSON ONCE and sends it to
every connection of those people. outbox.py calls it after a commit.

The hub lives in this process's memory. That is why Paper Plane runs ONE
server process (render.yaml): if a second process took some of the sockets,
a message saved by process A would never reach a tab connected to process B.
To grow past one process, publish() is the single place to change: it would
send the event through PostgreSQL's LISTEN/NOTIFY (or Redis), and every
process would deliver it to its own connections. (Explained, not built.)

Threads: each socket runs in its own thread (live.py), and any thread may
publish. The hub's lock guards the sets; each Connection has its own lock so
two threads never write into one socket at the same time.
"""
import itertools
import json
import threading
import time
from collections import defaultdict, deque

MAX_CONNECTIONS_PER_USER = 5

_ids = itertools.count(1)


class Connection:
    """One open WebSocket: one browser tab of one person."""

    def __init__(self, ws, user_id, session_version, now=None):
        self.id = next(_ids)
        self.ws = ws
        self.user_id = user_id
        # The cookie's session_version when the socket opened. Every event checks it is still
        # the user's (events.py): a password change makes this socket useless at once.
        self.session_version = session_version
        self.opened_at = now if now is not None else time.monotonic()
        self.last_heard = self.opened_at
        self.visible = True     # is the tab in front? (presence.py, stage 4)
        self.closed = False
        self._lock = threading.Lock()

    def send(self, text):
        """Send one frame. False if the socket is gone (the caller forgets it)."""
        with self._lock:
            if self.closed:
                return False
            try:
                self.ws.send(text)
                return True
            except Exception:  # a broken pipe, a reset: the socket is dead
                self.closed = True
                return False

    def send_json(self, payload):
        return self.send(json.dumps(payload, separators=(",", ":")))

    def close(self, code=1000, reason=""):
        with self._lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.ws.close(code, reason)
            except Exception:
                pass

    def __repr__(self):
        return f"<Connection {self.id} user={self.user_id}>"


class Hub:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.by_user = defaultdict(set)
        # Hooks for presence (stage 4): function(hub, connection), called outside the lock.
        self.on_connect = []
        self.on_disconnect = []
        self._windows = {}

    # ---------- Who is connected ----------

    def register(self, connection):
        """Add a new socket. False when the person already has too many open."""
        with self.lock:
            mine = self.by_user[connection.user_id]
            if len(mine) >= MAX_CONNECTIONS_PER_USER:
                if not mine:
                    del self.by_user[connection.user_id]
                return False
            mine.add(connection)
        for hook in self.on_connect:
            hook(self, connection)
        return True

    def unregister(self, connection):
        """Forget a socket (safe to call twice: the socket's own thread and a failed send may both)."""
        with self.lock:
            mine = self.by_user.get(connection.user_id)
            if not mine or connection not in mine:
                return False
            mine.discard(connection)
            if not mine:
                del self.by_user[connection.user_id]
        for hook in self.on_disconnect:
            hook(self, connection)
        return True

    def connections(self, user_id):
        with self.lock:
            return list(self.by_user.get(user_id, ()))

    def is_connected(self, user_id):
        with self.lock:
            return bool(self.by_user.get(user_id))

    def connected_users(self):
        with self.lock:
            return set(self.by_user)

    def count(self):
        with self.lock:
            return sum(len(sockets) for sockets in self.by_user.values())

    # ---------- Sending ----------

    def publish(self, user_ids, payload):
        """Send an event to every open tab of these people. Returns how many tabs got it.

        The JSON is made once, however many tabs there are. A tab whose socket has died
        is forgotten here; its own thread will notice too (unregister is safe twice)."""
        text = json.dumps(payload, separators=(",", ":"))
        with self.lock:
            targets = [connection for user_id in user_ids for connection in self.by_user.get(user_id, ())]
        sent = 0
        for connection in targets:
            if connection.send(text):
                sent += 1
            else:
                self.unregister(connection)
        return sent

    def close_user(self, user_id, code=4001, reason="logged_out"):
        """Close every socket of a person (a password change, "Log out other devices").
        The browser reloads on code 4001 and finds itself at the login page."""
        for connection in self.connections(user_id):
            connection.send_json({"type": "bye", "data": {"reason": reason}})
            connection.close(code, reason)
            self.unregister(connection)

    # ---------- Rate limits for events (in memory: quick, per process) ----------

    def allow(self, key, limit, window_seconds, now=None):
        """A sliding window: at most `limit` hits in the last `window_seconds`."""
        now = self.clock() if now is None else now
        with self.lock:
            hits = self._windows.setdefault(key, deque())
            while hits and hits[0] <= now - window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            if len(self._windows) > 50_000:  # never grow without end
                self._windows.clear()
            return True


HUB = "paperplane_hub"


def get(app):
    return app.extensions[HUB]
