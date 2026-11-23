"""Pictures and files sent in a chat.

Big things don't travel over the WebSocket: a file goes up with a normal HTTP
form (POST /chats/<id>/send, multipart), the message that points to it is
saved in the same transaction, and the WebSocket only carries the news
("message.new", with a link to the file). WebSocket frames stay small (16 KB).

PICTURES are opened with Pillow and saved AGAIN as WebP, by us:
- turned the right way up (the EXIF "orientation"), then the EXIF dropped:
  phone photos carry the GPS position of where they were taken;
- at most 1600 px on the long side, plus a 480 px thumbnail for the chat;
- anything that isn't really a picture fails to open and is refused, and a
  "decompression bomb" (a tiny file that opens into a gigantic picture) is
  refused by the pixel limit.
Because the bytes we serve are ones WE made, a picture can't smuggle in
something else (an HTML page pretending to be a PNG).

OTHER FILES (PDF, text, Office, zip) are checked by their name and kept as
they are, and are always DOWNLOADED (Content-Disposition: attachment), never
shown in the browser, with nosniff and a sandbox CSP, so a file can't run
as a page of this site.

WHO may fetch a file: only someone who may see the message it belongs to
(access.load, and the group's history rule). A deleted message takes its
files with it. Each person may keep 50 MB of files (a demo: 10 MB in all).
"""
import io
import mimetypes
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import delete, func, select
from werkzeug.utils import secure_filename

from . import access, chats
from .models import Attachment, Message, User
from .problems import Invalid, NotFound, TooMany

MAX_FILE_BYTES = 5 * 1000 * 1000
MAX_PER_PERSON = 50 * 1000 * 1000
MAX_PER_DEMO = 10 * 1000 * 1000
MAX_PIXELS = 40_000_000
IMAGE_SIDE = 1600
THUMB_SIDE = 480
IMAGE_TYPES = {"JPEG", "PNG", "GIF", "WEBP"}
FILE_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain",
    "csv": "text/csv",
    "md": "text/markdown",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
}
ALLOWED_WORDS = "pictures (JPEG, PNG, GIF, WebP), PDF, text, CSV, Word, Excel, PowerPoint and zip files"

Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow refuses anything bigger (a decompression bomb)


@dataclass
class Prepared:
    """A checked upload, ready to save with its message."""
    kind: str
    filename: str
    content_type: str
    data: bytes
    thumb: bytes | None = None
    width: int | None = None
    height: int | None = None


def clean_filename(name, fallback="file"):
    """A safe name for downloads: no folders, no strange characters, not too long."""
    name = secure_filename(name or "") or fallback
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    stem = stem[:80] or fallback
    return f"{stem}.{extension.lower()}" if extension else stem


def webp(picture, side):
    """A copy at most `side` px on the long side, as WebP bytes (no EXIF: we don't pass any)."""
    copy = picture.copy()
    copy.thumbnail((side, side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    copy.save(out, "WEBP", quality=82, method=4)
    return out.getvalue(), copy.size


def prepare(upload_name, raw):
    """Check an upload and make what we will keep. Raises Invalid with a plain reason."""
    if not raw:
        raise Invalid("That file is empty.")
    if len(raw) > MAX_FILE_BYTES:
        raise Invalid("A file can be at most 5 MB.")
    name = clean_filename(upload_name)
    extension = name.rpartition(".")[2].lower() if "." in name else ""
    looks_like_picture = extension in ("jpg", "jpeg", "png", "gif", "webp")
    if looks_like_picture:
        try:
            # Pillow only WARNS between 1x and 2x the pixel limit: make that a refusal too.
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                picture = Image.open(io.BytesIO(raw))
                picture.load()
            with picture:
                if picture.format not in IMAGE_TYPES:
                    raise Invalid("That picture type isn't supported.")
                picture.load()
                picture = ImageOps.exif_transpose(picture)  # the right way up, before EXIF is gone
                if picture.mode not in ("RGB", "RGBA"):
                    picture = picture.convert("RGBA" if "transparency" in picture.info or picture.mode in ("LA", "P") else "RGB")
                data, (width, height) = webp(picture, IMAGE_SIDE)
                thumb, _ = webp(picture, THUMB_SIDE)
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise Invalid("That picture is far too big to open.") from None
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
            raise Invalid("That file isn't a picture we can open.") from None
        stem = name.rpartition(".")[0] or "picture"
        return Prepared("image", f"{stem}.webp", "image/webp", data, thumb, width, height)
    if extension not in FILE_TYPES:
        raise Invalid(f"You can send {ALLOWED_WORDS}.")
    return Prepared("file", name, FILE_TYPES[extension], raw)


def check_room(s, me, size):
    """Each person may keep 50 MB of files; a whole demo 10 MB."""
    if me.is_demo:
        used = s.scalar(select(func.coalesce(func.sum(Attachment.size), 0))
                        .join(User, User.id == Attachment.uploader_id)
                        .where(User.demo_team_id == me.demo_team_id)) or 0
        limit = MAX_PER_DEMO
    else:
        used = s.scalar(select(func.coalesce(func.sum(Attachment.size), 0))
                        .where(Attachment.uploader_id == me.id)) or 0
        limit = MAX_PER_PERSON
    if used + size > limit:
        raise TooMany("You've used all your space for files. Delete some messages with files to make room.")


def attach(s, message, uploader, prepared):
    attachment = Attachment(message_id=message.id, conversation_id=message.conversation_id, uploader_id=uploader.id,
                            kind=prepared.kind, filename=prepared.filename, content_type=prepared.content_type,
                            size=len(prepared.data), width=prepared.width, height=prepared.height,
                            data=prepared.data, thumb=prepared.thumb, created_at=message.created_at)
    s.add(attachment)
    s.flush()
    return attachment


def attachments_for(s, message_ids):
    """{message id: [attachment, ...]} without loading the bytes."""
    found = {}
    if message_ids:
        for attachment in s.scalars(select(Attachment).where(Attachment.message_id.in_(message_ids))
                                    .order_by(Attachment.id)):
            found.setdefault(attachment.message_id, []).append(attachment)
    return found


def forget(s, message):
    """A deleted message's files are deleted too (the bytes, not just the link)."""
    s.execute(delete(Attachment).where(Attachment.message_id == message.id))


def load_for(s, me, attachment_id):
    """The attachment, if this person may see its message; else NotFound."""
    attachment = s.get(Attachment, attachment_id)
    if attachment is None:
        raise NotFound()
    message = s.get(Message, attachment.message_id)
    conversation, membership = access.load(s, me, attachment.conversation_id)
    if message is None or message.deleted_at is not None or message.seq <= chats.visible_from(conversation, membership):
        raise NotFound()
    return attachment


def human_size(size):
    if size < 1000:
        return f"{size} B"
    if size < 1000 * 1000:
        return f"{size / 1000:.0f} KB"
    return f"{size / 1000 / 1000:.1f} MB"


def guess_type(name):
    return mimetypes.guess_type(name)[0] or "application/octet-stream"
