"""Accounts: sign up, log in, recover a forgotten password, log out.

Anyone can sign up with a username and a password. There is no email, so a
forgotten password is recovered with a one-time RECOVERY CODE, shown once at
sign-up (like Hire Wire). Using it gives a new code.

Passwords and recovery codes are stored only as hashes. The login cookie holds
the user's id and their session_version; load_user() checks both on every
request, so an old cookie (after a password change or "Log out other devices")
stops working straight away. The WebSocket handshake makes the same check
(live.py), and those changes also close the person's open sockets.
"""
import functools
import hmac
import re
import secrets
from datetime import timedelta

import click
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from flask.cli import with_appcontext
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, limits
from .dates import utcnow
from .models import User

bp = Blueprint("auth", __name__)

NAME_MAX_LENGTH = 80
PASSWORD_MIN_LENGTH = 8
USERNAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,19}")
USERNAME_RULE = "Use 3 to 20 small letters, numbers or _, starting with a letter."
# Names people would mistake for the site itself.
RESERVED_USERNAMES = {"admin", "administrator", "paperplane", "paper_plane", "support", "help", "system",
                      "demo", "root", "moderator", "staff", "official", "everyone", "here"}
# After 5 wrong passwords in a row, the account waits 15 minutes.
MAX_FAILED_LOGINS = 5
LOCK_TIME = timedelta(minutes=15)
# One address may try at most this many logins (or recoveries) in 15 minutes, and make
# this many accounts in an hour.
LOGINS_PER_IP = 30
SIGNUPS_PER_IP = 5
# Recovery codes: 12 letters and numbers that can't be mixed up (no 0/O, 1/I/L).
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

WRONG_LOGIN = "Wrong username or password."
WRONG_RECOVERY = "That username and recovery code don't match."
LOCKED = "Too many failed attempts. Please wait 15 minutes and try again."
TOO_MANY = "Too many tries from your network. Please wait 15 minutes and try again."


# ---------- Checking what people type ----------

def clean_username(text):
    return (text or "").strip().lstrip("@").lower()[:41]


def username_problem(s, username):
    if not username:
        return "Choose a username."
    if not USERNAME_PATTERN.fullmatch(username):
        return USERNAME_RULE
    if username in RESERVED_USERNAMES:
        return "That username is taken. Try another."
    if find_user(s, username) is not None:
        return "That username is taken. Try another."
    return None


def clean_name(text):
    return " ".join((text or "").split())


def name_problem(name, what="your name"):
    if not name:
        return f"Enter {what}."
    if len(name) > NAME_MAX_LENGTH:
        return f"Use at most {NAME_MAX_LENGTH} characters."
    return None


def password_problem(password, confirm):
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Use at least {PASSWORD_MIN_LENGTH} characters."
    if password != confirm:
        return "The two passwords are not the same."
    return None


def new_recovery_code():
    """XXXX-XXXX-XXXX: about 59 bits, plenty when tries are limited."""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def clean_code(text):
    """People copy codes with spaces, dashes and small letters: compare just the letters."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


# ---------- Users in the database ----------

def find_user(s, username):
    return s.scalar(select(User).where(User.username == clean_username(username)))


def create_user(s, username, name, password, **fields):
    """Add a user. Returns (user, recovery code). The caller commits."""
    code = new_recovery_code()
    user = User(username=username, name=name, password_hash=generate_password_hash(password),
                recovery_hash=generate_password_hash(clean_code(code)), **fields)
    s.add(user)
    s.flush()
    return user, code


def set_password(user, password):
    user.password_hash = generate_password_hash(password)
    user.failed_logins = 0
    user.locked_until = None
    user.session_version += 1  # every other device is logged out


def set_recovery_code(user):
    code = new_recovery_code()
    user.recovery_hash = generate_password_hash(clean_code(code))
    return code


def _failed(s, user, moment):
    user.failed_logins += 1
    if user.failed_logins >= MAX_FAILED_LOGINS:
        user.failed_logins, user.locked_until = 0, moment + LOCK_TIME
    s.commit()


def check_login(s, username, password, moment):
    """Return (user, None) if the password is right, else (None, message).
    The message never says whether the username exists."""
    if not limits.hit(s, f"login-ip:{limits.client_ip()}", LOGINS_PER_IP, 900, moment):
        s.commit()
        return None, TOO_MANY
    user = find_user(s, username)
    if user is None or user.is_demo:
        # Same work as a real check, so the answer takes as long either way.
        check_password_hash(_dummy_hash(), password or "")
        s.commit()
        return None, WRONG_LOGIN
    if user.locked_until and user.locked_until > moment:
        s.commit()
        return None, LOCKED
    if not check_password_hash(user.password_hash, password or ""):
        _failed(s, user, moment)
        return None, WRONG_LOGIN
    user.failed_logins, user.locked_until = 0, None
    s.commit()
    return user, None


def check_recovery(s, username, code, moment):
    """Like check_login, with the recovery code instead of the password."""
    if not limits.hit(s, f"login-ip:{limits.client_ip()}", LOGINS_PER_IP, 900, moment):
        s.commit()
        return None, TOO_MANY
    user = find_user(s, username)
    if user is None or user.is_demo or not user.recovery_hash:
        check_password_hash(_dummy_hash(), clean_code(code))
        s.commit()
        return None, WRONG_RECOVERY
    if user.locked_until and user.locked_until > moment:
        s.commit()
        return None, LOCKED
    if not check_password_hash(user.recovery_hash, clean_code(code)):
        _failed(s, user, moment)
        return None, WRONG_RECOVERY
    return user, None


@functools.cache
def _dummy_hash():
    return generate_password_hash(secrets.token_urlsafe(16))


def safe_next(target, default=None):
    """Only go back to a page on this site after logging in.

    "/chats/7" is fine. "https://evil.example" and "//evil.example" are not:
    a fake link could send people there after a real login. Browsers drop tabs
    and newlines and read a backslash as a slash, so those are out too.
    """
    if (target and target.startswith("/") and not target.startswith("//")
            and "\\" not in target and not any(ord(c) < 32 for c in target)):
        return target
    return default or url_for("chats.index")


def log_in(user):
    csrf = session.get("csrf")
    session.clear()  # a new session for the new user (old cookies can't be reused)
    if csrf and user.is_demo:
        session["csrf"] = csrf  # demo pages that are open keep working
    session.permanent = True  # stay logged in for PERMANENT_SESSION_LIFETIME
    session["user_id"] = user.id
    session["session_version"] = user.session_version


def user_from_session(s, data, moment):
    """The logged-in user for this cookie's data, or None. Pages and the WebSocket
    handshake both ask this, so they agree on who is logged in."""
    user_id = data.get("user_id")
    if user_id is None:
        return None
    user = s.get(User, user_id)
    if user is None or data.get("session_version") != user.session_version:
        return None
    if user.is_demo and (user.demo_team is None or user.demo_team.expires_at <= moment):
        return None
    return user


# ---------- Every request ----------

@bp.before_app_request
def load_user():
    g.user = None
    if session.get("user_id") is None:
        return
    user = user_from_session(db.session(), session, utcnow())
    if user is None:
        # Keep the CSRF token: only the login part of this cookie is out of date.
        session.pop("user_id", None)
        session.pop("session_version", None)
    else:
        g.user = user


def csrf_token():
    """A secret per session, put into every form as a hidden field.

    Another website could make your browser send a form to this site (your
    login cookie goes with it). It can't read this secret, so its form fails.
    (A WebSocket can't carry it the same way: live.py checks the Origin instead.)
    """
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@bp.before_app_request
def check_csrf():
    if request.method not in ("POST", "DELETE") or request.endpoint in current_app.config.get("CSRF_EXEMPT", ()):
        return
    sent = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    # compare_digest takes the same time for every wrong guess.
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, "This form has expired. Please go back, reload the page and try again.")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            # Only a GET can be repeated after logging in.
            next_page = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("auth.login", next=next_page))
        return view(*args, **kwargs)
    return wrapped


def not_for_demo(message="The demo can't change this."):
    if g.user is not None and g.user.is_demo:
        abort(403, message)


# ---------- Pages ----------

@bp.get("/")
def index():
    return redirect(url_for("chats.index") if g.user else url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("chats.index"))
    next_page = request.values.get("next", "")
    error = None
    username = ""
    if request.method == "POST":
        s = db.session()
        username = clean_username(request.form.get("username", ""))
        user, error = check_login(s, username, request.form.get("password", ""), utcnow())
        if user:
            log_in(user)
            return redirect(safe_next(next_page))
    status = 401 if error else 200
    return render_template("login.html", error=error, username=username, next=next_page), status


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user is not None:
        return redirect(url_for("chats.index"))
    errors, form = {}, {}
    if request.method == "POST":
        s = db.session()
        form = {"name": clean_name(request.form.get("name")), "username": clean_username(request.form.get("username"))}
        password = request.form.get("password", "")
        if problem := name_problem(form["name"]):
            errors["name"] = problem
        if problem := username_problem(s, form["username"]):
            errors["username"] = problem
        if problem := password_problem(password, request.form.get("confirm_password", "")):
            errors["password"] = problem
        if not errors:
            if not limits.hit(s, f"signup-ip:{limits.client_ip()}", SIGNUPS_PER_IP, 3600, utcnow()):
                s.commit()
                abort(429, "That's a lot of new accounts from your network. Please try again in an hour.")
            user, code = create_user(s, form["username"], form["name"], password)
            s.commit()
            log_in(user)
            # Shown once, straight away (not after a redirect, so the code is never in a cookie).
            return render_template("recovery_code.html", code=code, first_time=True)
    return render_template("signup.html", errors=errors, form=form), 400 if errors else 200


@bp.route("/recover", methods=["GET", "POST"])
def recover():
    if g.user is not None:
        return redirect(url_for("settings.page"))
    errors, username = {}, ""
    if request.method == "POST":
        s = db.session()
        username = clean_username(request.form.get("username", ""))
        password = request.form.get("password", "")
        if problem := password_problem(password, request.form.get("confirm_password", "")):
            errors["password"] = problem
        else:
            user, problem = check_recovery(s, username, request.form.get("code", ""), utcnow())
            if problem:
                errors["code"] = problem
            else:
                set_password(user, password)
                code = set_recovery_code(user)  # the old code is used up
                s.commit()
                log_in(user)
                return render_template("recovery_code.html", code=code, first_time=False)
    return render_template("recover.html", errors=errors, username=username), 400 if errors else 200


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have logged out.")
    return redirect(url_for("auth.login"))


# ---------- Command line ----------

@click.command("set-password")
@with_appcontext
@click.argument("username")
def set_password_command(username):
    """Change someone's password (and unlock the account). Also ends their sessions."""
    s = db.session()
    user = find_user(s, username)
    if user is None or user.is_demo:
        raise click.ClickException(f"There is no account called {username}.")
    password = click.prompt("New password", hide_input=True, confirmation_prompt=True)
    if len(password) < PASSWORD_MIN_LENGTH:
        raise click.ClickException(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    set_password(user, password)
    s.commit()
    click.echo("The password is changed.")


def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.cli.add_command(set_password_command)
