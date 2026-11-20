"""The permission table, walked for every role and action (the authorization lesson)."""
from datetime import datetime

import pytest

from paperplane import access
from paperplane.access import ADMIN, MEMBER, OWNER
from paperplane.models import Conversation, Membership
from paperplane.problems import Forbidden

GROUP = Conversation(id=1, kind="group", title="Team", deleted_at=None)
DM = Conversation(id=2, kind="dm", deleted_at=None)


def person(role, user_id=10, removed=False):
    return Membership(user_id=user_id, role=role, removed_at=datetime(2026, 1, 1) if removed else None)


# What each role may do in a group, written out by hand: if the table in access.py changes,
# this list must be changed on purpose too.
EXPECTED = {
    OWNER: {"read", "send", "react", "edit_own", "delete_own", "leave", "delete_any", "rename", "add_people",
            "remove_people", "mute_member", "invite_links", "change_settings", "change_roles", "delete_group"},
    ADMIN: {"read", "send", "react", "edit_own", "delete_own", "leave", "delete_any", "rename", "add_people",
            "remove_people", "mute_member", "invite_links", "change_settings"},
    MEMBER: {"read", "send", "react", "edit_own", "delete_own", "leave"},
}


@pytest.mark.parametrize("role", access.ROLES)
@pytest.mark.parametrize("action", access.ACTIONS)
def test_group_table(role, action):
    target = person(MEMBER, user_id=99)  # someone below everyone, for the actions done TO someone
    assert access.can(action, GROUP, person(role), target=target) == (action in EXPECTED[role])


@pytest.mark.parametrize("action", access.ACTIONS)
def test_in_a_dm_both_people_can_only_talk(action):
    allowed = {"read", "send", "react", "edit_own", "delete_own"}
    assert access.can(action, DM, person(MEMBER)) == (action in allowed)


@pytest.mark.parametrize("action", access.ACTIONS)
def test_a_removed_member_or_a_stranger_can_do_nothing(action):
    assert not access.can(action, GROUP, person(OWNER, removed=True), target=person(MEMBER, 99))
    assert not access.can(action, GROUP, None, target=person(MEMBER, 99))


def test_a_deleted_group_allows_nothing():
    gone = Conversation(id=3, kind="group", deleted_at=datetime(2026, 1, 1))
    assert not access.can("read", gone, person(OWNER))


@pytest.mark.parametrize("actor, target, allowed", [
    (OWNER, ADMIN, True), (OWNER, MEMBER, True),
    (ADMIN, MEMBER, True), (ADMIN, ADMIN, False), (ADMIN, OWNER, False),
    (MEMBER, MEMBER, False),
])
def test_removing_and_muting_go_only_downwards(actor, target, allowed):
    for action in ("remove_people", "mute_member"):
        assert access.can(action, GROUP, person(actor, 10), target=person(target, 20)) == allowed


def test_nobody_acts_on_themselves():
    me = person(OWNER, 10)
    for action in access.TARGETED:
        assert not access.can(action, GROUP, me, target=me)


def test_only_the_owner_changes_roles_and_not_the_owners():
    owner = person(OWNER, 10)
    assert access.can("change_roles", GROUP, owner, target=person(ADMIN, 20))
    assert access.can("change_roles", GROUP, owner, target=person(MEMBER, 20))
    assert not access.can("change_roles", GROUP, person(ADMIN, 10), target=person(MEMBER, 20))


def test_muted_or_blocked_people_cant_speak_but_can_read():
    for flags in ({"muted": True}, {"blocked": True}):
        assert not access.can("send", GROUP, person(MEMBER), **flags)
        assert not access.can("react", GROUP, person(MEMBER), **flags)
        assert access.can("read", GROUP, person(MEMBER), **flags)
        assert access.can("leave", GROUP, person(MEMBER), **flags)


def test_require_says_why():
    with pytest.raises(Forbidden, match="Only the group's owner and admins can rename it."):
        access.require("rename", GROUP, person(MEMBER))


def test_an_unknown_action_is_a_mistake_in_the_code():
    with pytest.raises(ValueError):
        access.can("fly", GROUP, person(OWNER))
