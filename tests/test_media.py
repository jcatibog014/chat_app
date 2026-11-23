"""A chat's Photos, Files and Links: what was sent, newest first, only to people who may read
the chat (members now, the history rule, deleted messages gone), and on the chat info page."""
import io
import re

import pytest
from PIL import Image
from sqlalchemy import select

from paperplane import db, media
from paperplane.formatting import find_links
from paperplane.models import Attachment, LinkPreview, Message

from .conftest import browser, make_user, post, text, words


def picture(colour="#1d6fb8"):
    out = io.BytesIO()
    Image.new("RGB", (300, 200), colour).save(out, "PNG")
    return out.getvalue()


def send(client, chat_id, body="", name=None, data=None):
    with client.session_transaction() as session:
        session["csrf"] = "test-csrf-token"
    fields = {"csrf_token": "test-csrf-token", "body": body}
    if name:
        fields["file"] = (io.BytesIO(data), name)
    response = client.post(f"/chats/{chat_id}/send", content_type="multipart/form-data", data=fields)
    assert response.status_code == 302, words(response)
    return response


@pytest.fixture
def chat(app):
    """Ella and Finn's DM; Gus is a stranger."""
    ids = {name: make_user(app, name, f"{name.title()} Person") for name in ("ella", "finn", "gus")}
    clients = {name: browser(app, user_id) for name, user_id in ids.items()}
    response = post(clients["ella"], "/chats/new/dm", {"username": "finn"})
    return {"ids": ids, "client": clients, "chat": int(response.headers["Location"].rsplit("/", 1)[1])}


def page(chat, who="ella", **args):
    query = "&".join(f"{key}={value}" for key, value in args.items())
    return chat["client"][who].get(f"/chats/{chat['chat']}/media" + (f"?{query}" if query else ""))


def message_id(app, body):
    with app.app_context():
        return db.session().scalar(select(Message.id).where(Message.body == body))


# ---------- Finding links ----------

def test_links_are_found_the_way_the_chat_shows_them():
    assert find_links("See https://example.com/a. And (https://example.org/b) twice https://example.com/a!") == [
        "https://example.com/a", "https://example.org/b"]
    assert find_links("no links, just http and www.example.com") == []


# ---------- The page ----------

def test_photos_files_and_links_each_have_a_tab(app, chat):
    ella, finn = chat["client"]["ella"], chat["client"]["finn"]
    send(ella, chat["chat"], "Our logo", "logo.png", picture())
    send(finn, chat["chat"], "", "notes.txt", b"Hello notes")
    send(finn, chat["chat"], "Look: https://hiking.example/batulao and http://www.maps.example/trail")
    photos = text(page(chat))
    assert re.search(r'href="[^"]+tab=photos" aria-current="page"', photos)  # the first tab with something in it
    assert 'class="media-photo"' in photos and "/thumb" in photos and "You ·" in photos
    files = words(page(chat, tab="files"))
    assert "notes.txt" in files and "11 B" in files and "Finn Person" in files and "Show in chat" in files
    links = text(page(chat, tab="links"))
    assert 'href="https://hiking.example/batulao"' in links and "hiking.example" in links and "maps.example" in links
    # The tabs say how many of each.
    numbers = re.findall(r'<span class="media-count">(\d+)</span>', photos)
    assert numbers == ["1", "1", "2"]


def test_show_in_chat_goes_to_the_message(app, chat):
    send(chat["client"]["ella"], chat["chat"], "", "a.txt", b"A")
    with app.app_context():
        message = db.session().scalars(select(Message).where(Message.kind == "text")).one()
    files = text(page(chat, tab="files"))
    assert f'/chats/{chat["chat"]}?before={message.seq + 20}#m-{message.id}' in files
    assert chat["client"]["ella"].get(f'/chats/{chat["chat"]}?before={message.seq + 20}').status_code == 200


def test_a_link_with_a_preview_shows_its_title(app, chat):
    send(chat["client"]["ella"], chat["chat"], "Trail: https://en.wikipedia.org/wiki/Mount_Batulao")
    with app.app_context():
        s = db.session()
        s.add(LinkPreview(message_id=message_id(app, "Trail: https://en.wikipedia.org/wiki/Mount_Batulao"),
                          url="https://en.wikipedia.org/wiki/Mount_Batulao", site_name="Wikipedia",
                          title="Mount Batulao", description="A mountain in Batangas."))
        s.commit()
    links = words(page(chat, tab="links"))
    assert "Mount Batulao" in links and "Wikipedia" in links and "A mountain in Batangas." in links


def test_newest_first(app, chat):
    for colour in ("#111111", "#222222", "#333333"):
        send(chat["client"]["ella"], chat["chat"], colour, "p.png", picture(colour))
    with app.app_context():
        ids = db.session().scalars(select(Attachment.id).order_by(Attachment.id)).all()
    shown = [int(found) for found in re.findall(r"/files/(\d+)/thumb", text(page(chat)))]
    assert shown == list(reversed(ids))


def test_empty_tabs_say_so(app, chat):
    response = page(chat)
    assert response.status_code == 200 and "No Photos Yet" in words(response)
    assert "No Links Yet" in words(page(chat, tab="links"))
    assert "No Photos Yet" in words(page(chat, tab="something-else"))


def test_older_ones_come_a_page_at_a_time(app, chat, monkeypatch):
    monkeypatch.setattr(media, "PAGE", 2)
    for number in range(5):
        send(chat["client"]["ella"], chat["chat"], f"Read https://site{number}.example/page")
    first = text(page(chat, tab="links"))
    assert "site4.example" in first and "site3.example" in first and "site2.example" not in first
    older = re.search(r'href="([^"]+)">Show older', first).group(1).replace("&amp;", "&")
    second = text(chat["client"]["ella"].get(older))
    assert "site2.example" in second and "site1.example" in second and "site4.example" not in second
    assert "Show older" in second
    third = text(chat["client"]["ella"].get(re.search(r'href="([^"]+)">Show older', second).group(1).replace("&amp;", "&")))
    assert "site0.example" in third and "Show older" not in third


# ---------- Who may see ----------

def test_strangers_get_not_found(app, chat):
    send(chat["client"]["ella"], chat["chat"], "", "logo.png", picture())
    assert page(chat, "gus").status_code == 404
    assert page(chat, "gus", tab="links").status_code == 404
    assert browser(app).get(f"/chats/{chat['chat']}/media").status_code == 302  # log in first


def test_deleted_messages_leave_the_history(app, chat):
    send(chat["client"]["ella"], chat["chat"], "Oops https://wrong.example/page", "logo.png", picture())
    post(chat["client"]["ella"], f"/messages/{message_id(app, 'Oops https://wrong.example/page')}/delete")
    assert "wrong.example" not in text(page(chat, tab="links"))
    assert 'class="media-photo"' not in text(page(chat))
    with app.app_context():
        s = db.session()
        from paperplane.models import Conversation, Membership
        conversation = s.get(Conversation, chat["chat"])
        membership = s.scalar(select(Membership).where(Membership.conversation_id == chat["chat"]))
        assert media.counts(s, conversation, membership) == {"photos": 0, "files": 0, "links": 0}


def test_a_new_member_sees_only_what_came_after_they_joined_when_history_is_hidden(app):
    ids = {name: make_user(app, name, f"{name.title()} Person") for name in ("olga", "sam", "max")}
    olga, sam = browser(app, ids["olga"]), browser(app, ids["sam"])
    response = post(olga, "/chats/new/group", {"title": "Club", "usernames": "max"})
    chat_id = int(response.headers["Location"].rsplit("/", 1)[1])
    send(olga, chat_id, "Old https://before.example/x", "old.png", picture())
    post(olga, f"/chats/{chat_id}/history", {"history_visible": "0"})
    post(olga, f"/chats/{chat_id}/add", {"usernames": "sam"})
    send(olga, chat_id, "New https://after.example/y", "new.png", picture("#aa0000"))
    links = text(sam.get(f"/chats/{chat_id}/media?tab=links"))
    assert "after.example" in links and "before.example" not in links
    assert len(re.findall(r'class="media-photo"', text(sam.get(f"/chats/{chat_id}/media")))) == 1
    assert len(re.findall(r'class="media-photo"', text(olga.get(f"/chats/{chat_id}/media")))) == 2
    # Someone who left sees nothing at all.
    post(sam, f"/chats/{chat_id}/leave")
    assert sam.get(f"/chats/{chat_id}/media").status_code == 404


# ---------- Chat info ----------

def test_chat_info_shows_the_latest_photos_and_the_numbers(app, chat):
    for number in range(8):
        send(chat["client"]["ella"], chat["chat"], f"Photo {number}", "p.png", picture())
    send(chat["client"]["finn"], chat["chat"], "https://one.example/x")
    info = text(chat["client"]["ella"].get(f"/chats/{chat['chat']}/info"))
    assert "Photos, Files and Links" in info
    assert len(re.findall(r'<li><a href="[^"]+/media\?tab=photos">\s*<img', info)) == 6  # the latest six
    kinds = re.findall(r"<span>(Photos|Files|Links)</span><strong>(\d+)</strong>", info)
    assert kinds == [("Photos", "8"), ("Files", "0"), ("Links", "1")]


def test_group_info_has_it_too(app):
    ids = {name: make_user(app, name, f"{name.title()} Person") for name in ("olga", "max")}
    olga = browser(app, ids["olga"])
    response = post(olga, "/chats/new/group", {"title": "Club", "usernames": "max"})
    chat_id = int(response.headers["Location"].rsplit("/", 1)[1])
    info = words(olga.get(f"/chats/{chat_id}/info"))
    assert "Photos, Files and Links" in info and "See all" in info
    assert "Back to group info" in words(olga.get(f"/chats/{chat_id}/media"))
