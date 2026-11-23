"""Search, muting a chat, and notifications on this device (web push)."""
from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .. import chats, db, notify
from ..auth import login_required, safe_next
from ..dates import utcnow
from ..problems import Problem

bp = Blueprint("search", __name__)


@bp.get("/search")
@login_required
def page():
    s = db.session()
    query = " ".join(request.args.get("q", "").split())[:100]
    chat_id = request.args.get("chat", type=int)
    rows = chats.chat_list(s, g.user)
    results = notify.search(s, g.user, query, chat_id) if query else []
    titles = {row.conversation.id: row.title for row in rows}
    return render_template("search.html", query=query, chat_id=chat_id, rows=rows, titles=titles,
                           results=results, too_short=bool(query) and len(query) < 2, snippet=notify.snippet)


@bp.post("/chats/<int:chat_id>/mute")
@login_required
def mute_chat(chat_id):
    s = db.session()
    choice = request.form.get("choice", "")
    try:
        notify.mute_chat(s, g.user, chat_id, choice, utcnow())
    except Problem as problem:
        s.rollback()
        if problem.status == 404:
            raise
        flash(problem.message, "error")
        return redirect(url_for("chats.show", chat_id=chat_id))
    s.commit()
    flash("Notifications are back on for this chat." if choice == "off" else "This chat is muted.")
    return redirect(safe_next(request.form.get("next"), url_for("chats.show", chat_id=chat_id)))


@bp.post("/notifications/devices")
@login_required
def add_device():
    """app.js posts the browser's push subscription here (JSON, with the X-CSRF-Token header)."""
    s = db.session()
    try:
        notify.save_device(s, g.user, request.get_json(silent=True) or {})
    except Problem as problem:
        return jsonify(error=problem.code, message=problem.message), problem.status
    s.commit()
    return jsonify(ok=True, devices=notify.device_count(s, g.user))


@bp.post("/notifications/devices/forget")
@login_required
def forget_devices():
    s = db.session()
    count = notify.forget_devices(s, g.user)
    s.commit()
    flash(f"Phone and browser notifications are off on {count} device{'s' if count != 1 else ''}." if count
          else "No device had notifications on.")
    return redirect(url_for("settings.page", section="notifications"))


def notifications_section(s, user):
    return {"settings_sections": ["_settings_notifications.html"], "device_count": notify.device_count(s, user),
            "push_key": current_app.config.get("VAPID_PUBLIC_KEY", "")}


def init_app(app):
    from . import settings
    if notifications_section not in settings.extras:
        settings.extras.insert(0, notifications_section)
    app.jinja_env.globals.update(MUTE_CHAT_CHOICES=notify.MUTE_CHAT_CHOICES, muted_words=notify.muted_words,
                                 chat_is_muted=notify.is_muted)

    @app.get("/sw.js")
    def service_worker():
        # From the top of the site, not /static/: a service worker only looks after the pages
        # below the address it came from.
        response = app.send_static_file("sw.js")
        response.headers["Cache-Control"] = "no-cache"
        return response
