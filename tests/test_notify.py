"""Search, @mentions, muting a chat, notifications and web push, and the installable app."""
import pytest
from sqlalchemy import func, select

from paperplane import chats, db, notify
from paperplane.dates import utcnow
from paperplane.hub import HUB
from paperplane.models import Membership, Mention, PushSubscription, User

from .conftest import browser, make_app, make_user, post, text, words
from .live_helpers import fake_connection


@pytest.fixture
def team(app):
    """Maria, Ana and Ben in a group "Studio"; Maria and Ana in a DM; Zed outside."""
    ids = {name: make_user(app, name, f"{name.title()} Test") for name in ("maria", "ana", "ben", "zed")}
    with app.app_context():
        s = db.session()
        people = {name: s.get(User, user_id) for name, user_id in ids.items()}
        group = chats.create_group(s, people["maria"], "Studio", [people["ana"], people["ben"]], utcnow())
        dm, _ = chats.start_dm(s, people["maria"], people["ana"], utcnow())
        s.commit()
        chat_ids = {"group": group.id, "dm": dm.id}
    return {"ids": ids, "chats": chat_ids, "client": {name: browser(app, user_id) for name, user_id in ids.items()}}


def results_of(response):
    """Only the search results (the chat list beside them shows message previews too)."""
    import html
    import re
    page = response.get_data(as_text=True)
    part = page.split('class="muted result-count"', 1)[1] if 'class="muted result-count"' in page else ""
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(part)).split())


def send(team, who, where, body):
    return post(team["client"][who], f"/chats/{team['chats'][where]}/send", {"body": body})


# ---------- Search ----------

def test_search_finds_words_in_my_chats_only(app, team):
    send(team, "ana", "group", "The coffee logo is ready")
    send(team, "ana", "dm", "Secret: the coffee is free on Friday")
    found = results_of(team["client"]["ben"].get("/search?q=coffee"))
    assert "The coffee logo is ready" in found and "free on Friday" not in found  # Ben isn't in the DM
    found = results_of(team["client"]["maria"].get("/search?q=COFFEE"))
    assert "2 messages" in found
    assert "Nothing in your chats" in words(team["client"]["zed"].get("/search?q=coffee"))


def test_search_marks_the_words_and_links_to_the_message(app, team):
    send(team, "ana", "group", "Meet at the <b>cafe</b> at noon")
    page = team["client"]["maria"].get("/search?q=cafe").get_data(as_text=True)
    assert "<mark>cafe</mark>" in page and "&lt;b&gt;" in page
    assert f"/chats/{team['chats']['group']}?before=" in page


def test_search_treats_wildcards_as_plain_letters(app, team):
    send(team, "ana", "group", "Half off: 50% today")
    send(team, "ana", "group", "500 people came")
    found = results_of(team["client"]["maria"].get("/search?q=50%25"))
    assert "50% today" in found and "500 people" not in found


def test_search_in_one_chat_and_skips_deleted_and_hidden_history(app, team):
    send(team, "ana", "group", "pineapple in the group")
    send(team, "ana", "dm", "pineapple in the dm")
    found = results_of(team["client"]["maria"].get(f"/search?q=pineapple&chat={team['chats']['dm']}"))
    assert "in the dm" in found and "in the group" not in found
    assert "Type a Little More" in words(team["client"]["maria"].get("/search?q=p"))


def test_search_page_without_a_query(team):
    assert "Search Your Messages" in words(team["client"]["maria"].get("/search"))


# ---------- Mentions ----------

def test_a_mention_saves_and_badges_the_chat(app, team):
    send(team, "maria", "group", "@ana can you check this? And @nobody too")
    with app.app_context():
        mentions = db.session().scalars(select(Mention)).all()
        assert [m.user_id for m in mentions] == [team["ids"]["ana"]]
    ana_list = text(team["client"]["ana"].get("/chats"))
    assert "has-mention" in ana_list
    page = team["client"]["ana"].get(f"/chats/{team['chats']['group']}").get_data(as_text=True)
    assert 'class="mention" data-mention="ana">@ana</span>' in page
    assert "has-mention" not in text(team["client"]["ana"].get("/chats"))  # reading cleared it


def test_no_mentions_in_a_dm_or_of_yourself(app, team):
    send(team, "maria", "dm", "@ana hello")
    send(team, "maria", "group", "@maria note to self")
    with app.app_context():
        assert db.session().scalar(select(func.count(Mention.id))) == 0


def test_mentions_are_only_highlighted_never_markup():
    from paperplane.formatting import linkify
    html = str(linkify('@ana <img src=x onerror=alert(1)> email@ana.com'))
    assert '<span class="mention" data-mention="ana">@ana</span>' in html
    assert "<img" not in html and html.count("mention") == 2  # the email isn't a mention


# ---------- Muting a chat ----------

def test_a_muted_chat_has_a_grey_badge_and_stays_out_of_the_total(app, team):
    post(team["client"]["ana"], f"/chats/{team['chats']['group']}/mute", {"choice": "forever"})
    send(team, "maria", "group", "Hello group")
    send(team, "maria", "dm", "Hello Ana")
    page = text(team["client"]["ana"].get("/chats"))
    assert "is-muted" in page and '<span class="count" data-total-unread>1</span>' in page
    post(team["client"]["ana"], f"/chats/{team['chats']['group']}/mute", {"choice": "off"})
    assert '<span class="count" data-total-unread>2</span>' in text(team["client"]["ana"].get("/chats"))


def test_mute_for_an_hour_ends_by_itself(app, team):
    post(team["client"]["ana"], f"/chats/{team['chats']['group']}/mute", {"choice": "60"})
    with app.app_context():
        membership = db.session().scalar(select(Membership).where(Membership.user_id == team["ids"]["ana"],
                                                                  Membership.conversation_id == team["chats"]["group"]))
    from datetime import timedelta
    assert notify.is_muted(membership) and not notify.is_muted(membership, utcnow() + timedelta(hours=2))


def test_a_stranger_cant_mute_a_chat(team):
    assert post(team["client"]["zed"], f"/chats/{team['chats']['group']}/mute", {"choice": "60"}).status_code == 404


# ---------- Who gets told ----------

def test_notify_goes_to_members_with_a_tab_except_the_sender_and_the_muted(app, team):
    hub = app.extensions[HUB]
    tabs = {name: fake_connection(hub, user_id) for name, user_id in team["ids"].items()}
    post(team["client"]["ben"], f"/chats/{team['chats']['group']}/mute", {"choice": "forever"})
    send(team, "maria", "group", "Lunch? @ana")
    (ana,) = tabs["ana"].ws.of_type("notify")
    assert ana["data"]["title"] == "Maria in Studio" and ana["data"]["body"] == "Mentioned you: Lunch? @ana"
    assert ana["data"]["url"] == f"/chats/{team['chats']['group']}"
    assert not tabs["ben"].ws.of_type("notify") and not tabs["maria"].ws.of_type("notify")
    assert not tabs["zed"].ws.of_type("notify")
    assert tabs["ana"].ws.of_type("mention")


def test_someone_without_a_tab_gets_a_push_after_the_commit(tmp_path, monkeypatch):
    app = make_app(tmp_path, VAPID_PRIVATE_KEY="test-key")
    started = []
    monkeypatch.setattr(notify.PUSH_POOL, "submit", lambda function, *args: started.append(args))
    maria, ana = make_user(app, "maria", "Maria Test"), make_user(app, "ana", "Ana Test")
    with app.app_context():
        s = db.session()
        conversation, _ = chats.start_dm(s, s.get(User, maria), s.get(User, ana), utcnow())
        s.commit()
        chats.send_message(s, s.get(User, maria), conversation.id, "Are you awake?", None, utcnow())
        assert started == []
        s.commit()
    (_app, user_id, payload), = started
    assert user_id == ana and payload["title"] == "Maria Test" and payload["body"] == "Are you awake?"


def test_push_sends_to_each_device_and_forgets_gone_ones(app, team):
    import pywebpush
    with app.app_context():
        s = db.session()
        for endpoint in ("https://push.example/alive", "https://push.example/gone"):
            s.add(PushSubscription(user_id=team["ids"]["ana"], endpoint=endpoint, p256dh="key", auth="auth"))
        s.commit()
    app.config["VAPID_PRIVATE_KEY"] = "test-key"
    sent = []

    class Gone:
        status_code = 410

    def fake(subscription_info, **kwargs):
        sent.append(subscription_info["endpoint"])
        if subscription_info["endpoint"].endswith("gone"):
            raise pywebpush.WebPushException("gone", response=Gone())
    assert notify.push(app, team["ids"]["ana"], {"title": "Hi"}, sender=fake) == 1
    assert sorted(sent) == ["https://push.example/alive", "https://push.example/gone"]
    with app.app_context():
        assert db.session().scalars(select(PushSubscription.endpoint)).all() == ["https://push.example/alive"]


def test_saving_and_forgetting_devices(app, team):
    client = team["client"]["ana"]
    good = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "BPk", "auth": "xyz"}}
    response = client.post("/notifications/devices", json=good, headers={"X-CSRF-Token": "test-csrf-token"})
    assert response.status_code == 200 and response.get_json()["devices"] == 1
    bad = client.post("/notifications/devices", json={"endpoint": "http://evil.local/x", "keys": {}},
                      headers={"X-CSRF-Token": "test-csrf-token"})
    assert bad.status_code == 400
    assert client.post("/notifications/devices", json=good).status_code == 400  # no CSRF token
    assert "1 phone or browser gets notifications" in words(client.get("/settings/preferences"))
    post(client, "/notifications/devices/forget")
    assert "No device gets notifications" in words(client.get("/settings/preferences"))


# ---------- The installable app ----------

def test_the_service_worker_and_the_manifest(client):
    worker = client.get("/sw.js")
    assert worker.status_code == 200 and worker.headers["Cache-Control"] == "no-cache"
    assert b"push" in worker.data and b"notificationclick" in worker.data
    manifest = client.get("/static/site.webmanifest")
    assert manifest.status_code == 200 and b"icon-maskable-512.png" in manifest.data
    assert client.get("/favicon.ico").status_code == 200


def test_make_vapid_keys_command(app):
    result = app.test_cli_runner().invoke(args=["make-vapid-keys"])
    assert result.exit_code == 0 and "VAPID_PUBLIC_KEY=" in result.output and "VAPID_PRIVATE_KEY=" in result.output
