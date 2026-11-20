"""The look: both dark-mode blocks agree, and the main colours are readable (4.5:1)."""
import re
from pathlib import Path

CSS = (Path(__file__).parent.parent / "paperplane" / "static" / "style.css").read_text(encoding="utf-8")


def tokens(block):
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def root_block():
    return CSS.split(":root {", 1)[1].split("\n}", 1)[0]


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


def test_text_colours_are_readable_in_both_themes():
    light = tokens(root_block())
    dark = {**light, **dark_blocks()[0]}
    for theme in (light, dark):
        for ink, ground in [("--ink", "--bg"), ("--ink", "--surface"), ("--muted", "--bg"), ("--muted", "--surface"),
                            ("--muted", "--surface-soft"), ("--link", "--surface"), ("--on-accent", "--accent"),
                            ("--danger", "--surface"), ("--ink", "--bubble-mine"), ("--ink", "--bubble-theirs"),
                            ("--sky-ink", "--sky-top"), ("--sky-ink", "--sky-bottom"), ("--sky-muted", "--sky-top")]:
            assert contrast(theme[ink], theme[ground]) >= 4.5, (ink, ground, theme[ink], theme[ground])


def test_avatar_tones_carry_white_letters():
    for colour in re.findall(r"\.tone-\d \{ background: (#[0-9a-f]{6}); \}", CSS):
        assert contrast("#ffffff", colour) >= 4.5, colour


def test_no_inline_styles_or_scripts_in_templates():
    """The CSP forbids them, so the browser would ignore them."""
    for template in (Path(__file__).parent.parent / "paperplane" / "templates").glob("*.html"):
        source = template.read_text(encoding="utf-8")
        assert 'style="' not in source, template.name
        assert not re.search(r"<script(?![^>]*\bsrc=)", source), template.name
