"""Problems: why something can't be done, said once and used by pages AND WebSocket events.

The domain code (chats.py, groups.py, ...) raises these. A page turns one into
an HTTP error (an error page, or a 400 with the form shown again); the event
router (events.py) turns it into {"type": "error", "code": ..., "message": ...}.
"""


class Problem(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class NotFound(Problem):
    """Also for things that exist but aren't yours to see: saying "forbidden" would
    tell a stranger that chat 41 exists. So a stranger and a typo look the same."""
    status = 404
    code = "not_found"

    def __init__(self, message="We couldn't find that. It may have been deleted, or it isn't yours to see."):
        super().__init__(message)


class Forbidden(Problem):
    """You can see it, but your role doesn't allow this (a member renaming a group)."""
    status = 403
    code = "forbidden"


class Invalid(Problem):
    """What was sent isn't right (an empty message, a name that is too long)."""
    status = 400
    code = "invalid"


class TooMany(Problem):
    status = 429
    code = "too_many"


class Conflict(Problem):
    """It can't be done in the state things are in (the owner leaving without handing over)."""
    status = 409
    code = "conflict"
