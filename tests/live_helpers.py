"""Helpers for the live tests: a fake socket, and a real server with real WebSocket clients."""
import json
import threading
import time

from simple_websocket import Client, ConnectionClosed
from simple_websocket.errors import ConnectionError as Refused  # noqa: F401  (a refused handshake)
from werkzeug.serving import make_server

from paperplane.hub import Connection


class BrowserLikeClient(Client):
    """simple-websocket's client sends "Host: 127.0.0.1" without the port; browsers send
    "127.0.0.1:5015". The Origin check compares with the Host, so act like a browser."""

    def handshake(self):
        self.host = f"{self.host}:{self.port}"
        super().handshake()


class FakeSocket:
    """Stands in for a WebSocket: remembers what was sent to it."""

    def __init__(self):
        self.sent = []
        self.closed_with = None

    def send(self, text):
        if self.closed_with is not None:
            raise ConnectionError("closed")
        self.sent.append(json.loads(text))

    def close(self, code=1000, reason=""):
        self.closed_with = (code, reason)

    def of_type(self, kind):
        return [frame for frame in self.sent if frame["type"] == kind]


def fake_connection(hub, user_id, session_version=1, register=True):
    connection = Connection(FakeSocket(), user_id, session_version)
    if register:
        assert hub.register(connection)
    return connection


class Server:
    """The real app on a free 127.0.0.1 port, in a thread (WebSockets need a real server)."""

    def __init__(self, app):
        self.app = app
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.port = self.server.port
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.clients = []

    def connect(self, cookie, origin=None, extra_headers=None):
        """A WebSocket client with this login cookie. Raises ConnectionError when refused."""
        headers = {"Origin": origin or self.base}
        if cookie:
            headers["Cookie"] = f"paperplane_session={cookie}"
        headers.update(extra_headers or {})
        client = LiveClient(BrowserLikeClient.connect(f"ws://127.0.0.1:{self.port}/ws", headers=headers))
        self.clients.append(client)
        return client

    def stop(self):
        for client in self.clients:
            client.close()
        self.server.shutdown()


class LiveClient:
    """A WebSocket client that speaks the event protocol."""

    def __init__(self, ws):
        self.ws = ws
        self.inbox = []
        self.refs = 0

    def send(self, kind, data=None, ref=True):
        frame = {"type": kind, "data": data or {}}
        if ref:
            self.refs += 1
            frame["ref"] = f"t{self.refs}"
        self.ws.send(json.dumps(frame))
        return frame.get("ref")

    def request(self, kind, data=None, timeout=5):
        """Send and wait for the answer to THIS request (other frames are kept in the inbox)."""
        ref = self.send(kind, data)
        return self.wait(lambda frame: frame.get("ref") == ref, timeout)

    def wait(self, matches, timeout=5):
        for frame in list(self.inbox):
            if matches(frame):
                self.inbox.remove(frame)
                return frame
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.ws.receive(timeout=max(0.01, deadline - time.monotonic()))
            if text is None:
                continue
            frame = json.loads(text)
            if matches(frame):
                return frame
            self.inbox.append(frame)
        raise AssertionError(f"Nothing matched within {timeout} s. Inbox: {self.inbox}")

    def wait_for(self, kind, timeout=5, **fields):
        return self.wait(lambda frame: frame["type"] == kind and all(
            frame.get("data", {}).get(name) == value for name, value in fields.items()), timeout)

    def closed(self, timeout=5):
        """True once the server has closed this socket."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                text = self.ws.receive(timeout=0.2)
            except ConnectionClosed:
                return True
            if text is not None:
                self.inbox.append(json.loads(text))
            elif not self.ws.connected:
                return True
        return False

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
