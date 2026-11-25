"""Calls: ringing, answering, declining, hanging up, passing the browsers' connection notes, group
calls, and what a call leaves behind (a line in the chat, a missed-call notification). The sound
and pictures themselves are WebRTC between browsers, checked in Edge, not here."""
import json

import pytest
from sqlalchemy import select

from paperplane import calls, chats, db, events
from paperplane.dates import utcnow
from paperplane.hub import HUB
from paperplane.models import Activity, Call, Message, User

from .conftest import browser, make_user, post, text, words
from .live_helpers import fake_connection


@pytest.fixture
def world(app):
    """Maria and Ana in a DM; a group (Maria, Ana, Ben, Cy); Dan outside. One tab each."""
    ids = {name: make_user(app, name, f"{name.title()} Person") for name in ("maria", "ana", "ben", "cy", "dan")}
    with app.app_context():
        s = db.session()
        dm, _ = chats.start_dm(s, s.get(User, ids["maria"]), s.get(User, ids["ana"]), utcnow())
        group = chats.create_group(s, s.get(User, ids["maria"]), "Club",
                                   [s.get(User, ids[n]) for n in ("ana", "ben", "cy")], utcnow())
        s.commit()
        dm_id, group_id = dm.id, group.id
    hub = app.extensions[HUB]
    tabs = {name: fake_connection(hub, user_id) for name, user_id in ids.items()}
    yield {"ids": ids, "tabs": tabs, "dm": dm_id, "group": group_id, "hub": hub}
    calls.IN_CALL.clear()


def run(app, tab, kind, data=None):
    replies = events.dispatch(app, tab, json.dumps({"type": kind, "ref": "r", "data": data or {}}))
    return replies[0]


def start(app, world, who="maria", chat="dm", kind="audio"):
    reply = run(app, world["tabs"][who], "call.start", {"conversation_id": world[chat], "kind": kind})
    assert reply["type"] == "ack", reply
    return reply["data"]


def system_lines(app, chat_id):
    with app.app_context():
        return db.session().scalars(select(Message.body).where(Message.conversation_id == chat_id,
                                                               Message.kind == "system")).all()


def the_call(app, call_id):
    with app.app_context():
        return db.session().get(Call, call_id)


# ---------- A DM call ----------

def test_a_call_rings_the_other_person_and_connects(app, world):
    answer = start(app, world)
    assert answer["join"] is False and answer["ice_servers"][0]["urls"].startswith("stun:")
    call_id = answer["call"]["id"]
    (ring,) = world["tabs"]["ana"].ws.of_type("call.ring")
    assert ring["data"]["from"]["name"] == "Maria Person" and ring["data"]["kind"] == "audio" and ring["data"]["title"] == "Maria Person"
    assert not world["tabs"]["ben"].ws.of_type("call.ring")
    joined = run(app, world["tabs"]["ana"], "call.join", {"call_id": call_id})
    assert joined["type"] == "ack" and {p["name"] for p in joined["data"]["call"]["people"]} == {"Maria Person", "Ana Person"}
    assert world["tabs"]["maria"].ws.of_type("call.joined")[-1]["data"]["user"]["id"] == world["ids"]["ana"]
    assert the_call(app, call_id).answered_at is not None
    # The browsers' notes go to the right person only.
    note = {"sdp": {"type": "offer", "sdp": "v=0..."}}
    assert run(app, world["tabs"]["ana"], "call.signal", {"call_id": call_id, "to": world["ids"]["maria"], "data": note})["type"] == "ack"
    assert world["tabs"]["maria"].ws.of_type("call.signal")[-1]["data"] == {"call_id": call_id, "from": world["ids"]["ana"], "data": note}
    assert not world["tabs"]["ben"].ws.of_type("call.signal")
    # Hanging up ends a DM call for both, with one line in the chat.
    run(app, world["tabs"]["ana"], "call.leave", {"call_id": call_id})
    assert the_call(app, call_id).ended_at is not None
    assert world["tabs"]["maria"].ws.of_type("call.ended")
    (line,) = system_lines(app, world["dm"])
    assert line.startswith("Audio call · ") and line.endswith("sec")


def test_nobody_answers_it_is_a_missed_call(app, world, client):
    call_id = start(app, world, kind="video")["call"]["id"]
    run(app, world["tabs"]["maria"], "call.leave", {"call_id": call_id})  # the caller's tab gave up
    assert system_lines(app, world["dm"]) == ["Missed video call"]
    with app.app_context():
        missed = db.session().scalars(select(Activity).where(Activity.kind == "call_missed")).all()
        assert [(a.user_id, a.actor_id, a.emoji) for a in missed] == [(world["ids"]["ana"], world["ids"]["maria"], "video")]
    page = words(browser(app, world["ids"]["ana"]).get("/notifications"))
    assert "Maria Person called you (missed video call)" in page


def test_declining_ends_a_dm_call_without_a_missed_call(app, world):
    call_id = start(app, world)["call"]["id"]
    assert run(app, world["tabs"]["ana"], "call.decline", {"call_id": call_id})["type"] == "ack"
    assert world["tabs"]["maria"].ws.of_type("call.declined")[-1]["data"]["user"]["first"] == "Ana"
    assert the_call(app, call_id).ended_at is not None
    with app.app_context():
        assert not db.session().scalars(select(Activity).where(Activity.kind == "call_missed")).all()


def test_a_closed_tab_leaves_its_call(app, world):
    call_id = start(app, world)["call"]["id"]
    run(app, world["tabs"]["ana"], "call.join", {"call_id": call_id})
    world["hub"].unregister(world["tabs"]["ana"])
    assert the_call(app, call_id).ended_at is not None


def test_my_other_tabs_stop_ringing(app, world):
    second = fake_connection(world["hub"], world["ids"]["ana"])
    call_id = start(app, world)["call"]["id"]
    run(app, world["tabs"]["ana"], "call.join", {"call_id": call_id})
    assert second.ws.of_type("call.taken")[-1]["data"]["call_id"] == call_id


# ---------- Who may call ----------

def test_strangers_and_blocked_people_cant_call(app, world):
    assert run(app, world["tabs"]["dan"], "call.start", {"conversation_id": world["dm"], "kind": "audio"})["code"] == "not_found"
    call_id = start(app, world)["call"]["id"]
    assert run(app, world["tabs"]["dan"], "call.join", {"call_id": call_id})["code"] == "not_found"
    assert run(app, world["tabs"]["dan"], "call.signal", {"call_id": call_id, "to": world["ids"]["maria"], "data": {}})["code"] == "not_found"
    run(app, world["tabs"]["maria"], "call.leave", {"call_id": call_id})
    post(browser(app, world["ids"]["ana"]), f"/people/{world['ids']['maria']}/block")
    assert run(app, world["tabs"]["maria"], "call.start", {"conversation_id": world["dm"], "kind": "audio"})["code"] == "forbidden"


def test_bad_calls_are_refused(app, world):
    assert run(app, world["tabs"]["maria"], "call.start", {"conversation_id": world["dm"], "kind": "hologram"})["code"] == "invalid"
    call_id = start(app, world)["call"]["id"]
    run(app, world["tabs"]["ana"], "call.join", {"call_id": call_id})
    huge = {"sdp": {"type": "offer", "sdp": "x" * (calls.SIGNAL_MAX + 10)}}
    assert run(app, world["tabs"]["maria"], "call.signal", {"call_id": call_id, "to": world["ids"]["ana"], "data": huge})["code"] == "invalid"
    assert run(app, world["tabs"]["maria"], "call.signal", {"call_id": call_id, "to": world["ids"]["maria"], "data": {}})["code"] == "not_found"


def test_no_ringing_for_someone_who_restricted_the_caller(app, world):
    post(browser(app, world["ids"]["ana"]), f"/people/{world['ids']['maria']}/restrict")
    start(app, world)
    assert not world["tabs"]["ana"].ws.of_type("call.ring")


# ---------- Group calls ----------

def test_a_group_call_goes_on_until_the_last_person_leaves(app, world):
    answer = start(app, world, chat="group", kind="video")
    call_id = answer["call"]["id"]
    rings = {name: world["tabs"][name].ws.of_type("call.ring") for name in ("ana", "ben", "cy")}
    assert all(rings.values()) and rings["ben"][0]["data"]["title"] == "Club" and rings["ben"][0]["data"]["is_group"]
    run(app, world["tabs"]["ana"], "call.join", {"call_id": call_id})
    joined = run(app, world["tabs"]["ben"], "call.join", {"call_id": call_id})["data"]["call"]
    assert len(joined["people"]) == 3  # Ben offers to Maria and Ana
    # Starting again in the same chat hands the call back to join.
    again = run(app, world["tabs"]["cy"], "call.start", {"conversation_id": world["group"], "kind": "audio"})["data"]
    assert again["join"] is True and again["call"]["id"] == call_id
    state = world["tabs"]["cy"].ws.of_type("call.state")[-1]["data"]
    assert state["call_id"] == call_id and state["count"] == 3
    for name in ("maria", "ana"):
        run(app, world["tabs"][name], "call.leave", {"call_id": call_id})
    assert the_call(app, call_id).ended_at is None  # Ben is still there
    run(app, world["tabs"]["ben"], "call.leave", {"call_id": call_id})
    assert the_call(app, call_id).ended_at is not None
    assert system_lines(app, world["group"])[-1].startswith("Group video call · ")
    with app.app_context():
        missed = db.session().scalars(select(Activity.user_id).where(Activity.kind == "call_missed")).all()
        assert missed == [world["ids"]["cy"]]  # only Cy never joined
    assert world["tabs"]["cy"].ws.of_type("call.state")[-1]["data"]["call_id"] is None


def test_a_group_call_has_room_for_six(app, world, monkeypatch):
    monkeypatch.setattr(calls, "MAX_PEOPLE", 2)
    call_id = start(app, world, chat="group")["call"]["id"]
    run(app, world["tabs"]["ana"], "call.join", {"call_id": call_id})
    assert run(app, world["tabs"]["ben"], "call.join", {"call_id": call_id})["code"] == "too_many"


def test_a_call_nobodys_tab_is_in_is_ended_when_someone_starts_another(app, world):
    old = start(app, world, chat="group")["call"]["id"]
    calls.IN_CALL.clear()  # the server restarted
    fresh = start(app, world, who="ana", chat="group")
    assert fresh["join"] is False and fresh["call"]["id"] != old
    assert the_call(app, old).ended_at is not None


# ---------- The pages ----------

def test_the_chat_has_call_buttons_and_a_join_bar(app, world):
    maria = browser(app, world["ids"]["maria"])
    page = text(maria.get(f"/chats/{world['group']}"))
    assert 'data-call-start="audio" title="Group audio call" hidden' in page and 'data-call-start="video"' in page
    assert '<div class="call-banner" data-call-banner hidden>' in page
    call_id = start(app, world, who="ana", chat="group", kind="video")["call"]["id"]
    page = text(maria.get(f"/chats/{world['group']}"))
    assert f'data-call-id="{call_id}" data-call-kind="video"' in page and "Video call going on · 1 in the call" in page
    dm = text(maria.get(f"/chats/{world['dm']}"))
    assert 'title="Audio call"' in dm and 'title="Video call"' in dm


def test_the_demo_teammate_declines_and_texts_back(app):
    from paperplane import crew
    client = browser(app)
    post(client, "/demo")
    with client.session_transaction() as session:
        mika_id = session["user_id"]
    with app.app_context():
        s = db.session()
        mika = s.get(User, mika_id)
        ana = s.scalar(select(User).where(User.demo_team_id == mika.demo_team_id, User.username.like("%.ana")))
        dm = chats.start_dm(s, mika, ana, utcnow())[0].id
        version = mika.session_version
    hub = app.extensions[HUB]
    tab = fake_connection(hub, mika_id, version)
    team = crew.get(app)
    team.seed = 3
    team.step(0)
    reply = run(app, tab, "call.start", {"conversation_id": dm, "kind": "audio"})
    assert reply["type"] == "ack", reply
    call_id = reply["data"]["call"]["id"]
    team.step(1)
    team.step(5)
    assert the_call(app, call_id).ended_at is not None and tab.ws.of_type("call.declined")
    team.step(9)
    with app.app_context():
        last = db.session().scalars(select(Message).where(Message.conversation_id == dm, Message.kind == "text")
                                    .order_by(Message.id.desc())).first()
        assert last.sender_id == ana.id and last.body in crew.CALL_REPLIES
    calls.IN_CALL.clear()
