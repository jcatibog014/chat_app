"""The Notifications page (/notifications): who mentioned you, replied, reacted, posted a story,
called, or changed something for you in a group. Clicking a line reads it and takes you there
(/notifications/<id>); "Mark all as read" reads them all. (Push settings stay in Settings >
Preferences; this page is the list.)"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import activity, db
from ..auth import login_required
from ..dates import utcnow

bp = Blueprint("activity", __name__)


@bp.get("/notifications")
@login_required
def page():
    s = db.session()
    moment = utcnow()
    activity.purge(s, g.user, moment)
    before = request.args.get("before", type=int)
    lines, more = activity.page(s, g.user, moment, before)
    s.commit()  # the purge
    new = [line for line in lines if line.is_new]
    earlier = [line for line in lines if not line.is_new]
    return render_template("notifications.html", new=new, earlier=earlier, more=more, before=before,
                           older=lines[-1].item.id if more and lines else None)


@bp.get("/notifications/<int:activity_id>")
@login_required
def open_line(activity_id):
    """A click on a line: it's read, then on to where it happened (or back to the list)."""
    s = db.session()
    line = activity.open_line(s, g.user, activity_id, utcnow())
    if line is None:
        abort(404)
    s.commit()
    return redirect(line.url or url_for("activity.page"))


@bp.post("/notifications/<int:activity_id>/read")
@login_required
def read_line(activity_id):
    """A line with nowhere to go (a group you left, a story that ended): mark it read."""
    s = db.session()
    if activity.open_line(s, g.user, activity_id, utcnow()) is None:
        abort(404)
    s.commit()
    return redirect(url_for("activity.page"))


@bp.post("/notifications/read")
@login_required
def read_all():
    s = db.session()
    activity.mark_read(s, g.user, utcnow())
    s.commit()
    flash("All notifications are marked as read.")
    return redirect(url_for("activity.page"))


def unread():
    """The number beside Notifications in the menu (worked out once per page)."""
    if g.get("user") is None:
        return 0
    if "activity_unread" not in g:
        g.activity_unread = activity.unread_count(db.session(), g.user.id)
    return g.activity_unread


def init_app(app):
    app.jinja_env.globals.update(activity_unread=unread)
