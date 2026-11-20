"""What the browser can ask over the WebSocket. Each handler is small: it calls
the same domain functions as the pages (chats.py), which check the rules.

    message.send  {conversation_id, body, client_id}   -> ack {message}
    read          {conversation_id, seq}               -> ack {}
    sync          {open: {conversation_id, version}}   -> ack {list_html, total_unread, changes, version, reload}
"""
from . import access, chats
from .chats import BODY_MAX_LENGTH, render_fragment
from .events import on


@on("message.send", limit=(20, 10), conversation_id=(int, True), body=(str, True, BODY_MAX_LENGTH + 100),
    client_id=(str, True, 40))
def send(ctx, data):
    message, duplicate = chats.send_message(ctx.s, ctx.user, data["conversation_id"], data["body"],
                                            data["client_id"], ctx.now)
    # The ack carries the message too: when it was a duplicate, nothing new was broadcast,
    # and this tab still needs the saved copy to replace its "sending..." bubble.
    return {"message": chats.message_data(message), "duplicate": duplicate}


@on("read", limit=(60, 10), conversation_id=(int, True), seq=(int, True))
def read(ctx, data):
    chats.mark_read(ctx.s, ctx.user, data["conversation_id"], data["seq"])
    return {}


@on("sync", limit=(10, 10), open=(dict, False))
def sync(ctx, data):
    """After a reconnect: the chat list as it is now, and what changed in the open chat.
    The browser says which version of the open chat it has; only newer changes come back."""
    rows = chats.chat_list(ctx.s, ctx.user)
    result = {
        "list_html": render_fragment("_chat_list_live.html", rows=rows, me=ctx.user),
        "total_unread": chats.total_unread(ctx.s, ctx.user, rows),
        "changes": [],
        "reload": False,
    }
    opened = data.get("open") or {}
    if opened.get("conversation_id") is not None:
        try:
            conversation, membership = access.load(ctx.s, ctx.user, opened["conversation_id"])
        except Exception:
            result["gone"] = True  # removed from it, or it was deleted, while away
            return result
        version = opened.get("version")
        version = version if isinstance(version, int) and not isinstance(version, bool) else 0
        changed, reload = chats.changes_since(ctx.s, conversation, membership, version)
        result.update(changes=[chats.message_data(m) for m in changed], reload=reload,
                      version=conversation.version, last_seq=conversation.last_seq)
    return result
