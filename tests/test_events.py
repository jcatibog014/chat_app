"""The event router and handlers, without any network: frames in, frames out."""
import json

import pytest
from sqlalchemy import func, select

from paperplane import auth, chats, db, events
from paperplane.dates import utcnow
from paperplane.hub import HUB
from paperplane.models import Message, User

from .conftest import make_user
from .live_helpers import fake_connection


@pytest.fixture
def world(app):
    """Maria and Ana in a DM, Ben outside it, each with one open (fake) tab."""
    ids = {name: make_user(app, name, name.title()) for name in ("maria", "ana", "ben")}
    with app.app_context():
        s = db.session()
        conversation, _ = chats.start_dm(s, s.get(User, ids["maria"]), s.get(User, ids["ana"]), utcnow())
        s.commit()
        chat_id = conversation.id
    hub = app.extensions[HUB]
    tabs = {name: fake_connection(hub, user_id) for name, user_id in ids.items()}
    return {"ids": ids, "tabs": tabs, "chat": chat_id, "hub": hub}


def frame(kind, data=None, ref="r1"):
    return json.dumps({"type": kind, "ref": ref, "data": data or {}})


def run(app, connection, kind, data=None, ref="r1"):
    replies = events.dispatch(app, connection, frame(kind, data, ref))
    assert len(replies) == 1
    return replies[0]


def test_bad_frames_get_errors_not_crashes(app, world):
    tab = world["tabs"]["maria"]
    assert events.dispatch(app, tab, "not json")[0]["code"] == "bad_json"
    assert events.dispatch(app, tab, "[1, 2]")[0]["code"] == "bad_frame"
    assert run(app, tab, "fly.away")["code"] == "unknown_type"
    assert run(app, tab, "message.send", {"conversation_id": "x", "body": "hi", "client_id": "abcdefgh12"})["code"] == "bad_request"
    assert run(app, tab, "message.send", {"conversation_id": True, "body": "hi", "client_id": "abcdefgh12"})["code"] == "bad_request"
    assert run(app, tab, "message.send", {"body": "hi", "client_id": "abcdefgh12"})["message"] == "Missing conversation_id."
    assert run(app, tab, "message.send", {"conversation_id": 1, "body": 5, "client_id": "abcdefgh12"})["code"] == "bad_request"


def test_every_answer_carries_the_ref_it_answers(app, world):
    tab = world["tabs"]["maria"]
    assert run(app, tab, "fly.away", ref="c42")["ref"] == "c42"
    assert "ref" not in events.dispatch(app, tab, json.dumps({"type": "fly", "ref": {"no": 1}}))[0]


def test_ping_gets_a_pong_without_the_database(app, world):
    assert run(app, world["tabs"]["maria"], "ping", ref="p1") == {"type": "pong", "ref": "p1"}


def test_send_acks_the_sender_and_reaches_both_members_only(app, world):
    tabs = world["tabs"]
    answer = run(app, tabs["maria"], "message.send",
                 {"conversation_id": world["chat"], "body": "Hello Ana", "client_id": "client-0001"})
    assert answer["type"] == "ack" and answer["data"]["message"]["body"] == "Hello Ana"
    assert answer["data"]["duplicate"] is False
    for name in ("maria", "ana"):
        (news,) = tabs[name].ws.of_type("message.new")
        assert news["data"]["body"] == "Hello Ana" and news["data"]["client_id"] == "client-0001"
    assert tabs["ben"].ws.sent == []


def test_a_resent_message_is_saved_once_and_announced_once(app, world):
    tabs = world["tabs"]
    data = {"conversation_id": world["chat"], "body": "Once", "client_id": "client-0002"}
    first = run(app, tabs["maria"], "message.send", data)
    again = run(app, tabs["maria"], "message.send", data)
    assert again["data"]["duplicate"] is True
    assert again["data"]["message"]["id"] == first["data"]["message"]["id"]
    assert len(tabs["ana"].ws.of_type("message.new")) == 1
    with app.app_context():
        assert db.session().scalar(select(func.count(Message.id))) == 1


def test_a_stranger_gets_not_found_and_nobody_hears_anything(app, world):
    tabs = world["tabs"]
    answer = run(app, tabs["ben"], "message.send",
                 {"conversation_id": world["chat"], "body": "Let me in", "client_id": "client-0003"})
    assert answer["type"] == "error" and answer["code"] == "not_found"
    assert tabs["maria"].ws.sent == [] and tabs["ana"].ws.sent == []


def test_an_empty_message_is_invalid(app, world):
    answer = run(app, world["tabs"]["maria"], "message.send",
                 {"conversation_id": world["chat"], "body": "   ", "client_id": "client-0004"})
    assert answer["code"] == "invalid" and answer["message"] == "Type a message first."


def test_a_socket_from_before_a_password_change_is_logged_out(app, world):
    with app.app_context():
        s = db.session()
        auth.set_password(s.get(User, world["ids"]["maria"]), "another password")
        s.commit()
    tab = world["tabs"]["maria"]
    answer = run(app, tab, "message.send", {"conversation_id": world["chat"], "body": "hi", "client_id": "client-0005"})
    assert answer["code"] == "logged_out"
    assert tab.ws.closed_with == (4001, "logged_out")


def test_sending_too_fast_is_slowed_down(app, world):
    tab = world["tabs"]["maria"]
    codes = [run(app, tab, "message.send", {"conversation_id": world["chat"], "body": f"spam {n}",
                                             "client_id": f"client-1{n:04d}"})["type"] for n in range(22)]
    assert codes.count("ack") == 20
    last = run(app, tab, "message.send", {"conversation_id": world["chat"], "body": "x", "client_id": "client-19999"})
    assert last["code"] == "too_many"


def test_read_clears_my_badge_on_my_other_tabs_and_tells_the_total(app, world):
    tabs = world["tabs"]
    run(app, tabs["maria"], "message.send", {"conversation_id": world["chat"], "body": "1", "client_id": "client-0006"})
    other_tab = fake_connection(world["hub"], world["ids"]["ana"])
    answer = run(app, tabs["ana"], "read", {"conversation_id": world["chat"], "seq": 1})
    assert answer["type"] == "ack"
    (read,) = other_tab.ws.of_type("read")
    assert read["data"] == {"conversation_id": world["chat"], "user_id": world["ids"]["ana"], "seq": 1}
    assert other_tab.ws.of_type("unread.total")[0]["data"] == {"total": 0}
    assert tabs["maria"].ws.of_type("read")  # members hear it too (for "Seen by")
    assert not tabs["maria"].ws.of_type("unread.total")  # but only Ana gets her own total


def test_sync_sends_only_what_changed_since_the_version_the_tab_has(app, world):
    tabs = world["tabs"]
    for n in range(3):
        run(app, tabs["maria"], "message.send", {"conversation_id": world["chat"], "body": f"m{n}",
                                                 "client_id": f"client-200{n}"})
    answer = run(app, tabs["ana"], "sync", {"open": {"conversation_id": world["chat"], "version": 1}})
    data = answer["data"]
    assert [m["body"] for m in data["changes"]] == ["m1", "m2"]
    assert data["version"] == 3 and data["reload"] is False and data["total_unread"] == 3
    assert "Maria" in data["list_html"]


def test_sync_says_reload_when_too_much_changed(app, world):
    with app.app_context():
        s = db.session()
        maria = s.get(User, world["ids"]["maria"])
        for n in range(chats.CATCH_UP_LIMIT + 5):
            chats.send_message(s, maria, world["chat"], f"n{n}", None, utcnow())
        s.commit()
    answer = run(app, world["tabs"]["ana"], "sync", {"open": {"conversation_id": world["chat"], "version": 0}})
    assert answer["data"]["reload"] is True and answer["data"]["changes"] == []


def test_sync_for_a_chat_you_left_says_gone(app, world):
    answer = run(app, world["tabs"]["ben"], "sync", {"open": {"conversation_id": world["chat"], "version": 0}})
    assert answer["data"]["gone"] is True


def test_a_crashing_handler_answers_server_error_and_rolls_back(app, world, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(chats, "mark_read", broken)
    answer = run(app, world["tabs"]["maria"], "read", {"conversation_id": world["chat"], "seq": 1})
    assert answer["code"] == "server_error" and "boom" not in answer["message"]
