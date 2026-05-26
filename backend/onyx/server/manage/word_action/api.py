"""Admin + end-user APIs for the Word generation action and its SharePoint
folder picker. Admin routes are gated by ``FULL_ADMIN_PANEL_ACCESS``; end-user
routes only require ``BASIC_ACCESS`` plus a configured + enabled action.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from onyx.auth.permissions import require_permission
from onyx.configs.constants import DocumentSource
from onyx.db.connector_credential_pair import get_connector_credential_pair_from_id
from onyx.db.engine.sql_engine import get_session
from onyx.db.enums import Permission
from onyx.db.models import Connector
from onyx.db.models import ConnectorCredentialPair
from onyx.db.models import User
from onyx.db.word_action_config import get_or_create_sharepoint_action_config
from onyx.db.word_action_config import get_sharepoint_action_config
from onyx.db.word_action_config import record_scope_detection
from onyx.db.word_action_config import update_sharepoint_action_config
from onyx.file_store.utils import get_default_file_store
from onyx.integrations.sharepoint_writer.graph_client import (
    auth_bundle_from_credential_json,
)
from onyx.integrations.sharepoint_writer.graph_client import GraphClientError
from onyx.integrations.sharepoint_writer.graph_client import GraphTokenProvider
from onyx.integrations.sharepoint_writer.permission_check import user_has_write_access
from onyx.integrations.sharepoint_writer.scope_detection import detect_write_scopes
from onyx.integrations.sharepoint_writer.site_browser import list_drives
from onyx.integrations.sharepoint_writer.site_browser import list_folders
from onyx.integrations.sharepoint_writer.site_browser import list_sites
from onyx.integrations.sharepoint_writer.uploader import upload_docx
from onyx.server.manage.word_action.models import ArtifactCapabilityView
from onyx.server.manage.word_action.models import DriveView
from onyx.server.manage.word_action.models import FolderUploadRequest
from onyx.server.manage.word_action.models import FolderView
from onyx.server.manage.word_action.models import ScopeDetectionResponse
from onyx.server.manage.word_action.models import SharePointActionConfigUpdateRequest
from onyx.server.manage.word_action.models import SharePointActionConfigView
from onyx.server.manage.word_action.models import SharePointCcPairOption
from onyx.server.manage.word_action.models import SiteView
from onyx.server.manage.word_action.models import UploadResultView
from onyx.utils.logger import setup_logger


logger = setup_logger()


admin_router = APIRouter(prefix="/admin/word-action")
user_router = APIRouter(prefix="/sharepoint-action")


def _view_from_config_orm(config) -> SharePointActionConfigView:
    return SharePointActionConfigView(
        id=config.id,
        is_enabled=config.is_enabled,
        cc_pair_id=config.cc_pair_id,
        write_scopes_available=config.write_scopes_available,
        detected_roles=list(config.detected_roles or []),
        last_scope_check_at=config.last_scope_check_at,
        allow_download_when_sp_available=config.allow_download_when_sp_available,
        template_sp_drive_id=config.template_sp_drive_id,
        template_sp_item_id=config.template_sp_item_id,
    )


def _build_token_provider_or_400(
    db_session: Session,
    cc_pair_id: int | None,
) -> GraphTokenProvider:
    if cc_pair_id is None:
        raise HTTPException(
            status_code=400,
            detail="No SharePoint connector credential pair selected.",
        )
    cc_pair = get_connector_credential_pair_from_id(
        db_session=db_session, cc_pair_id=cc_pair_id, eager_load_credential=True
    )
    if cc_pair is None or cc_pair.credential is None:
        raise HTTPException(
            status_code=400, detail="SharePoint credential pair not found."
        )
    credential_json = cc_pair.credential.credential_json.get_value(apply_mask=False)
    if not isinstance(credential_json, dict):
        raise HTTPException(
            status_code=400, detail="SharePoint credential is malformed."
        )
    try:
        bundle = auth_bundle_from_credential_json(credential_json)
        return GraphTokenProvider(bundle)
    except GraphClientError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not initialize Graph client: {exc}",
        ) from exc


def _get_token_provider_for_active_config(
    db_session: Session,
) -> GraphTokenProvider:
    config = get_sharepoint_action_config(db_session)
    if config is None or not config.is_enabled:
        raise HTTPException(status_code=400, detail="Word action is not enabled.")
    if not config.write_scopes_available:
        raise HTTPException(
            status_code=400,
            detail=(
                "SharePoint write scopes are not consented for this app. Ask your "
                "M365 admin to grant Sites.ReadWrite.All or Files.ReadWrite.All."
            ),
        )
    return _build_token_provider_or_400(db_session, config.cc_pair_id)


def _require_user_email(user: User) -> str:
    if not user.email:
        raise HTTPException(
            status_code=400,
            detail="Your Onyx account has no email; cannot resolve SharePoint identity.",
        )
    return user.email


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@admin_router.get("", response_model=SharePointActionConfigView)
def get_config(
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> SharePointActionConfigView:
    config = get_or_create_sharepoint_action_config(db_session)
    db_session.commit()
    return _view_from_config_orm(config)


@admin_router.put("", response_model=SharePointActionConfigView)
def update_config(
    payload: SharePointActionConfigUpdateRequest,
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> SharePointActionConfigView:
    config = update_sharepoint_action_config(
        is_enabled=payload.is_enabled,
        cc_pair_id=payload.cc_pair_id,
        allow_download_when_sp_available=payload.allow_download_when_sp_available,
        template_sp_drive_id=payload.template_sp_drive_id,
        template_sp_item_id=payload.template_sp_item_id,
        clear_template=payload.clear_template,
        db_session=db_session,
    )
    db_session.commit()
    return _view_from_config_orm(config)


@admin_router.get("/cc-pairs", response_model=list[SharePointCcPairOption])
def list_available_cc_pairs(
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> list[SharePointCcPairOption]:
    stmt = (
        select(ConnectorCredentialPair)
        .join(ConnectorCredentialPair.connector)
        .where(Connector.source == DocumentSource.SHAREPOINT.value)
        .order_by(ConnectorCredentialPair.id.asc())
    )
    pairs = list(db_session.scalars(stmt).unique().all())
    return [
        SharePointCcPairOption(cc_pair_id=p.id, name=p.name or f"SharePoint #{p.id}")
        for p in pairs
    ]


@admin_router.post("/detect-scopes", response_model=ScopeDetectionResponse)
def detect_scopes(
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> ScopeDetectionResponse:
    config = get_or_create_sharepoint_action_config(db_session)
    token_provider = _build_token_provider_or_400(db_session, config.cc_pair_id)
    try:
        status = detect_write_scopes(token_provider)
    except GraphClientError as exc:
        raise HTTPException(
            status_code=400, detail=f"Failed to acquire Graph token: {exc}"
        ) from exc

    record_scope_detection(
        write_scopes_available=status.has_write,
        detected_roles=status.detected_roles,
        db_session=db_session,
    )
    db_session.commit()
    return ScopeDetectionResponse(
        has_write=status.has_write,
        detected_roles=status.detected_roles,
        missing_scopes=status.missing_scopes,
    )


# ---------------------------------------------------------------------------
# End-user endpoints (chat-time folder picker + upload)
# ---------------------------------------------------------------------------


@user_router.get("/capabilities", response_model=ArtifactCapabilityView)
def get_capabilities(
    _: User = Depends(require_permission(Permission.BASIC_ACCESS)),
    db_session: Session = Depends(get_session),
) -> ArtifactCapabilityView:
    config = get_sharepoint_action_config(db_session)
    if config is None:
        return ArtifactCapabilityView(
            enabled=False,
            write_scopes_available=False,
            allow_download_when_sp_available=True,
        )
    return ArtifactCapabilityView(
        enabled=bool(config.is_enabled and config.cc_pair_id is not None),
        write_scopes_available=config.write_scopes_available,
        allow_download_when_sp_available=config.allow_download_when_sp_available,
    )


@user_router.get("/sites", response_model=list[SiteView])
def get_sites(
    _: User = Depends(require_permission(Permission.BASIC_ACCESS)),
    db_session: Session = Depends(get_session),
) -> list[SiteView]:
    token_provider = _get_token_provider_for_active_config(db_session)
    try:
        sites = list_sites(token_provider)
    except GraphClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"SharePoint listing failed: {exc}"
        ) from exc
    return [
        SiteView(id=s.id, display_name=s.display_name, web_url=s.web_url) for s in sites
    ]


@user_router.get("/sites/{site_id}/drives", response_model=list[DriveView])
def get_drives(
    site_id: str,
    _: User = Depends(require_permission(Permission.BASIC_ACCESS)),
    db_session: Session = Depends(get_session),
) -> list[DriveView]:
    token_provider = _get_token_provider_for_active_config(db_session)
    try:
        drives = list_drives(token_provider, site_id=site_id)
    except GraphClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"SharePoint listing failed: {exc}"
        ) from exc
    return [DriveView(id=d.id, name=d.name, web_url=d.web_url) for d in drives]


@user_router.get("/drives/{drive_id}/folders", response_model=list[FolderView])
def get_folders(
    drive_id: str,
    parent_id: str = "root",
    user: User = Depends(require_permission(Permission.BASIC_ACCESS)),
    db_session: Session = Depends(get_session),
) -> list[FolderView]:
    user_email = _require_user_email(user)
    token_provider = _get_token_provider_for_active_config(db_session)
    try:
        folders = list_folders(token_provider, drive_id=drive_id, parent_id=parent_id)
    except GraphClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"SharePoint listing failed: {exc}"
        ) from exc

    results: list[FolderView] = []
    for folder in folders:
        can_write = user_has_write_access(
            token_provider,
            user_email=user_email,
            drive_id=drive_id,
            item_id=folder.id,
        )
        results.append(
            FolderView(
                id=folder.id,
                name=folder.name,
                web_url=folder.web_url,
                can_write=can_write,
            )
        )
    return results


@user_router.post("/upload", response_model=UploadResultView)
def upload_to_sharepoint(
    payload: FolderUploadRequest,
    user: User = Depends(require_permission(Permission.BASIC_ACCESS)),
    db_session: Session = Depends(get_session),
) -> UploadResultView:
    user_email = _require_user_email(user)
    token_provider = _get_token_provider_for_active_config(db_session)

    # Defense in depth: even though the picker filters folders, re-validate
    # write permission here so a malicious client can't bypass via direct API.
    if not user_has_write_access(
        token_provider,
        user_email=user_email,
        drive_id=payload.drive_id,
        item_id=payload.folder_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="You don't have write permission for the selected folder.",
        )

    file_store = get_default_file_store()
    try:
        file_record = file_store.read_file_record(payload.file_id)
        body_io = file_store.read_file(payload.file_id, mode="b")
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail=f"File not found in file store: {exc}"
        ) from exc

    content_bytes = body_io.read() if hasattr(body_io, "read") else bytes(body_io)
    filename = file_record.display_name or "document.docx"

    try:
        result = upload_docx(
            token_provider,
            drive_id=payload.drive_id,
            folder_id=payload.folder_id,
            filename=filename,
            content_bytes=content_bytes,
        )
    except GraphClientError as exc:
        raise HTTPException(
            status_code=502, detail=f"SharePoint upload failed: {exc}"
        ) from exc

    # The .docx lives canonically in SharePoint now; the next ingestion cycle
    # will index it back into Onyx. Clean up the file_store entry to avoid
    # double storage. Best-effort — if cleanup fails, the file is harmless.
    try:
        file_store.delete_file(payload.file_id)
    except Exception as exc:
        logger.warning("Could not delete file_store entry %s: %s", payload.file_id, exc)

    return UploadResultView(
        item_id=result.item_id,
        drive_id=result.drive_id,
        web_url=result.web_url,
        filename=result.filename,
    )
