"""Search in this chat: words and/or who said them, in ONE chat, under the chat's own rules
(members only, the history rule, deleted messages left out), and each result opening the chat
at its message with a way back to the results."""
import re

import pytest
from sqlalchemy import select

from paperplane import db, notify
from paperplane.models import Message

from .conftest import browser, make_user, post, text, words


@pytest.fixture
def club(app):
    """A group (Olga, Max, Sam), a DM between Olga and Max, and Gus, a stranger."""
    ids = {name: make_user(app, name, f"{name.title()} Person") for name in ("olga", "max", "sam", "gus")}
    clients = {name: browser(app, user_id) for name, user_id in ids.items()}
    group = int(post(clients["olga"], "/chats/new/group", {"title": "Club", "usernames": "max sam"})
                .headers["Location"].rsplit("/", 1)[1])
    dm = int(post(clients["olga"], "/chats/new/dm", {"username": "max"}).headers["Location"].rsplit("/", 1)[1])
    return {"ids": ids, "client": clients, "group": group, "dm": dm}


def say(club, who, chat, body):
    response = post(club["client"][who], f"/chats/{club[chat]}/send", {"body": body})
    assert response.status_code == 302


def find(club, who="olga", chat="group", **args):
    return club["client"][who].get(f"/chats/{club[chat]}/find", query_string=args)


def result_bodies(response):
    return [re.sub(r"<[^>]+>", "", body).strip()
            for body in re.findall(r'<span class="result-body">(.*?)</span>\s*</span>', text(response), re.S)]


def test_the_chat_header_has_a_search_button(app, club):
    page = text(club["client"]["olga"].get(f"/chats/{club['group']}"))
    assert re.search(rf'href="/chats/{club["group"]}/find" title="Search in this chat"', page)
    empty = words(find(club))
    assert "Search in Club" in empty and "Search This Chat" in empty and "From anyone" in empty


def test_words_are_found_in_this_chat_only(app, club):
    say(club, "olga", "group", "The hike is on Saturday")
    say(club, "max", "group", "Saturday works, see you at 5")
    say(club, "olga", "dm", "Saturday lunch, just us?")
    response = find(club, q="saturday")
    bodies = result_bodies(response)
    assert bodies == ["Saturday works, see you at 5", "The hike is on Saturday"]  # newest first, DM left out
    assert "<mark>Saturday</mark>" in text(response)
    assert "2 messages with \"saturday\"" in words(response)


def test_choose_who_said_it(app, club):
    say(club, "olga", "group", "I booked the bus")
    say(club, "max", "group", "I booked the cabin")
    say(club, "max", "group", "Bring snacks")
    max_only = find(club, q="booked", **{"from": club["ids"]["max"]})
    assert result_bodies(max_only) == ["I booked the cabin"] and 'from Max' in words(max_only)
    # A person with no words: everything they said here.
    assert result_bodies(find(club, **{"from": club["ids"]["max"]})) == ["Bring snacks", "I booked the cabin"]
    mine = find(club, **{"from": club["ids"]["olga"]})
    assert result_bodies(mine) == ["I booked the bus"] and "from you" in words(mine)
    # Someone not in the chat is ignored, not an error.
    assert "Search This Chat" in words(find(club, **{"from": club["ids"]["gus"]}))


def test_the_list_of_people_is_you_then_the_others(app, club):
    options = re.findall(r'<option value="(\d*)"[^>]*>([^<]+)</option>', text(find(club)))
    assert [name for _value, name in options] == ["From anyone", "From You", "From Max Person", "From Sam Person"]


def test_a_result_opens_the_chat_at_its_message(app, club):
    for number in range(30):
        say(club, "max", "group", f"filler {number}")
    say(club, "olga", "group", "Remember the tent")
    for number in range(30):
        say(club, "max", "group", f"later {number}")
    with app.app_context():
        message = db.session().scalar(select(Message).where(Message.body == "Remember the tent"))
    link = re.search(r'class="result" href="([^"]+)"', text(find(club, q="tent"))).group(1).replace("&amp;", "&")
    assert link == f"/chats/{club['group']}?before={message.seq + 20}&find=tent#m-{message.id}"
    page = text(club["client"]["olga"].get(link.split("#")[0]))
    assert f'id="m-{message.id}"' in page  # the message is on the page it opens
    assert 'data-find-bar' in page and 'Found "<strong data-find-words>tent</strong>"' in page
    assert f'href="/chats/{club["group"]}/find?q=tent">Back to results' in page
    # Without ?find= there is no bar.
    assert "data-find-bar" not in text(club["client"]["olga"].get(f"/chats/{club['group']}"))


def test_too_short_and_nothing_found(app, club):
    assert "Type a Little More" in words(find(club, q="a"))
    assert "No Messages Found" in words(find(club, q="zebra"))
    assert "Sam hasn't said anything here yet." in words(find(club, **{"from": club["ids"]["sam"]}))
    assert "Nothing from Sam in this chat has \"zebra\"" in words(find(club, q="zebra", **{"from": club["ids"]["sam"]}))


def test_percent_and_underscore_are_plain_letters(app, club):
    say(club, "olga", "group", "We are 50% done")
    say(club, "olga", "group", "We are 500 done")
    assert result_bodies(find(club, q="50%")) == ["We are 50% done"]


def test_older_results_come_a_page_at_a_time(app, club, monkeypatch):
    monkeypatch.setattr(notify, "SEARCH_LIMIT", 2)
    for number in range(5):
        say(club, "olga", "group", f"note {number}")
    first = find(club, q="note")
    assert result_bodies(first) == ["note 4", "note 3"] and "2+ messages" in words(first)
    older = re.search(r'href="([^"]+)">Show older results', text(first)).group(1).replace("&amp;", "&")
    second = club["client"]["olga"].get(older)
    assert result_bodies(second) == ["note 2", "note 1"]


# ---------- Who may search ----------

def test_strangers_and_people_who_left_get_not_found(app, club):
    say(club, "olga", "group", "secret plans")
    assert find(club, "gus", q="secret").status_code == 404
    post(club["client"]["sam"], f"/chats/{club['group']}/leave")
    assert find(club, "sam", q="secret").status_code == 404
    assert browser(app).get(f"/chats/{club['group']}/find?q=secret").status_code == 302  # log in first


def test_deleted_messages_are_not_found(app, club):
    say(club, "olga", "group", "oops wrong chat")
    with app.app_context():
        message_id = db.session().scalar(select(Message.id).where(Message.body == "oops wrong chat"))
    post(club["client"]["olga"], f"/messages/{message_id}/delete")
    assert result_bodies(find(club, q="oops")) == []


def test_hidden_history_stays_hidden(app, club):
    ids = club["ids"]
    say(club, "olga", "group", "before Gus came")
    post(club["client"]["olga"], f"/chats/{club['group']}/history", {"history_visible": "0"})
    post(club["client"]["olga"], f"/chats/{club['group']}/add", {"usernames": "gus"})
    say(club, "olga", "group", "after Gus came")
    assert result_bodies(find(club, "gus", q="came")) == ["after Gus came"]
    assert result_bodies(find(club, "gus", **{"from": ids["olga"]})) == ["after Gus came"]
    assert len(result_bodies(find(club, "olga", q="came"))) == 2
