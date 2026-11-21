"""Try Demo: one click, and the visitor has their own sample team for 24 hours.

Friend links: the visitor can hand a teammate to a real friend (or to
themselves in a private window, or on their phone), to see two real people
chatting live. A link is a signed token (tokens.py) naming the team and the
teammate; it works until the demo ends. Opening it only SHOWS a page (chat
apps open links to draw previews); the button on that page logs in.
"""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, url_for
from sqlalchemy import select

from .. import db, limits, seed, tokens
from ..auth import log_in, login_required
from ..dates import utcnow
from ..models import User

bp = Blueprint("demo", __name__)
DEMOS_PER_IP = 10  # an hour


@bp.post("/demo")
def start():
    """A fresh sample team for this visitor alone. Nobody sees anyone else's demo."""
    if g.user is not None and g.user.is_demo:
        return redirect(url_for("chats.index"))  # this browser already has one
    s = db.session()
    moment = utcnow()
    seed.purge(s, moment)
    # Not while developing on your own PC (--debug): screenshots start many demos in a row.
    if not current_app.debug and not limits.hit(s, f"demo-ip:{limits.client_ip()}", DEMOS_PER_IP, 3600, moment):
        s.commit()
        flash("That's a lot of demos from your network. Please try again in an hour.", "error")
        return redirect(url_for("auth.login"))
    if seed.count(s) >= seed.MAX_DEMOS:
        s.commit()
        flash("The demo is very busy right now. Please try again in a little while.", "error")
        return redirect(url_for("auth.login"))
    me = seed.build(s, moment)
    s.commit()
    log_in(me)
    # Shown once, as a toast (_toasts.html); the small Demo tag in the menu stays.
    flash(f"You are Mika, a designer at {seed.TEAM_NAME}. Your teammates are made up, but they answer. "
          "Everything is deleted after 24 hours.", "demo")
    return redirect(url_for("chats.index"))


def friend_link(user):
    return url_for("demo.join", token=tokens.make("friend", user.demo_team_id, user.id), _external=True)


@bp.get("/demo/friends")
@login_required
def friends():
    """The visitor's links: one per teammate."""
    if not g.user.is_demo or g.user.is_bot:
        abort(404)
    s = db.session()
    mates = s.scalars(select(User).where(User.demo_team_id == g.user.demo_team_id, User.is_bot.is_(True))
                      .order_by(User.name)).all()
    return render_template("demo_friends.html", mates=[(mate, friend_link(mate)) for mate in mates])


def teammate_from(token):
    """The teammate a friend link names, while its demo lasts; else 404."""
    parts = tokens.read_ints("friend", token, 2)
    if parts is None:
        abort(404)
    team_id, user_id = parts
    user = db.session().get(User, user_id)
    if user is None or user.demo_team_id != team_id or not user.is_bot or user.demo_team.expires_at <= utcnow():
        abort(404)
    return user


@bp.get("/demo/join/<token>")
def join(token):
    mate = teammate_from(token)
    return render_template("demo_join.html", mate=mate, token=token)


@bp.post("/demo/join/<token>")
def join_now(token):
    mate = teammate_from(token)
    log_in(mate)
    flash(f"You are {mate.first_name} in a demo of {seed.TEAM_NAME}. Whatever you send arrives live for the others.",
          "demo")
    return redirect(url_for("chats.index"))
