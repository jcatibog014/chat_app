"""The hub: who is connected, and sending to every open tab."""
from paperplane.hub import MAX_CONNECTIONS_PER_USER, Hub

from .live_helpers import fake_connection


def test_publish_reaches_every_tab_of_each_person_once():
    hub = Hub()
    tab1, tab2 = fake_connection(hub, 1), fake_connection(hub, 1)
    other = fake_connection(hub, 2)
    stranger = fake_connection(hub, 3)
    assert hub.publish({1, 2}, {"type": "message.new", "data": {"id": 7}}) == 3
    for connection in (tab1, tab2, other):
        assert connection.ws.sent == [{"type": "message.new", "data": {"id": 7}}]
    assert stranger.ws.sent == []


def test_too_many_tabs_are_refused():
    hub = Hub()
    for _ in range(MAX_CONNECTIONS_PER_USER):
        fake_connection(hub, 1)
    extra = fake_connection(hub, 1, register=False)
    assert not hub.register(extra)
    assert len(hub.connections(1)) == MAX_CONNECTIONS_PER_USER


def test_a_dead_socket_is_forgotten_when_sending_fails():
    hub = Hub()
    alive, dead = fake_connection(hub, 1), fake_connection(hub, 1)
    dead.ws.closed_with = (1006, "gone")
    assert hub.publish({1}, {"type": "x"}) == 1
    assert hub.connections(1) == [alive]


def test_unregister_twice_is_harmless_and_hooks_run_once():
    hub = Hub()
    seen = []
    hub.on_disconnect.append(lambda _hub, connection: seen.append(connection.id))
    connection = fake_connection(hub, 1)
    assert hub.unregister(connection)
    assert not hub.unregister(connection)
    assert seen == [connection.id] and not hub.is_connected(1)


def test_close_user_says_bye_and_closes_with_4001():
    hub = Hub()
    tabs = [fake_connection(hub, 1), fake_connection(hub, 1)]
    keep = fake_connection(hub, 2)
    hub.close_user(1)
    for tab in tabs:
        assert tab.ws.of_type("bye") and tab.ws.closed_with == (4001, "logged_out")
    assert not hub.is_connected(1) and hub.is_connected(2) and keep.ws.closed_with is None


def test_rate_limit_is_a_sliding_window():
    hub = Hub()
    assert all(hub.allow("send:1", 3, 10, now=t) for t in (0, 1, 2))
    assert not hub.allow("send:1", 3, 10, now=5)
    assert hub.allow("send:2", 3, 10, now=5)  # someone else's window
    assert hub.allow("send:1", 3, 10, now=10.5)  # the first try has left the window
