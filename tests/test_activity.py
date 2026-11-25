"""The Notifications page: mentions, replies, reactions, stories and group changes, never about
yourself or across a block, taken back when undone, marked read when opened, and live."""
import re

import pytest
from sqlalchemy import func, select

from paperplane import db
from paperplane.hub import HUB
from paperplane.models import Activity, Message, Story

from .conftest import browser, make_user, post, text, words
from .live_helpers import fake_connection


@pytest.fixture
def team(app):
    """Olga owns a group with Max and Sam; Olga and Max also have a DM."""
    ids = {name: make_user(app, name, f"{name.title()} Person") for name in ("olga", "max", "sam", "gus")}
    clients = {name: browser(app, user_id) for name, user_id in ids.items()}
    group = int(post(clients["olga"], "/chats/new/group", {"title": "Club", "usernames": "max sam"})
                .headers["Location"].rsplit("/", 1)[1])
    dm = int(post(clients["olga"], "/chats/new/dm", {"username": "max"}).headers["Location"].rsplit("/", 1)[1])
    return {"ids": ids, "client": clients, "group": group, "dm": dm}


def say(team, who, chat, body, **extra):
    assert post(team["client"][who], f"/chats/{team[chat]}/send", {"body": body, **extra}).status_code == 302
    return message_id(team["app"] if "app" in team else None, body)


def message_id(_app, body):
    return db.session().scalar(select(Message.id).where(Message.body == body).order_by(Message.id.desc()))


def lines_of(app, who_id, kind=None):
    with app.app_context():
        query = select(Activity).where(Activity.user_id == who_id)
        if kind:
            query = query.where(Activity.kind == kind)
        return db.session().scalars(query.order_by(Activity.id)).all()


def page(team, who):
    return team["client"][who].get("/notifications")


def badge(team, who):
    found = re.search(r"<span class=\"count\" data-activity-unread( hidden)?>(\d+)</span>",
                      text(team["client"][who].get("/settings")))
    return 0 if found.group(1) else int(found.group(2))


# ---------- What makes a line ----------

def test_a_mention_shows_and_opening_the_page_reads_it(app, team):
    with app.app_context():
        say(team, "olga", "group", "@max can you bring the tent?")
    assert badge(team, "max") == 1 and badge(team, "sam") == 0
    first = text(page(team, "max"))
    assert "<h2>New</h2>" in first and "mentioned you" in first and "Olga Person" in first and "Club" in first
    assert '"@max can you bring the tent?"' in first and 'class="activity is-new"' in first
    # Opening the page doesn't read it: clicking the line does, and goes to the message.
    assert badge(team, "max") == 1
    link = re.search(r'class="activity-row" href="(/notifications/\d+)"', first).group(1)
    opened = team["client"]["max"].get(link)
    assert re.search(rf'^/chats/{team["group"]}\?before=\d+#m-\d+$', opened.headers["Location"])
    assert badge(team, "max") == 0
    second = text(page(team, "max"))
    assert "<h2>New</h2>" not in second and "<h2>Earlier</h2>" in second


def test_mark_all_as_read_and_lines_with_nowhere_to_go(app, team):
    with app.app_context():
        say(team, "olga", "group", "@sam one")
        say(team, "olga", "group", "@sam two")
    assert badge(team, "sam") == 2 and "Mark all as read" in words(page(team, "sam"))
    post(team["client"]["sam"], "/notifications/read")
    assert badge(team, "sam") == 0 and "Mark all as read" not in words(page(team, "sam"))
    # Removed from the group: the new line has no link, so it's a button that just reads it.
    post(team["client"]["olga"], f"/chats/{team['group']}/members/{team['ids']['sam']}/remove")
    html = text(page(team, "sam"))
    action = re.search(r'<form method="post" action="(/notifications/\d+/read)">', html).group(1)
    assert post(team["client"]["sam"], action).headers["Location"] == "/notifications"
    assert badge(team, "sam") == 0
    # Nobody opens someone else's line.
    with app.app_context():
        sams = lines_of(app, team["ids"]["sam"])[0].id
    assert team["client"]["max"].get(f"/notifications/{sams}").status_code == 404


def test_replies_and_reactions_to_your_message(app, team):
    with app.app_context():
        target = say(team, "max", "group", "Who has the map?")
        say(team, "olga", "group", "I do!", reply_to=str(target))
    post(team["client"]["sam"], f"/messages/{target}/react", {"emoji": "👍"})
    max_lines = [(line.kind, line.emoji) for line in lines_of(app, team["ids"]["max"])]
    assert ("reply", None) in max_lines and ("reaction", "👍") in max_lines
    words_on_page = words(page(team, "max"))
    assert "Olga Person replied to your message in Club" in words_on_page
    assert "Sam Person reacted 👍 to your message in Club" in words_on_page and '"Who has the map?"' in words_on_page
    # Taking the reaction back takes the line back.
    post(team["client"]["sam"], f"/messages/{target}/react", {"emoji": "👍"})
    assert not lines_of(app, team["ids"]["max"], "reaction")


def test_nothing_about_yourself(app, team):
    with app.app_context():
        mine = say(team, "olga", "group", "@olga talking to myself")
        say(team, "olga", "group", "and replying to myself", reply_to=str(mine))
    post(team["client"]["olga"], f"/messages/{mine}/react", {"emoji": "😂"})
    assert lines_of(app, team["ids"]["olga"]) == []
    assert "No Notifications Yet" in words(page(team, "olga"))


def test_a_deleted_message_takes_its_lines(app, team):
    with app.app_context():
        oops = say(team, "olga", "group", "@sam oops")
    assert len(lines_of(app, team["ids"]["sam"])) == 1
    post(team["client"]["olga"], f"/messages/{oops}/delete")
    assert lines_of(app, team["ids"]["sam"]) == [] and badge(team, "sam") == 0


def test_group_changes(app, team):
    post(team["client"]["olga"], f"/chats/{team['group']}/add", {"usernames": "gus"})
    post(team["client"]["olga"], f"/chats/{team['group']}/members/{team['ids']['max']}/role", {"role": "admin"})
    post(team["client"]["olga"], f"/chats/{team['group']}/members/{team['ids']['sam']}/remove")
    assert [line.kind for line in lines_of(app, team["ids"]["sam"])] == ["group_removed"]  # Sam was there from the start
    assert "Olga Person made you an admin of Club" in words(page(team, "max"))
    assert "Olga Person added you to Club" in words(page(team, "gus"))
    removed = text(page(team, "sam"))
    assert "removed you from" in removed
    # Sam left the group: nothing on Sam's page links into it any more.
    assert f'href="/chats/{team["group"]}' not in removed
    post(team["client"]["olga"], f"/chats/{team['group']}/members/{team['ids']['max']}/owner")
    assert "Olga Person made you the owner of Club" in words(page(team, "max"))


def test_blocking_removes_the_lines_and_stops_new_ones(app, team):
    with app.app_context():
        say(team, "olga", "group", "@max hello")
    post(team["client"]["max"], f"/people/{team['ids']['olga']}/block")
    assert lines_of(app, team["ids"]["max"]) == []
    with app.app_context():
        say(team, "olga", "group", "@max still there?")
    assert lines_of(app, team["ids"]["max"]) == []


# ---------- Stories ----------

def card(client, words_on_it, audience="everyone"):
    return post(client, "/stories/new", {"kind": "text", "text": words_on_it, "background": "pink", "audience": audience})


def test_a_new_story_tells_the_people_who_may_see_it(app, team):
    post(team["client"]["max"], f"/stories/people/{team['ids']['olga']}/mute")
    card(team["client"]["olga"], "Hike photos soon")
    assert len(lines_of(app, team["ids"]["sam"], "story_new")) == 1
    assert lines_of(app, team["ids"]["max"], "story_new") == []        # Max muted Olga's stories
    assert lines_of(app, team["ids"]["gus"], "story_new") == []        # Gus doesn't chat with Olga
    # A second story freshens the unread line instead of adding another...
    card(team["client"]["olga"], "And another")
    assert len(lines_of(app, team["ids"]["sam"], "story_new")) == 1
    assert "Olga Person posted a story" in words(page(team, "sam"))
    post(team["client"]["sam"], "/notifications/read")
    # ...but once read, the next story is a new line.
    card(team["client"]["olga"], "A third")
    assert len(lines_of(app, team["ids"]["sam"], "story_new")) == 2


def test_close_friends_stories_tell_only_the_list(app, team):
    post(team["client"]["olga"], "/settings/privacy", {"action": "close_friends", "friend": [str(team["ids"]["sam"])]})
    card(team["client"]["olga"], "Just us", audience="close")
    assert len(lines_of(app, team["ids"]["sam"], "story_new")) == 1
    assert lines_of(app, team["ids"]["max"], "story_new") == []


def test_story_reactions_and_replies(app, team):
    card(team["client"]["olga"], "Sunset")
    with app.app_context():
        story_id = db.session().scalar(select(Story.id))
    post(team["client"]["max"], f"/stories/{story_id}/react", {"emoji": "❤️"})
    post(team["client"]["max"], f"/stories/{story_id}/react", {"emoji": "😮"})  # replaces it
    reactions = lines_of(app, team["ids"]["olga"], "story_reaction")
    assert [line.emoji for line in reactions] == ["😮"]
    post(team["client"]["max"], f"/stories/{story_id}/reply", {"body": "Wow, where?"})
    olga = words(page(team, "olga"))
    assert "Max Person reacted 😮 to your story" in olga and "Max Person replied to your story" in olga
    assert '"Wow, where?"' in olga
    post(team["client"]["max"], f"/stories/{story_id}/react", {"emoji": "😮"})  # taken back
    assert lines_of(app, team["ids"]["olga"], "story_reaction") == []


# ---------- The page, the menus, live ----------

def test_the_menus_have_notifications_with_the_number(app, team):
    with app.app_context():
        say(team, "olga", "group", "@sam look")
    page_html = text(team["client"]["sam"].get("/chats"))
    assert re.search(r'href="/notifications">.*?<span class="side-label">Notifications</span><span class="count" data-activity-unread>1</span>',
                     page_html, re.S)
    bar = re.search(r'<nav class="bottom-nav" aria-label="Main">(.*?)</nav>', page_html, re.S).group(1)
    assert bar.index("Chats") < bar.index("Stories") < bar.index("Notifications") < bar.index("Profile")
    assert "data-activity-unread" in bar


def test_older_lines_come_a_page_at_a_time(app, team, monkeypatch):
    from paperplane import activity
    monkeypatch.setattr(activity, "PAGE", 2)
    with app.app_context():
        for number in range(5):
            say(team, "olga", "group", f"@sam number {number}")
    first = text(page(team, "sam"))
    assert "number 4" in first and "number 3" in first and "number 2" not in first
    older = re.search(r'href="(/notifications\?before=\d+)">Show older', first).group(1)
    second = text(team["client"]["sam"].get(older))
    assert "number 2" in second and "number 1" in second


def test_the_number_changes_live(app, team):
    tab = fake_connection(app.extensions[HUB], team["ids"]["max"])
    with app.app_context():
        say(team, "olga", "group", "@max ping")
    assert tab.ws.of_type("activity.count")[-1]["data"] == {"unread": 1}
    page(team, "max")  # looking doesn't read
    assert tab.ws.of_type("activity.count")[-1]["data"] == {"unread": 1}
    post(team["client"]["max"], "/notifications/read")
    assert tab.ws.of_type("activity.count")[-1]["data"] == {"unread": 0}


def test_strangers_see_only_their_own(app, team):
    with app.app_context():
        say(team, "olga", "group", "@max private plans")
        assert db.session().scalar(select(func.count(Activity.id))) == 1
    assert "private plans" not in text(page(team, "gus"))
    assert browser(app).get("/notifications").status_code == 302  # log in first
