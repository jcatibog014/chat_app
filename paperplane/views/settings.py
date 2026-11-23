"""Settings, in pages: Account, Appearance, Notifications, Privacy, Devices.

There is ONE way in (Settings in the profile menu) and a small menu inside
Settings to move between its pages (settings.html). /settings opens Account.
Every form posts to the page it is on and comes back to it.

Other parts of the app add their own sections (who sees you online, blocked
people, notifications): `extras` give the templates and their data, and
SECTION_PAGES says which page each one belongs on.
"""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .. import db
from ..auth import (clean_name, login_required, name_problem, not_for_demo, password_problem, set_password,
                    set_recovery_code)
from ..dates import is_zone, zone_choices

bp = Blueprint("settings", __name__)

# The pages, in the order of the menu: (address, title, icon, a few words about it).
PAGES = [
    ("account", "Account", "person", "Your name, password and time zone"),
    ("appearance", "Appearance", "palette", "Colour, and light or dark"),
    ("notifications", "Notifications", "bell", "Notifications on your devices"),
    ("privacy", "Privacy", "shield", "Who sees you online, blocked people"),
    ("devices", "Devices", "devices", "Install the app, log out"),
]
PAGE_NAMES = [name for name, *_rest in PAGES]

# Sections other modules add: action name -> function(s, user, form) -> (errors, message)
actions = {}
# Extra things the pages show: function(s, user) -> dict (settings_sections: templates to include)
extras = []
SECTION_ORDER = ["_settings_notifications.html", "_settings_presence.html", "_settings_blocked.html"]
SECTION_PAGES = {"_settings_notifications.html": "notifications", "_settings_presence.html": "privacy",
                 "_settings_blocked.html": "privacy"}
# Which page an action comes back to.
ACTION_PAGES = {"name": "account", "password": "account", "recovery": "account", "time_zone": "account",
                "accent": "appearance", "show_presence": "privacy", "logout_others": "devices"}

ACCENTS = [
    ("pink", "Pink", "The logo's colour"),
    ("purple", "Purple", None),
    ("red", "Red", None),
    ("blue", "Blue", "A clear sky"),
]
ACCENT_NAMES = [name for name, *_rest in ACCENTS]
# The browser's bar colour on phones, per colour (the sidebar's top colour).
THEME_COLOURS = {"pink": "#c2185b", "purple": "#7b2cbf", "red": "#c62828", "blue": "#2b6cb0"}


def accent_of(user):
    return user.accent if user is not None and user.accent in ACCENT_NAMES else "pink"


def end_other_sessions(user):
    """Every other device's cookie stops working; this one is brought up to date.
    Open WebSockets made with an old cookie are closed too (hub.close_user)."""
    user.session_version += 1
    session["session_version"] = user.session_version


def save_time_zone(s, user, form):
    zone_name = form.get("time_zone", "").strip()
    if not is_zone(zone_name):
        return {"time_zone": "Choose a time zone from the list."}, None
    user.time_zone = zone_name
    return {}, "Your time zone is saved."


def save_accent(s, user, form):
    """The app's colour. Allowed in the demo too: it changes nothing but the look."""
    accent = form.get("accent", "")
    if accent not in ACCENT_NAMES:
        return {"accent": "Choose one of the colours."}, None
    user.accent = accent
    return {}, f"Paper Plane is {dict((n, t) for n, t, _ in ACCENTS)[accent].lower()} now."


actions["time_zone"] = save_time_zone
actions["accent"] = save_accent


def back(section):
    return redirect(url_for("settings.page", section=section))


@bp.route("/settings", methods=["GET", "POST"], defaults={"section": "account"})
@bp.route("/settings/<section>", methods=["GET", "POST"])
@login_required
def page(section):
    if section not in PAGE_NAMES:
        abort(404)
    s = db.session()
    errors = {}
    if request.method == "POST":
        action = request.form.get("action")
        if ACTION_PAGES.get(action, section) != section and action in ACTION_PAGES:
            section = ACTION_PAGES[action]
        if action == "name":
            not_for_demo("The demo can't change its account.")
            name = clean_name(request.form.get("user_name"))
            if problem := name_problem(name):
                errors["user_name"] = problem
            else:
                g.user.name = name
                s.commit()
                flash("Your name has been changed.")
                return back("account")
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
                return back("account")
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
            return back("devices")
        elif action in actions:
            errors, message = actions[action](s, g.user, request.form)
            if not errors:
                s.commit()
                flash(message)
                return back(ACTION_PAGES.get(action, section))
            s.rollback()
        else:
            abort(400, "Choose something to change.")
    data = {"settings_sections": []}
    for extra in extras:
        more = dict(extra(s, g.user))
        data["settings_sections"] += [name for name in more.pop("settings_sections", [])
                                      if SECTION_PAGES.get(name) == section]
        data.update(more)
    # A fixed order, whichever part of the app added its section first.
    data["settings_sections"].sort(key=lambda name: SECTION_ORDER.index(name) if name in SECTION_ORDER else 99)
    title = dict((name, title) for name, title, *_ in PAGES)[section]
    return render_template("settings.html", section=section, section_title=title, pages=PAGES, accents=ACCENTS,
                           errors=errors, form=request.form, zones=zone_choices(), **data), 400 if errors else 200


def close_sockets(user_id):
    """After someone's sessions end, close their open WebSockets too, so an old tab can't keep
    listening. (Every tab gets "bye" and reloads; this one simply lands on the new page.)"""
    hub = current_app.extensions.get("paperplane_hub")
    if hub is not None:
        hub.close_user(user_id)


def init_app(app):
    from flask import g as app_g
    app.jinja_env.globals.update(
        accent=lambda: accent_of(app_g.get("user")),
        theme_colour=lambda: THEME_COLOURS[accent_of(app_g.get("user"))])
