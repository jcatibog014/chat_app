"""Message tools: reply, edit, delete for everyone, reactions, and "Seen by"."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from paperplane import chats, db, events, tools
from paperplane.dates import utcnow
from paperplane.hub import HUB
from paperplane.models import Conversation, Membership, Message, Reaction, User
from paperplane.problems import Forbidden, Invalid, NotFound

from .conftest import browser, make_user, post, text, words
from .live_helpers import fake_connection


@pytest.fixture
def team(app):
    """Maria (the group's owner), Ana and Ben in a group; Maria and Ana in a DM."""
    ids = {name: make_user(app, name, f"{name.title()} Test") for name in ("maria", "ana", "ben")}
    with app.app_context():
        s = db.session()
        people = {name: s.get(User, user_id) for name, user_id in ids.items()}
        group = chats.create_group(s, people["maria"], "Team", [people["ana"], people["ben"]], utcnow())
        dm, _ = chats.start_dm(s, people["maria"], people["ana"], utcnow())
        s.commit()
        chat_ids = {"group": group.id, "dm": dm.id}
    clients = {name: browser(app, user_id) for name, user_id in ids.items()}
    return {"ids": ids, "chats": chat_ids, "clients": clients}


def say(app, team, who, where, body, **kwargs):
    with app.app_context():
        s = db.session()
        message, _ = chats.send_message(s, s.get(User, team["ids"][who]), team["chats"][where], body, None,
                                        utcnow(), **kwargs)
        s.commit()
        return message.id


def with_session(app, work):
    with app.app_context():
        s = db.session()
        result = work(s)
        s.commit()
        return result


def user(s, team, who):
    return s.get(User, team["ids"][who])


# ---------- Edit ----------

def test_edit_your_own_message(app, team):
    message_id = say(app, team, "ana", "group", "Helo")
    with_session(app, lambda s: tools.edit_message(s, user(s, team, "ana"), message_id, "Hello", utcnow()))
    page = words(team["clients"]["maria"].get(f"/chats/{team['chats']['group']}"))
    assert "Hello" in page and "edited" in page and "Helo" not in page


def test_nobody_edits_someone_elses_message_not_even_the_owner(app, team):
    message_id = say(app, team, "ana", "group", "Mine")
    with pytest.raises(Forbidden):
        with_session(app, lambda s: tools.edit_message(s, user(s, team, "maria"), message_id, "Yours now", utcnow()))
    assert team["clients"]["maria"].get(f"/messages/{message_id}/edit").status_code == 403


def test_edits_stop_after_a_day(app, team):
    message_id = say(app, team, "ana", "group", "Old news")
    later = utcnow() + timedelta(hours=25)
    with pytest.raises(Forbidden, match="24 hours"):
        with_session(app, lambda s: tools.edit_message(s, user(s, team, "ana"), message_id, "New news", later))


def test_the_edit_page_works_without_javascript(app, team):
    message_id = say(app, team, "ana", "group", "Typo")
    client = team["clients"]["ana"]
    assert "Edit Message" in words(client.get(f"/messages/{message_id}/edit"))
    response = post(client, f"/messages/{message_id}/edit", {"body": "Fixed"})
    assert response.status_code == 302
    assert "Fixed" in words(client.get(f"/chats/{team['chats']['group']}"))
    assert "Type a message first." in words(post(client, f"/messages/{message_id}/edit", {"body": " "}))


# ---------- Delete for everyone ----------

def test_delete_your_own_message_erases_its_words(app, team):
    message_id = say(app, team, "ana", "group", "Oops, secret")
    response = post(team["clients"]["ana"], f"/messages/{message_id}/delete")
    assert response.status_code == 302
    with app.app_context():
        message = db.session().get(Message, message_id)
        assert message.deleted_at is not None and message.body == ""
    page = words(team["clients"]["ben"].get(f"/chats/{team['chats']['group']}"))
    assert "Message deleted" in page and "Oops, secret" not in page


def test_a_groups_owner_may_delete_anyones_message_a_member_may_not(app, team):
    first = say(app, team, "ana", "group", "Rude words")
    second = say(app, team, "ana", "group", "More rude words")
    assert post(team["clients"]["ben"], f"/messages/{first}/delete").status_code == 403
    assert post(team["clients"]["maria"], f"/messages/{first}/delete").status_code == 302
    with app.app_context():
        assert db.session().get(Message, first).deleted_at is not None
        assert db.session().get(Message, second).deleted_at is None


def test_in_a_dm_you_can_only_delete_your_own(app, team):
    message_id = say(app, team, "ana", "dm", "In our DM")
    assert post(team["clients"]["maria"], f"/messages/{message_id}/delete").status_code == 403


def test_deleting_twice_is_fine_and_takes_the_reactions(app, team):
    message_id = say(app, team, "ana", "group", "Hi")
    with_session(app, lambda s: tools.react(s, user(s, team, "ben"), message_id, "👍", utcnow()))
    for _ in range(2):
        with_session(app, lambda s: tools.delete_message(s, user(s, team, "ana"), message_id, utcnow()))
    with app.app_context():
        assert db.session().scalar(select(func.count(Reaction.id))) == 0


def test_a_stranger_cant_touch_a_message(app, team):
    outsider = make_user(app, "zed", "Zed Outside")
    message_id = say(app, team, "ana", "group", "Members only")
    client = browser(app, outsider)
    for url in (f"/messages/{message_id}/delete", f"/messages/{message_id}/react"):
        assert post(client, url, {"emoji": "👍"}).status_code == 404
    assert client.get(f"/messages/{message_id}/edit").status_code == 404


# ---------- Reactions ----------

def test_reacting_twice_takes_it_back(app, team):
    message_id = say(app, team, "ana", "group", "Pizza tonight?")
    react = lambda s: tools.react(s, user(s, team, "ben"), message_id, "🎉", utcnow())  # noqa: E731
    assert with_session(app, react) is True
    page = team["clients"]["ben"].get(f"/chats/{team['chats']['group']}").get_data(as_text=True)
    assert 'class="reaction is-mine"' in page and "Party: Ben" in page
    assert with_session(app, react) is False
    with app.app_context():
        assert db.session().scalar(select(func.count(Reaction.id))) == 0


def test_only_the_six_reactions(app, team):
    message_id = say(app, team, "ana", "group", "Hi")
    with pytest.raises(Invalid):
        with_session(app, lambda s: tools.react(s, user(s, team, "ben"), message_id, "<b>", utcnow()))


def test_reactions_group_and_count(app, team):
    message_id = say(app, team, "ana", "group", "We won!")
    for who in ("maria", "ben"):
        with_session(app, lambda s, who=who: tools.react(s, user(s, team, who), message_id, "🎉", utcnow()))
    page = words(team["clients"]["ana"].get(f"/chats/{team['chats']['group']}"))
    assert "Party, 2: Maria, Ben" in text(team["clients"]["ana"].get(f"/chats/{team['chats']['group']}"))
    assert "🎉 2" in page


# ---------- Replies ----------

def test_a_reply_quotes_what_it_answers(app, team):
    question = say(app, team, "ana", "group", "Who has the keys?")
    client = team["clients"]["ben"]
    page = words(client.get(f"/chats/{team['chats']['group']}?reply={question}"))
    assert "Replying to Ana : Who has the keys?" in page or "Replying to Ana: Who has the keys?" in page
    post(client, f"/chats/{team['chats']['group']}/send", {"body": "I do", "reply_to": str(question)})
    html = text(client.get(f"/chats/{team['chats']['group']}"))
    assert f'href="#m-{question}" data-jump="{question}"' in html


def test_you_cant_reply_to_a_message_from_another_chat(app, team):
    elsewhere = say(app, team, "ana", "dm", "Private")
    with pytest.raises(NotFound):
        say(app, team, "ben", "group", "Gotcha", reply_to_id=elsewhere)


def test_a_reply_to_a_deleted_message_says_so(app, team):
    question = say(app, team, "ana", "group", "Original")
    say(app, team, "ben", "group", "Answer", reply_to_id=question)
    with_session(app, lambda s: tools.delete_message(s, user(s, team, "ana"), question, utcnow()))
    page = words(team["clients"]["maria"].get(f"/chats/{team['chats']['group']}"))
    assert "Ana Message deleted" in page and "Original" not in page


# ---------- Seen by ----------

def test_seen_in_a_dm(app, team):
    say(app, team, "maria", "dm", "Are you there?")
    client = team["clients"]["maria"]
    assert "Seen" not in words(client.get(f"/chats/{team['chats']['dm']}"))
    team["clients"]["ana"].get(f"/chats/{team['chats']['dm']}")  # Ana opens it
    assert "Seen" in words(client.get(f"/chats/{team['chats']['dm']}"))


def test_seen_by_names_in_a_group(app, team):
    say(app, team, "maria", "group", "Meeting at 3")
    group = team["chats"]["group"]
    team["clients"]["ana"].get(f"/chats/{group}")
    assert "Seen by Ana" in words(team["clients"]["maria"].get(f"/chats/{group}"))
    team["clients"]["ben"].get(f"/chats/{group}")
    assert "Seen by everyone" in words(team["clients"]["maria"].get(f"/chats/{group}"))


def test_seen_words():
    group = Conversation(kind="group")
    message = Message(sender_id=1, seq=5, kind="text")
    readers = {1: ("Me", 5), 2: ("Ana", 5), 3: ("Ben", 4), 4: ("Cy", 6)}
    assert tools.seen_words(group, readers, message, 1) == "Seen by Ana and Cy"
    assert tools.seen_words(group, readers, Message(sender_id=2, seq=5, kind="text"), 1) == ""


# ---------- Over the WebSocket ----------

def run(app, connection, kind, data):
    return events.dispatch(app, connection, json.dumps({"type": kind, "ref": "x", "data": data}))[0]


def test_tools_over_the_socket_reach_every_member(app, team):
    hub = app.extensions[HUB]
    tabs = {name: fake_connection(hub, user_id) for name, user_id in team["ids"].items()}
    message_id = say(app, team, "ana", "group", "Hi tem")
    assert run(app, tabs["ana"], "message.edit", {"message_id": message_id, "body": "Hi team"})["type"] == "ack"
    assert run(app, tabs["ben"], "message.react", {"message_id": message_id, "emoji": "👍"})["data"] == {"added": True}
    assert run(app, tabs["ben"], "message.delete", {"message_id": message_id})["code"] == "forbidden"
    assert run(app, tabs["maria"], "message.delete", {"message_id": message_id})["type"] == "ack"
    updates = [frame["data"] for frame in tabs["ben"].ws.of_type("message.updated")]
    assert [update["body"] for update in updates] == ["Hi team", "Hi team", None]
    assert updates[1]["reactions"][0]["users"] == [team["ids"]["ben"]]
    assert updates[2]["deleted"] is True and updates[2]["version"] > updates[1]["version"]


def test_live_html_never_carries_a_csrf_token(app, team):
    """A live message is drawn once for everyone: the sender's token must not be in it."""
    hub = app.extensions[HUB]
    tab = fake_connection(hub, team["ids"]["ben"])
    with app.test_request_context():
        from flask import session
        session["csrf"] = "senders-secret-token"
        with app.app_context():
            s = db.session()
            chats.send_message(s, s.get(User, team["ids"]["ana"]), team["chats"]["group"], "Hi", None, utcnow())
            s.commit()
    (news,) = tab.ws.of_type("message.new")
    assert "senders-secret-token" not in news["data"]["html"] and "csrf_token" not in news["data"]["html"]
    # The page's own copy does have the reader's token in its forms.
    page = team["clients"]["ben"].get(f"/chats/{team['chats']['group']}").get_data(as_text=True)
    assert 'name="csrf_token"' in page.split('class="msg-menu"', 1)[1]


def test_catch_up_brings_edits_too(app, team):
    hub = app.extensions[HUB]
    tab = fake_connection(hub, team["ids"]["ben"])
    message_id = say(app, team, "ana", "group", "Draft")
    with app.app_context():
        version = db.session().get(Conversation, team["chats"]["group"]).version
    with_session(app, lambda s: tools.edit_message(s, user(s, team, "ana"), message_id, "Final", utcnow()))
    answer = run(app, tab, "sync", {"open": {"conversation_id": team["chats"]["group"], "version": version}})
    assert [m["body"] for m in answer["data"]["changes"]] == ["Final"]


def test_membership_is_checked_before_touching_old_history(app, team):
    """A member who joined a group that hides its history can't react to what came before."""
    early = say(app, team, "ana", "group", "Before Zed")
    zed = make_user(app, "zed", "Zed New")
    with app.app_context():
        s = db.session()
        conversation = s.get(Conversation, team["chats"]["group"])
        conversation.history_visible = False
        s.add(Membership(conversation_id=conversation.id, user_id=zed, role="member",
                         joined_seq=conversation.last_seq, last_read_seq=conversation.last_seq))
        s.commit()
    assert post(browser(app, zed), f"/messages/{early}/react", {"emoji": "👍"}).status_code == 404
