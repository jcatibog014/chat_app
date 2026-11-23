"""The story pages: the Stories page, the circles (a fragment for live refresh), posting,
the viewer, and the forms on it (react, reply, delete, mute). Pictures are served only to
people who may see the story (stories.load_visible), with the same safe headers as chat files."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import db, presence, stories, tools
from ..auth import login_required, safe_next
from ..dates import utcnow
from ..models import User
from ..problems import Invalid, Problem, TooMany
from .files import respond

bp = Blueprint("stories", __name__)


def tray_data(s):
    mine, others = stories.tray(s, g.user, utcnow())
    return {"my_ring": mine, "rings": others}


@bp.get("/stories")
@login_required
def index():
    s = db.session()
    moment = utcnow()
    mine, others = stories.tray(s, g.user, moment)
    my_stories = mine.stories if mine else []
    return render_template("stories.html", my_ring=mine, rings=others, my_stories=my_stories,
                           stats=stories.stats_for(s, [story.id for story in my_stories]),
                           muted_count=len(stories.muted_ids(s, g.user.id)))


@bp.get("/stories/tray")
@login_required
def tray():
    """The circles alone, for a page to swap in when a story is posted or deleted (stories.js)."""
    s = db.session()
    return render_template("_story_tray_live.html", **tray_data(s))


@bp.route("/stories/new", methods=["GET", "POST"])
@login_required
def new():
    s = db.session()
    error = None
    if request.method == "POST":
        upload = request.files.get("photo")
        kind = request.form.get("kind", "text")
        try:
            stories.post(s, g.user, kind, request.form.get("text"), request.form.get("background"),
                         request.form.get("audience", "everyone"), utcnow(),
                         upload_name=upload.filename if upload else None,
                         raw=upload.read() if upload and upload.filename else None)
        except (Invalid, TooMany) as problem:
            s.rollback()
            error = problem.message
        else:
            s.commit()
            flash("Your story is up. It disappears in 24 hours.")
            return redirect(url_for("stories.view", person_id=g.user.id))
    return render_template("story_new.html", error=error, form=request.form,
                           close_count=len(stories.close_friend_ids(s, g.user.id))), 400 if error else 200


def sequence(s, viewer, moment):
    """The people whose stories the viewer goes through, in the circles' order (me first)."""
    mine, others = stories.tray(s, viewer, moment)
    return ([mine] if mine else []) + others


@bp.get("/stories/<int:person_id>")
@login_required
def view(person_id):
    s = db.session()
    moment = utcnow()
    person = s.get(User, person_id)
    found = stories.live_stories_of(s, person_id, g.user, moment) if person else []
    if not found:
        abort(404)  # gone, never there, or not yours to see: all the same
    wanted = request.args.get("story", type=int)
    index = next((i for i, story in enumerate(found) if story.id == wanted), None)
    if index is None:
        # Start at the first story not seen yet (or the first one).
        seen = stories.seen_ids(s, g.user.id, [item.id for item in found])
        index = 0 if person_id == g.user.id else next((i for i, item in enumerate(found) if item.id not in seen), 0)
    story = found[index]
    stories.mark_seen(s, g.user, story, moment)
    s.commit()
    # Where Next and Previous go: the next story, or the next (or previous) person's.
    order = sequence(s, g.user, moment)
    people = [ring.person.id for ring in order]
    here = people.index(person_id) if person_id in people else -1
    if index + 1 < len(found):
        next_url = url_for("stories.view", person_id=person_id, story=found[index + 1].id)
    elif 0 <= here < len(people) - 1:
        next_url = url_for("stories.view", person_id=people[here + 1])
    else:
        next_url = url_for("stories.index")
    if index > 0:
        prev_url = url_for("stories.view", person_id=person_id, story=found[index - 1].id)
    elif here > 0:
        prev_url = url_for("stories.view", person_id=people[here - 1])
    else:
        prev_url = None
    mine = person_id == g.user.id
    return render_template(
        "story_view.html", person=person, story=story, stories=found, index=index, next_url=next_url,
        prev_url=prev_url, mine=mine,
        viewers=stories.viewers_of(s, story) if mine else [],
        my_reaction=None if mine else stories.my_reaction(s, g.user, story),
        story_muted=person_id in stories.muted_ids(s, g.user.id))


def back_to_story(story_id, person_id):
    return redirect(safe_next(request.form.get("next"), url_for("stories.view", person_id=person_id, story=story_id)))


@bp.post("/stories/<int:story_id>/react")
@login_required
def react(story_id):
    s = db.session()
    story = stories.load_visible(s, g.user, story_id, utcnow())
    try:
        stories.react(s, g.user, story_id, request.form.get("emoji", ""), utcnow())
    except Invalid as problem:
        s.rollback()
        flash(problem.message, "error")
        return back_to_story(story_id, story.user_id)
    s.commit()
    return back_to_story(story_id, story.user_id)


@bp.post("/stories/<int:story_id>/reply")
@login_required
def reply(story_id):
    s = db.session()
    story = stories.load_visible(s, g.user, story_id, utcnow())
    try:
        message = stories.reply(s, g.user, story_id, request.form.get("body"), utcnow())
    except Problem as problem:
        s.rollback()
        if problem.status == 404:
            raise
        flash(problem.message, "error")
        return back_to_story(story_id, story.user_id)
    s.commit()
    flash(f"Sent to {story.user.first_name}, in your chat.")
    return redirect(url_for("chats.show", chat_id=message.conversation_id, _anchor=f"m-{message.id}"))


@bp.post("/stories/<int:story_id>/delete")
@login_required
def delete(story_id):
    s = db.session()
    stories.delete_story(s, g.user, story_id)
    s.commit()
    flash("Your story is deleted.")
    return redirect(url_for("stories.index"))


@bp.post("/stories/people/<int:person_id>/mute")
@login_required
def mute(person_id):
    s = db.session()
    person = s.get(User, person_id)
    stories.mute(s, g.user, person_id)
    s.commit()
    flash(f"You won't see {person.first_name}'s stories. Unmute in Settings, Privacy.")
    return redirect(url_for("stories.index"))


@bp.post("/stories/people/<int:person_id>/unmute")
@login_required
def unmute(person_id):
    s = db.session()
    stories.unmute(s, g.user, person_id)
    s.commit()
    person = s.get(User, person_id)
    flash(f"You'll see {person.first_name if person else 'their'} stories again.")
    return redirect(safe_next(request.form.get("next"), url_for("settings.page", section="privacy")))


def picture(story_id, column):
    s = db.session()
    story = stories.load_visible(s, g.user, story_id, utcnow())
    if story.kind != "photo":
        abort(404)
    s.refresh(story, attribute_names=[column])
    data = getattr(story, column)
    if data is None:
        abort(404)
    return respond(data, "image/webp", f"story-{story.id}.webp", True)


@bp.get("/stories/<int:story_id>/image")
@login_required
def image(story_id):
    return picture(story_id, "image")


@bp.get("/stories/<int:story_id>/thumb")
@login_required
def thumb(story_id):
    return picture(story_id, "thumb")


# ---------- Settings, Privacy: Close Friends and Muted Stories ----------

def save_close_friends(s, user, form):
    ids = {int(value) for value in form.getlist("friend") if value.isdigit()}
    chosen = stories.set_close_friends(s, user, ids)
    return {}, (f"Your Close Friends list has {len(chosen)} {'person' if len(chosen) == 1 else 'people'}."
                if chosen else "Your Close Friends list is empty.")


def privacy_sections(s, user):
    from sqlalchemy import select
    contact_ids = presence.contacts(s, user.id)
    people = s.scalars(select(User).where(User.id.in_(contact_ids)).order_by(User.name)).all() if contact_ids else []
    return {"settings_sections": ["_settings_close_friends.html", "_settings_story_mutes.html"],
            "story_contacts": people, "close_ids": stories.close_friend_ids(s, user.id),
            "story_muted_people": stories.muted_people(s, user)}


def init_app(app):
    from . import settings
    settings.actions["close_friends"] = save_close_friends
    settings.ACTION_PAGES["close_friends"] = "privacy"
    for name in ("_settings_close_friends.html", "_settings_story_mutes.html"):
        settings.SECTION_PAGES[name] = "privacy"
        if name not in settings.SECTION_ORDER:
            settings.SECTION_ORDER.append(name)
    if privacy_sections not in settings.extras:
        settings.extras.append(privacy_sections)
    app.jinja_env.globals.update(story_tray=lambda: tray_data(db.session()) if g.get("user") else {},
                                 REACTION_EMOJI=tools.EMOJI)
