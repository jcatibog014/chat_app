"""Stories: who may see them (everyone you chat with, or Close Friends), 24 hours and gone,
Seen by for the owner only, reactions, private replies, muting, and the live events."""
import io
import re
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import func, select

from paperplane import db, stories
from paperplane.dates import utcnow
from paperplane.hub import HUB
from paperplane.models import Message, Story, StoryView, User
from paperplane.problems import NotFound, TooMany

from .conftest import browser, make_user, post, text, words
from .live_helpers import fake_connection


@pytest.fixture
def crowd(app):
    """Ella chats with Finn and Gus (a DM each). Hal is a stranger."""
    ids = {name: make_user(app, name, f"{name.title()} Person") for name in ("ella", "finn", "gus", "hal")}
    clients = {name: browser(app, user_id) for name, user_id in ids.items()}
    chats = {}
    for friend in ("finn", "gus"):
        response = post(clients["ella"], "/chats/new/dm", {"username": friend})
        chats[friend] = int(response.headers["Location"].rsplit("/", 1)[1])
    return {"ids": ids, "client": clients, "chat": chats}


def card(client, words_on_it="Hello from Ella", audience="everyone", background="pink"):
    return post(client, "/stories/new", {"kind": "text", "text": words_on_it, "background": background,
                                         "audience": audience})


def photo(client, caption="", camera="SpyCam 3000"):
    picture = Image.new("RGB", (900, 1600), "#d81b60")
    exif = Image.Exif()
    exif[0x010F] = camera  # "Make": a stand-in for the details phones add (GPS...)
    raw = io.BytesIO()
    picture.save(raw, "JPEG", exif=exif.tobytes())
    with client.session_transaction() as session:
        session["csrf"] = "test-csrf-token"
    return client.post("/stories/new", content_type="multipart/form-data", data={
        "csrf_token": "test-csrf-token", "kind": "photo", "text": caption, "audience": "everyone",
        "photo": (io.BytesIO(raw.getvalue()), "beach.jpg")})


def only_story(app):
    with app.app_context():
        return db.session().scalars(select(Story)).one()


def end_story(app, story_id, hours=25):
    with app.app_context():
        s = db.session()
        story = s.get(Story, story_id)
        story.created_at -= timedelta(hours=hours)
        story.expires_at -= timedelta(hours=hours)
        s.commit()


# ---------- Posting ----------

def test_a_text_card_is_posted_and_shown_to_the_people_you_chat_with(app, crowd):
    response = card(crowd["client"]["ella"])
    assert response.status_code == 302 and response.headers["Location"].endswith(f"/stories/{crowd['ids']['ella']}")
    story = only_story(app)
    assert story.kind == "text" and story.background == "pink" and story.audience == "everyone"
    assert story.expires_at - story.created_at == timedelta(hours=24)
    for friend in ("finn", "gus"):
        page = text(crowd["client"][friend].get(f"/stories/{crowd['ids']['ella']}"))
        assert "Hello from Ella" in page and "story-bg-pink" in page
    # Strangers: "not found", the same as a story that was never there.
    assert crowd["client"]["hal"].get(f"/stories/{crowd['ids']['ella']}").status_code == 404
    assert crowd["client"]["hal"].get("/stories/999999").status_code == 404


def test_a_photo_story_loses_its_hidden_details(app, crowd):
    assert photo(crowd["client"]["ella"], caption="At the beach").status_code == 302
    story = only_story(app)
    assert story.kind == "photo" and story.text == "At the beach"
    image = crowd["client"]["finn"].get(f"/stories/{story.id}/image")
    assert image.status_code == 200 and image.mimetype == "image/webp"
    assert image.headers["X-Content-Type-Options"] == "nosniff"
    saved = Image.open(io.BytesIO(image.data))
    assert max(saved.size) <= 1600 and b"SpyCam" not in image.data and 0x010F not in saved.getexif()
    assert crowd["client"]["finn"].get(f"/stories/{story.id}/thumb").status_code == 200
    # The picture's address is checked too.
    for name in ("hal",):
        assert crowd["client"][name].get(f"/stories/{story.id}/image").status_code == 404
    assert browser(app).get(f"/stories/{story.id}/image").status_code == 302  # logged out: log in first


def test_a_story_needs_words_or_a_picture(app, crowd):
    client = crowd["client"]["ella"]
    blank = card(client, words_on_it="   ")
    assert blank.status_code == 400 and "Write something for your card." in words(blank)
    assert post(client, "/stories/new", {"kind": "photo", "audience": "everyone"}).status_code == 400
    assert post(client, "/stories/new", {"kind": "text", "text": "Hi", "background": "plaid",
                                         "audience": "everyone"}).status_code == 400
    assert post(client, "/stories/new", {"kind": "text", "text": "Hi", "background": "pink",
                                         "audience": "the world"}).status_code == 400
    assert post(client, "/stories/new", {"kind": "text", "text": "x" * 251, "background": "pink",
                                         "audience": "everyone"}).status_code == 400
    with app.app_context():
        assert db.session().scalar(select(func.count(Story.id))) == 0


def test_a_picture_story_must_be_a_picture(app, crowd):
    client = crowd["client"]["ella"]
    response = client.post("/stories/new", content_type="multipart/form-data", data={
        "csrf_token": "test-csrf-token", "kind": "photo", "audience": "everyone",
        "photo": (io.BytesIO(b"just some notes"), "notes.txt")})
    assert response.status_code == 400 and "must be a picture" in words(response)


def test_at_most_ten_stories_at_a_time(app, crowd):
    for number in range(stories.MAX_LIVE):
        assert card(crowd["client"]["ella"], f"Story {number}").status_code == 302
    response = card(crowd["client"]["ella"], "One too many")
    assert response.status_code == 400 and "10 stories at a time" in words(response)


def test_the_new_story_page_works_without_javascript(app, crowd):
    page = text(crowd["client"]["ella"].get("/stories/new"))
    assert "<title>New Story - Paper Plane</title>" in page
    for part in ('name="kind" value="photo"', 'name="kind" value="text"', 'name="photo"', 'name="text"',
                 'name="background" value="purple"', 'name="audience" value="everyone"',
                 'name="audience" value="close"', 'enctype="multipart/form-data"'):
        assert part in page, part


# ---------- Who may see ----------

def test_close_friends_only_reach_the_list(app, crowd):
    ella = crowd["client"]["ella"]
    response = post(ella, "/settings/privacy", {"action": "close_friends", "friend": [str(crowd["ids"]["finn"])]})
    assert response.status_code == 302
    card(ella, "Just for close friends", audience="close")
    url = f"/stories/{crowd['ids']['ella']}"
    assert "Just for close friends" in text(crowd["client"]["finn"].get(url))
    assert crowd["client"]["gus"].get(url).status_code == 404
    # The owner sees it marked, and Gus's circles don't show Ella at all.
    assert 'class="close-tag"' in text(ella.get("/stories"))
    assert f'href="{url}"' not in text(crowd["client"]["gus"].get("/chats"))


def test_close_friends_are_only_people_you_chat_with(app, crowd):
    post(crowd["client"]["ella"], "/settings/privacy",
         {"action": "close_friends", "friend": [str(crowd["ids"]["finn"]), str(crowd["ids"]["hal"])]})
    with app.app_context():
        assert stories.close_friend_ids(db.session(), crowd["ids"]["ella"]) == {crowd["ids"]["finn"]}


def test_a_blocked_person_cant_see_your_stories(app, crowd):
    card(crowd["client"]["ella"])
    post(crowd["client"]["ella"], f"/people/{crowd['ids']['finn']}/block")
    assert crowd["client"]["finn"].get(f"/stories/{crowd['ids']['ella']}").status_code == 404
    # And it works both ways: Ella doesn't see Finn's.
    card(crowd["client"]["finn"], "Finn's day")
    assert crowd["client"]["ella"].get(f"/stories/{crowd['ids']['finn']}").status_code == 404


def test_demo_people_and_real_people_never_see_each_others_stories(app, crowd):
    demo = browser(app)
    post(demo, "/demo")
    card(crowd["client"]["ella"], "Real world")
    with app.app_context():
        s = db.session()
        mika = s.scalar(select(User).where(User.demo_team_id.is_not(None), User.is_bot.is_(False)))
        demo_story = s.scalar(select(Story).where(Story.user_id == mika.id))
    assert demo.get(f"/stories/{crowd['ids']['ella']}").status_code == 404
    assert crowd["client"]["finn"].get(f"/stories/{mika.id}").status_code == 404
    assert crowd["client"]["finn"].get(f"/stories/{demo_story.id}/image").status_code == 404


def test_a_story_ends_after_24_hours_and_is_purged(app, crowd):
    card(crowd["client"]["ella"])
    story = only_story(app)
    end_story(app, story.id)
    assert crowd["client"]["finn"].get(f"/stories/{crowd['ids']['ella']}").status_code == 404
    assert "Hello from Ella" not in text(crowd["client"]["finn"].get("/stories"))
    result = app.test_cli_runner().invoke(args=["purge-stories"])
    assert result.exit_code == 0 and "1" in result.output
    with app.app_context():
        assert db.session().scalar(select(func.count(Story.id))) == 0


# ---------- Seen by, reactions ----------

def test_seen_by_counts_each_person_once_and_only_the_owner_sees_it(app, crowd):
    card(crowd["client"]["ella"])
    url = f"/stories/{crowd['ids']['ella']}"
    for _ in range(2):
        crowd["client"]["finn"].get(url)
    crowd["client"]["ella"].get(url)  # the owner looking doesn't count
    with app.app_context():
        assert db.session().scalar(select(func.count(StoryView.id))) == 1
    owner = words(crowd["client"]["ella"].get(url))
    assert "Seen by 1" in owner and "Finn Person" in owner
    viewer = text(crowd["client"]["finn"].get(url))
    assert "Seen by" not in viewer and "story-seen-list" not in viewer


def test_a_reaction_replaces_and_the_same_one_takes_it_back(app, crowd):
    card(crowd["client"]["ella"])
    story = only_story(app)
    finn = crowd["client"]["finn"]
    post(finn, f"/stories/{story.id}/react", {"emoji": "❤️"})
    post(finn, f"/stories/{story.id}/react", {"emoji": "😂"})
    with app.app_context():
        s = db.session()
        assert stories.my_reaction(s, s.get(User, crowd["ids"]["finn"]), story) == "😂"
    assert "😂" in text(crowd["client"]["ella"].get(f"/stories/{crowd['ids']['ella']}"))
    post(finn, f"/stories/{story.id}/react", {"emoji": "😂"})
    with app.app_context():
        s = db.session()
        assert stories.my_reaction(s, s.get(User, crowd["ids"]["finn"]), story) is None
    # Only the six reactions, and never on a story you can't see.
    assert post(finn, f"/stories/{story.id}/react", {"emoji": "💩"}).status_code == 302
    assert post(crowd["client"]["hal"], f"/stories/{story.id}/react", {"emoji": "❤️"}).status_code == 404


# ---------- Replying privately ----------

def test_a_reply_lands_in_the_dm_with_the_story_quoted(app, crowd):
    card(crowd["client"]["ella"], "Guess where I am")
    story = only_story(app)
    response = post(crowd["client"]["finn"], f"/stories/{story.id}/reply", {"body": "Baguio?"})
    assert response.status_code == 302 and f"/chats/{crowd['chat']['finn']}" in response.headers["Location"]
    with app.app_context():
        message = db.session().scalars(select(Message).where(Message.body == "Baguio?")).one()
        assert message.story_id == story.id and message.is_story_reply
    page = words(crowd["client"]["ella"].get(f"/chats/{crowd['chat']['finn']}"))
    assert "Replied to Ella's story" in page and "Guess where I am" in page and "Baguio?" in page
    # A day later the quote says so (and the story itself is gone).
    end_story(app, story.id)
    assert "Story expired" in words(crowd["client"]["finn"].get(f"/chats/{crowd['chat']['finn']}"))
    stories_purged = app.test_cli_runner().invoke(args=["purge-stories"])
    assert stories_purged.exit_code == 0
    assert "Story expired" in words(crowd["client"]["finn"].get(f"/chats/{crowd['chat']['finn']}"))


def test_a_reply_needs_words_and_a_story_you_can_see(app, crowd):
    card(crowd["client"]["ella"])
    story = only_story(app)
    assert post(crowd["client"]["hal"], f"/stories/{story.id}/reply", {"body": "Hi"}).status_code == 404
    response = post(crowd["client"]["finn"], f"/stories/{story.id}/reply", {"body": "   "})
    assert response.status_code == 302 and "/stories/" in response.headers["Location"]
    with app.app_context():
        assert db.session().scalar(select(func.count(Message.id)).where(Message.is_story_reply.is_(True))) == 0


def test_a_story_reply_is_refused_after_a_block(app, crowd):
    card(crowd["client"]["ella"])
    story = only_story(app)
    with app.app_context():
        s = db.session()
        from paperplane import blocks
        blocks.block(s, s.get(User, crowd["ids"]["ella"]), s.get(User, crowd["ids"]["finn"]), utcnow())
        s.commit()
        with pytest.raises(NotFound):
            stories.reply(s, s.get(User, crowd["ids"]["finn"]), story.id, "Hi", utcnow())


# ---------- The circles, the Stories page, muting ----------

def test_the_circles_show_new_stories_first_and_grey_once_seen(app, crowd):
    card(crowd["client"]["finn"], "Finn's story")
    card(crowd["client"]["gus"], "Gus's story")
    ella = crowd["client"]["ella"]
    crowd["client"]["ella"].get(f"/stories/{crowd['ids']['finn']}")  # Ella has seen Finn's
    tray = text(ella.get("/stories/tray"))
    gus_at, finn_at = tray.index("Gus"), tray.index("Finn")
    assert gus_at < finn_at  # unseen first
    rings = re.findall(r'class="ring (is-\w+)"', tray)
    assert rings[0] == "is-empty" and rings[1:] == ["is-new", "is-seen"]  # Ella has none of her own yet
    assert 'data-story-tray' in text(ella.get("/chats"))


def test_muting_hides_someones_stories_until_unmuted(app, crowd):
    card(crowd["client"]["finn"], "Finn's story")
    ella = crowd["client"]["ella"]
    finn_id = crowd["ids"]["finn"]
    assert post(ella, f"/stories/people/{finn_id}/mute").status_code == 302
    assert "Finn" not in text(ella.get("/stories/tray")) and "Finn's story" not in text(ella.get("/stories"))
    privacy = words(ella.get("/settings/privacy"))
    assert "Muted Stories" in privacy and "Finn Person" in privacy
    # Muting only hides: the story can still be opened from a link.
    assert ella.get(f"/stories/{finn_id}").status_code == 200
    post(ella, f"/stories/people/{finn_id}/unmute")
    assert "Finn" in text(ella.get("/stories/tray"))
    # Strangers can't be muted (so ids can't be probed).
    assert post(ella, f"/stories/people/{crowd['ids']['hal']}/mute").status_code == 404


def test_only_the_owner_can_delete_a_story(app, crowd):
    card(crowd["client"]["ella"])
    story = only_story(app)
    assert post(crowd["client"]["finn"], f"/stories/{story.id}/delete").status_code == 404
    assert post(crowd["client"]["ella"], f"/stories/{story.id}/delete").status_code == 302
    with app.app_context():
        assert db.session().get(Story, story.id) is None


def test_the_viewer_goes_to_the_next_story_then_the_next_person(app, crowd):
    card(crowd["client"]["finn"], "Finn one")
    card(crowd["client"]["finn"], "Finn two")
    card(crowd["client"]["gus"], "Gus one")
    ella = crowd["client"]["ella"]
    first = text(ella.get(f"/stories/{crowd['ids']['finn']}"))
    assert "Finn one" in first
    next_url = re.search(r'data-next="([^"]+)"', first).group(1).replace("&amp;", "&")
    second = text(ella.get(next_url))
    assert "Finn two" in second
    last = re.search(r'data-next="([^"]+)"', second).group(1)
    assert last.endswith(f"/stories/{crowd['ids']['gus']}") or last == "/stories"
    # Opening Finn again starts at the first one not seen yet: none left, so the first.
    assert "Finn one" in text(ella.get(f"/stories/{crowd['ids']['finn']}"))


# ---------- Live ----------

def test_a_new_story_reaches_only_the_people_who_may_see_it(app, crowd):
    hub = app.extensions[HUB]
    tabs = {name: fake_connection(hub, crowd["ids"][name]) for name in ("finn", "gus", "hal")}
    post(crowd["client"]["ella"], "/settings/privacy", {"action": "close_friends", "friend": [str(crowd["ids"]["finn"])]})
    card(crowd["client"]["ella"], "Close only", audience="close")
    assert tabs["finn"].ws.of_type("story.new")
    assert not tabs["gus"].ws.of_type("story.new") and not tabs["hal"].ws.of_type("story.new")


def test_seen_and_reactions_reach_the_owner_live(app, crowd):
    card(crowd["client"]["ella"])
    story = only_story(app)
    owner = fake_connection(app.extensions[HUB], crowd["ids"]["ella"])
    other = fake_connection(app.extensions[HUB], crowd["ids"]["gus"])
    crowd["client"]["finn"].get(f"/stories/{crowd['ids']['ella']}")
    post(crowd["client"]["finn"], f"/stories/{story.id}/react", {"emoji": "🎉"})
    (seen,) = owner.ws.of_type("story.seen")
    assert seen["data"] == {"story_id": story.id, "count": 1}
    (reacted,) = owner.ws.of_type("story.reacted")
    assert reacted["data"]["emoji"] == "🎉" and reacted["data"]["name"] == "Finn"
    assert not other.ws.of_type("story.seen") and not other.ws.of_type("story.reacted")


def test_deleting_tells_the_audience(app, crowd):
    card(crowd["client"]["ella"])
    story = only_story(app)
    tab = fake_connection(app.extensions[HUB], crowd["ids"]["finn"])
    post(crowd["client"]["ella"], f"/stories/{story.id}/delete")
    (gone,) = tab.ws.of_type("story.gone")
    assert gone["data"]["story_id"] == story.id


def test_too_many_is_a_problem_in_the_code_too(app, crowd):
    with app.app_context():
        s = db.session()
        ella = s.get(User, crowd["ids"]["ella"])
        for number in range(stories.MAX_LIVE):
            stories.post(s, ella, "text", f"Story {number}", "blue", "everyone", utcnow())
        with pytest.raises(TooMany):
            stories.post(s, ella, "text", "Eleven", "blue", "everyone", utcnow())
