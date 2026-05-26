"""Upload generated .docx bytes to a SharePoint document library via Graph.

Reference:
- learn.microsoft.com/graph/api/driveitem-put-content (small files, < 4 MB)
- learn.microsoft.com/graph/api/driveitem-createuploadsession (chunked)
- learn.microsoft.com/graph/api/driveitem-put-content#instance-attributes
  (the ``@microsoft.graph.conflictBehavior`` query parameter)
"""

from __future__ import annotations

import re
from urllib.parse import quote

import requests

from onyx.configs.app_configs import REQUEST_TIMEOUT_SECONDS
from onyx.integrations.sharepoint_writer.graph_client import graph_post_json
from onyx.integrations.sharepoint_writer.graph_client import graph_put_bytes
from onyx.integrations.sharepoint_writer.graph_client import GraphClientError
from onyx.integrations.sharepoint_writer.graph_client import GraphTokenProvider
from onyx.integrations.sharepoint_writer.models import UploadResult
from onyx.utils.logger import setup_logger


logger = setup_logger()


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# Graph's documented threshold for the simple PUT upload path. Above this we
# must use createUploadSession + chunked PATCH.
SIMPLE_UPLOAD_MAX_BYTES = 4 * 1024 * 1024  # 4 MB
UPLOAD_CHUNK_BYTES = 5 * 1024 * 1024  # 5 MB — multiple of 320 KiB recommended


_FILENAME_SAFE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def sanitize_filename(name: str, *, default: str = "document") -> str:
    """Strip characters that SharePoint rejects in filenames.

    SharePoint disallows ``\\ / : * ? " < > |`` plus control characters. We
    also collapse repeated whitespace and trim length.
    """
    cleaned = _FILENAME_SAFE.sub(" ", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = default
    if not cleaned.lower().endswith(".docx"):
        cleaned = f"{cleaned}.docx"
    # SharePoint limits item names to 400 chars, but keep more conservative
    if len(cleaned) > 200:
        stem, _, ext = cleaned.rpartition(".")
        cleaned = f"{stem[:190]}.{ext}"
    return cleaned


def _simple_upload(
    token_provider: GraphTokenProvider,
    drive_id: str,
    folder_id: str,
    filename: str,
    content_bytes: bytes,
) -> dict:
    """PUT < 4 MB content directly. Auto-renames on conflict."""
    url = (
        f"{token_provider.graph_base}/drives/{drive_id}/items/"
        f"{quote(folder_id, safe='')}:/{quote(filename)}:/content"
        "?@microsoft.graph.conflictBehavior=rename"
    )
    return graph_put_bytes(url, token_provider, content_bytes, content_type=DOCX_MIME)


def _chunked_upload(
    token_provider: GraphTokenProvider,
    drive_id: str,
    folder_id: str,
    filename: str,
    content_bytes: bytes,
) -> dict:
    """Large-file path: open an upload session, then PUT byte ranges."""
    session_url = (
        f"{token_provider.graph_base}/drives/{drive_id}/items/"
        f"{quote(folder_id, safe='')}:/{quote(filename)}:/createUploadSession"
    )
    session_resp = graph_post_json(
        session_url,
        token_provider,
        body={
            "item": {
                "@microsoft.graph.conflictBehavior": "rename",
                "name": filename,
            }
        },
    )
    upload_url = session_resp.get("uploadUrl")
    if not upload_url:
        raise GraphClientError(
            "createUploadSession returned no uploadUrl: " + str(session_resp)
        )

    total = len(content_bytes)
    offset = 0
    last_response: dict = {}
    while offset < total:
        end = min(offset + UPLOAD_CHUNK_BYTES, total) - 1
        chunk = content_bytes[offset : end + 1]
        headers = {
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {offset}-{end}/{total}",
        }
        # The session uploadUrl is pre-authenticated — no bearer header.
        resp = requests.put(
            upload_url,
            headers=headers,
            data=chunk,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if resp.status_code not in (200, 201, 202):
            raise GraphClientError(
                f"Upload chunk {offset}-{end} -> {resp.status_code}: {resp.text[:300]}"
            )
        if resp.content:
            try:
                last_response = resp.json()
            except ValueError:
                last_response = {}
        offset = end + 1

    return last_response


def upload_docx(
    token_provider: GraphTokenProvider,
    drive_id: str,
    folder_id: str,
    filename: str,
    content_bytes: bytes,
) -> UploadResult:
    """Upload a .docx to ``{drive_id}/{folder_id}/{filename}``.

    Picks the simple PUT path for small files and the chunked upload session
    for files at or above ``SIMPLE_UPLOAD_MAX_BYTES``. Conflicts are resolved
    by rename (Graph appends ``" (1)"``, ``" (2)"``, ...).
    """
    safe_name = sanitize_filename(filename)
    if len(content_bytes) < SIMPLE_UPLOAD_MAX_BYTES:
        payload = _simple_upload(
            token_provider, drive_id, folder_id, safe_name, content_bytes
        )
    else:
        payload = _chunked_upload(
            token_provider, drive_id, folder_id, safe_name, content_bytes
        )

    item_id = payload.get("id")
    web_url = payload.get("webUrl")
    final_name = payload.get("name", safe_name)
    if not item_id or not web_url:
        raise GraphClientError(
            f"Upload succeeded but response missing id/webUrl: {payload}"
        )

    return UploadResult(
        item_id=item_id,
        drive_id=drive_id,
        web_url=web_url,
        filename=final_name,
    )


def download_item_bytes(
    token_provider: GraphTokenProvider, drive_id: str, item_id: str
) -> bytes:
    """Download a drive item's raw bytes (used to fetch SharePoint-hosted
    reference templates for Pandoc)."""
    url = (
        f"{token_provider.graph_base}/drives/{drive_id}/items/"
        f"{quote(item_id, safe='')}/content"
    )
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token_provider.acquire()}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
        # Graph 302s to a pre-authed CDN URL for content
        allow_redirects=True,
    )
    if not resp.ok:
        raise GraphClientError(
            f"Graph GET content {url} -> {resp.status_code}: {resp.text[:300]}"
        )
    return resp.content
