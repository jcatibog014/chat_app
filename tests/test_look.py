"""The look: both dark-mode blocks agree, and the main colours are readable (4.5:1)."""
import re
from pathlib import Path

CSS = (Path(__file__).parent.parent / "paperplane" / "static" / "style.css").read_text(encoding="utf-8")


def tokens(block):
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def root_block():
    return CSS.split(":root {\n  color-scheme: light;", 1)[1].split("\n}", 1)[0]


ACCENTS = ("pink", "purple", "red", "blue")


def palette(name):
    """A palette's --pal-* values (pink is also the default, on plain :root)."""
    opening = ':root, :root[data-accent="pink"] {' if name == "pink" else f':root[data-accent="{name}"] {{'
    return tokens(CSS.split(opening, 1)[1].split("}", 1)[0])


def resolve(theme, pal):
    """Put the palette's colours in place of var(--pal-...)."""
    return {name: pal[value[4:-1]] if value.startswith("var(--pal-") else value for name, value in theme.items()}


def dark_blocks():
    device = CSS.split(':root:not([data-theme="light"]) {', 1)[1].split("}", 1)[0]
    chosen = CSS.split(':root[data-theme="dark"] {', 1)[1].split("}", 1)[0]
    return tokens(device), tokens(chosen)


def luminance(colour):
    colour = colour.strip().lstrip("#")
    channels = [int(colour[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_both_dark_mode_blocks_are_the_same():
    device, chosen = dark_blocks()
    assert device == chosen


def test_every_palette_has_every_colour():
    names = set(palette("pink"))
    assert len(names) == 36  # 18 colours, each for light and dark
    for accent in ACCENTS:
        assert set(palette(accent)) == names, accent


def test_text_colours_are_readable_in_every_palette_and_both_themes():
    for accent in ACCENTS:
        pal = palette(accent)
        light = resolve(tokens(root_block()), pal)
        dark = {**light, **resolve(dark_blocks()[0], pal)}
        for theme in (light, dark):
            check_theme(theme)


def check_theme(theme):
    if True:
        for ink, ground in [("--ink", "--bg"), ("--ink", "--surface"), ("--muted", "--bg"), ("--muted", "--surface"),
                            ("--muted", "--surface-soft"), ("--link", "--surface"), ("--on-accent", "--accent"),
                            ("--danger", "--surface"), ("--ink", "--bubble-mine"), ("--ink", "--bubble-theirs"),
                            ("--sky-ink", "--sky-top"), ("--sky-ink", "--sky-bottom"), ("--sky-muted", "--sky-top"),
                            ("--on-mention", "--mention"), ("--surface", "--muted"), ("--on-accent", "--accent")]:
            assert contrast(theme[ink], theme[ground]) >= 4.5, (ink, ground, theme[ink], theme[ground])
        # Outlined controls stand out from the cards (3:1).
        assert contrast(theme["--control-border"], theme["--surface"]) >= 3, theme["--control-border"]


def test_avatar_tones_carry_white_letters():
    for colour in re.findall(r"\.tone-\d \{ background: (#[0-9a-f]{6}); \}", CSS):
        assert contrast("#ffffff", colour) >= 4.5, colour


def test_the_owners_logo_and_every_icon_are_there(client):
    """The logo is the owner's picture (docs/logo), drawn into the icons by docs/make_icons.py."""
    root = Path(__file__).parent.parent
    assert (root / "docs" / "logo" / "paper-plane-logo.webp").exists()
    for name in ("logo.png", "icon-32.png", "icon-192.png", "icon-512.png", "apple-touch-icon.png",
                 "icon-maskable-512.png", "favicon.ico"):
        assert (root / "paperplane" / "static" / "icons" / name).exists(), name
    page = client.get("/login").get_data(as_text=True)
    assert "icons/logo.png" in page and "favicon.svg" not in page
    assert client.get("/static/icons/logo.png").mimetype == "image/png"


def test_no_inline_styles_or_scripts_in_templates():
    """The CSP forbids them, so the browser would ignore them."""
    for template in (Path(__file__).parent.parent / "paperplane" / "templates").glob("*.html"):
        source = template.read_text(encoding="utf-8")
        assert 'style="' not in source, template.name
        assert not re.search(r"<script(?![^>]*\bsrc=)", source), template.name
