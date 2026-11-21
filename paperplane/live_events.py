"""What the browser can ask over the WebSocket. Each handler is small: it calls
the same domain functions as the pages (chats.py), which check the rules.

    message.send  {conversation_id, body, client_id, reply_to?} -> ack {message}
    message.edit  {message_id, body}                   -> ack {message}
    message.delete {message_id}                        -> ack {message}
    message.react {message_id, emoji}                  -> ack {added}
    presence      {visible}                            -> ack {}
    typing        {conversation_id}                    -> ack {}
    read          {conversation_id, seq}               -> ack {}
    sync          {open: {conversation_id, version}}   -> ack {list_html, total_unread, changes, version, reload}
"""
from . import access, chats, outbox, presence, tools
from .chats import BODY_MAX_LENGTH, render_fragment
from .events import on


@on("message.send", limit=(20, 10), conversation_id=(int, True), body=(str, True, BODY_MAX_LENGTH + 100),
    client_id=(str, True, 40), reply_to=(int, False))
def send(ctx, data):
    message, duplicate = chats.send_message(ctx.s, ctx.user, data["conversation_id"], data["body"],
                                            data["client_id"], ctx.now, reply_to_id=data["reply_to"])
    # The ack carries the message too: when it was a duplicate, nothing new was broadcast,
    # and this tab still needs the saved copy to replace its "sending..." bubble.
    return {"message": chats.message_data(message), "duplicate": duplicate}


@on("read", limit=(60, 10), conversation_id=(int, True), seq=(int, True))
def read(ctx, data):
    chats.mark_read(ctx.s, ctx.user, data["conversation_id"], data["seq"])
    return {}


@on("message.edit", limit=(20, 10), message_id=(int, True), body=(str, True, BODY_MAX_LENGTH + 100))
def edit(ctx, data):
    message = tools.edit_message(ctx.s, ctx.user, data["message_id"], data["body"], ctx.now)
    return {"message": chats.message_data(message)}


@on("message.delete", limit=(20, 10), message_id=(int, True))
def remove(ctx, data):
    message = tools.delete_message(ctx.s, ctx.user, data["message_id"], ctx.now)
    return {"message": chats.message_data(message)}


@on("message.react", limit=(30, 10), message_id=(int, True), emoji=(str, True, 16))
def react(ctx, data):
    added = tools.react(ctx.s, ctx.user, data["message_id"], data["emoji"], ctx.now)
    return {"added": added}


@on("presence", limit=(30, 10), visible=(bool, True))
def visibility(ctx, data):
    """The tab was hidden or shown (Away after 5 minutes hidden, presence.py)."""
    presence.get(ctx.app).set_visible(ctx.connection, data["visible"])
    return {}


@on("typing", limit=(30, 10), conversation_id=(int, True))
def typing(ctx, data):
    """Someone is typing: tell the chat's other members. Never stored, never sent back to
    the typer, and only from someone who may send there (a muted member types to nobody).
    The browser sends it at most every 3 s; the server drops anything faster than 2 s."""
    conversation, membership = access.load(ctx.s, ctx.user, data["conversation_id"])
    chats.require_speaking("send", ctx.s, conversation, membership, ctx.user, ctx.now)
    if ctx.hub.allow(f"typing:{ctx.user.id}:{conversation.id}", 1, 2):
        others = chats.member_ids(ctx.s, conversation) - {ctx.user.id}
        outbox.emit(ctx.s, others, "typing", {"conversation_id": conversation.id, "user_id": ctx.user.id,
                                              "name": ctx.user.first_name})
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
