"""Times. The database always holds naive UTC. People see times in their own
time zone (Settings, Asia/Manila at first).

Nothing here reads the clock except utcnow(). Code that decides something
about time (presence, typing, the demo crew) is handed the moment as an
argument, so tests can say "it is now 9:00" without waiting.
"""
from datetime import UTC, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

DEFAULT_ZONE = "Asia/Manila"


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


@lru_cache(maxsize=256)
def zone(name):
    """A ZoneInfo, or Manila's when the name is empty or unknown."""
    try:
        return ZoneInfo(name or DEFAULT_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_ZONE)


def is_zone(name):
    return bool(name) and name in zone_names()


@lru_cache(maxsize=1)
def zone_names():
    return frozenset(available_timezones())


def zone_choices():
    """Every real place's time zone ("Asia/Manila"), sorted."""
    return sorted(name for name in zone_names() if "/" in name and not name.startswith(("Etc/", "SystemV/")))


def to_zone(moment, name):
    """Naive UTC from the database -> an aware datetime in that zone."""
    return moment.replace(tzinfo=UTC).astimezone(zone(name))


def iso(moment):
    """For <time datetime="..."> and JSON: the browser reads it as UTC because of the Z."""
    return moment.replace(microsecond=0).isoformat() + "Z" if moment else None
