"""The menus (the owner's layout): the sky has only Chats and the profile; Settings and Log out
are in the profile menu; New Chat and Search open inside the chat page; phones get a bottom bar."""
import re

from .conftest import text


def side_links(page):
    return re.search(r'<ul class="side-links">(.*?)</ul>', page, re.S).group(1)


def test_the_sky_menu_has_only_chats(maria):
    client, _ = maria
    links = side_links(text(client.get("/chats")))
    assert "Chats" in links
    for gone in ("New Chat", "Search", "Settings"):
        assert gone not in links


def test_the_profile_menu_has_settings_and_log_out(maria):
    client, _ = maria
    page = text(client.get("/chats"))
    menus = re.findall(r'<div class="account-items" role="menu">(.*?)</form>', page, re.S)
    assert len(menus) == 2  # the sky's (computers) and the bottom bar's (phones)
    for menu in menus:
        assert "Maria Santos" in menu and "@maria" in menu and "/settings" in menu and "Log out" in menu


def test_phones_get_a_bottom_bar_with_chats_and_profile(maria):
    client, _ = maria
    bar = re.search(r'<nav class="bottom-nav" aria-label="Main">(.*?)</nav>', text(client.get("/settings")), re.S).group(1)
    assert "Chats" in bar and "Profile" in bar and "data-total-unread" in bar
    assert 'aria-current="page"' in bar.split("Chats")[1]  # Settings marks Profile, not Chats


def test_new_chat_and_search_open_beside_the_chat_list(maria):
    client, _ = maria
    for url, title, button in (("/chats/new", "New Chat", "New chat"), ("/search", "Search", "Search messages")):
        page = text(client.get(url))
        assert 'class="chat-list-pane"' in page and 'class="conversation side-panel"' in page, url
        assert f'<h2 id="panel-title">{title}</h2>' in page
        assert re.search(rf'is-here" href="[^"]+" title="{button}" aria-current="page"', page), url
    chats = text(client.get("/chats"))
    assert 'title="Search messages"' in chats and 'title="New chat"' in chats


def test_the_demo_profile_menu_offers_a_friend_link(app):
    from .conftest import browser, post
    client = browser(app)
    post(client, "/demo")
    page = text(client.get("/chats"))
    assert "Invite a friend" in page and "Leave the demo" in page and 'class="demo-chip"' in page
