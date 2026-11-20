"""Settings: your name, password, recovery code, time zone, and logging out other devices."""
from paperplane import db
from paperplane.models import User

from .conftest import PASSWORD, browser, is_logged_in, post, words


def user(app, user_id):
    with app.app_context():
        return db.session().get(User, user_id)


def test_settings_shows_every_section(maria):
    client, _ = maria
    page = words(client.get("/settings"))
    for heading in ("Your Account", "Time Zone", "Appearance", "Devices", "Log Out Other Devices"):
        assert heading in page


def test_change_name(app, maria):
    client, user_id = maria
    response = post(client, "/settings", {"action": "name", "user_name": "  Maria   Clara "}, follow_redirects=True)
    assert "Your name has been changed." in words(response)
    assert user(app, user_id).name == "Maria Clara"


def test_change_password_keeps_this_device_and_logs_out_the_others(app, maria):
    client, user_id = maria
    other = browser(app, user_id)
    response = post(client, "/settings", {"action": "password", "current_password": PASSWORD,
                                          "new_password": "a new one!", "confirm_password": "a new one!"})
    assert response.status_code == 302
    assert is_logged_in(client)
    assert not is_logged_in(other)


def test_change_password_needs_the_current_one(maria):
    client, _ = maria
    response = post(client, "/settings", {"action": "password", "current_password": "wrong",
                                          "new_password": "a new one!", "confirm_password": "a new one!"})
    assert response.status_code == 400 and "That isn't your current password." in words(response)


def test_a_new_recovery_code_needs_the_password(app, maria):
    client, user_id = maria
    old_hash = user(app, user_id).recovery_hash
    assert "isn't your current password" in words(post(client, "/settings", {"action": "recovery", "recovery_password": "no"}))
    response = post(client, "/settings", {"action": "recovery", "recovery_password": PASSWORD})
    assert "Your New Recovery Code" in words(response)
    assert user(app, user_id).recovery_hash != old_hash


def test_time_zone(app, maria):
    client, user_id = maria
    post(client, "/settings", {"action": "time_zone", "time_zone": "Asia/Tokyo"})
    assert user(app, user_id).time_zone == "Asia/Tokyo"
    response = post(client, "/settings", {"action": "time_zone", "time_zone": "Mars/Base"})
    assert response.status_code == 400


def test_log_out_other_devices(app, maria):
    client, user_id = maria
    other = browser(app, user_id)
    response = post(client, "/settings", {"action": "logout_others"}, follow_redirects=True)
    assert "Your other devices are logged out." in words(response)
    assert is_logged_in(client)
    assert not is_logged_in(other)
