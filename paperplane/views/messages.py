"""Message tools as plain forms (without JavaScript): react, delete, edit.
With JavaScript, chat.js does the same over the WebSocket (live_events.py)."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import db, tools
from ..auth import login_required
from ..dates import utcnow
from ..problems import Problem

bp = Blueprint("messages", __name__)


def back_to(message):
    return redirect(url_for("chats.show", chat_id=message.conversation_id, _anchor=f"m-{message.id}"))


@bp.post("/messages/<int:message_id>/react")
@login_required
def react(message_id):
    s = db.session()
    message, _conversation, _membership = tools.load_message(s, g.user, message_id)
    tools.react(s, g.user, message_id, request.form.get("emoji", ""), utcnow())
    s.commit()
    return back_to(message)


@bp.post("/messages/<int:message_id>/delete")
@login_required
def delete(message_id):
    s = db.session()
    message = tools.delete_message(s, g.user, message_id, utcnow())
    s.commit()
    flash("The message was deleted for everyone.")
    return back_to(message)


@bp.route("/messages/<int:message_id>/edit", methods=["GET", "POST"])
@login_required
def edit(message_id):
    s = db.session()
    message, conversation, _membership = tools.load_message(s, g.user, message_id)
    if message.sender_id != g.user.id or message.deleted_at is not None:
        from ..problems import Forbidden
        raise Forbidden("You can only edit your own messages.")
    error = None
    if request.method == "POST":
        try:
            tools.edit_message(s, g.user, message_id, request.form.get("body"), utcnow())
            s.commit()
            return back_to(message)
        except Problem as problem:
            s.rollback()
            error = problem.message
    return render_template("edit_message.html", message=message, conversation=conversation, error=error,
                           body=request.form.get("body", message.body)), 400 if error else 200


def init_app(app):
    app.jinja_env.globals.update(REACTION_EMOJI=tools.EMOJI, REACTION_NAMES=tools.EMOJI_NAMES)
