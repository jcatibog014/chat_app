"""Chats over plain pages: start a DM or a group, send, read, unread counts, and who sees what."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from paperplane import chats, db, outbox
from paperplane.dates import utcnow
from paperplane.models import Conversation, Membership, Message, User
from paperplane.problems import Invalid, NotFound

from .conftest import browser, make_user, post, text, words


@pytest.fixture
def people(app):
    """Maria, Ana and Ben, each logged in in their own browser. Returns {name: (client, id)}."""
    result = {}
    for username, name in (("maria", "Maria Santos"), ("ana", "Ana Reyes"), ("ben", "Ben Cruz")):
        user_id = make_user(app, username, name)
        result[username] = (browser(app, user_id), user_id)
    return result


def dm(client, username):
    response = post(client, "/chats/new/dm", {"username": username})
    assert response.status_code == 302, words(response)
    return int(response.headers["Location"].rsplit("/", 1)[1])


def group(client, title, usernames):
    response = post(client, "/chats/new/group", {"title": title, "usernames": usernames})
    assert response.status_code == 302, words(response)
    return int(response.headers["Location"].rsplit("/", 1)[1])


def send(client, chat_id, body, client_id=None):
    data = {"body": body}
    if client_id:
        data["client_id"] = client_id
    return post(client, f"/chats/{chat_id}/send", data)


def count(app, model):
    with app.app_context():
        return db.session().scalar(select(func.count(model.id)))


def test_a_dm_is_found_by_username_and_made_once(app, people):
    maria, _ = people["maria"]
    ana, _ = people["ana"]
    first = dm(maria, "@Ana")
    assert dm(maria, "ana") == first
    assert dm(ana, "maria") == first  # the same DM from the other side
    assert count(app, Conversation) == 1
    page = words(maria.get(f"/chats/{first}"))
    assert "Ana Reyes" in page and "@ana" in page and "This is the start of your messages with Ana Reyes" in page


def test_no_dm_with_yourself_or_nobody(people):
    maria, _ = people["maria"]
    for name in ("maria", "nobody", ""):
        response = post(maria, "/chats/new/dm", {"username": name})
        assert response.status_code == 404
        assert "couldn't find anyone" in words(response)


def test_sending_shows_on_both_sides_with_unread_counts(app, people):
    maria, _ = people["maria"]
    ana, _ = people["ana"]
    chat = dm(maria, "ana")
    send(maria, chat, "Hello Ana!\nSecond line.")
    send(maria, chat, "Are you there?")
    # Ana's list: 2 unread, the last message as the preview.
    page = text(ana.get("/chats"))
    assert re.search(r'data-unread\s*><span class="visually-hidden">Unread: </span><span data-unread-number>2<', page)
    assert "Are you there?" in page
    assert '<span class="count" data-total-unread>2</span>' in page
    # Maria's own messages aren't unread for her.
    assert "data-total-unread hidden" in text(maria.get("/chats"))
    # Opening the chat reads it.
    opened = text(ana.get(f"/chats/{chat}"))
    assert "Hello Ana!\nSecond line." in opened
    assert "data-total-unread hidden" in text(ana.get("/chats"))


def test_a_message_sent_twice_is_saved_once(app, people):
    maria, _ = people["maria"]
    chat = dm(maria, "ana")
    send(maria, chat, "Only once", client_id="abcdef1234567890")
    send(maria, chat, "Only once", client_id="abcdef1234567890")
    assert count(app, Message) == 1


def test_an_empty_message_is_refused_politely(app, people):
    maria, _ = people["maria"]
    chat = dm(maria, "ana")
    response = send(maria, chat, "   \n  ")
    assert response.status_code == 302
    assert "Type a message first." in words(maria.get(f"/chats/{chat}"))
    assert count(app, Message) == 0


def test_strangers_get_not_found_never_forbidden(app, people):
    maria, _ = people["maria"]
    ben, _ = people["ben"]
    chat = dm(maria, "ana")
    assert ben.get(f"/chats/{chat}").status_code == 404
    assert send(ben, chat, "Let me in").status_code == 404
    assert ben.get("/chats/99999").status_code == 404
    assert count(app, Message) == 0


def test_a_group_with_its_owner_and_members(app, people):
    maria, maria_id = people["maria"]
    ben, _ = people["ben"]
    chat = group(maria, "  Weekend   Hike ", "ana, @ben")
    with app.app_context():
        s = db.session()
        conversation = s.get(Conversation, chat)
        assert conversation.title == "Weekend Hike" and conversation.is_group
        roles = {m.user_id: m.role for m in chats.members(s, conversation)}
        assert roles[maria_id] == "owner" and sorted(roles.values()) == ["member", "member", "owner"]
    send(ben, chat, "Hi all")
    assert "Ben Cruz" in words(maria.get(f"/chats/{chat}")) and "Hi all" in words(maria.get(f"/chats/{chat}"))


def test_a_group_needs_a_name_and_real_people(people):
    maria, _ = people["maria"]
    assert "Give the group a name." in words(post(maria, "/chats/new/group", {"title": " ", "usernames": "ana"}))
    assert "We couldn't find: @ghost." in words(post(maria, "/chats/new/group", {"title": "X", "usernames": "ana ghost"}))
    assert "Pick at least one person" in words(post(maria, "/chats/new/group", {"title": "X", "usernames": ""}))


def test_the_list_puts_the_newest_chat_first(people):
    maria, _ = people["maria"]
    with_ana = dm(maria, "ana")
    with_ben = dm(maria, "ben")
    send(maria, with_ben, "first")
    send(maria, with_ana, "second")
    page = text(maria.get("/chats"))
    assert page.index(f'data-chat="{with_ana}"') < page.index(f'data-chat="{with_ben}"')


def test_messages_are_escaped_and_links_are_safe(people):
    maria, _ = people["maria"]
    ana, _ = people["ana"]
    chat = dm(maria, "ana")
    send(maria, chat, '<script>alert(1)</script> see https://example.com/page?a=1&b=2.')
    page = ana.get(f"/chats/{chat}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert '<a href="https://example.com/page?a=1&amp;b=2" target="_blank" rel="noopener noreferrer nofollow ugc">' in page


def test_javascript_links_are_never_made():
    from paperplane.formatting import linkify
    assert "<a" not in str(linkify("javascript:alert(1) and data:text/html,hi"))
    sneaky = str(linkify('https://x.example/"onmouseover="alert(1)'))
    assert 'onmouseover="' not in sneaky and '<a href="https://x.example/"' in sneaky


def test_load_earlier_messages(app, people):
    maria, maria_id = people["maria"]
    chat = dm(maria, "ana")
    with app.app_context():
        s = db.session()
        me = s.get(User, maria_id)
        for number in range(1, 56):
            chats.send_message(s, me, chat, f"note {number}", None, utcnow())
        s.commit()
    latest = words(maria.get(f"/chats/{chat}"))
    assert "note 55" in latest and "note 6 " in latest + " " and "note 5 " not in latest + " "
    assert "Load earlier messages" in latest
    earlier = words(maria.get(f"/chats/{chat}?before=6"))
    assert "note 1 " in earlier + " " and "note 5" in earlier and "Back to the latest messages" in earlier


def test_sequence_numbers_and_versions_go_up_together(app, people):
    maria, maria_id = people["maria"]
    chat = dm(maria, "ana")
    for body in ("a", "b", "c"):
        send(maria, chat, body)
    with app.app_context():
        s = db.session()
        conversation = s.get(Conversation, chat)
        seqs = s.scalars(select(Message.seq).where(Message.conversation_id == chat).order_by(Message.id)).all()
        assert seqs == [1, 2, 3] and conversation.last_seq == 3 and conversation.version == 3


def test_live_events_wait_for_the_commit(app, people):
    """outbox.py: the events are held on the session, and a rollback throws them away."""
    maria, maria_id = people["maria"]
    chat = dm(maria, "ana")
    with app.app_context():
        s = db.session()
        me = s.get(User, maria_id)
        chats.send_message(s, me, chat, "never saved", None, utcnow())
        assert [payload["type"] for _ids, payload in outbox.pending(s)] == ["message.new"]
        s.rollback()
        assert outbox.pending(s) == []


def test_the_message_event_goes_to_the_members_only(app, people):
    maria, maria_id = people["maria"]
    _, ana_id = people["ana"]
    chat = dm(maria, "ana")
    with app.app_context():
        s = db.session()
        chats.send_message(s, s.get(User, maria_id), chat, "hi", None, utcnow())
        (user_ids, payload), = outbox.pending(s)
        assert user_ids == {maria_id, ana_id}
        assert payload["data"]["body"] == "hi" and 'class="msg theirs' in payload["data"]["html"]


def test_mark_read_never_goes_backwards(app, people):
    maria, maria_id = people["maria"]
    _, ana_id = people["ana"]
    chat = dm(maria, "ana")
    for body in ("a", "b", "c"):
        send(maria, chat, body)
    with app.app_context():
        s = db.session()
        ana = s.get(User, ana_id)
        chats.mark_read(s, ana, chat, 2)
        chats.mark_read(s, ana, chat, 1)
        chats.mark_read(s, ana, chat, 999)  # never past the end
        membership = s.scalar(select(Membership).where(Membership.user_id == ana_id))
        assert membership.last_read_seq == 3


def test_domain_rules_raise_problems(app, people):
    _, maria_id = people["maria"]
    _, ben_id = people["ben"]
    maria_client, _ = people["maria"]
    chat = dm(maria_client, "ana")
    with app.app_context():
        s = db.session()
        with pytest.raises(Invalid):
            chats.send_message(s, s.get(User, maria_id), chat, "x" * 4001, None, utcnow())
        with pytest.raises(NotFound):
            chats.send_message(s, s.get(User, ben_id), chat, "hello", None, utcnow())


def test_a_hidden_history_starts_at_joining(app, people):
    """Groups can hide what was said before someone joined (stage 7 makes it a setting)."""
    maria, maria_id = people["maria"]
    ben, ben_id = people["ben"]
    chat = group(maria, "Secrets", "ana")
    send(maria, chat, "before Ben came")
    with app.app_context():
        s = db.session()
        conversation = s.get(Conversation, chat)
        conversation.history_visible = False
        s.add(Membership(conversation_id=chat, user_id=ben_id, role="member", joined_seq=conversation.last_seq,
                         last_read_seq=conversation.last_seq))
        s.commit()
    send(maria, chat, "after Ben came")
    page = words(ben.get(f"/chats/{chat}"))
    assert "after Ben came" in page and "before Ben came" not in page


def test_demo_people_and_real_people_never_meet(app, people):
    maria, _ = people["maria"]
    demo = browser(app)
    post(demo, "/demo")
    # Mika can't find Maria, and Maria can't find Mika.
    assert post(demo, "/chats/new/dm", {"username": "maria"}).status_code == 404
    with app.app_context():
        mika = db.session().scalar(select(User).where(User.username.like("d%.mika")))
    assert post(maria, "/chats/new/dm", {"username": mika.username}).status_code == 404
    assert post(maria, "/chats/new/dm", {"person": str(mika.id)}).status_code == 404
    # Ticking a demo person's id on the group form does nothing either.
    response = post(maria, "/chats/new/group", {"title": "Sneaky", "person": str(mika.id)})
    assert "Pick at least one person" in words(response)


def test_the_new_chat_page_offers_people_you_know(people):
    maria, _ = people["maria"]
    dm(maria, "ana")
    page = words(maria.get("/chats/new"))
    assert "Message Someone" in page and "New Group" in page and "People You Know" in page and "Ana Reyes" in page
    assert "Ben Cruz" not in page  # Maria hasn't talked with Ben


def test_times_show_in_the_readers_time_zone(app, people):
    maria, maria_id = people["maria"]
    chat = dm(maria, "ana")
    with app.app_context():
        s = db.session()
        me = s.get(User, maria_id)
        me.time_zone = "Asia/Tokyo"
        moment = utcnow().replace(hour=1, minute=5, second=0, microsecond=0) - timedelta(days=0)
        chats.send_message(s, me, chat, "early", None, moment)
        s.commit()
    assert "10:05 AM" in words(maria.get(f"/chats/{chat}"))
