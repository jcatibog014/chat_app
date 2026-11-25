"""Try Demo: a private sample team for each visitor, gone after 24 hours."""
from datetime import timedelta

from sqlalchemy import func, select

from paperplane import db, seed
from paperplane.dates import utcnow
from paperplane.models import DemoTeam, User

from .conftest import browser, is_logged_in, post, words


def start_demo(app):
    client = browser(app)
    response = post(client, "/demo")
    assert response.status_code == 302
    return client


def me(app, client):
    with client.session_transaction() as session:
        user_id = session["user_id"]
    with app.app_context():
        return db.session().get(User, user_id)


def test_try_demo_makes_a_team_and_logs_you_in_as_mika(app):
    client = start_demo(app)
    mika = me(app, client)
    assert mika.name == "Mika Santos" and mika.handle == "mika" and mika.is_demo and not mika.is_bot
    page = words(client.get("/chats", follow_redirects=True))
    assert "You are Mika" in page and "Demo" in page
    with app.app_context():
        team = db.session().scalars(select(User).where(User.demo_team_id == mika.demo_team_id)).all()
        assert sorted(person.handle for person in team) == ["ana", "ben", "carlo", "dana", "mika"]
        assert sum(person.is_bot for person in team) == 4


def test_each_visitor_gets_their_own_team(app):
    first, second = me(app, start_demo(app)), me(app, start_demo(app))
    assert first.demo_team_id != second.demo_team_id


def test_demo_people_cant_log_in_with_a_password(app, client):
    mika = me(app, start_demo(app))
    for password in ("", "password", mika.username):
        response = post(client, "/login", {"username": mika.username, "password": password})
        assert response.status_code == 401


def test_the_demo_cant_change_its_account(app):
    client = start_demo(app)
    response = post(client, "/settings", {"action": "name", "user_name": "Someone Else"})
    assert response.status_code == 403
    assert "can't be changed" in words(client.get("/settings"))


def test_an_old_demo_is_deleted_and_its_cookie_stops_working(app):
    client = start_demo(app)
    mika = me(app, client)
    with app.app_context():
        s = db.session()
        assert seed.purge(s, utcnow() + timedelta(hours=25)) == 1
        s.commit()
        assert s.scalar(select(func.count(DemoTeam.id))) == 0
        assert s.scalar(select(func.count(User.id))) == 0
    assert not is_logged_in(client)
    # A new demo never gets the old one's ids.
    newer = me(app, start_demo(app))
    assert newer.id > mika.id + 4 and newer.demo_team_id > mika.demo_team_id


def test_an_expired_demo_logs_out_even_before_it_is_deleted(app):
    client = start_demo(app)
    mika = me(app, client)
    with app.app_context():
        s = db.session()
        s.get(DemoTeam, mika.demo_team_id).expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert not is_logged_in(client)


def creator_handle(app, chat):
    with app.app_context():
        return db.session().get(User, chat.created_by_id).handle


def people_with_stories(app, mika):
    from paperplane.models import Story
    with app.app_context():
        return set(db.session().scalars(select(Story.user_id).join(User, User.id == Story.user_id).where(
            User.demo_team_id == mika.demo_team_id)))


def demo_pages(app, client):
    """Every page the demo can open, with what each must show (an example, never an empty page)."""
    mika = me(app, client)
    with app.app_context():
        from paperplane.models import Conversation
        chats = db.session().scalars(select(Conversation).where(Conversation.demo_team_id == mika.demo_team_id)).all()
    pages = {"/chats": ["Papel Studio", "Weekend Hike", "Launch Plans", "Ana Reyes", "Ben Cruz", "Carlo Diaz"],
             "/chats/new": ["People You Know", "Dana Lim"],
             "/settings": ["Your Account", "Time Zone"],
             "/settings/appearance": ["Pink", "Purple"], "/settings/notifications": ["On This Device"],
             "/settings/privacy": ["Show When I'm Online", "Blocked People", "Close Friends", "Muted Stories",
                                   "Dana Lim"],
             "/settings/devices": ["Leave the Demo"],
             "/stories": ["Your Stories", ">4</strong> seen", "1 reacted", "Ana Reyes", "Ben Cruz", "Carlo Diaz"],
             "/stories/new": ["Who can see it", "Close Friends", "2 people"],
             f"/stories/{mika.id}": ["Sketching the Kalye Coffee cups today.", 'data-seen-count', ">4</strong>", "Ben Cruz", "❤️"],
             "/stories/tray": ["Ana", "Ben", "Carlo", 'class="ring is-new"', 'class="ring is-seen"'],
             "/demo/friends": ["Invite a Friend", "Ana Reyes", "/demo/join/"],
             "/search": ["Search Your Messages"],
             "/search?q=mockups": ["<mark>mockups</mark>", "Papel Studio"]}
    studio = next(chat for chat in chats if chat.title == "Papel Studio")
    pages[f"/chats/{studio.id}/find?q=mockups"] = ["<mark>mockups</mark>", "Back to the chat"]
    for person in people_with_stories(app, mika):
        pages[f"/stories/{person}"] = ["story-face"]
    for chat in chats:
        pages[f"/chats/{chat.id}"] = ["msg-text"]
        pages[f"/chats/{chat.id}/info"] = ["Group Info", "People", "Leave the Group"] if chat.is_group else ["Chat Info", "Block"]
        samples = {"Papel Studio": 'class="photo"', "Weekend Hike": 'class="link-card"', "Launch Plans": 'class="file-card"',
                   None: 'class="story-quote"'}
        if chat.title is None and chat.created_by_id != story_replier(app, mika):
            samples = {}
        if chat.title in samples:  # a picture, a link preview and a file to look at
            pages[f"/chats/{chat.id}"].append(samples[chat.title])
        # Photos, Files and Links: every chat's history has something (the first tab with something opens).
        pages[f"/chats/{chat.id}/info"].append("Photos, Files and Links")
        media_samples = {"Papel Studio": "cup-mockup", "Weekend Hike": "Mount Batulao", "Launch Plans": "launch-checklist.txt",
                         "ana": "kalye-logo-final", "ben": "batulao-trail-map", "carlo": "kalye-palette-notes.txt"}
        key = chat.title or creator_handle(app, chat)
        pages[f"/chats/{chat.id}/media"] = ["Photos, Files and Links", media_samples[key]]
        pages[f"/chats/{chat.id}/find"] = ["Search in", "Search This Chat", "From You"]
        if chat.title == "Launch Plans":  # Mika's own group: every control, an admin and a working invite link
            pages[f"/chats/{chat.id}/info"] += ["Add People", "Invite Links", "2 of 10 used", "Admin", "Delete the Group"]
    return pages


def story_replier(app, mika):
    with app.app_context():
        return db.session().scalar(select(User.id).where(User.demo_team_id == mika.demo_team_id,
                                                         User.username.like("%.carlo")))


def test_every_page_of_the_demo_shows_an_example(app):
    client = start_demo(app)
    for url, must_show in demo_pages(app, client).items():
        response = client.get(url)
        assert response.status_code == 200, url
        page = response.get_data(as_text=True)
        for words_needed in must_show:
            assert words_needed in page, (url, words_needed)
        assert "No chats yet" not in page and "No Messages Yet" not in page, url


def test_the_demo_starts_with_unread_messages(app):
    client = start_demo(app)
    page = client.get("/chats").get_data(as_text=True)
    assert '<span class="count" data-total-unread>6</span>' in page


def test_purge_demos_command(app):
    start_demo(app)
    result = app.test_cli_runner().invoke(args=["purge-demos"])
    assert result.exit_code == 0 and "Deleted 0 demos." in result.output
