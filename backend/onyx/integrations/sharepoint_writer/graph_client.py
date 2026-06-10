"""Clean-room Microsoft Graph client wrapper for the Word action.

Reuses the MSAL token-acquire pattern documented at
https://learn.microsoft.com/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow
but exposes a small surface (token + GET/PUT helpers) tailored to the writer
side of the SharePoint integration. Independent implementation — no code copied
from the connector or the EE permission utils.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from datetime import datetime
from datetime import timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any

import msal
import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from onyx.configs.app_configs import REQUEST_TIMEOUT_SECONDS
from onyx.integrations.sharepoint_writer.models import SharePointAuthBundle
from onyx.utils.logger import setup_logger


logger = setup_logger()

GRAPH_API_MAX_RETRIES = 5
GRAPH_API_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class GraphClientError(RuntimeError):
    """Raised when Graph returns a non-retryable error or retries are exhausted."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _parse_retry_after(value: str | None) -> float | None:
    """Retry-After may be delta-seconds or an HTTP-date (RFC 9110)."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())


def _build_msal_app(bundle: SharePointAuthBundle) -> msal.ConfidentialClientApplication:
    authority_url = f"{bundle.authority_host}/{bundle.sp_directory_id}"

    if bundle.authentication_method == "certificate":
        if not bundle.sp_private_key or not bundle.sp_certificate_password:
            raise GraphClientError(
                "Certificate auth requires both sp_private_key and sp_certificate_password"
            )
        pfx_data = base64.b64decode(bundle.sp_private_key)
        private_key, certificate, _additional = pkcs12.load_key_and_certificates(
            pfx_data, bundle.sp_certificate_password.encode("utf-8")
        )
        if certificate is None or private_key is None:
            raise GraphClientError("Failed to load certificate / private key from PFX")
        key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        client_credential: dict[str, Any] = {
            "private_key": key_pem.decode("utf-8"),
            "thumbprint": certificate.fingerprint(hashes.SHA1()).hex(),
        }
        return msal.ConfidentialClientApplication(
            authority=authority_url,
            client_id=bundle.sp_client_id,
            client_credential=client_credential,
        )

    if not bundle.sp_client_secret:
        raise GraphClientError("Client-secret auth requires sp_client_secret")
    return msal.ConfidentialClientApplication(
        authority=authority_url,
        client_id=bundle.sp_client_id,
        client_credential=bundle.sp_client_secret,
    )


def auth_bundle_from_credential_json(creds: dict[str, Any]) -> SharePointAuthBundle:
    """Decode the dict shape persisted by the SharePoint connector into a typed bundle."""
    return SharePointAuthBundle(
        sp_client_id=creds["sp_client_id"],
        sp_directory_id=creds["sp_directory_id"],
        authentication_method=creds.get("authentication_method", "client_secret"),
        sp_client_secret=creds.get("sp_client_secret"),
        sp_private_key=creds.get("sp_private_key"),
        sp_certificate_password=creds.get("sp_certificate_password"),
        graph_api_host=creds.get("graph_api_host", "https://graph.microsoft.com"),
        authority_host=creds.get("authority_host", "https://login.microsoftonline.com"),
    )


class GraphTokenProvider:
    """Acquires and caches the app-only access token for Microsoft Graph."""

    def __init__(self, bundle: SharePointAuthBundle):
        self._bundle = bundle
        self._app = _build_msal_app(bundle)
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0
        # acquire() is called from parallel permission checks
        self._token_lock = Lock()

    @property
    def graph_host(self) -> str:
        return self._bundle.graph_api_host.rstrip("/")

    @property
    def graph_base(self) -> str:
        return f"{self.graph_host}/v1.0"

    def acquire(self) -> str:
        with self._token_lock:
            now = time.time()
            # Refresh 60s before actual expiry to avoid mid-request expiration.
            if self._cached_token and now < self._token_expires_at - 60:
                return self._cached_token

            result = self._app.acquire_token_for_client(
                scopes=[f"{self.graph_host}/.default"]
            )
            if not result or "access_token" not in result:
                error = (result or {}).get("error_description", "no token returned")
                raise GraphClientError(f"MSAL failed to acquire Graph token: {error}")

            self._cached_token = result["access_token"]
            # MSAL gives expires_in in seconds; default 3300 (~55min) if missing.
            self._token_expires_at = now + int(result.get("expires_in", 3300))
            return self._cached_token


def _request_with_retry(
    method: str,
    url: str,
    token_provider: GraphTokenProvider,
    *,
    params: dict[str, Any] | None = None,
    data: bytes | None = None,
    json_body: Any | None = None,
    content_type: str | None = None,
) -> requests.Response:
    for attempt in range(GRAPH_API_MAX_RETRIES + 1):
        headers: dict[str, str] = {
            "Authorization": f"Bearer {token_provider.acquire()}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                data=data,
                json=json_body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= GRAPH_API_MAX_RETRIES:
                raise GraphClientError(
                    f"Graph {method} {url} failed after {attempt + 1} attempts: {exc}"
                ) from exc
            wait = min(2**attempt, 60)
            logger.warning(
                "Graph %s connection error (attempt %s/%s), retrying in %ss",
                method,
                attempt + 1,
                GRAPH_API_MAX_RETRIES + 1,
                wait,
            )
            time.sleep(wait)
            continue

        if (
            resp.status_code in GRAPH_API_RETRYABLE_STATUSES
            and attempt < GRAPH_API_MAX_RETRIES
        ):
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            wait_s = (
                min(retry_after, 60.0)
                if retry_after is not None
                else float(min(2**attempt, 60))
            )
            logger.warning(
                "Graph %s %s returned %s, retrying in %ss",
                method,
                url,
                resp.status_code,
                wait_s,
            )
            time.sleep(wait_s)
            continue

        return resp

    raise GraphClientError(
        f"Graph {method} {url} exhausted {GRAPH_API_MAX_RETRIES + 1} attempts"
    )


def graph_get(
    url: str,
    token_provider: GraphTokenProvider,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resp = _request_with_retry("GET", url, token_provider, params=params)
    if not resp.ok:
        raise GraphClientError(
            f"Graph GET {url} -> {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )
    return resp.json()


def graph_get_all_pages(
    url: str,
    token_provider: GraphTokenProvider,
    *,
    params: dict[str, Any] | None = None,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    """Walk @odata.nextLink to gather a full collection. Capped to avoid runaways."""
    items: list[dict[str, Any]] = []
    next_url: str | None = url
    next_params = params
    pages = 0
    while next_url and pages < max_pages:
        payload = graph_get(next_url, token_provider, params=next_params)
        items.extend(payload.get("value", []))
        next_url = payload.get("@odata.nextLink")
        # nextLink already encodes all query params
        next_params = None
        pages += 1
    return items


def graph_put_bytes(
    url: str,
    token_provider: GraphTokenProvider,
    data: bytes,
    *,
    content_type: str,
) -> dict[str, Any]:
    resp = _request_with_retry(
        "PUT", url, token_provider, data=data, content_type=content_type
    )
    if not resp.ok:
        raise GraphClientError(
            f"Graph PUT {url} -> {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )
    return resp.json()


def graph_post_json(
    url: str,
    token_provider: GraphTokenProvider,
    body: Any,
) -> dict[str, Any]:
    resp = _request_with_retry(
        "POST",
        url,
        token_provider,
        json_body=body,
        content_type="application/json",
    )
    if not resp.ok:
        raise GraphClientError(
            f"Graph POST {url} -> {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )
    return resp.json() if resp.content else {}


# Convenience type alias for tests that want to inject a fake token source
TokenAcquirer = Callable[[], str]
