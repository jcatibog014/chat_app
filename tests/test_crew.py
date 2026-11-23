"""The demo crew: teammates come online, type and answer, through the real code paths."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from paperplane import chats, crew, db, presence, tokens
from paperplane.dates import utcnow
from paperplane.hub import HUB
from paperplane.models import Conversation, DemoTeam, Message, User

from .conftest import browser, post, words
from .live_helpers import fake_connection


@pytest.fixture
def demo(app):
    """A started demo with Mika's (real, fake-socket) tab open, and the crew with a fixed seed."""
    client = browser(app)
    post(client, "/demo")
    with client.session_transaction() as session:
        mika_id = session["user_id"]
    with app.app_context():
        s = db.session()
        mika = s.get(User, mika_id)
        team_id = mika.demo_team_id
        people = {user.handle: user.id for user in s.scalars(select(User).where(User.demo_team_id == team_id))}
        rooms = {}
        for conversation in s.scalars(select(Conversation).where(Conversation.demo_team_id == team_id)):
            title = conversation.title or chats.title_for(s, conversation, mika_id)
            rooms[title] = conversation.id
    hub = app.extensions[HUB]
    tab = fake_connection(hub, mika_id)
    the_crew = crew.get(app)
    the_crew.seed = 7
    return {"client": client, "people": people, "rooms": rooms, "tab": tab, "crew": the_crew, "hub": hub,
            "team": team_id}


def messages_from(app, chat_id, user_id):
    with app.app_context():
        return db.session().scalars(select(Message.body).where(Message.conversation_id == chat_id,
                                                               Message.sender_id == user_id)).all()


def test_teammates_come_online_when_the_visitor_arrives(app, demo):
    team, people, hub = demo["crew"], demo["people"], demo["hub"]
    team.step(0)
    team.step(2)
    assert hub.is_connected(people["ana"]) and hub.is_connected(people["ben"])
    assert not hub.is_connected(people["carlo"])
    assert presence.get(app).of(people["ana"]) == "online"
    heard = {frame["data"]["user_id"] for frame in demo["tab"].ws.of_type("presence")}
    assert {people["ana"], people["ben"]} <= heard
    team.step(15)
    assert hub.is_connected(people["carlo"])


def test_a_teammate_reads_types_and_answers(app, demo):
    team, people, rooms, tab = demo["crew"], demo["people"], demo["rooms"], demo["tab"]
    team.step(0)
    post(demo["client"], f"/chats/{rooms['Ana Reyes']}/send", {"body": "Hello Ana! Did you get the files?"})
    team.step(1)
    team.step(3.5)
    assert tab.ws.of_type("typing") and tab.ws.of_type("typing")[0]["data"]["name"] == "Ana"
    reads = [frame for frame in tab.ws.of_type("read") if frame["data"]["user_id"] == people["ana"]]
    assert reads  # "Seen" before the answer
    team.step(10)
    answers = messages_from(app, rooms["Ana Reyes"], people["ana"])
    assert answers[-1] in ("Hi Mika!", "Hey! What's up?", "Hello! How's your day going?")
    assert any(frame["data"]["sender_id"] == people["ana"] for frame in tab.ws.of_type("message.new"))


def test_one_answer_at_a_time_per_chat(app, demo):
    team, people, rooms = demo["crew"], demo["people"], demo["rooms"]
    team.step(0)
    for text in ("one", "two", "three"):
        post(demo["client"], f"/chats/{rooms['Ben Cruz']}/send", {"body": text})
    team.step(1)
    team.step(10)
    before = len(messages_from(app, rooms["Ben Cruz"], people["ben"]))
    team.step(20)
    assert len(messages_from(app, rooms["Ben Cruz"], people["ben"])) == before


def test_the_crew_never_answers_itself(app, demo):
    """The crew's own messages in a group don't start a conversation between teammates."""
    team, rooms = demo["crew"], demo["rooms"]
    team.step(0)
    team.step(200)  # all the scripted lines have been said
    assert not team.heard_queue
    assert not any(play.replying for play in team.teams.values())
    with app.app_context():
        texts = db.session().scalars(select(Message.body).where(Message.conversation_id == rooms["Papel Studio"])).all()
    assert texts.count("The printer works again, by the way.") == 1


def test_scripted_lines_arrive_on_time(app, demo):
    team, people, rooms = demo["crew"], demo["people"], demo["rooms"]
    team.step(0)
    team.step(21)
    assert "The printer works again, by the way." not in messages_from(app, rooms["Papel Studio"], people["carlo"])
    team.step(23)
    assert "The printer works again, by the way." in messages_from(app, rooms["Papel Studio"], people["carlo"])
    team.step(130)
    assert "I bought trail mix for everyone!" in messages_from(app, rooms["Weekend Hike"], people["dana"])


def test_the_demo_goes_quiet_a_minute_after_the_visitor_leaves(app, demo):
    team, people, hub = demo["crew"], demo["people"], demo["hub"]
    team.step(0)
    team.step(2)
    hub.unregister(demo["tab"])
    team.step(30)
    assert hub.is_connected(people["ana"])
    team.step(70)
    assert not hub.is_connected(people["ana"]) and not team.teams


def test_replies_follow_the_words():
    import random
    rng = random.Random(1)
    assert crew.reply_for("Thanks so much!", rng) in ("You're welcome!", "Anytime!", "No problem at all.")
    assert crew.reply_for("Ready for the hike?", rng) in ("Can't wait for Saturday!", "I'll bring extra water.",
                                                          "Don't forget your jacket!")
    assert crew.reply_for("this is something else", rng) in crew.FALLBACK


# ---------- Friend links ----------

def test_the_visitor_gets_a_link_per_teammate(app, demo):
    page = words(demo["client"].get("/demo/friends"))
    assert "Invite a Friend" in page
    for name in ("Ana Reyes", "Ben Cruz", "Carlo Diaz", "Dana Lim"):
        assert name in page
    assert "/demo/join/" in demo["client"].get("/demo/friends").get_data(as_text=True)


def test_a_friend_link_shows_a_page_then_logs_in_on_the_button(app, demo):
    with app.app_context():
        token = tokens.make("friend", demo["team"], demo["people"]["ana"])
    friend = browser(app)
    page = friend.get(f"/demo/join/{token}")
    assert page.status_code == 200 and "Join as Ana" in words(page)
    with friend.session_transaction() as session:
        assert "user_id" not in session  # opening the link alone does nothing
    response = post(friend, f"/demo/join/{token}")
    assert response.status_code == 302
    with friend.session_transaction() as session:
        assert session["user_id"] == demo["people"]["ana"]
    assert "You are Ana" in words(friend.get("/chats"))


def test_bad_or_old_friend_links_are_not_found(app, demo):
    with app.app_context():
        good = tokens.make("friend", demo["team"], demo["people"]["ana"])
        wrong_purpose = tokens.make("invite", demo["team"], demo["people"]["ana"])
        mika_link = tokens.make("friend", demo["team"], demo["people"]["mika"])
    for token in ("nonsense", good[:-2] + "xx", wrong_purpose, mika_link):
        assert browser(app).get(f"/demo/join/{token}").status_code == 404
    with app.app_context():
        s = db.session()
        s.get(DemoTeam, demo["team"]).expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert browser(app).get(f"/demo/join/{good}").status_code == 404


def test_only_the_visitor_has_friend_links(app, demo):
    with app.app_context():
        token = tokens.make("friend", demo["team"], demo["people"]["ana"])
    friend = browser(app)
    post(friend, f"/demo/join/{token}")
    assert friend.get("/demo/friends").status_code == 404


def test_the_crew_steps_aside_for_a_real_friend(app, demo):
    team, people, rooms, hub = demo["crew"], demo["people"], demo["rooms"], demo["hub"]
    team.step(0)
    team.step(2)
    fake_connection(hub, people["ana"])  # a friend opened the link and has a tab open
    post(demo["client"], f"/chats/{rooms['Ana Reyes']}/send", {"body": "Hi Ana, is that really you?"})
    team.step(3)
    team.step(15)
    assert messages_from(app, rooms["Ana Reyes"], people["ana"])[-1] == "Thank you!"  # only the seeded one


def test_a_friends_message_gets_answers_from_the_others(app, demo):
    """A friend playing Ana speaks in a group: one of the other teammates answers."""
    team, people, rooms, hub = demo["crew"], demo["people"], demo["rooms"], demo["hub"]
    with app.app_context():
        s = db.session()
        chats.send_message(s, s.get(User, people["ana"]), rooms["Weekend Hike"], "Who's bringing snacks?", None, utcnow())
        s.commit()
    team.step(0)
    team.step(1)
    team.step(12)
    with app.app_context():
        senders = db.session().scalars(select(Message.sender_id).where(
            Message.conversation_id == rooms["Weekend Hike"]).order_by(Message.seq.desc()).limit(1)).all()
    assert senders[0] in (people["ben"], people["dana"])


def test_a_teammate_looks_at_the_visitors_new_story_and_reacts(app, demo):
    from paperplane.models import Story, StoryReaction, StoryView
    team, people, tab = demo["crew"], demo["people"], demo["tab"]
    team.step(0)
    post(demo["client"], "/stories/new", {"kind": "text", "text": "Fresh one", "background": "teal",
                                         "audience": "everyone"})
    with app.app_context():
        story_id = db.session().scalar(select(Story.id).where(Story.text == "Fresh one"))
    team.step(1)
    team.step(5.5)
    with app.app_context():
        s = db.session()
        assert s.scalar(select(StoryView.viewer_id).where(StoryView.story_id == story_id)) == people["ana"]
    assert [frame["data"]["story_id"] for frame in tab.ws.of_type("story.seen")] == [story_id]
    team.step(8)
    with app.app_context():
        reaction = db.session().scalar(select(StoryReaction).where(StoryReaction.story_id == story_id))
        assert reaction.user_id == people["ana"] and reaction.emoji in crew.STORY_REACTIONS
    assert tab.ws.of_type("story.reacted")[-1]["data"]["name"] == "Ana"


def test_the_crew_skips_a_close_friends_story_its_teammates_cant_see(app, demo):
    from paperplane.models import CloseFriend, Story, StoryView
    team = demo["crew"]
    team.step(0)
    with app.app_context():
        s = db.session()
        s.query(CloseFriend).filter(CloseFriend.owner_id == demo["tab"].user_id).delete()
        s.commit()
    post(demo["client"], "/stories/new", {"kind": "text", "text": "Nobody", "background": "teal", "audience": "close"})
    team.step(1)
    team.step(10)
    with app.app_context():
        s = db.session()
        story_id = s.scalar(select(Story.id).where(Story.text == "Nobody"))
        assert not s.scalar(select(StoryView.id).where(StoryView.story_id == story_id))
