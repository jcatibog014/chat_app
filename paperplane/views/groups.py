"""The chat info page (a group's people, roles, invite links and settings; a DM's Block
button), its forms, invite links (/join/<code>) and blocking. Every form posts here and
comes back to the info page; the changes go out live through the outbox (groups.py)."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import access, blocks, chats, db, groups
from ..auth import login_required, safe_next
from ..dates import utcnow
from ..models import User
from ..problems import Problem

bp = Blueprint("groups", __name__)


def back(chat_id, anchor=None):
    return redirect(url_for("groups.info", chat_id=chat_id, _anchor=anchor))


@bp.get("/chats/<int:chat_id>/info")
@login_required
def info(chat_id):
    s = db.session()
    conversation, membership = access.load(s, g.user, chat_id)
    moment = utcnow()
    if conversation.is_dm:
        other = chats.other_person(s, conversation, g.user.id)
        return render_template("chat_info.html", conversation=conversation, membership=membership, other=other,
                               i_blocked=blocks.i_blocked(s, g.user.id, other.id),
                               they_blocked=blocks.i_blocked(s, other.id, g.user.id))
    members = chats.members(s, conversation)
    members.sort(key=lambda m: (-access.RANK[m.role], m.user.name.lower()))
    in_group = {m.user_id for m in members}
    can = lambda action, target=None: access.can(action, conversation, membership, target)  # noqa: E731
    return render_template(
        "group_info.html", conversation=conversation, membership=membership, members=members, can=can,
        moment=moment, is_muted=groups.is_muted, role_names=access.ROLE_NAMES,
        invites=groups.active_invites(s, conversation, moment) if can("invite_links") else [],
        addable=[person for person in chats.people_i_know(s, g.user) if person.id not in in_group],
        errors={}, form={})


def act(chat_id, work, message, anchor=None):
    """Run a group action, commit, and come back to the info page with a toast (or the problem)."""
    s = db.session()
    try:
        work(s)
    except Problem as problem:
        s.rollback()
        if problem.status == 404:
            raise
        flash(problem.message, "error")
        return back(chat_id, anchor)
    s.commit()
    if message:
        flash(message)
    return back(chat_id, anchor)


@bp.post("/chats/<int:chat_id>/rename")
@login_required
def rename(chat_id):
    return act(chat_id, lambda s: groups.rename(s, g.user, chat_id, request.form.get("title"), utcnow()),
               "The group's name is changed.")


@bp.post("/chats/<int:chat_id>/history")
@login_required
def history(chat_id):
    visible = request.form.get("history_visible") == "1"
    return act(chat_id, lambda s: groups.set_history(s, g.user, chat_id, visible, utcnow()),
               "New members will see earlier messages." if visible else "New members will see only what comes after they join.",
               "settings")


@bp.post("/chats/<int:chat_id>/add")
@login_required
def add(chat_id):
    from .chats import people_from_form
    s = db.session()
    people, unknown = people_from_form(s, request.form)
    if unknown:
        flash(f"We couldn't find: {', '.join('@' + name for name in unknown)}.", "error")
        return back(chat_id, "add")
    if not people:
        flash("Pick someone to add.", "error")
        return back(chat_id, "add")
    added = []
    return act(chat_id, lambda s: added.extend(groups.add_people(s, g.user, chat_id, people, utcnow())),
               "Added to the group.", "people")


@bp.post("/chats/<int:chat_id>/members/<int:user_id>/remove")
@login_required
def remove(chat_id, user_id):
    return act(chat_id, lambda s: groups.remove_person(s, g.user, chat_id, user_id, utcnow()),
               "They're out of the group.", "people")


@bp.post("/chats/<int:chat_id>/members/<int:user_id>/role")
@login_required
def role(chat_id, user_id):
    return act(chat_id, lambda s: groups.set_role(s, g.user, chat_id, user_id, request.form.get("role"), utcnow()),
               "Their role is changed.", "people")


@bp.post("/chats/<int:chat_id>/members/<int:user_id>/owner")
@login_required
def owner(chat_id, user_id):
    return act(chat_id, lambda s: groups.hand_over(s, g.user, chat_id, user_id, utcnow()),
               "They own the group now. You are an admin.", "people")


@bp.post("/chats/<int:chat_id>/members/<int:user_id>/mute")
@login_required
def mute(chat_id, user_id):
    choice = request.form.get("minutes", "")
    return act(chat_id, lambda s: groups.mute(s, g.user, chat_id, user_id, choice, utcnow()),
               "They can send messages again." if choice == "off" else "They're muted.", "people")


@bp.post("/chats/<int:chat_id>/invites")
@login_required
def create_invite(chat_id):
    return act(chat_id, lambda s: groups.create_invite(s, g.user, chat_id, request.form.get("expiry", ""),
                                                        request.form.get("uses", ""), utcnow()),
               "Your invite link is ready. Copy it and send it to people.", "invites")


@bp.post("/chats/<int:chat_id>/invites/<int:invite_id>/revoke")
@login_required
def revoke_invite(chat_id, invite_id):
    return act(chat_id, lambda s: groups.revoke_invite(s, g.user, chat_id, invite_id, utcnow()),
               "That invite link no longer works.", "invites")


@bp.post("/chats/<int:chat_id>/leave")
@login_required
def leave(chat_id):
    s = db.session()
    groups.leave(s, g.user, chat_id, utcnow())
    s.commit()
    flash("You left the group.")
    return redirect(url_for("chats.index"))


@bp.post("/chats/<int:chat_id>/delete")
@login_required
def delete(chat_id):
    s = db.session()
    groups.delete_group(s, g.user, chat_id, utcnow())
    s.commit()
    flash("The group is deleted.")
    return redirect(url_for("chats.index"))


# ---------- Invite links: /join/<code> ----------

@bp.get("/join/<code>")
@login_required
def join_page(code):
    s = db.session()
    link = groups.find_invite(s, code)
    problem = groups.invite_problem(link, utcnow()) if link else "We couldn't find that invite link."
    if problem is None and link.conversation.demo_team_id != g.user.demo_team_id:
        problem = "We couldn't find that invite link."
    if problem:
        return render_template("error.html", code=404, message=problem), 404
    return render_template("join_group.html", link=link, conversation=link.conversation,
                           count=groups.member_count(s, link.conversation))


@bp.post("/join/<code>")
@login_required
def join(code):
    s = db.session()
    conversation = groups.join(s, g.user, code, utcnow())
    s.commit()
    return redirect(url_for("chats.show", chat_id=conversation.id))


# ---------- Blocking ----------

@bp.post("/people/<int:user_id>/block")
@login_required
def block(user_id):
    s = db.session()
    other = s.get(User, user_id)
    blocks.block(s, g.user, other, utcnow())
    s.commit()
    flash(f"You blocked {other.name}. Neither of you can message the other now.")
    return redirect(safe_next(request.form.get("next"), url_for("settings.page", _anchor="blocked")))


@bp.post("/people/<int:user_id>/unblock")
@login_required
def unblock(user_id):
    s = db.session()
    other = s.get(User, user_id)
    if other is None:
        abort(404)
    blocks.unblock(s, g.user, other, utcnow())
    s.commit()
    flash(f"You unblocked {other.name}.")
    return redirect(safe_next(request.form.get("next"), url_for("settings.page", _anchor="blocked")))


def blocked_section(s, user):
    return {"settings_sections": ["_settings_blocked.html"], "blocked_people": blocks.blocked_people(s, user)}


def init_app(app):
    from . import settings
    if blocked_section not in settings.extras:
        settings.extras.append(blocked_section)
    app.jinja_env.globals.update(MUTE_CHOICES=groups.MUTE_CHOICES)
