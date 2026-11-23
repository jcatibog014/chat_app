"""Presence (online, away, offline with a grace time) and typing, with a pretend clock."""
import json

import pytest

from paperplane import chats, db, events, presence
from paperplane.dates import utcnow
from paperplane.hub import HUB, Hub
from paperplane.models import User
from paperplane.presence import AWAY_AFTER, GRACE, Presence

from .conftest import browser, make_user, post, words
from .live_helpers import fake_connection


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def setup(app):
    """Maria and Ana share a DM; Ben shares nothing. A hub and tracker with a pretend clock."""
    ids = {name: make_user(app, name, name.title()) for name in ("maria", "ana", "ben")}
    with app.app_context():
        s = db.session()
        conversation, _ = chats.start_dm(s, s.get(User, ids["maria"]), s.get(User, ids["ana"]), utcnow())
        s.commit()
        chat_id = conversation.id
    clock = Clock()
    hub = Hub(clock=clock)
    tracker = Presence(app, hub, clock=clock)
    return {"ids": ids, "hub": hub, "tracker": tracker, "clock": clock, "chat": chat_id}


def statuses_heard(connection, user_id):
    return [frame["data"]["status"] for frame in connection.ws.of_type("presence") if frame["data"]["user_id"] == user_id]


def test_opening_a_tab_tells_contacts_only(setup):
    hub, ids = setup["hub"], setup["ids"]
    ana_tab, ben_tab = fake_connection(hub, ids["ana"]), fake_connection(hub, ids["ben"])
    fake_connection(hub, ids["maria"])
    assert statuses_heard(ana_tab, ids["maria"]) == ["online"]
    assert statuses_heard(ben_tab, ids["maria"]) == []  # Ben shares no chat with Maria
    fake_connection(hub, ids["maria"])  # a second tab changes nothing
    assert statuses_heard(ana_tab, ids["maria"]) == ["online"]


def test_offline_waits_for_the_grace_time_and_saves_last_seen(app, setup):
    hub, ids, tracker, clock = setup["hub"], setup["ids"], setup["tracker"], setup["clock"]
    ana_tab = fake_connection(hub, ids["ana"])
    maria_tab = fake_connection(hub, ids["maria"])
    hub.unregister(maria_tab)
    tracker.tick()
    assert tracker.of(ids["maria"]) == "online"  # a reload comes back within 10 s
    clock.now += GRACE + 1
    tracker.tick()
    assert tracker.of(ids["maria"]) == "offline"
    (offline,) = [frame for frame in ana_tab.ws.of_type("presence") if frame["data"]["status"] == "offline"]
    assert offline["data"]["last_seen"].endswith("Z")
    with app.app_context():
        assert db.session().get(User, ids["maria"]).last_seen_at is not None


def test_a_reload_within_the_grace_time_never_shows_offline(setup):
    hub, ids, tracker, clock = setup["hub"], setup["ids"], setup["tracker"], setup["clock"]
    ana_tab = fake_connection(hub, ids["ana"])
    old = fake_connection(hub, ids["maria"])
    hub.unregister(old)
    clock.now += 3
    fake_connection(hub, ids["maria"])  # the reloaded page's socket
    clock.now += GRACE * 2
    tracker.tick()
    assert statuses_heard(ana_tab, ids["maria"]) == ["online"]


def test_away_after_every_tab_is_hidden_for_five_minutes(setup):
    hub, ids, tracker, clock = setup["hub"], setup["ids"], setup["tracker"], setup["clock"]
    ana_tab = fake_connection(hub, ids["ana"])
    first, second = fake_connection(hub, ids["maria"]), fake_connection(hub, ids["maria"])
    tracker.set_visible(first, False)
    clock.now += AWAY_AFTER + 1
    tracker.tick()
    assert tracker.of(ids["maria"]) == "online"  # the other tab is still in front
    tracker.set_visible(second, False)
    clock.now += AWAY_AFTER + 1
    tracker.tick()
    assert tracker.of(ids["maria"]) == "away"
    tracker.set_visible(first, True)
    assert statuses_heard(ana_tab, ids["maria"]) == ["online", "away", "online"]


def test_hiding_presence_tells_nobody_and_looks_offline(app, setup):
    hub, ids, tracker = setup["hub"], setup["ids"], setup["tracker"]
    with app.app_context():
        s = db.session()
        s.get(User, ids["maria"]).show_presence = False
        s.commit()
    ana_tab = fake_connection(hub, ids["ana"])
    fake_connection(hub, ids["maria"])
    assert statuses_heard(ana_tab, ids["maria"]) == []
    with app.app_context():
        s = db.session()
        maria = s.get(User, ids["maria"])
        assert presence.status_for_page(None, maria) == "offline"
        assert tracker.snapshot_for(s, ids["ana"]) == {}


def test_snapshot_lists_contacts_who_share(app, setup):
    hub, ids, tracker = setup["hub"], setup["ids"], setup["tracker"]
    fake_connection(hub, ids["ana"])
    with app.app_context():
        s = db.session()
        assert tracker.snapshot_for(s, ids["maria"]) == {ids["ana"]: "online"}
        assert tracker.snapshot_for(s, ids["ben"]) == {}


def test_the_dm_header_says_online_or_last_seen(app):
    maria_id = make_user(app, "maria", "Maria Santos")
    ana_id = make_user(app, "ana", "Ana Reyes")
    maria = browser(app, maria_id)
    response = post(maria, "/chats/new/dm", {"username": "ana"})
    chat = response.headers["Location"]
    assert "@ana" in words(maria.get(chat))  # never seen yet
    hub = app.extensions[HUB]
    tab = fake_connection(hub, ana_id)
    assert "Online" in words(maria.get(chat))
    assert 'dot dot-online" data-presence-dot="%d"' % ana_id in maria.get("/chats").get_data(as_text=True)
    hub.unregister(tab)
    presence.get(app).tick(presence.get(app).clock() + GRACE + 1)
    assert "Last seen just now" in words(maria.get(chat))


def test_the_settings_switch(app):
    maria_id = make_user(app, "maria")
    client = browser(app, maria_id)
    assert "Show When I'm Online" in words(client.get("/settings/privacy"))
    response = post(client, "/settings", {"action": "show_presence", "show_presence": "0"}, follow_redirects=True)
    assert "Nobody can see when you're online now." in words(response)
    with app.app_context():
        assert db.session().get(User, maria_id).show_presence is False
    post(client, "/settings", {"action": "show_presence", "show_presence": "1"})
    with app.app_context():
        assert db.session().get(User, maria_id).show_presence is True


def test_the_page_dot_comes_from_the_apps_tracker(app):
    """The tracker made by create_app is wired to the hub: a socket makes the person online."""
    user_id = make_user(app, "maria")
    hub = app.extensions[HUB]
    connection = fake_connection(hub, user_id)
    assert presence.get(app).of(user_id) == "online"
    hub.unregister(connection)
    assert presence.get(app).of(user_id) == "online"  # grace


# ---------- Typing ----------

def typing(app, connection, chat_id):
    return events.dispatch(app, connection, json.dumps({"type": "typing", "ref": "t", "data": {"conversation_id": chat_id}}))[0]


def test_typing_reaches_the_others_but_not_the_typer(app, setup):
    ids, chat = setup["ids"], setup["chat"]
    hub = app.extensions[HUB]
    maria_tab, ana_tab, ben_tab = (fake_connection(hub, ids[name]) for name in ("maria", "ana", "ben"))
    assert typing(app, maria_tab, chat)["type"] == "ack"
    (news,) = ana_tab.ws.of_type("typing")
    assert news["data"] == {"conversation_id": chat, "user_id": ids["maria"], "name": "Maria"}
    assert not maria_tab.ws.of_type("typing") and not ben_tab.ws.of_type("typing")


def test_typing_is_thinned_out_on_the_server(app, setup):
    ids, chat = setup["ids"], setup["chat"]
    hub = app.extensions[HUB]
    maria_tab, ana_tab = fake_connection(hub, ids["maria"]), fake_connection(hub, ids["ana"])
    for _ in range(5):
        typing(app, maria_tab, chat)
    assert len(ana_tab.ws.of_type("typing")) == 1


def test_a_stranger_cant_type_into_a_chat(app, setup):
    ids, chat = setup["ids"], setup["chat"]
    hub = app.extensions[HUB]
    ana_tab, ben_tab = fake_connection(hub, ids["ana"]), fake_connection(hub, ids["ben"])
    assert typing(app, ben_tab, chat)["code"] == "not_found"
    assert not ana_tab.ws.of_type("typing")
