"""Detect which Microsoft Graph application permissions are consented to the
SharePoint app registration backing the Onyx connector.

Approach: the app-only access token issued by Azure AD is a signed JWT. We
don't need to validate the signature — it was issued to us — but the payload
contains a ``roles`` claim listing the granted application permissions
(e.g. ``["Sites.Read.All", "Files.ReadWrite.All"]``). Decoding that claim is
faster and cheaper than probing the Graph API with a write call to see what
fails.

JWT format and ``roles`` claim semantics are documented at:
- https://learn.microsoft.com/azure/active-directory/develop/access-tokens
- https://learn.microsoft.com/graph/permissions-reference
"""

from __future__ import annotations

import base64
import json
from typing import Any

from onyx.integrations.sharepoint_writer.graph_client import GraphTokenProvider
from onyx.integrations.sharepoint_writer.models import ScopeStatus
from onyx.utils.logger import setup_logger


logger = setup_logger()


# Permissions documented at learn.microsoft.com/graph/permissions-reference
# Either one is sufficient to upload to a document library via Graph.
WRITE_SCOPES: frozenset[str] = frozenset(
    {"Sites.ReadWrite.All", "Files.ReadWrite.All", "Sites.Manage.All"}
)
# Convenience for the admin UI — list these as "missing" when none are present.
REQUIRED_FOR_UI: tuple[str, ...] = ("Sites.ReadWrite.All", "Files.ReadWrite.All")


def _b64url_decode_segment(segment: str) -> bytes:
    """Decode one base64url-encoded JWT segment, handling missing padding."""
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode the middle segment of a JWT without validating the signature.

    The Graph token was issued to us by Azure AD; we're not authenticating
    something we received from an untrusted source.
    """
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("Token is not a JWT (expected at least header.payload.sig)")
    try:
        payload_bytes = _b64url_decode_segment(parts[1])
        return json.loads(payload_bytes)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to decode JWT payload: {exc}") from exc


def extract_roles(token: str) -> list[str]:
    """Return the ``roles`` claim from the JWT payload, or [] if absent.

    For client_credentials (app-only) tokens, ``roles`` lists the granted
    application permissions. For delegated tokens, scopes appear in ``scp``
    instead — we don't support delegated flow yet, but we coerce ``scp`` here
    so the same detector keeps working if that changes.
    """
    payload = decode_jwt_payload(token)
    roles = payload.get("roles")
    if isinstance(roles, list):
        return [str(r) for r in roles]
    scp = payload.get("scp")
    if isinstance(scp, str):
        # scp claim is space-separated in delegated tokens
        return scp.split()
    return []


def assess_scopes(roles: list[str]) -> ScopeStatus:
    role_set = set(roles)
    granted_write = role_set & WRITE_SCOPES
    if granted_write:
        return ScopeStatus(
            has_write=True,
            detected_roles=sorted(role_set),
            missing_scopes=[],
        )
    return ScopeStatus(
        has_write=False,
        detected_roles=sorted(role_set),
        missing_scopes=list(REQUIRED_FOR_UI),
    )


def detect_write_scopes(token_provider: GraphTokenProvider) -> ScopeStatus:
    """End-to-end detection: acquire token, decode, assess.

    Raises if the token can't be acquired; never raises on missing roles
    (returns ``has_write=False`` instead, which is the expected outcome when
    the M365 admin hasn't consented to write scopes yet).
    """
    token = token_provider.acquire()
    try:
        roles = extract_roles(token)
    except ValueError as exc:
        logger.warning("Could not parse Graph token for scope detection: %s", exc)
        return ScopeStatus(
            has_write=False,
            detected_roles=[],
            missing_scopes=list(REQUIRED_FOR_UI),
        )
    return assess_scopes(roles)
