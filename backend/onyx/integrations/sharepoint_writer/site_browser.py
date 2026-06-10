"""Microsoft Graph helpers for browsing SharePoint sites, drives, and folders.

All endpoints used here are documented at https://learn.microsoft.com/graph.
"""

from __future__ import annotations

from urllib.parse import quote

from onyx.integrations.sharepoint_writer.graph_client import graph_get
from onyx.integrations.sharepoint_writer.graph_client import graph_get_all_pages
from onyx.integrations.sharepoint_writer.graph_client import GraphTokenProvider
from onyx.integrations.sharepoint_writer.models import DriveRef
from onyx.integrations.sharepoint_writer.models import FolderRef
from onyx.integrations.sharepoint_writer.models import SiteRef


# learn.microsoft.com/graph/api/site-search
def list_sites(
    token_provider: GraphTokenProvider, *, query: str = "*"
) -> list[SiteRef]:
    url = f"{token_provider.graph_base}/sites"
    params = {"search": query, "$select": "id,displayName,name,webUrl", "$top": "200"}
    items = graph_get_all_pages(url, token_provider, params=params, max_pages=10)
    sites: list[SiteRef] = []
    for raw in items:
        site_id = raw.get("id")
        if not site_id:
            continue
        sites.append(
            SiteRef(
                id=site_id,
                display_name=raw.get("displayName") or raw.get("name") or site_id,
                web_url=raw.get("webUrl", ""),
            )
        )
    return sites


# learn.microsoft.com/graph/api/drive-list
def list_drives(token_provider: GraphTokenProvider, site_id: str) -> list[DriveRef]:
    url = f"{token_provider.graph_base}/sites/{site_id}/drives"
    params = {"$select": "id,name,webUrl"}
    payload = graph_get(url, token_provider, params=params)
    drives: list[DriveRef] = []
    for raw in payload.get("value", []):
        drive_id = raw.get("id")
        if not drive_id:
            continue
        drives.append(
            DriveRef(
                id=drive_id,
                name=raw.get("name") or drive_id,
                web_url=raw.get("webUrl"),
            )
        )
    return drives


# learn.microsoft.com/graph/api/driveitem-list-children
def list_folders(
    token_provider: GraphTokenProvider,
    drive_id: str,
    *,
    parent_id: str = "root",
) -> list[FolderRef]:
    """List subfolders of ``parent_id`` inside ``drive_id``. Files filtered out."""
    if parent_id == "root":
        url = f"{token_provider.graph_base}/drives/{drive_id}/root/children"
    else:
        url = (
            f"{token_provider.graph_base}/drives/{drive_id}/items/"
            f"{quote(parent_id, safe='')}/children"
        )
    # No ``$filter=folder ne null`` here: Graph frequently rejects $filter on
    # driveItem facet properties with 400. Files are filtered out client-side
    # below instead.
    params = {
        "$select": "id,name,webUrl,folder,parentReference",
        "$top": "200",
    }
    items = graph_get_all_pages(url, token_provider, params=params, max_pages=10)
    folders: list[FolderRef] = []
    for raw in items:
        if "folder" not in raw:
            continue
        item_id = raw.get("id")
        if not item_id:
            continue
        parent_ref = raw.get("parentReference") or {}
        folders.append(
            FolderRef(
                id=item_id,
                name=raw.get("name", item_id),
                web_url=raw.get("webUrl"),
                parent_path=parent_ref.get("path"),
            )
        )
    return folders


def get_item(token_provider: GraphTokenProvider, drive_id: str, item_id: str) -> dict:
    """Fetch a drive item by id (used to resolve the root id of a drive, etc.)."""
    url = (
        f"{token_provider.graph_base}/drives/{drive_id}/items/"
        f"{quote(item_id, safe='')}"
    )
    return graph_get(url, token_provider)


def get_drive_root_id(token_provider: GraphTokenProvider, drive_id: str) -> str:
    """Resolve the literal item id of a drive's root folder.

    Some callers (permission_check, uploader) need the root's real item id
    rather than the alias ``"root"``.
    """
    url = f"{token_provider.graph_base}/drives/{drive_id}/root"
    payload = graph_get(url, token_provider, params={"$select": "id"})
    root_id = payload.get("id")
    if not root_id:
        raise RuntimeError(f"Drive {drive_id} returned no root id")
    return str(root_id)
