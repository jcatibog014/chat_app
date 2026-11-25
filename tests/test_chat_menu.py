"""The chat list's choices: the ⋯ menu beside each chat (computers) and the long-press preview
(phones): mute, mark as read, restrict, block, leave, and delete (for me only)."""
import re

import pytest
from sqlalchemy import select

from paperplane import db, presence
from paperplane.hub import HUB
from paperplane.models import Activity, Message

from .conftest import browser, make_user, post, text, words
from .live_helpers import fake_connection


@pytest.fixture
def team(app):
    """Olga and Max share a DM and a group (with Sam)."""
    ids = {name: make_user(app, name, f"{name.title()} Person") for name in ("olga", "max", "sam")}
    clients = {name: browser(app, user_id) for name, user_id in ids.items()}
    dm = int(post(clients["olga"], "/chats/new/dm", {"username": "max"}).headers["Location"].rsplit("/", 1)[1])
    group = int(post(clients["olga"], "/chats/new/group", {"title": "Club", "usernames": "max sam"})
                .headers["Location"].rsplit("/", 1)[1])
    return {"ids": ids, "client": clients, "dm": dm, "group": group}


def say(team, who, chat, body):
    assert post(team["client"][who], f"/chats/{team[chat]}/send", {"body": body}).status_code == 302


def row_html(team, who, chat):
    page = text(team["client"][who].get("/chats"))
    return re.search(rf'<li class="chat-row[^"]*"\s+data-chat="{team[chat]}".*?</li>\s*(?=<li class="chat-row|</ul>)', page, re.S).group(0)


def menu_labels(html):
    return re.findall(r'<button class="menu-item[^"]*" type="submit">\s*<svg.*?</svg>\s*<span>([^<]+)</span>', html, re.S)


def listed(team, who, chat):
    return f'data-chat="{team[chat]}"' in text(team["client"][who].get("/chats"))


# ---------- The menu ----------

def test_a_dm_row_has_its_choices_beside_the_time(app, team):
    say(team, "max", "dm", "hi olga")
    row = row_html(team, "olga", "dm")
    assert 'class="row-menu"' in row and 'title="Options for Max Person"' in row
    assert menu_labels(row) == ["Mute", "Mark as read", "Restrict", "Block", "Delete chat"]
    assert row.index("chat-row-time") < row.index("row-menu")


def test_a_group_row_has_leave_instead_of_restrict_and_block(app, team):
    assert menu_labels(row_html(team, "olga", "group")) == ["Mute", "Leave group", "Delete chat"]


def test_mark_as_read_and_mute_from_the_list(app, team):
    say(team, "max", "dm", "unread one")
    post(team["client"]["olga"], f"/chats/{team['dm']}/read", {"next": "/chats"})
    assert "Mark as read" not in menu_labels(row_html(team, "olga", "dm"))
    post(team["client"]["olga"], f"/chats/{team['dm']}/mute", {"choice": "forever", "next": "/chats"})
    row = row_html(team, "olga", "dm")
    assert menu_labels(row)[0] == "Unmute" and 'data-muted="1"' in row


# ---------- Delete chat (for me) ----------

def test_deleting_a_chat_is_only_for_me_and_it_comes_back_with_new_messages(app, team):
    say(team, "max", "dm", "an old secret")
    response = post(team["client"]["olga"], f"/chats/{team['dm']}/clear", {"next": f"/chats/{team['dm']}"})
    assert response.headers["Location"] == "/chats"  # never back to the chat just deleted
    assert "is deleted" in words(team["client"]["olga"].get("/chats"))
    assert not listed(team, "olga", "dm")
    assert "an old secret" not in words(team["client"]["olga"].get(f"/chats/{team['dm']}"))
    assert "an old secret" in words(team["client"]["max"].get(f"/chats/{team['dm']}"))  # Max still has it
    assert "No Messages Found" in words(team["client"]["olga"].get("/search?q=secret"))
    # A new message brings the chat back, with only what came after.
    say(team, "max", "dm", "a new hello")
    assert listed(team, "olga", "dm")
    page = words(team["client"]["olga"].get(f"/chats/{team['dm']}"))
    assert "a new hello" in page and "an old secret" not in page


def test_deleting_a_group_chat_keeps_me_in_the_group(app, team):
    say(team, "sam", "group", "group talk")
    post(team["client"]["olga"], f"/chats/{team['group']}/clear")
    assert not listed(team, "olga", "group")
    say(team, "sam", "group", "still here?")
    assert listed(team, "olga", "group")
    assert team["client"]["olga"].get(f"/chats/{team['group']}/info").status_code == 200


def test_deleting_tells_my_other_tabs(app, team):
    tab = fake_connection(app.extensions[HUB], team["ids"]["olga"])
    post(team["client"]["olga"], f"/chats/{team['dm']}/clear")
    assert tab.ws.of_type("conversation.removed")[-1]["data"] == {"conversation_id": team["dm"], "why": "cleared"}


# ---------- Restrict ----------

def test_restricting_quiets_their_messages(app, team):
    tab = fake_connection(app.extensions[HUB], team["ids"]["olga"])
    response = post(team["client"]["olga"], f"/people/{team['ids']['max']}/restrict", {"next": "/chats"})
    assert response.status_code == 302 and "You restricted Max Person" in words(team["client"]["olga"].get("/chats"))
    say(team, "max", "dm", "are you there?")
    say(team, "max", "group", "@olga look at this")
    # No notification of any kind from Max...
    assert not [f for f in tab.ws.of_type("notify") if f["data"]["conversation_id"] in (team["dm"], team["group"])]
    with app.app_context():
        assert not db.session().scalars(select(Activity).where(Activity.user_id == team["ids"]["olga"])).all()
    # ...his chat is marked, and stays out of the menu's number (its own grey badge stays).
    row = row_html(team, "olga", "dm")
    assert 'title="Restricted"' in row and 'data-muted="1"' in row and menu_labels(row)[2] == "Unrestrict"
    menu_number = re.search(r'<span class="count" data-total-unread[^>]*>(\d+)</span>', text(team["client"]["olga"].get("/chats"))).group(1)
    assert menu_number == "1"  # only the group's message counts
    # Max is never told: nothing on his side says so (his own menu still just offers him Restrict).
    max_row = row_html(team, "max", "dm")
    assert 'title="Restricted"' not in max_row and "Unrestrict" not in max_row
    assert "restricted" not in words(team["client"]["max"].get(f"/chats/{team['dm']}")).lower()


def test_restricted_people_dont_see_each_other_online(app, team):
    with app.app_context():
        s = db.session()
        assert team["ids"]["max"] in presence.contacts(s, team["ids"]["olga"])
    post(team["client"]["olga"], f"/people/{team['ids']['max']}/restrict")
    with app.app_context():
        s = db.session()
        assert team["ids"]["max"] not in presence.contacts(s, team["ids"]["olga"])
        assert team["ids"]["olga"] not in presence.contacts(s, team["ids"]["max"])
        assert team["ids"]["sam"] in presence.contacts(s, team["ids"]["olga"])


def test_unrestrict_from_settings(app, team):
    post(team["client"]["olga"], f"/people/{team['ids']['max']}/restrict")
    privacy = words(team["client"]["olga"].get("/settings/privacy"))
    assert "Restricted People" in privacy and "Max Person" in privacy
    assert privacy.index("Restricted People") < privacy.index("Blocked People")
    post(team["client"]["olga"], f"/people/{team['ids']['max']}/unrestrict", {"next": "/settings/privacy"})
    assert "You haven't restricted anyone" in words(team["client"]["olga"].get("/settings/privacy"))
    assert menu_labels(row_html(team, "olga", "dm"))[1] == "Restrict"


def test_you_cant_restrict_yourself_or_a_stranger(app, team):
    outsider = make_user(app, "zed", "Zed Person")
    assert post(team["client"]["olga"], f"/people/{team['ids']['olga']}/restrict").status_code == 404
    assert post(team["client"]["olga"], "/people/999999/restrict").status_code == 404
    demo = browser(app)
    post(demo, "/demo")
    assert post(demo, f"/people/{outsider}/restrict").status_code == 404  # the other world


# ---------- Block and leave, from the list ----------

def test_block_and_leave_from_the_list(app, team):
    post(team["client"]["olga"], f"/people/{team['ids']['max']}/block", {"next": "/chats"})
    assert menu_labels(row_html(team, "olga", "dm"))[2] == "Unblock"
    post(team["client"]["sam"], f"/chats/{team['group']}/leave")
    assert not listed(team, "sam", "group")


# ---------- The phone's long-press preview ----------

def test_the_preview_shows_the_latest_messages_and_the_choices_without_reading(app, team):
    for number in range(12):
        say(team, "max", "dm", f"message {number}")
    peek = team["client"]["olga"].get(f"/chats/{team['dm']}/peek")
    assert peek.status_code == 200
    html = text(peek)
    assert "<html" not in html and 'data-peek' in html and "Max Person" in html
    assert "message 11" in html and "message 4" in html and "message 3" not in html  # the latest 8
    labels = re.findall(r'<button class="peek-action[^"]*" type="submit">\s*<svg.*?</svg>\s*<span>([^<]+)</span>', html, re.S)
    assert labels == ["Mute", "Mark as read", "Restrict", "Block", "Delete chat"]
    # Looking doesn't read the chat.
    assert re.search(r'data-unread-number>12<', text(team["client"]["olga"].get("/chats")))
    assert team["client"]["sam"].get(f"/chats/{team['dm']}/peek").status_code == 404


def test_a_live_row_for_someone_else_carries_no_token(app, team):
    tab = fake_connection(app.extensions[HUB], team["ids"]["sam"])
    post(team["client"]["olga"], "/chats/new/dm", {"username": "sam"})
    html = tab.ws.of_type("conversation.new")[-1]["data"]["html"]
    assert 'class="row-menu"' in html and "csrf_token" not in html
