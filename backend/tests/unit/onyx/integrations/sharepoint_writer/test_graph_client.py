from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
import requests

from onyx.integrations.sharepoint_writer.graph_client import (
    auth_bundle_from_credential_json,
)
from onyx.integrations.sharepoint_writer.graph_client import graph_get
from onyx.integrations.sharepoint_writer.graph_client import graph_get_all_pages
from onyx.integrations.sharepoint_writer.graph_client import graph_put_bytes
from onyx.integrations.sharepoint_writer.graph_client import GraphClientError
from onyx.integrations.sharepoint_writer.graph_client import GraphTokenProvider


class _FakeTokenProvider:
    def __init__(self, token: str = "fake-token") -> None:
        self._token = token
        self.graph_host = "https://graph.microsoft.com"
        self.graph_base = "https://graph.microsoft.com/v1.0"

    def acquire(self) -> str:
        return self._token


def _make_response(status: int, json_body: Any = None, text: str = "") -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.json.return_value = json_body if json_body is not None else {}
    resp.text = text
    resp.headers = {}
    resp.content = b"x" if json_body is not None else b""
    return resp


def test_auth_bundle_from_credential_json_client_secret() -> None:
    bundle = auth_bundle_from_credential_json(
        {
            "sp_client_id": "client-id-123",
            "sp_directory_id": "tenant-456",
            "sp_client_secret": "shh",
        }
    )
    assert bundle.sp_client_id == "client-id-123"
    assert bundle.sp_directory_id == "tenant-456"
    assert bundle.sp_client_secret == "shh"
    assert bundle.authentication_method == "client_secret"
    assert bundle.graph_api_host == "https://graph.microsoft.com"


def test_auth_bundle_preserves_environment_hosts() -> None:
    bundle = auth_bundle_from_credential_json(
        {
            "sp_client_id": "id",
            "sp_directory_id": "tenant",
            "sp_client_secret": "shh",
            "graph_api_host": "https://graph.microsoft.us",
            "authority_host": "https://login.microsoftonline.us",
        }
    )
    assert bundle.graph_api_host == "https://graph.microsoft.us"
    assert bundle.authority_host == "https://login.microsoftonline.us"


def test_graph_get_returns_json_body() -> None:
    fake_resp = _make_response(200, json_body={"value": [{"id": "a"}]})
    with patch(
        "onyx.integrations.sharepoint_writer.graph_client.requests.request",
        return_value=fake_resp,
    ) as mock_req:
        result = graph_get(
            "https://graph.microsoft.com/v1.0/sites", _FakeTokenProvider()
        )
        assert result == {"value": [{"id": "a"}]}
        # Check auth header was sent
        call_kwargs = mock_req.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer fake-token"


def test_graph_get_raises_on_non_ok_after_retries() -> None:
    fake_resp = _make_response(404, text="not found")
    with patch(
        "onyx.integrations.sharepoint_writer.graph_client.requests.request",
        return_value=fake_resp,
    ):
        with pytest.raises(GraphClientError, match="404"):
            graph_get("https://graph.microsoft.com/v1.0/sites", _FakeTokenProvider())


def test_graph_get_retries_on_429_then_succeeds() -> None:
    bad = _make_response(429)
    bad.headers = {"Retry-After": "0"}
    good = _make_response(200, json_body={"ok": True})
    with (
        patch(
            "onyx.integrations.sharepoint_writer.graph_client.requests.request",
            side_effect=[bad, good],
        ),
        patch("onyx.integrations.sharepoint_writer.graph_client.time.sleep"),
    ):
        result = graph_get(
            "https://graph.microsoft.com/v1.0/sites", _FakeTokenProvider()
        )
        assert result == {"ok": True}


def test_graph_get_retries_on_503() -> None:
    bad = _make_response(503)
    bad.headers = {}
    good = _make_response(200, json_body={"ok": True})
    with (
        patch(
            "onyx.integrations.sharepoint_writer.graph_client.requests.request",
            side_effect=[bad, good],
        ),
        patch("onyx.integrations.sharepoint_writer.graph_client.time.sleep"),
    ):
        result = graph_get(
            "https://graph.microsoft.com/v1.0/sites", _FakeTokenProvider()
        )
        assert result == {"ok": True}


def test_graph_get_all_pages_walks_odata_next_link() -> None:
    page1 = _make_response(
        200,
        json_body={
            "value": [{"id": "1"}, {"id": "2"}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/sites?$skiptoken=next",
        },
    )
    page2 = _make_response(200, json_body={"value": [{"id": "3"}]})
    with patch(
        "onyx.integrations.sharepoint_writer.graph_client.requests.request",
        side_effect=[page1, page2],
    ):
        items = graph_get_all_pages(
            "https://graph.microsoft.com/v1.0/sites", _FakeTokenProvider()
        )
        assert [i["id"] for i in items] == ["1", "2", "3"]


def test_graph_get_all_pages_respects_max_pages() -> None:
    """Defensive cap — don't infinite-loop on a misbehaving server."""
    looping = _make_response(
        200,
        json_body={
            "value": [{"id": "x"}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/sites?$skiptoken=again",
        },
    )
    with patch(
        "onyx.integrations.sharepoint_writer.graph_client.requests.request",
        return_value=looping,
    ):
        items = graph_get_all_pages(
            "https://graph.microsoft.com/v1.0/sites",
            _FakeTokenProvider(),
            max_pages=3,
        )
        assert len(items) == 3


def test_graph_put_bytes_sends_payload_and_content_type() -> None:
    fake_resp = _make_response(201, json_body={"id": "new-item"})
    with patch(
        "onyx.integrations.sharepoint_writer.graph_client.requests.request",
        return_value=fake_resp,
    ) as mock_req:
        result = graph_put_bytes(
            "https://graph.microsoft.com/v1.0/drives/d/items/f:/a.docx:/content",
            _FakeTokenProvider(),
            b"docx-bytes-here",
            content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
        )
        assert result == {"id": "new-item"}
        call_kwargs = mock_req.call_args.kwargs
        assert call_kwargs["data"] == b"docx-bytes-here"
        assert "wordprocessingml" in call_kwargs["headers"]["Content-Type"]


def test_graph_put_bytes_raises_on_403() -> None:
    fake_resp = _make_response(403, text="insufficient permissions")
    with patch(
        "onyx.integrations.sharepoint_writer.graph_client.requests.request",
        return_value=fake_resp,
    ):
        with pytest.raises(GraphClientError, match="403"):
            graph_put_bytes(
                "https://graph.microsoft.com/v1.0/drives/d/items/f:/a.docx:/content",
                _FakeTokenProvider(),
                b"x",
                content_type="application/octet-stream",
            )


class _FakeMsalApp:
    def __init__(self, *, token_response: dict[str, Any]) -> None:
        self._token_response = token_response
        self.calls = 0

    def acquire_token_for_client(
        self,
        scopes: list[str],  # noqa: ARG002
    ) -> dict[str, Any]:
        self.calls += 1
        return self._token_response


def test_token_provider_caches_token_until_expiry() -> None:
    from onyx.integrations.sharepoint_writer.models import SharePointAuthBundle

    bundle = SharePointAuthBundle(
        sp_client_id="id", sp_directory_id="tenant", sp_client_secret="shh"
    )
    fake_app = _FakeMsalApp(token_response={"access_token": "abc", "expires_in": 3600})
    with patch(
        "onyx.integrations.sharepoint_writer.graph_client._build_msal_app",
        return_value=fake_app,
    ):
        provider = GraphTokenProvider(bundle)
        assert provider.acquire() == "abc"
        assert provider.acquire() == "abc"
        assert fake_app.calls == 1  # cached, not re-acquired


def test_token_provider_raises_on_msal_failure() -> None:
    from onyx.integrations.sharepoint_writer.models import SharePointAuthBundle

    bundle = SharePointAuthBundle(
        sp_client_id="id", sp_directory_id="tenant", sp_client_secret="shh"
    )
    fake_app = _FakeMsalApp(
        token_response={"error": "invalid_client", "error_description": "bad secret"}
    )
    with patch(
        "onyx.integrations.sharepoint_writer.graph_client._build_msal_app",
        return_value=fake_app,
    ):
        provider = GraphTokenProvider(bundle)
        with pytest.raises(GraphClientError, match="bad secret"):
            provider.acquire()
