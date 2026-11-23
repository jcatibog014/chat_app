"""A chat's history of photos, files and links (chat info > Photos, Files and Links).

The same rules as reading the chat itself: you must be a member now (access.load, in the
view), a group that hides its history shows you only what came after you joined
(chats.visible_from), and deleted messages never show (their files are deleted with them,
files.forget). Newest first, a page at a time; "Show older" asks for the ones before the
last one shown.

- Photos and Files are attachments (`attachments.kind` "image" or "file"), with the message
  they came in (who sent it, when, and where it is in the chat).
- Links are the web addresses in messages, found the same way the chat makes them into links
  (formatting.find_links), with the page's title when the message has a preview.
"""
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import func, select

from . import chats
from .formatting import find_links
from .models import Attachment, LinkPreview, Message

PAGE = 48
TABS = ("photos", "files", "links")
TAB_NAMES = {"photos": "Photos", "files": "Files", "links": "Links"}
FILE_KINDS = {"photos": "image", "files": "file"}


@dataclass
class Link:
    url: str
    host: str
    title: str | None
    site_name: str | None
    description: str | None
    message: Message

    @property
    def created_at(self) -> datetime:
        return self.message.created_at


def host_of(url):
    host = urlsplit(url).hostname or url
    return host[4:] if host.startswith("www.") else host


def _readable(conversation, membership):
    """The conditions for messages this member may see in this chat."""
    return (Message.conversation_id == conversation.id, Message.deleted_at.is_(None),
            Message.seq > chats.visible_from(conversation, membership))


def attachments(s, conversation, membership, tab, before=None, limit=None):
    """Photos or files (tab), newest first: ([(attachment, message)], more?)."""
    limit = limit or PAGE
    query = (select(Attachment, Message).join(Message, Message.id == Attachment.message_id)
             .where(Attachment.kind == FILE_KINDS[tab], *_readable(conversation, membership)))
    if before is not None:
        query = query.where(Attachment.id < before)
    rows = s.execute(query.order_by(Attachment.id.desc()).limit(limit + 1)).all()
    return [tuple(row) for row in rows[:limit]], len(rows) > limit


def _link_messages(conversation, membership):
    return select(Message).where(Message.kind == "text", func.lower(Message.body).like("%http%"),
                                 *_readable(conversation, membership))


def links(s, conversation, membership, before=None, limit=None):
    """Links, newest first: ([Link], more?). Pages go by message (before = a message's seq)."""
    limit = limit or PAGE
    query = _link_messages(conversation, membership)
    if before is not None:
        query = query.where(Message.seq < before)
    messages = s.scalars(query.order_by(Message.seq.desc()).limit(limit + 1)).all()
    more = len(messages) > limit
    messages = messages[:limit]
    previews = {preview.message_id: preview for preview in s.scalars(
        select(LinkPreview).where(LinkPreview.message_id.in_([m.id for m in messages])))} if messages else {}
    found = []
    for message in messages:
        preview = previews.get(message.id)
        for url in find_links(message.body):
            card = preview if preview is not None and preview.url == url else None
            found.append(Link(url=url, host=host_of(url), title=card.title if card else None,
                              site_name=card.site_name if card else None,
                              description=card.description if card else None, message=message))
    return found, more


def counts(s, conversation, membership):
    """{"photos": n, "files": n, "links": n} for the chat info page."""
    readable = _readable(conversation, membership)
    numbers = dict.fromkeys(TABS, 0)
    for kind, number in s.execute(
            select(Attachment.kind, func.count(Attachment.id)).join(Message, Message.id == Attachment.message_id)
            .where(*readable).group_by(Attachment.kind)):
        for tab, file_kind in FILE_KINDS.items():
            if kind == file_kind:
                numbers[tab] = number
    bodies = s.scalars(_link_messages(conversation, membership).with_only_columns(Message.body))
    numbers["links"] = sum(len(find_links(body)) for body in bodies)
    return numbers


def first_tab(numbers):
    """The tab to open when none is asked for: the first one with something in it."""
    return next((tab for tab in TABS if numbers[tab]), TABS[0])
