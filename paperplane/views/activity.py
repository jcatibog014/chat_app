"""The Notifications page (/notifications): who mentioned you, replied, reacted, posted a story,
or changed something for you in a group. Opening it marks everything read. (Push and sound
settings stay in Settings > Notifications; this page is the list.)"""
from flask import Blueprint, g, render_template, request

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
    new = [line for line in lines if line.is_new]
    earlier = [line for line in lines if not line.is_new]
    activity.mark_read(s, g.user, moment)
    s.commit()
    g.activity_unread = 0
    return render_template("notifications.html", new=new, earlier=earlier, more=more, before=before,
                           older=lines[-1].item.id if more and lines else None)


def unread():
    """The number beside Notifications in the menu (worked out once per page)."""
    if g.get("user") is None:
        return 0
    if "activity_unread" not in g:
        g.activity_unread = activity.unread_count(db.session(), g.user.id)
    return g.activity_unread


def init_app(app):
    app.jinja_env.globals.update(activity_unread=unread)
