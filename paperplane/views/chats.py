"""The chat pages: the list of chats, one chat, sending by form, and New Chat.

These pages work without JavaScript: sending is a normal form POST, and the
page is drawn again. With JavaScript, socket.js and chat.js send and receive
over the WebSocket instead, and these pages are only loaded once.
"""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import access, chats, db, tools
from ..auth import login_required
from ..dates import utcnow
from ..models import User
from ..problems import Forbidden, Invalid, NotFound, Problem

bp = Blueprint("chats", __name__)


def page_data(s, open_id=None):
    rows = chats.chat_list(s, g.user)
    g.total_unread = chats.total_unread(s, g.user, rows)
    return {"rows": rows, "open_id": open_id}


def unread_total():
    """The number beside Chats in the menu, on every page (worked out once per page)."""
    if g.get("user") is None:
        return 0
    if "total_unread" not in g:
        g.total_unread = chats.total_unread(db.session(), g.user)
    return g.total_unread


@bp.get("/chats")
@login_required
def index():
    s = db.session()
    return render_template("chats.html", **page_data(s))


@bp.get("/chats/<int:chat_id>")
@login_required
def show(chat_id):
    s = db.session()
    conversation, membership = access.load(s, g.user, chat_id)
    before = request.args.get("before", type=int)
    messages, more = chats.messages_page(s, conversation, membership, before_seq=before)
    if before is None:
        # Opening a chat reads it. (A GET that changes something, but only "how far I've
        # read", which can't hurt to repeat.)
        chats.mark_read(s, g.user, conversation.id)
        s.commit()
    members = chats.members(s, conversation)
    tools.prepare(s, messages)
    readers = tools.readers(s, conversation)
    last = messages[-1] if messages and before is None else None
    replying = None
    if request.args.get("reply", type=int):
        try:
            target = tools.reply_target(s, g.user, conversation, membership, request.args.get("reply", type=int))
            tools.prepare(s, [target])
            replying = target
        except Problem:
            replying = None
    flags = chats.speaking_flags(s, conversation, membership, g.user, utcnow())
    return render_template(
        "chats.html", **page_data(s, conversation.id), cant_send_reason=cant_send_reason(s, conversation, membership, flags),
        conversation=conversation, membership=membership, messages=messages, more=more, before=before,
        members=members, title=chats.title_for(s, conversation, g.user.id),
        other=chats.other_person(s, conversation, g.user.id) if conversation.is_dm else None,
        client_id=chats.new_client_id(), replying=replying,
        seen=tools.seen_words(conversation, readers, last, g.user.id),
        readers={str(user_id): {"name": name, "seq": seq} for user_id, (name, seq) in readers.items()},
        can=lambda action, target=None: access.can(action, conversation, membership, target,
                                                   **(flags if action in access.SPEAKING else {})))


def cant_send_reason(s, conversation, membership, flags):
    """What the closed composer says, when you can't send here right now."""
    from .. import blocks
    from ..formatting import clock, short_when
    if flags.get("muted"):
        until = membership.muted_until
        when = clock(until) if short_when(until) == clock(until) else f"{short_when(until)}, {clock(until)}"
        return f"An admin muted you in this group until {when}. You can still read."
    if flags.get("blocked"):
        other = chats.other_person(s, conversation, g.user.id)
        if other is not None and blocks.i_blocked(s, g.user.id, other.id):
            return f"You blocked {other.first_name}. Unblock them in Chat Info to send messages."
        return "You can't reply to this chat."
    return "You can't send messages here right now."


@bp.post("/chats/<int:chat_id>/send")
@login_required
def send(chat_id):
    s = db.session()
    try:
        chats.send_message(s, g.user, chat_id, request.form.get("body"), request.form.get("client_id"), utcnow(),
                           reply_to_id=request.form.get("reply_to", type=int))
    except Invalid as problem:
        s.rollback()
        flash(problem.message, "error")
        return redirect(url_for("chats.show", chat_id=chat_id))
    s.commit()
    return redirect(url_for("chats.show", chat_id=chat_id, _anchor="latest"))


@bp.get("/chats/new")
@login_required
def new():
    s = db.session()
    return render_template("new_chat.html", **page_data(s), people=chats.people_i_know(s, g.user), errors={}, form={})


def people_from_form(s, form):
    """The people picked for a group: ticked boxes plus typed usernames."""
    people, unknown = {}, []
    for raw in form.getlist("person"):
        if raw.isdigit():
            person = s.get(User, int(raw))
            if person is not None and person.id != g.user.id and access.same_world(g.user, person):
                people[person.id] = person
    for name in form.get("usernames", "").replace(",", " ").split():
        person = chats.find_person(s, g.user, name)
        if person is None:
            unknown.append(name.lstrip("@"))
        else:
            people[person.id] = person
    return list(people.values()), unknown


@bp.post("/chats/new/dm")
@login_required
def new_dm():
    s = db.session()
    target = request.form.get("person") or request.form.get("username", "")
    person = s.get(User, int(target)) if target.isdigit() else chats.find_person(s, g.user, target)
    try:
        conversation, _made = chats.start_dm(s, g.user, person, utcnow())
    except Problem as problem:
        s.rollback()
        return render_template("new_chat.html", **page_data(s), people=chats.people_i_know(s, g.user),
                               errors={"username": problem.message}, form=request.form), problem.status
    s.commit()
    return redirect(url_for("chats.show", chat_id=conversation.id))


@bp.post("/chats/new/group")
@login_required
def new_group():
    s = db.session()
    people, unknown = people_from_form(s, request.form)
    errors = {}
    if unknown:
        errors["usernames"] = f"We couldn't find: {', '.join('@' + name for name in unknown)}."
    elif not people:
        errors["usernames"] = "Pick at least one person to add."
    if not errors:
        try:
            conversation = chats.create_group(s, g.user, request.form.get("title"), people, utcnow())
        except Invalid as problem:
            errors["title"] = problem.message
        except (NotFound, Forbidden) as problem:
            errors["usernames"] = problem.message
    if errors:
        s.rollback()
        return render_template("new_chat.html", **page_data(s), people=chats.people_i_know(s, g.user),
                               errors=errors, form=request.form, open_group=True), 400
    s.commit()
    flash("Your group is ready.")
    return redirect(url_for("chats.show", chat_id=conversation.id))


def init_app(app):
    app.jinja_env.globals.update(preview=chats.preview_of, thread=chats.thread, unread_total=unread_total)

    @app.errorhandler(Problem)
    def problem_page(problem):
        return render_template("error.html", code=problem.status, message=problem.message), problem.status
