r"""Take the README's pictures (docs/screenshots/), all from Try the demo.

Start the app first, in its own window (--debug: the demo limit is off, and templates are
read fresh), and let it run:

    cd C:\Users\USER\Documents\Workspace\chat_app
    .venv\Scripts\flask --app paperplane run --debug --port 5015

Then, in a second window (Playwright isn't in requirements.txt: borrow another project's):

    cd C:\Users\USER\Documents\Workspace\chat_app
    ..\expense_tracker\.venv\Scripts\python docs\take_screenshots.py

Every picture is made-up demo data. tests/test_readme.py checks that the README shows each
picture in docs/screenshots/, and nothing else.
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5015"
OUT = Path(__file__).parent / "screenshots"
DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}


def shot(page, name):
    time.sleep(0.5)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print("saved", name)


def start_demo(page):
    page.goto(BASE + "/login")
    page.click("text=Try the demo")
    page.wait_for_load_state("networkidle")
    # Close the welcome note so it doesn't cover the pictures.
    for close in page.locator("[data-toast-close]").all():
        close.click()
    time.sleep(0.6)


def open_chat(page, title):
    page.goto(BASE + "/chats")
    page.locator(".chat-row", has_text=title).locator("a").click()
    page.wait_for_selector("[data-composer-text]")
    time.sleep(0.6)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")

        # ---------- Before logging in ----------
        context = browser.new_context(viewport=DESKTOP, color_scheme="light")
        page = context.new_page()
        page.goto(BASE + "/login")
        shot(page, "login")
        page.goto(BASE + "/signup")
        shot(page, "signup")
        context.close()

        # ---------- The demo, light ----------
        context = browser.new_context(viewport=DESKTOP, color_scheme="light")
        page = context.new_page()
        start_demo(page)
        time.sleep(2)  # Ana and Ben come online
        shot(page, "chats")

        open_chat(page, "Papel Studio")
        shot(page, "group-chat")
        # The ... menu on Ana's message: reactions, Reply.
        message = page.locator(".msg.theirs", has_text="Thursday's review").last
        message.hover()
        message.locator(".msg-menu summary").click()
        shot(page, "message-menu")
        message.locator("a[data-tool=reply]").click()
        page.fill("[data-composer-text]", "3 PM works for me.")
        shot(page, "reply")
        page.keyboard.press("Enter")
        time.sleep(1)

        # A DM with Ana: send, then she reads, types and answers (the crew).
        open_chat(page, "Ana Reyes")
        page.fill("[data-composer-text]", "Hi Ana! Did you get the logo files?")
        page.keyboard.press("Enter")
        for _ in range(30):
            time.sleep(0.3)
            if "typing" in (page.locator("[data-typing]").text_content() or ""):
                break
        shot(page, "live-typing")
        time.sleep(6)
        shot(page, "live-reply")

        open_chat(page, "Weekend Hike")
        shot(page, "link-and-mute")
        open_chat(page, "Launch Plans")
        shot(page, "file")
        page.click("a[title='Group info']")
        page.wait_for_load_state("networkidle")
        shot(page, "group-info")
        page.locator(".member-row", has_text="Dana").locator("summary").click()
        shot(page, "member-menu")

        open_chat(page, "Carlo Diaz")
        page.click("a[title='Chat info']")
        page.wait_for_load_state("networkidle")
        shot(page, "chat-info")

        page.goto(BASE + "/chats/new")
        shot(page, "new-chat")
        page.goto(BASE + "/search?q=mockups")
        shot(page, "search")
        page.goto(BASE + "/settings")
        shot(page, "settings")

        # A friend plays Ben, in a second browser.
        page.goto(BASE + "/demo/friends")
        shot(page, "invite-friend")
        link = page.locator("input[aria-label='Link to play Ben Cruz']").input_value()
        friend_context = browser.new_context(viewport=DESKTOP, color_scheme="light")
        friend = friend_context.new_page()
        friend.goto(link)
        shot(friend, "join-demo")
        friend.click("button[type=submit]")
        friend.wait_for_load_state("networkidle")
        open_chat(page, "Ben Cruz")
        open_chat(friend, "Mika Santos")
        friend.fill("[data-composer-text]", "It's really me this time, not the made-up Ben!")
        friend.keyboard.press("Enter")
        time.sleep(1.5)
        shot(page, "friend-live")
        friend_context.close()
        context.close()

        # ---------- Dark mode ----------
        context = browser.new_context(viewport=DESKTOP, color_scheme="dark")
        page = context.new_page()
        start_demo(page)
        open_chat(page, "Papel Studio")
        shot(page, "dark-chat")
        context.close()

        # ---------- A phone ----------
        context = browser.new_context(viewport=PHONE, color_scheme="light", is_mobile=True, has_touch=True)
        page = context.new_page()
        start_demo(page)
        time.sleep(1.5)
        shot(page, "phone-chats")
        open_chat(page, "Papel Studio")
        shot(page, "phone-chat")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
