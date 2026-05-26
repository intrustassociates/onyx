from __future__ import annotations

import datetime

from pydantic import BaseModel


class SharePointActionConfigView(BaseModel):
    """Read shape of the tenant's Word/SharePoint action configuration."""

    id: int | None
    is_enabled: bool
    cc_pair_id: int | None
    write_scopes_available: bool
    detected_roles: list[str]
    last_scope_check_at: datetime.datetime | None
    allow_download_when_sp_available: bool
    template_sp_drive_id: str | None
    template_sp_item_id: str | None


class SharePointActionConfigUpdateRequest(BaseModel):
    is_enabled: bool | None = None
    cc_pair_id: int | None = None
    allow_download_when_sp_available: bool | None = None
    template_sp_drive_id: str | None = None
    template_sp_item_id: str | None = None
    clear_template: bool = False


class ScopeDetectionResponse(BaseModel):
    has_write: bool
    detected_roles: list[str]
    missing_scopes: list[str]


class SharePointCcPairOption(BaseModel):
    """Admin sees a dropdown of available SharePoint connector credential pairs."""

    cc_pair_id: int
    name: str


class SiteView(BaseModel):
    id: str
    display_name: str
    web_url: str


class DriveView(BaseModel):
    id: str
    name: str
    web_url: str | None = None


class FolderView(BaseModel):
    id: str
    name: str
    web_url: str | None = None
    can_write: bool


class FolderUploadRequest(BaseModel):
    file_id: str
    drive_id: str
    folder_id: str


class UploadResultView(BaseModel):
    item_id: str
    drive_id: str
    web_url: str
    filename: str


class ArtifactCapabilityView(BaseModel):
    """What the frontend needs to decide which artifact buttons to render.

    Pre-fetched by the chat UI when an artifact card appears, cached for the
    session. The lookup is cheap (single DB row) so we don't optimize further.
    """

    enabled: bool
    write_scopes_available: bool
    allow_download_when_sp_available: bool
