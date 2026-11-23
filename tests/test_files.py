"""Pictures and files: checked, re-saved, stored, and served only to the right people."""
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image
from sqlalchemy import func, select

from paperplane import chats, db, files, previews, tools
from paperplane.dates import utcnow
from paperplane.hub import HUB
from paperplane.models import Attachment, LinkPreview, Message, User
from paperplane.problems import Invalid

from .conftest import browser, make_app, make_user, post, words
from .live_helpers import fake_connection


def jpeg(width=200, height=100, colour="#1d6fb8", orientation=None, camera=None):
    picture = Image.new("RGB", (width, height), colour)
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation   # 6: the camera was turned; show it rotated
    if camera:
        exif[0x010F] = camera        # "Make": a stand-in for the details phones add (GPS...)
    out = io.BytesIO()
    picture.save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


@pytest.fixture
def pair(app):
    ids = {name: make_user(app, name, f"{name.title()} Person") for name in ("ella", "finn", "gus")}
    clients = {name: browser(app, user_id) for name, user_id in ids.items()}
    response = post(clients["ella"], "/chats/new/dm", {"username": "finn"})
    return {"ids": ids, "client": clients, "chat": int(response.headers["Location"].rsplit("/", 1)[1])}


def upload(client, chat_id, name, data, body="", json=False):
    with client.session_transaction() as session:
        session["csrf"] = "test-csrf-token"
    headers = {"Accept": "application/json"} if json else {}
    return client.post(f"/chats/{chat_id}/send", headers=headers, content_type="multipart/form-data", data={
        "csrf_token": "test-csrf-token", "body": body, "file": (io.BytesIO(data), name)})


def only_attachment(app):
    with app.app_context():
        return db.session().scalars(select(Attachment)).one()


# ---------- Checking and re-saving ----------

def test_a_picture_is_resaved_as_webp_without_its_hidden_details(app, pair):
    response = upload(pair["client"]["ella"], pair["chat"], "holiday.jpg", jpeg(camera="SpyCam 3000"))
    assert response.status_code == 302
    attachment = only_attachment(app)
    assert attachment.kind == "image" and attachment.filename == "holiday.webp" and attachment.content_type == "image/webp"
    with app.app_context():
        data = db.session().scalar(select(Attachment.data))
    picture = Image.open(io.BytesIO(data))
    assert picture.format == "WEBP" and not picture.getexif() and b"SpyCam" not in data


def test_a_turned_photo_is_turned_the_right_way(app, pair):
    upload(pair["client"]["ella"], pair["chat"], "turned.jpg", jpeg(200, 100, orientation=6))
    attachment = only_attachment(app)
    assert (attachment.width, attachment.height) == (100, 200)


def test_a_fake_picture_or_a_strange_file_is_refused(app, pair):
    client = pair["client"]["ella"]
    for name, data, words_ in [
        ("photo.png", b"<html><script>alert(1)</script></html>", "isn't a picture we can open"),
        ("tool.exe", b"MZ...", "You can send pictures"),
        ("page.html", b"<script>", "You can send pictures"),
        ("empty.txt", b"", "That file is empty."),
    ]:
        response = upload(client, pair["chat"], name, data, json=True)
        assert response.status_code == 400 and words_ in response.get_json()["message"], name
    with app.app_context():
        assert db.session().scalar(select(func.count(Attachment.id))) == 0
        assert db.session().scalar(select(func.count(Message.id))) == 0


def test_too_big_is_refused(app, pair):
    with pytest.raises(Invalid, match="at most 5 MB"):
        files.prepare("big.txt", b"x" * (files.MAX_FILE_BYTES + 1))


def test_a_decompression_bomb_is_refused(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 5000)
    with pytest.raises(Invalid, match="too big"):
        files.prepare("bomb.jpg", jpeg(100, 80))


def test_names_are_made_safe():
    assert files.clean_filename("../../etc/passwd") == "etc_passwd"
    assert files.clean_filename("Report FINAL.PDF") == "Report_FINAL.pdf"
    assert files.clean_filename("") == "file"


def test_a_file_with_words_and_json(app, pair):
    response = upload(pair["client"]["ella"], pair["chat"], "notes.txt", b"hello", body="Here you go", json=True)
    assert response.status_code == 200
    message = response.get_json()["message"]
    assert message["body"] == "Here you go" and message["attachments"][0]["name"] == "notes.txt"
    assert 'class="file-card"' in message["html"] and "notes.txt" in message["html"]


def test_the_list_says_photo_for_a_picture_only_message(app, pair):
    upload(pair["client"]["ella"], pair["chat"], "a.png", jpeg())
    assert "Photo" in words(pair["client"]["finn"].get("/chats"))


# ---------- Serving ----------

def test_a_picture_shows_and_a_file_downloads_safely(app, pair):
    upload(pair["client"]["ella"], pair["chat"], "a.jpg", jpeg())
    upload(pair["client"]["ella"], pair["chat"], "plan.pdf", b"%PDF-1.4 hello")
    with app.app_context():
        picture, document = db.session().scalars(select(Attachment).order_by(Attachment.id)).all()
    finn = pair["client"]["finn"]
    shown = finn.get(f"/files/{picture.id}")
    assert shown.status_code == 200 and shown.mimetype == "image/webp"
    assert shown.headers["Content-Disposition"].startswith("inline")
    assert finn.get(f"/files/{picture.id}/thumb").mimetype == "image/webp"
    got = finn.get(f"/files/{document.id}")
    assert got.headers["Content-Disposition"].startswith("attachment") and got.data == b"%PDF-1.4 hello"
    assert "sandbox" in got.headers["Content-Security-Policy"] and got.headers["X-Content-Type-Options"] == "nosniff"
    assert got.headers["Cache-Control"].startswith("private")
    assert finn.get(f"/files/{document.id}/thumb").status_code == 404


def test_only_the_chats_people_can_fetch_a_file(app, pair):
    upload(pair["client"]["ella"], pair["chat"], "secret.txt", b"for Finn only")
    attachment = only_attachment(app)
    assert pair["client"]["gus"].get(f"/files/{attachment.id}").status_code == 404
    assert browser(app).get(f"/files/{attachment.id}").status_code == 302  # to the login


def test_deleting_the_message_deletes_the_file(app, pair):
    upload(pair["client"]["ella"], pair["chat"], "oops.txt", b"wrong file")
    attachment = only_attachment(app)
    post(pair["client"]["ella"], f"/messages/{attachment.message_id}/delete")
    with app.app_context():
        assert db.session().scalar(select(func.count(Attachment.id))) == 0
    assert pair["client"]["finn"].get(f"/files/{attachment.id}").status_code == 404


def test_each_person_has_a_storage_limit(app, pair, monkeypatch):
    monkeypatch.setattr(files, "MAX_PER_PERSON", 20)
    assert upload(pair["client"]["ella"], pair["chat"], "a.txt", b"x" * 15).status_code == 302
    response = upload(pair["client"]["ella"], pair["chat"], "b.txt", b"x" * 15, json=True)
    assert response.status_code == 429 and "used all your space" in response.get_json()["message"]


def test_the_file_reaches_the_other_tab_live(app, pair):
    tab = fake_connection(app.extensions[HUB], pair["ids"]["finn"])
    upload(pair["client"]["ella"], pair["chat"], "pic.png", jpeg())
    (news,) = tab.ws.of_type("message.new")
    assert news["data"]["attachments"][0]["kind"] == "image" and "/files/" in news["data"]["html"]


# ---------- Link previews ----------

PAGE = """<html><head><title>Plain title</title>
<meta property="og:title" content="Mount Batulao &amp; friends">
<meta property="og:description" content="A good hike.">
<meta property="og:site_name" content="Hiking Notes"></head><body>Hi</body></html>"""


def test_reading_a_page():
    assert previews.read_page(PAGE) == {"title": "Mount Batulao & friends", "description": "A good hike.",
                                        "site_name": "Hiking Notes"}
    assert previews.read_page("<title>  Just   a title </title>")["title"] == "Just a title"
    assert previews.read_page("no tags")["title"] is None


def test_the_first_address_in_a_message():
    assert previews.first_url("see https://example.com/a?b=1. and https://two.example") == "https://example.com/a?b=1"
    assert previews.first_url("no address here") is None


@pytest.mark.parametrize("address", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/",
                                     "http://localhost:5015/", "http://printer.local/", "http://user:pw@example.com/",
                                     "ftp://example.com/", "http://[::1]/", "http://[::ffff:10.0.0.1]/"])
def test_private_or_strange_addresses_are_refused_before_connecting(address):
    with pytest.raises(previews.Refused):
        previews.fetch(address, resolver=lambda *a, **k: [(None, None, None, None, ("93.184.216.34", 80))])


def test_a_name_that_turns_out_private_is_refused():
    """DNS rebinding: the name looks public, the answer is private. We check the ANSWER."""
    calls = []

    def tricky(host, port, **kwargs):
        calls.append(host)
        return [(None, None, None, None, ("10.1.2.3", port))]
    with pytest.raises(previews.Refused, match="private"):
        previews.fetch("https://innocent.example/", resolver=tricky)
    assert calls == ["innocent.example"]


class Site(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/hop":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
            return
        if self.path == "/inward":
            self.send_response(302)
            self.send_header("Location", "http://10.9.9.9/secret")
            self.end_headers()
            return
        body = PAGE.encode() if self.path == "/page" else b"%PDF"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8" if self.path == "/page" else "application/pdf")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_fetching_follows_safe_redirects_and_reads_only_web_pages(site):
    final, html = previews.fetch(f"{site}/hop", allow_private=True)
    assert final.endswith("/page") and "Mount Batulao" in html
    with pytest.raises(previews.Refused, match="not a web page"):
        previews.fetch(f"{site}/file", allow_private=True)


def test_a_redirect_inward_is_checked_again(site):
    """Our own test site is "private" here; with the guard on, even its redirect is refused."""
    with pytest.raises(previews.Refused):
        previews.fetch(f"{site}/inward")


def test_a_preview_is_saved_and_sent_to_the_chat(app, pair):
    tab = fake_connection(app.extensions[HUB], pair["ids"]["finn"])
    post(pair["client"]["ella"], f"/chats/{pair['chat']}/send", {"body": "Look: https://hiking.example/batulao"})
    with app.app_context():
        message_id = db.session().scalar(select(Message.id))
    preview = previews.make_preview(app, message_id, fetcher=lambda url, allow_private: (url, PAGE))
    assert preview.title == "Mount Batulao & friends"
    updated = tab.ws.of_type("message.updated")[-1]["data"]
    assert updated["link"]["site_name"] == "Hiking Notes" and 'class="link-card"' in updated["html"]
    assert "A good hike." in words(pair["client"]["finn"].get(f"/chats/{pair['chat']}"))
    # Asking again does nothing (one preview per message).
    assert previews.make_preview(app, message_id, fetcher=lambda url, allow_private: (url, PAGE)) is None


def test_no_preview_for_a_deleted_message_or_a_refused_page(app, pair):
    post(pair["client"]["ella"], f"/chats/{pair['chat']}/send", {"body": "https://gone.example/page"})
    with app.app_context():
        message_id = db.session().scalar(select(Message.id))

    def refuse(url, allow_private):
        raise previews.Refused("nope")
    assert previews.make_preview(app, message_id, fetcher=refuse) is None
    post(pair["client"]["ella"], f"/messages/{message_id}/delete")
    assert previews.make_preview(app, message_id, fetcher=lambda url, allow_private: (url, PAGE)) is None


def test_previews_are_fetched_only_after_the_commit_and_only_when_turned_on(tmp_path, monkeypatch):
    app = make_app(tmp_path, PREVIEWS=True)
    started = []
    monkeypatch.setattr(previews.POOL, "submit", lambda function, *args: started.append(args))
    ella, finn = make_user(app, "ella"), make_user(app, "finn")
    with app.app_context():
        s = db.session()
        conversation, _ = chats.start_dm(s, s.get(User, ella), s.get(User, finn), utcnow())
        s.commit()
        chats.send_message(s, s.get(User, ella), conversation.id, "https://example.com/x", None, utcnow())
        assert started == []          # not before the commit
        s.rollback()
        assert started == []          # and never after a rollback
        chats.send_message(s, s.get(User, ella), conversation.id, "https://example.com/y", None, utcnow())
        chats.send_message(s, s.get(User, ella), conversation.id, "no address", None, utcnow())
        s.commit()
    assert len(started) == 1


def test_editing_drops_the_old_preview(app, pair):
    post(pair["client"]["ella"], f"/chats/{pair['chat']}/send", {"body": "https://one.example/"})
    with app.app_context():
        message_id = db.session().scalar(select(Message.id))
    previews.make_preview(app, message_id, fetcher=lambda url, allow_private: (url, PAGE))
    with app.app_context():
        s = db.session()
        tools.edit_message(s, s.get(User, pair["ids"]["ella"]), message_id, "https://two.example/", utcnow())
        s.commit()
        assert s.scalar(select(func.count(LinkPreview.id))) == 0
