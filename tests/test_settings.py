"""Settings: your name, password, recovery code, time zone, and logging out other devices."""
from paperplane import db
from paperplane.models import User

from .conftest import PASSWORD, browser, is_logged_in, post, words


def user(app, user_id):
    with app.app_context():
        return db.session().get(User, user_id)


PAGES = {
    "general": ["Your Account", "Time Zone"],
    "appearance": ["Colour", "Pink", "Purple", "Red", "Blue", "Light or Dark", "Device", "Light", "Dark"],
    "preferences": ["Notifications", "On This Device", "Show When I'm Online", "Blocked People"],
    "devices": ["Log Out Other Devices", "Log Out"],
}


def address(name):
    """General is Settings' first tab, at /settings itself."""
    return "/settings" if name == "general" else f"/settings/{name}"


def test_settings_has_a_page_for_each_part(maria):
    client, _ = maria
    for name, headings in PAGES.items():
        page = words(client.get(address(name)))
        for heading in headings:
            assert heading in page, (name, heading)
    # /settings opens General, and nothing from the other tabs.
    page = words(client.get("/settings"))
    assert "Your Account" in page and "Log Out Other Devices" not in page and "Blocked People" not in page


def test_settings_has_tabs_at_the_top(maria):
    client, _ = maria
    page = client.get("/settings/preferences").get_data(as_text=True)
    assert 'class="settings-nav"' not in page  # no second menu beside the cards
    menu = page.split('<nav class="tabs settings-tabs" aria-label="Settings">', 1)[1].split("</nav>", 1)[0]
    assert [name for name in PAGES if f'href="{address(name)}"' in menu] == list(PAGES)
    assert menu.count('aria-current="page"') == 1
    assert 'aria-current="page"' in menu.split('href="/settings/preferences"', 1)[1].split("</a>", 1)[0]
    # The way IN stays one link, in the profile menu (the sky menu has no Settings entry).
    sky = page.split('<ul class="side-links">', 1)[1].split("</ul>", 1)[0]
    assert "/settings" not in sky


def test_the_old_pages_go_to_their_new_tab(maria):
    client, _ = maria
    for old, new in (("account", "/settings"), ("notifications", "/settings/preferences"), ("privacy", "/settings/preferences")):
        response = client.get(f"/settings/{old}")
        assert response.status_code == 302 and response.headers["Location"] == new, old
    # A form posted to an old address still works, and comes back to the new tab.
    response = post(client, "/settings/privacy", {"action": "show_presence", "show_presence": "0"})
    assert response.headers["Location"].startswith("/settings/preferences")


def test_an_unknown_settings_page_is_not_found(maria):
    client, _ = maria
    assert client.get("/settings/secrets").status_code == 404


def test_each_form_comes_back_to_its_own_page(maria):
    client, _ = maria
    assert post(client, "/settings", {"action": "time_zone", "time_zone": "Asia/Tokyo"}).headers["Location"].endswith("/settings")
    assert post(client, "/settings/devices", {"action": "logout_others"}).headers["Location"].endswith("/settings/devices")
    assert post(client, "/settings/appearance", {"action": "accent", "accent": "red"}).headers["Location"].endswith("/settings/appearance")


# ---------- The app's colour ----------

def test_pink_is_the_default_colour(app, maria):
    client, user_id = maria
    assert user(app, user_id).accent == "pink"
    assert '<html lang="en" data-accent="pink">' in client.get("/chats").get_data(as_text=True)
    assert '<html lang="en" data-accent="pink">' in browser(app).get("/login").get_data(as_text=True)


def test_choosing_a_colour_saves_it_to_the_account(app, maria):
    client, user_id = maria
    response = post(client, "/settings/appearance", {"action": "accent", "accent": "purple"}, follow_redirects=True)
    assert "Paper Plane is purple now." in words(response)
    assert user(app, user_id).accent == "purple"
    # Every page, on any device, is drawn in it.
    other_device = browser(app, user_id)
    page = other_device.get("/chats").get_data(as_text=True)
    assert '<html lang="en" data-accent="purple">' in page and '<meta name="theme-color" content="#7b2cbf">' in page
    checked = other_device.get("/settings/appearance").get_data(as_text=True)
    assert 'value="purple" checked' in checked


def test_only_the_four_colours(app, maria):
    client, user_id = maria
    response = post(client, "/settings/appearance", {"action": "accent", "accent": "rainbow"})
    assert response.status_code == 400 and "Choose one of the colours." in words(response)
    assert user(app, user_id).accent == "pink"


def test_the_demo_may_try_colours(app):
    demo = browser(app)
    post(demo, "/demo")
    response = post(demo, "/settings/appearance", {"action": "accent", "accent": "blue"}, follow_redirects=True)
    assert response.status_code == 200 and 'data-accent="blue"' in response.get_data(as_text=True)


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
