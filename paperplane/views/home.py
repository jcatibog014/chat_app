"""The Chats page. Stage 2 fills it with real chats."""
from flask import Blueprint, render_template

from ..auth import login_required

bp = Blueprint("chats", __name__)


@bp.get("/chats")
@login_required
def index():
    return render_template("chats.html")
