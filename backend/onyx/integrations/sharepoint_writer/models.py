from __future__ import annotations

from pydantic import BaseModel


class ScopeStatus(BaseModel):
    """Result of detecting which Microsoft Graph application permissions are
    consented to the SharePoint app registration backing the Onyx connector."""

    has_write: bool
    detected_roles: list[str]
    missing_scopes: list[str]


class SiteRef(BaseModel):
    id: str
    display_name: str
    web_url: str


class DriveRef(BaseModel):
    id: str
    name: str
    web_url: str | None = None


class FolderRef(BaseModel):
    id: str
    name: str
    web_url: str | None = None
    parent_path: str | None = None
    can_write: bool = False


class UploadResult(BaseModel):
    item_id: str
    drive_id: str
    web_url: str
    filename: str


class SharePointAuthBundle(BaseModel):
    """Decoded auth material from a SharePoint connector Credential row.

    Field names mirror what the existing connector stores in the credential JSON
    (see ``backend/onyx/connectors/sharepoint/connector.py:load_credentials``)
    so a writer can reuse the same row without translation.
    """

    sp_client_id: str
    sp_directory_id: str
    authentication_method: str = "client_secret"
    sp_client_secret: str | None = None
    sp_private_key: str | None = None
    sp_certificate_password: str | None = None
    graph_api_host: str = "https://graph.microsoft.com"
    authority_host: str = "https://login.microsoftonline.com"
