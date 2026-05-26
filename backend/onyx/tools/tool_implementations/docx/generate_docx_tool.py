"""LLM-callable tool that turns conversation text into a Word document.

The tool is **autonomous about generation** but **passive about delivery**: it
always writes the .docx bytes into Onyx's file store and emits a result packet
containing the new file id. The UI decides whether to render a "Download" or a
"Save to SharePoint" button based on the tenant's
:class:`onyx.db.models.SharePointActionConfig`. Uploading to SharePoint is a
separate, user-triggered action handled outside this tool.

That separation is intentional: tool calls are LLM-driven and synchronous,
while picking a SharePoint destination is interactive and per-message. Mixing
them would force the LLM to invent a folder path, which is unreliable.
"""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from typing_extensions import override

from onyx.chat.emitter import Emitter
from onyx.configs.constants import FileOrigin
from onyx.file_store.utils import get_default_file_store
from onyx.integrations.sharepoint_writer.graph_client import (
    auth_bundle_from_credential_json,
)
from onyx.integrations.sharepoint_writer.graph_client import GraphTokenProvider
from onyx.integrations.sharepoint_writer.uploader import download_item_bytes
from onyx.server.query_and_chat.placement import Placement
from onyx.server.query_and_chat.streaming_models import GenerateDocxResult
from onyx.server.query_and_chat.streaming_models import GenerateDocxStart
from onyx.server.query_and_chat.streaming_models import Packet
from onyx.tools.interface import Tool
from onyx.tools.models import GenerateDocxRichResponse
from onyx.tools.models import ToolCallException
from onyx.tools.models import ToolResponse
from onyx.tools.tool_implementations.docx.docx_builder import build_docx_from_markdown
from onyx.tools.tool_implementations.docx.docx_builder import title_to_filename
from onyx.utils.logger import setup_logger


logger = setup_logger()


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

TITLE_FIELD = "title"
CONTENT_FIELD = "content"


_TOOL_DESCRIPTION = (
    "Generate a Microsoft Word (.docx) document from the current conversation. "
    "Use this when the user explicitly asks for a Word file, report, contract "
    "draft, minutes, memo, or any other formal document they want to save or "
    "share. Structure the content as Markdown so headings, tables, and lists "
    "render correctly in Word."
)

_CONTENT_PARAM_DESCRIPTION = (
    "Document body in Markdown. Supported features:\n"
    "  # Heading 1 / ## Heading 2 / ### Heading 3 — use to structure sections, "
    "respect the hierarchy, do not skip levels.\n"
    "  Bullet lists (- item) and numbered lists (1. item), including nested.\n"
    "  Tables with | col1 | col2 | header and --- separator row. Prefer tables "
    "over comma-separated text for tabular data.\n"
    "  **bold**, *italic*, `inline code`, [links](https://...), blockquotes "
    "(> quoted text), horizontal rules (---) between major sections.\n"
    "  Fenced code blocks (```language) with syntax highlighting.\n"
    "Do NOT include the document title in the content — pass it via the title "
    "parameter instead."
)


class GenerateDocxTool(Tool[None]):
    NAME = "generate_docx"
    DISPLAY_NAME = "Word Document"
    DESCRIPTION = _TOOL_DESCRIPTION

    def __init__(
        self,
        tool_id: int,
        emitter: Emitter,
        *,
        reference_template_bytes: bytes | None = None,
    ) -> None:
        super().__init__(emitter=emitter)
        self._id = tool_id
        # Pre-fetched template bytes (admin can point at a .docx in SharePoint).
        # We accept bytes (not a path) so the caller can fetch from SP at
        # construction time and pass them in; ``None`` => Pandoc default style.
        self._reference_template_bytes = reference_template_bytes

    @property
    def id(self) -> int:
        return self._id

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def description(self) -> str:
        return self.DESCRIPTION

    @property
    def display_name(self) -> str:
        return self.DISPLAY_NAME

    @override
    def tool_definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        TITLE_FIELD: {
                            "type": "string",
                            "description": (
                                "Title of the document. Used as the top-level "
                                "heading (Heading 1) inside the file and as the "
                                "basis for the filename."
                            ),
                        },
                        CONTENT_FIELD: {
                            "type": "string",
                            "description": _CONTENT_PARAM_DESCRIPTION,
                        },
                    },
                    "required": [TITLE_FIELD, CONTENT_FIELD],
                },
            },
        }

    @override
    def emit_start(self, placement: Placement) -> None:
        self.emitter.emit(Packet(placement=placement, obj=GenerateDocxStart(title="")))

    @override
    def run(
        self,
        placement: Placement,
        override_kwargs: None,
        **llm_kwargs: Any,
    ) -> ToolResponse:
        title = str(llm_kwargs.get(TITLE_FIELD, "") or "").strip()
        content = str(llm_kwargs.get(CONTENT_FIELD, "") or "")
        if not title:
            raise ToolCallException(
                "generate_docx called without a title",
                "I need a document title to generate the Word file.",
            )
        if not content.strip():
            raise ToolCallException(
                "generate_docx called without content",
                "I need some Markdown content to put inside the Word file.",
            )

        # Re-emit the start packet with the actual title so the UI can label
        # the placeholder card while we generate.
        self.emitter.emit(
            Packet(placement=placement, obj=GenerateDocxStart(title=title))
        )

        try:
            docx_bytes = build_docx_from_markdown(
                title=title,
                content_markdown=content,
                reference_doc_bytes=self._reference_template_bytes,
            )
        except Exception as exc:
            logger.exception("Pandoc failed to build .docx for title=%r", title)
            raise ToolCallException(
                f"Pandoc conversion failed: {exc}",
                "I couldn't render the Word document. Please try simplifying the content.",
            )

        filename = title_to_filename(title)
        file_store = get_default_file_store()
        try:
            file_id = file_store.save_file(
                content=BytesIO(docx_bytes),
                display_name=filename,
                file_origin=FileOrigin.CHAT_UPLOAD,
                file_type=DOCX_MIME,
            )
        except Exception as exc:
            logger.exception("Failed to persist generated .docx to file_store")
            raise ToolCallException(
                f"File store save failed: {exc}",
                "I generated the Word file but couldn't save it. Please try again.",
            )

        self.emitter.emit(
            Packet(
                placement=placement,
                obj=GenerateDocxResult(
                    file_id=str(file_id), filename=filename, title=title
                ),
            )
        )

        rich = GenerateDocxRichResponse(
            file_id=str(file_id), filename=filename, title=title
        )
        return ToolResponse(
            rich_response=rich,
            llm_facing_response=json.dumps(
                {
                    "status": "ok",
                    "filename": filename,
                    "file_id": str(file_id),
                    "message": (
                        "Word document generated. The user can save it to "
                        "SharePoint or download it from the chat UI."
                    ),
                }
            ),
        )


def fetch_reference_template_bytes(
    credential_json: dict[str, Any],
    drive_id: str,
    item_id: str,
) -> bytes | None:
    """Fetch a SharePoint-hosted .docx template using the connector credentials.

    Returns ``None`` on any failure so the caller can fall back to the default
    Pandoc styling rather than aborting the whole tool call.
    """
    try:
        bundle = auth_bundle_from_credential_json(credential_json)
        token_provider = GraphTokenProvider(bundle)
        return download_item_bytes(token_provider, drive_id, item_id)
    except Exception as exc:
        logger.warning(
            "Could not fetch SharePoint reference template (%s/%s): %s",
            drive_id,
            item_id,
            exc,
        )
        return None
