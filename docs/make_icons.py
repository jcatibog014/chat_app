r"""Make the logo and the app icons (paperplane/static/icons/) from the owner's picture,
docs/logo/paper-plane-logo.webp: a pink paper plane with a dotted trail behind it, on a
WHITE background (932 x 823, no see-through parts; PR #6, before that two chat bubbles).

Because the picture has its own white background, it is shown on a white tile with round
corners everywhere, like an app icon: on the blue sky of the menu, on the login, and in a
browser tab. This script draws, through Playwright + Edge (a browser scales a picture smoothly):
- logo.png (256 px): the white tile, for the pages (logo() in _icons.html);
- icon-32/192/512.png and favicon.ico: the same tile, for tabs and the app manifest;
- apple-touch-icon.png: square and white, because iPhones round the corners themselves;
- icon-maskable-512.png: square and white, the plane smaller, because Android crops these
  into its own shape (a circle, a squircle...) and the plane must stay in the middle 80%.

Playwright isn't in requirements.txt: borrow another project's:

    cd C:\Users\USER\Documents\Workspace\chat_app
    ..\expense_tracker\.venv\Scripts\python docs\make_icons.py

Run it again after changing the picture, and change the service worker's CACHE number so
installed apps fetch the new icons.
"""
import base64
import struct
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "docs" / "logo" / "paper-plane-logo.webp"
ICONS = ROOT / "paperplane" / "static" / "icons"

# (file, size, square?, how big the plane is drawn: 1 = the whole picture fills the tile)
PNGS = [
    ("logo.png", 256, False, 0.84),
    ("icon-32.png", 32, False, 0.96),
    ("icon-192.png", 192, False, 0.84),
    ("icon-512.png", 512, False, 0.84),
    ("apple-touch-icon.png", 180, True, 0.86),
    ("icon-maskable-512.png", 512, True, 0.72),
]
ICO_SIZES = (16, 32, 48)


def html(size, square, scale):
    picture = base64.b64encode(SOURCE.read_bytes()).decode()
    radius = "0" if square else f"{size * 0.22}px"
    inner = size * scale
    return (f'<style>html, body {{ margin: 0; background: transparent; }}'
            f'#icon {{ width: {size}px; height: {size}px; overflow: hidden; position: relative;'
            f' background: #ffffff; border-radius: {radius}; }}'
            f'#icon img {{ position: absolute; width: {inner}px; height: {inner}px; object-fit: contain;'
            f' left: {(size - inner) / 2}px; top: {(size - inner) / 2}px; }}</style>'
            f'<div id="icon"><img src="data:image/webp;base64,{picture}" alt=""></div>')


def render(browser, size, square=False, scale=0.9):
    page = browser.new_page(viewport={"width": size, "height": size})
    page.set_content(html(size, square, scale))
    page.wait_for_function("document.querySelector('#icon img').complete")
    png = page.locator("#icon").screenshot(omit_background=True)
    page.close()
    return png


def write_ico(path, pictures):
    """favicon.ico is a small header, a list, then the PNG files themselves."""
    header = struct.pack("<HHH", 0, 1, len(pictures))
    offset = 6 + 16 * len(pictures)
    entries, data = b"", b""
    for size, png in pictures:
        entries += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(header + entries + data)


def main():
    ICONS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        for name, size, square, scale in PNGS:
            (ICONS / name).write_bytes(render(browser, size, square, scale))
            print("saved", name)
        write_ico(ICONS / "favicon.ico", [(size, render(browser, size, scale=0.96)) for size in ICO_SIZES])
        print("saved favicon.ico")
        browser.close()


if __name__ == "__main__":
    main()
