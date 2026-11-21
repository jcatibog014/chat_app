"""Serving pictures and files: only to people who may see the message they belong to."""
from urllib.parse import quote

from flask import Blueprint, Response, abort, g

from .. import db, files
from ..auth import login_required

bp = Blueprint("files", __name__)


def respond(data, content_type, filename, inline):
    response = Response(data, mimetype=content_type)
    disposition = "inline" if inline else "attachment"
    # filename* carries any name safely (RFC 5987); the plain one is the ASCII fallback.
    ascii_name = filename.encode("ascii", "ignore").decode() or "file"
    response.headers["Content-Disposition"] = f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    # A file is never a page of this site: no scripts, no forms, nothing, even if a browser opened it.
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    # The browser may keep it (it never changes), but only for this person, never a shared cache.
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


def load(attachment_id, *columns):
    s = db.session()
    attachment = files.load_for(s, g.user, attachment_id)
    s.refresh(attachment, attribute_names=list(columns))
    return attachment


@bp.get("/files/<int:attachment_id>")
@login_required
def show(attachment_id):
    attachment = load(attachment_id, "data")
    # Pictures are WebP files we made ourselves, so they may show in the browser; anything
    # else is always a download.
    return respond(attachment.data, attachment.content_type, attachment.filename, attachment.kind == "image")


@bp.get("/files/<int:attachment_id>/thumb")
@login_required
def thumb(attachment_id):
    attachment = load(attachment_id, "thumb")
    if attachment.kind != "image" or attachment.thumb is None:
        abort(404)
    return respond(attachment.thumb, "image/webp", attachment.filename, True)


def init_app(app):
    app.jinja_env.filters["human_size"] = files.human_size
