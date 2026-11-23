"""Running a group (roles, people, muting, leaving, deleting, invite links) and blocking."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from paperplane import chats, db, events, groups, presence
from paperplane.dates import utcnow
from paperplane.hub import HUB
from paperplane.models import Conversation, InviteLink, Membership, User

from .conftest import browser, make_user, post, words
from .live_helpers import fake_connection


@pytest.fixture
def club(app):
    """A group "Book Club": Olga (owner), Adam (admin), Mia and Max (members). Sam is outside."""
    names = {"olga": "Olga Owner", "adam": "Adam Admin", "mia": "Mia Member", "max": "Max Member", "sam": "Sam Outside"}
    ids = {handle: make_user(app, handle, name) for handle, name in names.items()}
    with app.app_context():
        s = db.session()
        people = {handle: s.get(User, user_id) for handle, user_id in ids.items()}
        group = chats.create_group(s, people["olga"], "Book Club", [people[h] for h in ("adam", "mia", "max")], utcnow())
        s.scalar(select(Membership).where(Membership.conversation_id == group.id,
                                          Membership.user_id == ids["adam"])).role = "admin"
        s.commit()
        chat_id = group.id
    return {"ids": ids, "chat": chat_id, "client": {h: browser(app, i) for h, i in ids.items()}}


def membership(app, club, who):
    with app.app_context():
        return db.session().scalar(select(Membership).where(Membership.conversation_id == club["chat"],
                                                            Membership.user_id == club["ids"][who]))


def info(club, who):
    return club["client"][who].get(f"/chats/{club['chat']}/info")


def chat_page(club, who):
    return club["client"][who].get(f"/chats/{club['chat']}")


def act(club, who, path, data=None):
    return post(club["client"][who], f"/chats/{club['chat']}/{path}", data or {}, follow_redirects=True)


# ---------- The info page ----------

def test_the_owner_sees_every_control_a_member_sees_few(club):
    owner = words(info(club, "olga"))
    for heading in ("Group Info", "People", "Add People", "Invite Links", "Group Settings", "Delete the Group"):
        assert heading in owner
    member = words(info(club, "mia"))
    assert "Leave the Group" in member
    for heading in ("Add People", "Invite Links", "Group Settings", "Delete the Group"):
        assert heading not in member
    assert "Owner" in member and "Admin" in member


def test_a_stranger_cant_open_the_info_page(club):
    assert info(club, "sam").status_code == 404


# ---------- Renaming ----------

def test_rename_leaves_a_line_and_tells_everyone_live(app, club):
    hub = app.extensions[HUB]
    tab = fake_connection(hub, club["ids"]["mia"])
    page = words(act(club, "adam", "rename", {"title": "Book Lovers"}))
    assert "The group's name is changed." in page
    assert "Adam renamed the group to Book Lovers" in words(chat_page(club, "mia"))
    assert tab.ws.of_type("conversation.updated")[0]["data"]["title"] == "Book Lovers"


def test_a_member_cant_rename(app, club):
    page = words(act(club, "mia", "rename", {"title": "Mia's Club"}))
    assert "Only the group's owner and admins can rename it." in page
    with app.app_context():
        assert db.session().get(Conversation, club["chat"]).title == "Book Club"


# ---------- People ----------

def test_adding_someone_brings_them_in_read_up_to_now(app, club):
    hub = app.extensions[HUB]
    sam_tab = fake_connection(hub, club["ids"]["sam"])
    act(club, "olga", "add", {"usernames": "sam"})
    sam = membership(app, club, "sam")
    # Read up to the moment he joined; the "Olga added Sam" line after it is his one unread.
    assert sam.removed_at is None and sam.role == "member" and sam.last_read_seq == sam.joined_seq
    assert "Olga added Sam" in words(chat_page(club, "sam"))
    assert sam_tab.ws.of_type("conversation.new")


def test_a_member_cant_add_people(app, club):
    assert "Only the group's owner and admins can add people." in words(act(club, "max", "add", {"usernames": "sam"}))
    assert membership(app, club, "sam") is None


def test_an_admin_removes_a_member_but_not_the_owner_or_another_admin(app, club):
    hub = app.extensions[HUB]
    max_tab = fake_connection(hub, club["ids"]["max"])
    act(club, "adam", f"members/{club['ids']['max']}/remove")
    assert membership(app, club, "max").removed_at is not None
    (gone,) = max_tab.ws.of_type("conversation.removed")
    assert gone["data"] == {"conversation_id": club["chat"], "why": "removed"}
    assert "You can only remove people with a lower role" in words(act(club, "adam", f"members/{club['ids']['olga']}/remove"))
    assert membership(app, club, "olga").removed_at is None


def test_a_removed_person_hears_nothing_more_and_can_do_nothing(app, club):
    hub = app.extensions[HUB]
    max_tab = fake_connection(hub, club["ids"]["max"])
    act(club, "olga", f"members/{club['ids']['max']}/remove")
    post(club["client"]["olga"], f"/chats/{club['chat']}/send", {"body": "Now that Max is gone..."})
    assert not [f for f in max_tab.ws.of_type("message.new") if "Max is gone" in (f["data"]["body"] or "")]
    assert chat_page(club, "max").status_code == 404
    answer = events.dispatch(app, max_tab, json.dumps({"type": "message.send", "ref": "r", "data": {
        "conversation_id": club["chat"], "body": "Let me back", "client_id": "removed-0001"}}))[0]
    assert answer["code"] == "not_found"


def test_only_the_owner_changes_roles(app, club):
    act(club, "olga", f"members/{club['ids']['mia']}/role", {"role": "admin"})
    assert membership(app, club, "mia").role == "admin"
    assert "Only the group's owner can change roles." in words(
        act(club, "adam", f"members/{club['ids']['max']}/role", {"role": "admin"}))


def test_handing_over_the_group(app, club):
    act(club, "olga", f"members/{club['ids']['mia']}/owner")
    assert membership(app, club, "mia").role == "owner" and membership(app, club, "olga").role == "admin"


# ---------- Muting ----------

def test_a_muted_member_can_read_but_not_send(app, club):
    act(club, "adam", f"members/{club['ids']['max']}/mute", {"minutes": "60"})
    page = chat_page(club, "max").get_data(as_text=True)
    assert "An admin muted you in this group until" in words(chat_page(club, "max"))
    assert "data-composer-text" not in page
    assert post(club["client"]["max"], f"/chats/{club['chat']}/send", {"body": "Hello?"}).status_code == 403
    act(club, "adam", f"members/{club['ids']['max']}/mute", {"minutes": "off"})
    assert "data-composer-text" in chat_page(club, "max").get_data(as_text=True)


def test_a_mute_ends_by_itself(app, club):
    act(club, "olga", f"members/{club['ids']['max']}/mute", {"minutes": "60"})
    with app.app_context():
        s = db.session()
        later = utcnow() + timedelta(hours=2)
        max_ = s.get(User, club["ids"]["max"])
        chats.send_message(s, max_, club["chat"], "I'm back", None, later)


def test_an_admin_cant_mute_another_admin(app, club):
    act(club, "olga", f"members/{club['ids']['mia']}/role", {"role": "admin"})
    assert "You can only mute people with a lower role" in words(act(club, "adam", f"members/{club['ids']['mia']}/mute", {"minutes": "60"}))


# ---------- Leaving and deleting ----------

def test_when_the_owner_leaves_an_admin_takes_over(app, club):
    act(club, "olga", "leave")
    assert membership(app, club, "adam").role == "owner"
    assert "Olga left" in words(chat_page(club, "mia")) and "Adam is the owner now" in words(chat_page(club, "mia"))


def test_the_last_one_out_deletes_the_group(app, club):
    for who in ("mia", "max", "adam", "olga"):
        act(club, who, "leave")
    with app.app_context():
        assert db.session().get(Conversation, club["chat"]).deleted_at is not None


def test_deleting_tells_everyone_and_closes_the_chat(app, club):
    hub = app.extensions[HUB]
    tab = fake_connection(hub, club["ids"]["mia"])
    assert post(club["client"]["adam"], f"/chats/{club['chat']}/delete").status_code == 403
    response = post(club["client"]["olga"], f"/chats/{club['chat']}/delete")
    assert response.status_code == 302
    assert tab.ws.of_type("conversation.removed")[0]["data"]["why"] == "deleted"
    assert chat_page(club, "mia").status_code == 404


# ---------- History ----------

def test_hiding_history_from_new_members(app, club):
    post(club["client"]["olga"], f"/chats/{club['chat']}/send", {"body": "Before Sam joined"})
    act(club, "olga", "history", {"history_visible": "0"})
    act(club, "olga", "add", {"usernames": "sam"})
    post(club["client"]["olga"], f"/chats/{club['chat']}/send", {"body": "Welcome Sam"})
    page = words(chat_page(club, "sam"))
    assert "Welcome Sam" in page and "Before Sam joined" not in page


# ---------- Invite links ----------

def make_link(app, club, expiry="7", uses="10"):
    act(club, "olga", "invites", {"expiry": expiry, "uses": uses})
    with app.app_context():
        return db.session().scalars(select(InviteLink).order_by(InviteLink.id.desc())).first().code


def test_joining_with_an_invite_link(app, club):
    code = make_link(app, club)
    sam = club["client"]["sam"]
    assert "Join group" in words(sam.get(f"/join/{code}")) and "Book Club" in words(sam.get(f"/join/{code}"))
    response = post(sam, f"/join/{code}")
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/chats/{club['chat']}")
    assert "Sam joined with an invite link" in words(chat_page(club, "olga"))
    post(sam, f"/join/{code}")  # twice is fine
    with app.app_context():
        assert db.session().scalars(select(InviteLink)).first().uses == 1


def test_a_link_for_one_person_is_used_up(app, club):
    code = make_link(app, club, uses="1")
    post(club["client"]["sam"], f"/join/{code}")
    late = make_user(app, "late", "Late Comer")
    assert post(browser(app, late), f"/join/{code}").status_code == 404
    assert "used up" in words(browser(app, late).get(f"/join/{code}"))


def test_a_turned_off_or_expired_link_stops_working(app, club):
    code = make_link(app, club)
    with app.app_context():
        link_id = db.session().scalar(select(InviteLink.id).where(InviteLink.code == code))
    act(club, "olga", f"invites/{link_id}/revoke")
    assert "turned off" in words(club["client"]["sam"].get(f"/join/{code}"))
    code = make_link(app, club, expiry="1")
    with app.app_context():
        s = db.session()
        s.scalar(select(InviteLink).where(InviteLink.code == code)).expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert post(club["client"]["sam"], f"/join/{code}").status_code == 404


def test_only_managers_make_links_and_demo_people_cant_use_real_ones(app, club):
    assert "Only the group's owner and admins can make invite links." in words(act(club, "max", "invites", {"expiry": "7", "uses": "10"}))
    code = make_link(app, club)
    demo = browser(app)
    post(demo, "/demo")
    assert post(demo, f"/join/{code}").status_code == 404


def test_the_join_page_needs_a_login(app, club):
    code = make_link(app, club)
    response = browser(app).get(f"/join/{code}")
    assert response.status_code == 302 and "/login" in response.headers["Location"]


# ---------- Blocking ----------

@pytest.fixture
def pair(app):
    ids = {name: make_user(app, name, f"{name.title()} Person") for name in ("ella", "finn")}
    clients = {name: browser(app, user_id) for name, user_id in ids.items()}
    response = post(clients["ella"], "/chats/new/dm", {"username": "finn"})
    chat_id = int(response.headers["Location"].rsplit("/", 1)[1])
    return {"ids": ids, "client": clients, "chat": chat_id}


def test_blocking_closes_the_dm_both_ways(app, pair):
    post(pair["client"]["ella"], f"/people/{pair['ids']['finn']}/block")
    ella = words(pair["client"]["ella"].get(f"/chats/{pair['chat']}"))
    finn = words(pair["client"]["finn"].get(f"/chats/{pair['chat']}"))
    assert "You blocked Finn. Unblock them in Chat Info" in ella
    assert "You can't reply to this chat." in finn
    for who in ("ella", "finn"):
        assert post(pair["client"][who], f"/chats/{pair['chat']}/send", {"body": "hi"}).status_code == 403


def test_blocked_people_cant_start_a_new_dm_or_add_each_other(app, pair):
    post(pair["client"]["finn"], f"/people/{pair['ids']['ella']}/block")
    with app.app_context():
        s = db.session()
        s.delete(s.get(Conversation, pair["chat"]))
        s.commit()
    assert post(pair["client"]["ella"], "/chats/new/dm", {"username": "finn"}).status_code == 403
    response = post(pair["client"]["ella"], "/chats/new/group", {"title": "Trap", "usernames": "finn"})
    assert response.status_code == 400 and "You can't add Finn Person." in words(response)
    make_user(app, "gus", "Gus Person")
    response = post(pair["client"]["ella"], "/chats/new/group", {"title": "Ours", "usernames": "gus"})
    group_id = int(response.headers["Location"].rsplit("/", 1)[1])
    assert "You can't add Finn Person." in words(post(pair["client"]["ella"], f"/chats/{group_id}/add",
                                                      {"usernames": "finn"}, follow_redirects=True))


def test_blocked_people_dont_see_each_other_online(app, pair):
    hub = app.extensions[HUB]
    ella_tab = fake_connection(hub, pair["ids"]["ella"])
    post(pair["client"]["ella"], f"/people/{pair['ids']['finn']}/block")
    fake_connection(hub, pair["ids"]["finn"])
    heard = [f for f in ella_tab.ws.of_type("presence") if f["data"]["user_id"] == pair["ids"]["finn"]]
    assert all(f["data"]["status"] == "offline" for f in heard)
    with app.app_context():
        s = db.session()
        assert presence.contacts(s, pair["ids"]["finn"]) == set()


def test_unblocking_puts_it_back_and_settings_lists_them(app, pair):
    post(pair["client"]["ella"], f"/people/{pair['ids']['finn']}/block")
    page = words(pair["client"]["ella"].get("/settings"))
    assert "Blocked People" in page and "Finn Person" in page
    post(pair["client"]["ella"], f"/people/{pair['ids']['finn']}/unblock")
    assert "You haven't blocked anyone." in words(pair["client"]["ella"].get("/settings"))
    assert post(pair["client"]["finn"], f"/chats/{pair['chat']}/send", {"body": "hi again"}).status_code == 302


def test_the_dm_info_page_offers_block(pair):
    page = words(pair["client"]["ella"].get(f"/chats/{pair['chat']}/info"))
    assert "Chat Info" in page and "Block Finn" in page


def test_block_redirects_only_inside_the_site(pair):
    response = post(pair["client"]["ella"], f"/people/{pair['ids']['finn']}/block", {"next": "https://evil.example"})
    assert "evil.example" not in response.headers["Location"]


def test_mute_flag_helper():
    m = Membership(muted_until=utcnow() + timedelta(minutes=5))
    assert groups.is_muted(m, utcnow()) and not groups.is_muted(m, utcnow() + timedelta(minutes=6))
