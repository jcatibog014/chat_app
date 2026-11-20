"""How times look on pages. A chat shows "9:42 AM" for today, "Yesterday",
a weekday for this week, then a date.

Pages are drawn on the server in the person's own time zone (g.user.time_zone),
so they read right without JavaScript. Messages that arrive live carry the
moment in UTC (<time datetime="...Z">) and app.js writes it the same way in the
browser's own words.
"""
import re
from datetime import timedelta

from flask import g
from markupsafe import Markup, escape

from .dates import DEFAULT_ZONE, iso, to_zone, utcnow


def _zone_name():
    user = g.get("user")
    return user.time_zone if user is not None else DEFAULT_ZONE


def clock(moment):
    """9:42 AM"""
    if moment is None:
        return ""
    local = to_zone(moment, _zone_name())
    return local.strftime("%I:%M %p").lstrip("0")


def day_label(moment, now=None):
    """Today / Yesterday / Monday / Nov 3 / Nov 3, 2025: the line between days in a chat."""
    if moment is None:
        return ""
    zone_name = _zone_name()
    local = to_zone(moment, zone_name).date()
    today = to_zone(now or utcnow(), zone_name).date()
    if local == today:
        return "Today"
    if local == today - timedelta(days=1):
        return "Yesterday"
    if today - local < timedelta(days=7):
        return local.strftime("%A")
    if local.year == today.year:
        return f"{local.strftime('%b')} {local.day}"
    return f"{local.strftime('%b')} {local.day}, {local.year}"


def short_when(moment, now=None):
    """The time beside a chat in the list: the clock today, else the day."""
    if moment is None:
        return ""
    label = day_label(moment, now)
    return clock(moment) if label == "Today" else label


def ago(moment, now=None):
    """"just now", "5 min ago", "3 hours ago", then the day: for "Last seen ..."."""
    if moment is None:
        return ""
    seconds = max(0, ((now or utcnow()) - moment).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    label = day_label(moment, now)
    return label.lower() if label == "Yesterday" else f"on {label}"


URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TRAILING = ".,!?;:)]}'\""


def linkify(text):
    """A message as safe HTML: everything escaped, web addresses made into links.

    The text is escaped FIRST piece by piece, so nothing a person types can become
    markup; only the <a> tags we add are real. Links open in a new tab, without
    telling the other site where they came from (noopener, noreferrer), and "ugc"
    tells search engines a user wrote it."""
    out = []
    position = 0
    for match in URL.finditer(text or ""):
        url = match.group(0)
        # "see https://example.com." -> the dot isn't part of the address.
        while url and url[-1] in TRAILING:
            if url[-1] == ")" and url.count("(") >= url.count(")"):
                break
            url = url[:-1]
        if len(url) < 12:
            continue
        start, end = match.start(), match.start() + len(url)
        out.append(escape(text[position:start]))
        out.append(Markup('<a href="{0}" target="_blank" rel="noopener noreferrer nofollow ugc">{0}</a>').format(url))
        position = end
    out.append(escape((text or "")[position:]))
    return Markup("").join(out)


def init_app(app):
    app.jinja_env.filters.update(clock=clock, day_label=day_label, short_when=short_when, ago=ago, iso=iso,
                                 linkify=linkify)
