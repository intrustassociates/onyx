from __future__ import annotations

import base64
import json

import pytest

from onyx.integrations.sharepoint_writer.scope_detection import assess_scopes
from onyx.integrations.sharepoint_writer.scope_detection import decode_jwt_payload
from onyx.integrations.sharepoint_writer.scope_detection import extract_roles
from onyx.integrations.sharepoint_writer.scope_detection import REQUIRED_FOR_UI


def _make_jwt(payload: dict) -> str:
    """Build a JWT-shaped token (header.payload.sig) with given payload."""

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(payload).encode())
    sig = b64(b"not-a-real-signature")
    return f"{header}.{body}.{sig}"


def test_decode_jwt_payload_roundtrip() -> None:
    token = _make_jwt({"roles": ["Sites.Read.All"], "exp": 1234567890})
    decoded = decode_jwt_payload(token)
    assert decoded["roles"] == ["Sites.Read.All"]
    assert decoded["exp"] == 1234567890


def test_decode_jwt_payload_handles_missing_padding() -> None:
    # Payload whose base64 encoding requires padding stripping
    token = _make_jwt({"roles": ["A"]})
    decoded = decode_jwt_payload(token)
    assert decoded == {"roles": ["A"]}


def test_decode_jwt_payload_rejects_non_jwt() -> None:
    with pytest.raises(ValueError):
        decode_jwt_payload("not-a-jwt")


def test_decode_jwt_payload_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        decode_jwt_payload("aaa.!!!notbase64!!!.bbb")


def test_extract_roles_from_app_only_token() -> None:
    token = _make_jwt({"roles": ["Sites.Read.All", "Files.ReadWrite.All"]})
    assert extract_roles(token) == ["Sites.Read.All", "Files.ReadWrite.All"]


def test_extract_roles_falls_back_to_scp_for_delegated_token() -> None:
    token = _make_jwt({"scp": "Sites.Read.All Files.ReadWrite.All openid"})
    assert extract_roles(token) == [
        "Sites.Read.All",
        "Files.ReadWrite.All",
        "openid",
    ]


def test_extract_roles_returns_empty_when_neither_claim_present() -> None:
    token = _make_jwt({"sub": "abc", "aud": "graph"})
    assert extract_roles(token) == []


def test_assess_scopes_grants_write_with_sites_readwrite() -> None:
    status = assess_scopes(["Sites.Read.All", "Sites.ReadWrite.All"])
    assert status.has_write is True
    assert status.missing_scopes == []
    assert "Sites.ReadWrite.All" in status.detected_roles


def test_assess_scopes_grants_write_with_files_readwrite() -> None:
    status = assess_scopes(["Files.ReadWrite.All"])
    assert status.has_write is True


def test_assess_scopes_grants_write_with_sites_manage() -> None:
    status = assess_scopes(["Sites.Manage.All"])
    assert status.has_write is True


def test_assess_scopes_reports_missing_when_read_only() -> None:
    status = assess_scopes(["Sites.Read.All", "Files.Read.All"])
    assert status.has_write is False
    assert set(status.missing_scopes) == set(REQUIRED_FOR_UI)
    assert "Sites.Read.All" in status.detected_roles


def test_assess_scopes_empty_roles() -> None:
    status = assess_scopes([])
    assert status.has_write is False
    assert status.detected_roles == []
    assert set(status.missing_scopes) == set(REQUIRED_FOR_UI)
