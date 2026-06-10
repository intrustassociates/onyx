"""Clean-room write-permission check for SharePoint drive items.

The Onyx EE module ``ee/onyx/external_permissions/sharepoint/permission_utils.py``
solves a different problem (enumerating *all* users with read access for ACL
sync). We need a much smaller answer: *does this one user have write access to
this one folder?* Implementing that directly from Microsoft's public Graph API
docs keeps this file OSS-clean.

Endpoints used (all documented at learn.microsoft.com/graph):
- ``GET /users/{upn}`` to resolve email → AAD object id
- ``GET /users/{id}/transitiveMemberOf`` to enumerate group memberships
- ``GET /drives/{drive-id}/items/{item-id}/permissions`` to read the per-item
  permissions, which include the modern ``grantedToV2`` payload + ``roles``
  array (``"read"``, ``"write"``, ``"owner"``).
"""

from __future__ import annotations

import time
from threading import Lock
from urllib.parse import quote

from onyx.integrations.sharepoint_writer.graph_client import graph_get
from onyx.integrations.sharepoint_writer.graph_client import graph_get_all_pages
from onyx.integrations.sharepoint_writer.graph_client import GraphClientError
from onyx.integrations.sharepoint_writer.graph_client import GraphTokenProvider
from onyx.utils.logger import setup_logger


logger = setup_logger()


# Per driveItem.permissions API, write capability is signaled by either of these
# role strings (lowercase). ``owner`` implies ``write``.
WRITE_GRAPH_ROLES: frozenset[str] = frozenset({"write", "owner"})


_GROUP_MEMBERSHIP_TTL = 300.0  # 5 min — memberships change rarely
_PERMISSION_TTL = 60.0  # 60 s — fine-grained, matches picker session


# Module-level caches, keyed by graph host + identifiers. A single Onyx
# deployment talks to one Azure tenant, so the host prefix only matters for
# sovereign-cloud setups. Process-local; cleared on restart.
_group_cache_lock = Lock()
_group_cache: dict[tuple[str, str], tuple[frozenset[str], float]] = {}
_perm_cache_lock = Lock()
_perm_cache: dict[tuple[str, str, str, str], tuple[bool, float]] = {}
_user_id_cache_lock = Lock()
_user_id_cache: dict[tuple[str, str], tuple[str | None, float]] = {}


def _resolve_user_id(token_provider: GraphTokenProvider, email: str) -> str | None:
    """Resolve an email/UPN to its AAD object id, returning None on 404."""
    url = (
        f"{token_provider.graph_base}/users/"
        f"{quote(email, safe='')}?$select=id,userPrincipalName,mail"
    )
    try:
        payload = graph_get(url, token_provider)
    except GraphClientError as exc:
        if exc.status_code == 404:
            return None
        raise
    return payload.get("id")


def _resolve_user_id_cached(
    token_provider: GraphTokenProvider, email: str
) -> str | None:
    """Cached email → AAD object id, so checking N folders costs one lookup."""
    cache_key = (token_provider.graph_host, email.lower())
    now = time.time()
    with _user_id_cache_lock:
        cached = _user_id_cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]

    user_id = _resolve_user_id(token_provider, email)
    with _user_id_cache_lock:
        _user_id_cache[cache_key] = (user_id, now + _GROUP_MEMBERSHIP_TTL)
    return user_id


def _fetch_user_group_ids(
    token_provider: GraphTokenProvider, user_id: str
) -> frozenset[str]:
    url = (
        f"{token_provider.graph_base}/users/{user_id}/transitiveMemberOf" f"?$select=id"
    )
    items = graph_get_all_pages(url, token_provider, max_pages=20)
    return frozenset(item["id"] for item in items if isinstance(item.get("id"), str))


def get_user_group_ids(
    token_provider: GraphTokenProvider, user_email: str
) -> frozenset[str]:
    """Return AAD group ids the user belongs to (transitive). Cached 5 min."""
    cache_key = (token_provider.graph_host, user_email.lower())
    now = time.time()
    with _group_cache_lock:
        cached = _group_cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]

    user_id = _resolve_user_id_cached(token_provider, user_email)
    if not user_id:
        logger.info("SharePoint write check: user %s not found in AAD", user_email)
        groups: frozenset[str] = frozenset()
    else:
        groups = _fetch_user_group_ids(token_provider, user_id)

    with _group_cache_lock:
        _group_cache[cache_key] = (groups, now + _GROUP_MEMBERSHIP_TTL)
    return groups


def _permissions_grant_write(
    permissions: list[dict],
    user_id: str | None,
    user_group_ids: frozenset[str],
) -> bool:
    """Walk the permissions list and decide if the user has write."""
    for perm in permissions:
        roles = {str(r).lower() for r in perm.get("roles", [])}
        if not (roles & WRITE_GRAPH_ROLES):
            continue

        granted_v2 = perm.get("grantedToV2") or {}
        granted_legacy = perm.get("grantedTo") or {}

        # Direct user grant (modern shape)
        v2_user = granted_v2.get("user") or {}
        if user_id and v2_user.get("id") == user_id:
            return True

        # Direct user grant (legacy shape, sometimes still returned)
        legacy_user = granted_legacy.get("user") or {}
        if user_id and legacy_user.get("id") == user_id:
            return True

        # Group grant — modern
        v2_group = granted_v2.get("group") or {}
        if v2_group.get("id") and v2_group["id"] in user_group_ids:
            return True

        # ``grantedToIdentitiesV2`` is the multi-grant variant of grantedToV2 —
        # same identity shape, plural.
        for identity in perm.get("grantedToIdentitiesV2", []) or []:
            iu = (identity or {}).get("user") or {}
            if user_id and iu.get("id") == user_id:
                return True
            ig = (identity or {}).get("group") or {}
            if ig.get("id") and ig["id"] in user_group_ids:
                return True

    return False


def user_has_write_access(
    token_provider: GraphTokenProvider,
    user_email: str,
    drive_id: str,
    item_id: str,
) -> bool:
    """Return True if the user can write to the given drive item (folder).

    Best-effort: returns False on any error, with a warning logged. The caller
    re-validates at upload time (defense in depth), so a transient false
    negative just means the user sees fewer folders for a moment.
    """
    cache_key = (token_provider.graph_host, user_email.lower(), drive_id, item_id)
    now = time.time()
    with _perm_cache_lock:
        cached = _perm_cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]

    try:
        user_id = _resolve_user_id_cached(token_provider, user_email)
        user_groups = get_user_group_ids(token_provider, user_email)
        url = (
            f"{token_provider.graph_base}/drives/{drive_id}/items/"
            f"{quote(item_id, safe='')}/permissions"
        )
        permissions = graph_get_all_pages(url, token_provider, max_pages=5)
        granted = _permissions_grant_write(permissions, user_id, user_groups)
    except GraphClientError as exc:
        logger.warning(
            "Permission check failed for %s on %s/%s: %s",
            user_email,
            drive_id,
            item_id,
            exc,
        )
        # Don't cache transient failures — the next call should retry Graph
        # instead of pinning the folder read-only for the TTL window.
        return False

    with _perm_cache_lock:
        _perm_cache[cache_key] = (granted, now + _PERMISSION_TTL)
    return granted


def invalidate_caches() -> None:
    """Useful for tests and for the admin "rescan permissions" action."""
    with _group_cache_lock:
        _group_cache.clear()
    with _perm_cache_lock:
        _perm_cache.clear()
    with _user_id_cache_lock:
        _user_id_cache.clear()
