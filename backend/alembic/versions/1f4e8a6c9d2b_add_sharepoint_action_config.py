"""add sharepoint action config

Revision ID: 1f4e8a6c9d2b
Revises: d129f37b3d87
Create Date: 2026-05-26 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "1f4e8a6c9d2b"
down_revision = "d129f37b3d87"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Seed GenerateDocxTool in the built-in tools table
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT INTO tool (name, display_name, description, in_code_tool_id, enabled)
            VALUES (:name, :display_name, :description, :in_code_tool_id, :enabled)
            """
        ),
        {
            "name": "GenerateDocxTool",
            "display_name": "Word Document",
            "description": (
                "Generate Microsoft Word (.docx) documents from chat content. "
                "When SharePoint integration is configured, users can save the "
                "generated document directly to a SharePoint folder where they "
                "have write access."
            ),
            "in_code_tool_id": "GenerateDocxTool",
            "enabled": True,
        },
    )

    op.create_table(
        "sharepoint_action_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("cc_pair_id", sa.Integer(), nullable=True),
        sa.Column(
            "write_scopes_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("detected_roles", postgresql.JSONB(), nullable=True),
        sa.Column("last_scope_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "allow_download_when_sp_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("template_sp_drive_id", sa.String(), nullable=True),
        sa.Column("template_sp_item_id", sa.String(), nullable=True),
        sa.Column(
            "time_created",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "time_updated",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["cc_pair_id"],
            ["connector_credential_pair.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("sharepoint_action_config")
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM tool WHERE in_code_tool_id = :id"),
        {"id": "GenerateDocxTool"},
    )
