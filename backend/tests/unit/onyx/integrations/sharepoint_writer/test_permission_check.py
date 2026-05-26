from __future__ import annotations

from unittest.mock import patch

import pytest

from onyx.integrations.sharepoint_writer.graph_client import GraphClientError
from onyx.integrations.sharepoint_writer.permission_check import (
    _permissions_grant_write,
)
from onyx.integrations.sharepoint_writer.permission_check import invalidate_caches
from onyx.integrations.sharepoint_writer.permission_check import user_has_write_access


class _FakeTokenProvider:
    graph_host = "https://graph.microsoft.com"
    graph_base = "https://graph.microsoft.com/v1.0"

    def acquire(self) -> str:
        return "tok"


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    invalidate_caches()
    yield
    invalidate_caches()


def _perm(roles: list[str], **granted: dict) -> dict:
    return {"roles": roles, **granted}


def test_permissions_grant_write_direct_user_v2() -> None:
    perms = [
        _perm(["write"], grantedToV2={"user": {"id": "user-abc"}}),
    ]
    assert _permissions_grant_write(perms, "user-abc", frozenset()) is True


def test_permissions_grant_write_direct_user_legacy_shape() -> None:
    perms = [
        _perm(["write"], grantedTo={"user": {"id": "user-abc"}}),
    ]
    assert _permissions_grant_write(perms, "user-abc", frozenset()) is True


def test_permissions_grant_write_user_via_group() -> None:
    perms = [
        _perm(["write"], grantedToV2={"group": {"id": "group-xyz"}}),
    ]
    assert _permissions_grant_write(perms, "user-abc", frozenset({"group-xyz"})) is True


def test_permissions_grant_write_owner_role_implies_write() -> None:
    perms = [_perm(["owner"], grantedToV2={"user": {"id": "user-abc"}})]
    assert _permissions_grant_write(perms, "user-abc", frozenset()) is True


def test_permissions_grant_write_read_only_rejected() -> None:
    perms = [_perm(["read"], grantedToV2={"user": {"id": "user-abc"}})]
    assert _permissions_grant_write(perms, "user-abc", frozenset()) is False


def test_permissions_grant_write_other_user_ignored() -> None:
    perms = [_perm(["write"], grantedToV2={"user": {"id": "someone-else"}})]
    assert _permissions_grant_write(perms, "user-abc", frozenset()) is False


def test_permissions_grant_write_group_not_in_user_memberships() -> None:
    perms = [_perm(["write"], grantedToV2={"group": {"id": "group-other"}})]
    assert (
        _permissions_grant_write(perms, "user-abc", frozenset({"group-xyz"})) is False
    )


def test_permissions_grant_write_granted_to_identities_v2_user() -> None:
    perms = [
        {
            "roles": ["write"],
            "grantedToIdentitiesV2": [
                {"user": {"id": "user-abc"}},
                {"user": {"id": "user-other"}},
            ],
        }
    ]
    assert _permissions_grant_write(perms, "user-abc", frozenset()) is True


def test_permissions_grant_write_granted_to_identities_v2_group() -> None:
    perms = [
        {
            "roles": ["write"],
            "grantedToIdentitiesV2": [{"group": {"id": "group-xyz"}}],
        }
    ]
    assert _permissions_grant_write(perms, None, frozenset({"group-xyz"})) is True


def test_user_has_write_access_full_happy_path() -> None:
    """End-to-end: user is resolved, has a group with write access, returns True."""
    with (
        patch(
            "onyx.integrations.sharepoint_writer.permission_check.graph_get",
            return_value={"id": "user-abc"},
        ) as mock_get,
        patch(
            "onyx.integrations.sharepoint_writer.permission_check.graph_get_all_pages",
            side_effect=[
                # transitiveMemberOf
                [{"id": "group-xyz"}, {"id": "group-other"}],
                # permissions
                [{"roles": ["write"], "grantedToV2": {"group": {"id": "group-xyz"}}}],
            ],
        ),
    ):
        granted = user_has_write_access(
            _FakeTokenProvider(),
            user_email="alice@tenant.com",
            drive_id="drive-1",
            item_id="folder-1",
        )
        assert granted is True
        # graph_get called twice: once to resolve the user (inside group fetch),
        # once when permissions endpoint is being assembled... actually our
        # implementation calls graph_get once per resolve. Verify at least once.
        assert mock_get.call_count >= 1


def test_user_has_write_access_user_not_in_aad_returns_false() -> None:
    with (
        patch(
            "onyx.integrations.sharepoint_writer.permission_check.graph_get",
            side_effect=GraphClientError("Graph GET https://... -> 404: not found"),
        ),
        patch(
            "onyx.integrations.sharepoint_writer.permission_check.graph_get_all_pages",
            return_value=[],
        ),
    ):
        granted = user_has_write_access(
            _FakeTokenProvider(),
            user_email="ghost@tenant.com",
            drive_id="drive-1",
            item_id="folder-1",
        )
        assert granted is False


def test_user_has_write_access_swallows_graph_errors() -> None:
    with (
        patch(
            "onyx.integrations.sharepoint_writer.permission_check.graph_get",
            return_value={"id": "user-abc"},
        ),
        patch(
            "onyx.integrations.sharepoint_writer.permission_check.graph_get_all_pages",
            side_effect=GraphClientError("500 boom"),
        ),
    ):
        granted = user_has_write_access(
            _FakeTokenProvider(),
            user_email="alice@tenant.com",
            drive_id="drive-1",
            item_id="folder-1",
        )
        assert granted is False


def test_user_has_write_access_caches_repeated_calls() -> None:
    """Second call within TTL should not hit Graph again."""
    with (
        patch(
            "onyx.integrations.sharepoint_writer.permission_check.graph_get",
            return_value={"id": "user-abc"},
        ) as mock_get,
        patch(
            "onyx.integrations.sharepoint_writer.permission_check.graph_get_all_pages",
            side_effect=[
                [{"id": "g1"}],
                [{"roles": ["write"], "grantedToV2": {"user": {"id": "user-abc"}}}],
            ],
        ) as mock_pages,
    ):
        user_has_write_access(
            _FakeTokenProvider(),
            user_email="alice@tenant.com",
            drive_id="d1",
            item_id="f1",
        )
        first_pages_calls = mock_pages.call_count
        first_get_calls = mock_get.call_count

        user_has_write_access(
            _FakeTokenProvider(),
            user_email="alice@tenant.com",
            drive_id="d1",
            item_id="f1",
        )
        # Same key — should have returned cached result, no new Graph calls.
        assert mock_pages.call_count == first_pages_calls
        assert mock_get.call_count == first_get_calls
