"""Signing up, logging in, the recovery code, and the cookie."""
import re

from paperplane import db
from paperplane.models import User

from .conftest import PASSWORD, browser, get_user, is_logged_in, make_user, post, text, words


def sign_up(client, username="ana", name="Ana Reyes", password="paper planes", confirm=None):
    return post(client, "/signup", {"name": name, "username": username, "password": password,
                                    "confirm_password": confirm if confirm is not None else password})


def code_on(response):
    return re.search(r'value="([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})"', text(response)).group(1)


def test_the_login_page_offers_sign_up_and_the_demo(client):
    page = words(client.get("/login"))
    assert "Log In" in page and "Create an account" in page and "Try the demo" in page


def test_sign_up_shows_a_recovery_code_once_and_logs_you_in(app, client):
    response = sign_up(client)
    assert response.status_code == 200
    assert "Save this code somewhere safe" in words(response)
    code = code_on(response)
    assert is_logged_in(client)
    user = get_user(app, "ana")
    assert user.name == "Ana Reyes" and user.recovery_hash and code not in user.recovery_hash
    # The code isn't kept in the cookie.
    with client.session_transaction() as session:
        assert code not in str(dict(session))


def test_sign_up_checks_what_you_typed(app, client):
    make_user(app, "taken")
    response = sign_up(client, username="Taken")
    assert response.status_code == 400
    assert "That username is taken" in words(response)
    assert "Use 3 to 20 small letters" in words(sign_up(client, username="a b"))
    assert "Use 3 to 20 small letters" in words(sign_up(client, username="d1.ana"))  # a demo's name can't be typed
    assert "That username is taken" in words(sign_up(client, username="admin"))
    assert "at least 8" in words(sign_up(client, password="short"))
    assert "not the same" in words(sign_up(client, confirm="something else"))
    assert "Enter your name" in words(sign_up(client, name="   "))
    assert not is_logged_in(client)


def test_usernames_are_kept_in_small_letters_and_without_the_at(app, client):
    sign_up(client, username="@BenCruz")
    assert get_user(app, "bencruz") is not None


def test_log_in_and_out(app, client):
    make_user(app, "maria")
    response = post(client, "/login", {"username": "Maria", "password": PASSWORD})
    assert response.status_code == 302 and response.headers["Location"].endswith("/chats")
    assert is_logged_in(client)
    post(client, "/logout")
    assert not is_logged_in(client)


def test_a_wrong_password_says_the_same_as_an_unknown_name(app, client):
    make_user(app, "maria")
    wrong = post(client, "/login", {"username": "maria", "password": "nope"})
    unknown = post(client, "/login", {"username": "nobody", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert "Wrong username or password." in text(wrong) and "Wrong username or password." in text(unknown)


def test_five_wrong_passwords_lock_the_account(app, client):
    make_user(app, "maria")
    for _ in range(5):
        post(client, "/login", {"username": "maria", "password": "nope"})
    response = post(client, "/login", {"username": "maria", "password": PASSWORD})
    assert "Too many failed attempts" in text(response)
    assert not is_logged_in(client)


def test_after_logging_in_you_go_back_only_to_a_page_on_this_site(app, client):
    make_user(app, "maria")
    good = post(client, "/login", {"username": "maria", "password": PASSWORD, "next": "/settings"})
    assert good.headers["Location"].endswith("/settings")
    post(client, "/logout")
    evil = post(client, "/login", {"username": "maria", "password": PASSWORD, "next": "//evil.example"})
    assert evil.headers["Location"].endswith("/chats")


def test_pages_need_a_login(client):
    response = client.get("/settings")
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_forms_need_the_csrf_token(app):
    user_id = make_user(app)
    client = browser(app, user_id)
    response = client.post("/settings", data={"action": "time_zone", "time_zone": "Asia/Tokyo"})
    assert response.status_code == 400


def test_the_recovery_code_sets_a_new_password_and_is_used_up(app, client):
    code = code_on(sign_up(client))
    post(client, "/logout")
    response = post(client, "/recover", {"username": "ana", "code": code.lower().replace("-", " "),
                                          "password": "new password", "confirm_password": "new password"})
    assert response.status_code == 200 and "Your New Recovery Code" in words(response)
    new_code = code_on(response)
    assert new_code != code
    post(client, "/logout")
    # The old code no longer works; the new password does.
    again = post(client, "/recover", {"username": "ana", "code": code, "password": "another one",
                                       "confirm_password": "another one"})
    assert "don't match" in words(again)
    assert post(client, "/login", {"username": "ana", "password": "new password"}).status_code == 302


def test_recovery_logs_out_the_other_devices(app, client):
    code = code_on(sign_up(client))
    other = browser(app, get_user(app, "ana").id)
    post(client, "/logout")
    post(client, "/recover", {"username": "ana", "code": code, "password": "new password",
                              "confirm_password": "new password"})
    assert not is_logged_in(other)


def test_another_local_apps_cookie_doesnt_log_you_out(app):
    """Browsers share cookies between ports of 127.0.0.1. Other Flask apps call theirs "session"."""
    client = browser(app, make_user(app))
    client.set_cookie("session", "another-apps-cookie", domain="localhost")
    assert is_logged_in(client)
    response = post(browser(app), "/login", {"username": "maria", "password": PASSWORD})
    assert "paperplane_session=" in response.headers.get("Set-Cookie", "")


def test_pages_have_a_strict_content_security_policy_with_the_websocket(maria):
    client, _ = maria
    policy = client.get("/chats").headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy and "wss://localhost" in policy and "'unsafe-inline'" not in policy


def test_set_password_command(app):
    make_user(app, "maria")
    result = app.test_cli_runner().invoke(args=["set-password", "maria"], input="brand new pass\nbrand new pass\n")
    assert result.exit_code == 0, result.output
    with app.app_context():
        from werkzeug.security import check_password_hash
        user = db.session().get(User, get_user(app, "maria").id)
        assert check_password_hash(user.password_hash, "brand new pass")
