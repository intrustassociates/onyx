"""attach generate_docx tool to default persona

Revision ID: 8e21c4f0b7d3
Revises: 3c9d2f8e7a51
Create Date: 2026-06-12

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8e21c4f0b7d3"
down_revision = "3c9d2f8e7a51"
branch_labels = None
depends_on = None

GENERATE_DOCX_TOOL = {
    "name": "generate_docx",
    "display_name": "Word Document",
    "description": (
        "Generate Microsoft Word (.docx) documents from Markdown content. "
        "Documents can be saved directly to SharePoint or downloaded."
    ),
    "in_code_tool_id": "GenerateDocxTool",
    "enabled": True,
}


def upgrade() -> None:
    conn = op.get_bind()

    existing = conn.execute(
        sa.text("SELECT id FROM tool WHERE in_code_tool_id = :in_code_tool_id"),
        {"in_code_tool_id": GENERATE_DOCX_TOOL["in_code_tool_id"]},
    ).fetchone()

    if existing:
        conn.execute(
            sa.text(
                """
                UPDATE tool
                SET name = :name,
                    display_name = :display_name,
                    description = :description
                WHERE in_code_tool_id = :in_code_tool_id
                """
            ),
            GENERATE_DOCX_TOOL,
        )
        tool_id = existing[0]
    else:
        result = conn.execute(
            sa.text(
                """
                INSERT INTO tool (name, display_name, description, in_code_tool_id, enabled)
                VALUES (:name, :display_name, :description, :in_code_tool_id, :enabled)
                RETURNING id
                """
            ),
            GENERATE_DOCX_TOOL,
        )
        tool_id = result.scalar_one()

    # Attach to the default persona (id=0) if not already attached
    conn.execute(
        sa.text(
            """
            INSERT INTO persona__tool (persona_id, tool_id)
            VALUES (0, :tool_id)
            ON CONFLICT DO NOTHING
            """
        ),
        {"tool_id": tool_id},
    )


def downgrade() -> None:
    conn = op.get_bind()
    in_code_tool_id = GENERATE_DOCX_TOOL["in_code_tool_id"]

    # Remove persona associations only; the tool row is managed by startup seeding
    conn.execute(
        sa.text(
            """
            DELETE FROM persona__tool
            WHERE tool_id IN (
                SELECT id FROM tool WHERE in_code_tool_id = :in_code_tool_id
            )
            """
        ),
        {"in_code_tool_id": in_code_tool_id},
    )
