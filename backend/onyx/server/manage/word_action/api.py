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
from onyx.db.file_record import get_filerecord_by_file_id_optional
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
from onyx.integrations.sharepoint_writer.permission_check import get_user_group_ids
from onyx.integrations.sharepoint_writer.permission_check import user_has_write_access
from onyx.integrations.sharepoint_writer.scope_detection import detect_write_scopes
from onyx.integrations.sharepoint_writer.site_browser import list_drives
from onyx.integrations.sharepoint_writer.site_browser import list_folders
from onyx.integrations.sharepoint_writer.site_browser import list_sites
from onyx.integrations.sharepoint_writer.uploader import upload_docx
from onyx.server.manage.word_action.models import ArtifactCapabilityView
from onyx.server.manage.word_action.models import ArtifactStatusView
from onyx.server.manage.word_action.models import DriveView
from onyx.server.manage.word_action.models import FolderUploadRequest
from onyx.server.manage.word_action.models import FolderView
from onyx.server.manage.word_action.models import ScopeDetectionResponse
from onyx.server.manage.word_action.models import SharePointActionConfigUpdateRequest
from onyx.server.manage.word_action.models import SharePointActionConfigView
from onyx.server.manage.word_action.models import SharePointCcPairOption
from onyx.server.manage.word_action.models import SiteView
from onyx.server.manage.word_action.models import UploadResultView
from onyx.tools.tool_implementations.docx.generate_docx_tool import (
    ARTIFACT_SAVED_URL_KEY,
)
from onyx.tools.tool_implementations.docx.generate_docx_tool import (
    is_docx_artifact_owned_by,
)
from onyx.utils.threadpool_concurrency import run_functions_tuples_in_parallel


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
    if (
        cc_pair is None
        or cc_pair.credential is None
        or cc_pair.credential.credential_json is None
    ):
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

    if not folders:
        return []

    # Warm the user-identity caches once so the parallel per-folder checks
    # below only fetch item permissions.
    try:
        get_user_group_ids(token_provider, user_email)
    except GraphClientError:
        pass  # each per-folder check degrades to can_write=False on its own
    can_writes: list[bool] = run_functions_tuples_in_parallel(
        [
            (user_has_write_access, (token_provider, user_email, drive_id, folder.id))
            for folder in folders
        ],
        max_workers=8,
    )
    return [
        FolderView(
            id=folder.id,
            name=folder.name,
            web_url=folder.web_url,
            can_write=can_write,
        )
        for folder, can_write in zip(folders, can_writes)
    ]


@user_router.get("/artifact/{file_id}", response_model=ArtifactStatusView)
def get_artifact_status(
    file_id: str,
    user: User = Depends(require_permission(Permission.BASIC_ACCESS)),
    db_session: Session = Depends(get_session),
) -> ArtifactStatusView:
    """Lets the chat card restore its state after a reload (saved vs pending).

    Reports ``exists=False`` for records the user doesn't own so the endpoint
    can't be used to probe which file ids exist.
    """
    record = get_filerecord_by_file_id_optional(file_id=file_id, db_session=db_session)
    if record is None or not is_docx_artifact_owned_by(
        record.file_metadata, record.file_type, str(user.id)
    ):
        return ArtifactStatusView(exists=False, saved_web_url=None)
    metadata = (
        dict(record.file_metadata) if isinstance(record.file_metadata, dict) else {}
    )
    saved_url = metadata.get(ARTIFACT_SAVED_URL_KEY)
    return ArtifactStatusView(
        exists=True, saved_web_url=saved_url if isinstance(saved_url, str) else None
    )


@user_router.post("/upload", response_model=UploadResultView)
def upload_to_sharepoint(
    payload: FolderUploadRequest,
    user: User = Depends(require_permission(Permission.BASIC_ACCESS)),
    db_session: Session = Depends(get_session),
) -> UploadResultView:
    user_email = _require_user_email(user)
    token_provider = _get_token_provider_for_active_config(db_session)

    # Only docx artifacts generated by this user's generate_docx calls may be
    # uploaded. 404 (not 403) for both missing and not-owned records, so the
    # endpoint doesn't reveal which file ids exist in the store.
    record = get_filerecord_by_file_id_optional(
        file_id=payload.file_id, db_session=db_session
    )
    if record is None or not is_docx_artifact_owned_by(
        record.file_metadata, record.file_type, str(user.id)
    ):
        raise HTTPException(status_code=404, detail="No such generated document.")

    metadata = (
        dict(record.file_metadata) if isinstance(record.file_metadata, dict) else {}
    )
    if metadata.get(ARTIFACT_SAVED_URL_KEY):
        raise HTTPException(
            status_code=409,
            detail="This document was already saved to SharePoint.",
        )

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
        body_io = file_store.read_file(payload.file_id, mode="b")
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail=f"File not found in file store: {exc}"
        ) from exc

    content_bytes = body_io.read() if hasattr(body_io, "read") else bytes(body_io)
    filename = record.display_name or "document.docx"

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
    # will index it back into Onyx. Keep the file_store entry but mark it as
    # saved — the chat card uses this (via /artifact/{file_id}) to restore the
    # "Saved to SharePoint" state after a page reload, and the 409 above stops
    # duplicate uploads.
    metadata[ARTIFACT_SAVED_URL_KEY] = result.web_url
    record.file_metadata = metadata
    db_session.commit()

    return UploadResultView(
        item_id=result.item_id,
        drive_id=result.drive_id,
        web_url=result.web_url,
        filename=result.filename,
    )
