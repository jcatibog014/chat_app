"""End to end: the real app on a real port, real WebSocket clients, real handshakes."""
import pytest
from simple_websocket import ConnectionClosed

from paperplane import db
from paperplane.models import User

from .conftest import PASSWORD, make_app, make_user, post
from .live_helpers import Refused, Server


@pytest.fixture
def live(tmp_path):
    # No SERVER_NAME: the real server is reached at 127.0.0.1:<port>.
    app = make_app(tmp_path, SERVER_NAME=None)
    server = Server(app)
    yield server
    server.stop()


def login_cookie(app, username):
    """Log in through the real form; return the signed cookie's value."""
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = "t"
    response = client.post("/login", data={"username": username, "password": PASSWORD, "csrf_token": "t"})
    assert response.status_code == 302
    return client.get_cookie("paperplane_session").value, client


def dm_between(app, client, username):
    response = post(client, "/chats/new/dm", {"username": username})
    return int(response.headers["Location"].rsplit("/", 1)[1])


def test_the_handshake_needs_a_login(live):
    with pytest.raises(Refused):
        live.connect(cookie=None)


def test_the_handshake_refuses_other_sites(live):
    """Cross-site WebSocket hijacking: the cookie is right, the page is somebody else's."""
    make_user(live.app, "maria")
    cookie, _ = login_cookie(live.app, "maria")
    with pytest.raises(Refused):
        live.connect(cookie, origin="https://evil.example")
    with pytest.raises(Refused):
        live.connect(cookie, origin="null")
    assert live.connect(cookie)  # from our own page it works


def test_a_plain_request_to_the_socket_address_is_refused(live):
    response = live.app.test_client().get("/ws")
    assert response.status_code == 400


def test_two_people_chat_live(live):
    make_user(live.app, "maria", "Maria Santos")
    ana_id = make_user(live.app, "ana", "Ana Reyes")
    maria_cookie, maria_client = login_cookie(live.app, "maria")
    ana_cookie, _ = login_cookie(live.app, "ana")
    chat = dm_between(live.app, maria_client, "ana")
    maria, ana = live.connect(maria_cookie), live.connect(ana_cookie)
    assert maria.wait_for("hello")["data"]["heartbeat"] == 25
    ana.wait_for("hello")
    answer = maria.request("message.send", {"conversation_id": chat, "body": "Hi Ana!", "client_id": "e2e-000001"})
    assert answer["type"] == "ack"
    news = ana.wait_for("message.new", body="Hi Ana!")
    assert news["data"]["sender_name"] == "Maria Santos" and "Hi Ana!" in news["data"]["html"]
    # Ana reads it: Maria hears (for "Seen by"), Ana's total drops to 0.
    ana.request("read", {"conversation_id": chat, "seq": news["data"]["seq"]})
    assert maria.wait_for("read", user_id=ana_id)["data"]["seq"] == news["data"]["seq"]
    assert ana.wait_for("unread.total")["data"]["total"] == 0


def test_a_form_post_reaches_open_sockets_too(live):
    """Sending without JavaScript still arrives live for the others (the outbox is shared)."""
    make_user(live.app, "maria")
    make_user(live.app, "ana")
    _, maria_client = login_cookie(live.app, "maria")
    ana_cookie, _ = login_cookie(live.app, "ana")
    chat = dm_between(live.app, maria_client, "ana")
    ana = live.connect(ana_cookie)
    ana.wait_for("hello")
    post(maria_client, f"/chats/{chat}/send", {"body": "From a plain form"})
    ana.wait_for("message.new", body="From a plain form")


def test_a_new_chat_appears_in_the_other_persons_list(live):
    make_user(live.app, "maria")
    make_user(live.app, "ana", "Ana Reyes")
    _, maria_client = login_cookie(live.app, "maria")
    ana_cookie, _ = login_cookie(live.app, "ana")
    ana = live.connect(ana_cookie)
    ana.wait_for("hello")
    chat = dm_between(live.app, maria_client, "ana")
    news = ana.wait_for("conversation.new", conversation_id=chat)
    assert "Maria" in news["data"]["html"]  # Ana's row is named after Maria


def test_coming_back_after_a_drop_catches_up(live):
    make_user(live.app, "maria")
    make_user(live.app, "ana")
    maria_cookie, maria_client = login_cookie(live.app, "maria")
    ana_cookie, _ = login_cookie(live.app, "ana")
    chat = dm_between(live.app, maria_client, "ana")
    ana = live.connect(ana_cookie)
    ana.wait_for("hello")
    maria = live.connect(maria_cookie)
    maria.request("message.send", {"conversation_id": chat, "body": "first", "client_id": "e2e-000010"})
    seen = ana.wait_for("message.new", body="first")["data"]["version"]
    ana.close()  # Ana's train goes into a tunnel...
    for n, body in enumerate(("second", "third")):
        maria.request("message.send", {"conversation_id": chat, "body": body, "client_id": f"e2e-00002{n}"})
    ana_again = live.connect(ana_cookie)  # ...and out again
    ana_again.wait_for("hello")
    answer = ana_again.request("sync", {"open": {"conversation_id": chat, "version": seen}})
    assert [m["body"] for m in answer["data"]["changes"]] == ["second", "third"]
    assert answer["data"]["total_unread"] == 3


def test_changing_the_password_closes_the_other_sockets(live):
    make_user(live.app, "maria")
    cookie, client = login_cookie(live.app, "maria")
    tab = live.connect(cookie)
    tab.wait_for("hello")
    post(client, "/settings", {"action": "password", "current_password": PASSWORD,
                               "new_password": "brand new pass", "confirm_password": "brand new pass"})
    assert tab.wait_for("bye")["data"]["reason"] == "logged_out"
    assert tab.closed()
    # The old cookie can't open a new socket either.
    with pytest.raises(Refused):
        live.connect(cookie)


def test_too_many_tabs(live):
    make_user(live.app, "maria")
    cookie, _ = login_cookie(live.app, "maria")
    tabs = [live.connect(cookie) for _ in range(5)]
    for tab in tabs:
        tab.wait_for("hello")
    sixth = live.connect(cookie)
    assert sixth.wait_for("error")["code"] == "too_many_connections"
    assert sixth.closed()


def test_me_json(live):
    make_user(live.app, "maria")
    _, client = login_cookie(live.app, "maria")
    assert client.get("/me.json").get_json()["logged_in"] is True
    assert live.app.test_client().get("/me.json").status_code == 401
    with live.app.app_context():
        assert db.session().query(User).count() == 1
