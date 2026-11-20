"""Settings: your account (name, password, recovery code), your time zone,
the look, and logging out. Later stages add sections (who sees you online,
blocked people, notifications).

Settings holds every control and preference (the theme, Log out); the menus
only take you to pages.
"""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .. import db
from ..auth import (clean_name, login_required, name_problem, not_for_demo, password_problem, set_password,
                    set_recovery_code)
from ..dates import is_zone, zone_choices

bp = Blueprint("settings", __name__)
# Sections that later modules add: action name -> function(s, user, form) -> (errors, message)
actions = {}
# Extra things the page shows: function(s, user) -> dict (settings_sections: templates to include)
extras = []


def end_other_sessions(user):
    """Every other device's cookie stops working; this one is brought up to date.
    Open WebSockets made with an old cookie are closed too (stage 3, hub.close_user)."""
    user.session_version += 1
    session["session_version"] = user.session_version


def save_time_zone(s, user, form):
    zone_name = form.get("time_zone", "").strip()
    if not is_zone(zone_name):
        return {"time_zone": "Choose a time zone from the list."}, None
    user.time_zone = zone_name
    return {}, "Your time zone is saved."


actions["time_zone"] = save_time_zone


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def page():
    s = db.session()
    errors = {}
    if request.method == "POST":
        action = request.form.get("action")
        if action == "name":
            not_for_demo("The demo can't change its account.")
            name = clean_name(request.form.get("user_name"))
            if problem := name_problem(name):
                errors["user_name"] = problem
            else:
                g.user.name = name
                s.commit()
                flash("Your name has been changed.")
                return redirect(url_for("settings.page"))
        elif action == "password":
            not_for_demo("The demo can't change its account.")
            if not check_password_hash(g.user.password_hash, request.form.get("current_password", "")):
                errors["current_password"] = "That isn't your current password."
            new = request.form.get("new_password", "")
            if problem := password_problem(new, request.form.get("confirm_password", "")):
                errors["new_password"] = problem
            if not errors:
                set_password(g.user, new)
                s.commit()
                session["session_version"] = g.user.session_version  # this device stays logged in
                close_sockets(g.user.id)
                flash("Your password has been changed. Your other devices are logged out.")
                return redirect(url_for("settings.page"))
        elif action == "recovery":
            not_for_demo("The demo can't change its account.")
            if not check_password_hash(g.user.password_hash, request.form.get("recovery_password", "")):
                errors["recovery_password"] = "That isn't your current password."
            else:
                code = set_recovery_code(g.user)
                s.commit()
                return render_template("recovery_code.html", code=code, first_time=False, from_settings=True)
        elif action == "logout_others":
            end_other_sessions(g.user)
            s.commit()
            close_sockets(g.user.id)
            flash("Your other devices are logged out.")
            return redirect(url_for("settings.page", _anchor="account"))
        elif action in actions:
            errors, message = actions[action](s, g.user, request.form)
            if not errors:
                s.commit()
                flash(message)
                return redirect(url_for("settings.page", _anchor=request.form.get("anchor") or None))
            s.rollback()
        else:
            abort(400, "Choose something to change.")
    data = {"settings_sections": []}
    for extra in extras:
        more = dict(extra(s, g.user))
        data["settings_sections"] += more.pop("settings_sections", [])
        data.update(more)
    return render_template("settings.html", errors=errors, form=request.form, zones=zone_choices(), **data), \
        400 if errors else 200


def close_sockets(user_id):
    """After someone's sessions end, close their open WebSockets too, so an old tab can't keep
    listening. (Every tab gets "bye" and reloads; this one simply lands on the new page.)"""
    hub = current_app.extensions.get("paperplane_hub")
    if hub is not None:
        hub.close_user(user_id)


def init_app(app):
    pass
