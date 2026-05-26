from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from onyx.db.models import SharePointActionConfig


def get_sharepoint_action_config(
    db_session: Session,
) -> SharePointActionConfig | None:
    """Single-row config per tenant. Returns the lowest-id row if multiple exist."""
    stmt = select(SharePointActionConfig).order_by(SharePointActionConfig.id.asc())
    return db_session.scalars(stmt).first()


def get_or_create_sharepoint_action_config(
    db_session: Session,
) -> SharePointActionConfig:
    config = get_sharepoint_action_config(db_session)
    if config is None:
        config = SharePointActionConfig()
        db_session.add(config)
        db_session.flush()
    return config


def update_sharepoint_action_config(
    *,
    is_enabled: bool | None = None,
    cc_pair_id: int | None = None,
    allow_download_when_sp_available: bool | None = None,
    template_sp_drive_id: str | None = None,
    template_sp_item_id: str | None = None,
    clear_template: bool = False,
    db_session: Session,
) -> SharePointActionConfig:
    config = get_or_create_sharepoint_action_config(db_session)

    if is_enabled is not None:
        config.is_enabled = is_enabled
    if cc_pair_id is not None:
        config.cc_pair_id = cc_pair_id
        # Invalidate scope detection — credentials may have changed
        config.write_scopes_available = False
        config.detected_roles = None
        config.last_scope_check_at = None
    if allow_download_when_sp_available is not None:
        config.allow_download_when_sp_available = allow_download_when_sp_available
    if clear_template:
        config.template_sp_drive_id = None
        config.template_sp_item_id = None
    else:
        if template_sp_drive_id is not None:
            config.template_sp_drive_id = template_sp_drive_id
        if template_sp_item_id is not None:
            config.template_sp_item_id = template_sp_item_id

    db_session.flush()
    db_session.refresh(config)
    return config


def record_scope_detection(
    *,
    write_scopes_available: bool,
    detected_roles: list[str],
    db_session: Session,
) -> SharePointActionConfig:
    config = get_or_create_sharepoint_action_config(db_session)
    config.write_scopes_available = write_scopes_available
    config.detected_roles = detected_roles
    config.last_scope_check_at = datetime.datetime.now(datetime.timezone.utc)
    db_session.flush()
    db_session.refresh(config)
    return config
