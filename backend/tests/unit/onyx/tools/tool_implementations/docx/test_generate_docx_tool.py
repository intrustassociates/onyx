from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from onyx.server.query_and_chat.placement import Placement
from onyx.server.query_and_chat.streaming_models import GenerateDocxResult
from onyx.server.query_and_chat.streaming_models import GenerateDocxStart
from onyx.tools.models import GenerateDocxRichResponse
from onyx.tools.models import ToolCallException
from onyx.tools.tool_implementations.docx.generate_docx_tool import (
    build_artifact_metadata,
)
from onyx.tools.tool_implementations.docx.generate_docx_tool import DOCX_MIME
from onyx.tools.tool_implementations.docx.generate_docx_tool import GenerateDocxTool
from onyx.tools.tool_implementations.docx.generate_docx_tool import (
    is_docx_artifact_owned_by,
)


@pytest.fixture
def fake_emitter() -> MagicMock:
    return MagicMock()


@pytest.fixture
def tool(fake_emitter: MagicMock) -> GenerateDocxTool:
    return GenerateDocxTool(tool_id=42, emitter=fake_emitter)


def test_tool_metadata(tool: GenerateDocxTool) -> None:
    assert tool.id == 42
    assert tool.name == "generate_docx"
    assert tool.display_name == "Word Document"


def test_tool_definition_advertises_markdown_features(tool: GenerateDocxTool) -> None:
    definition = tool.tool_definition()
    fn = definition["function"]
    assert fn["name"] == "generate_docx"
    params = fn["parameters"]["properties"]
    assert set(params.keys()) == {"title", "content"}
    assert "required" in fn["parameters"]
    assert "title" in fn["parameters"]["required"]
    assert "content" in fn["parameters"]["required"]
    # Description teaches the LLM what features are supported
    content_desc = params["content"]["description"]
    assert "Markdown" in content_desc
    assert "Heading" in content_desc
    assert "table" in content_desc.lower()


def test_tool_run_happy_path_saves_to_file_store(tool: GenerateDocxTool) -> None:
    fake_store = MagicMock()
    fake_store.save_file.return_value = "file-id-abc"
    with (
        patch(
            "onyx.tools.tool_implementations.docx.generate_docx_tool.get_default_file_store",
            return_value=fake_store,
        ),
        patch(
            "onyx.tools.tool_implementations.docx.generate_docx_tool.build_docx_from_markdown",
            return_value=b"PK-fake-docx-bytes",
        ),
    ):
        response = tool.run(
            placement=Placement(turn_index=0),
            override_kwargs=None,
            title="Quarterly Report",
            content="# Section\n\nBody.",
        )

    assert isinstance(response.rich_response, GenerateDocxRichResponse)
    assert response.rich_response.file_id == "file-id-abc"
    assert response.rich_response.filename == "Quarterly Report.docx"
    assert response.rich_response.title == "Quarterly Report"
    fake_store.save_file.assert_called_once()
    save_kwargs = fake_store.save_file.call_args.kwargs
    assert save_kwargs["display_name"] == "Quarterly Report.docx"
    assert (
        save_kwargs["file_type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    # No user_id on the tool → metadata carries only the generator marker
    assert save_kwargs["file_metadata"] == {"generator": "generate_docx"}


def test_tool_run_stamps_owner_when_user_id_given(fake_emitter: MagicMock) -> None:
    tool = GenerateDocxTool(tool_id=1, emitter=fake_emitter, user_id="user-123")
    fake_store = MagicMock()
    fake_store.save_file.return_value = "fid"
    with (
        patch(
            "onyx.tools.tool_implementations.docx.generate_docx_tool.get_default_file_store",
            return_value=fake_store,
        ),
        patch(
            "onyx.tools.tool_implementations.docx.generate_docx_tool.build_docx_from_markdown",
            return_value=b"PK",
        ),
    ):
        tool.run(
            placement=Placement(turn_index=0),
            override_kwargs=None,
            title="Doc",
            content="body",
        )
    save_kwargs = fake_store.save_file.call_args.kwargs
    assert save_kwargs["file_metadata"] == {
        "generator": "generate_docx",
        "owner_user_id": "user-123",
    }


def test_build_artifact_metadata_without_user_id_omits_owner() -> None:
    assert build_artifact_metadata(None) == {"generator": "generate_docx"}


def test_is_docx_artifact_owned_by_matrix() -> None:
    owned = build_artifact_metadata("user-1")
    ownerless = build_artifact_metadata(None)

    # Wrong MIME type → never an artifact
    assert is_docx_artifact_owned_by(owned, "text/plain", "user-1") is False
    # Missing/non-dict metadata → reject
    assert is_docx_artifact_owned_by(None, DOCX_MIME, "user-1") is False
    assert is_docx_artifact_owned_by("not-a-dict", DOCX_MIME, "user-1") is False
    # Missing generator marker → reject (arbitrary file_store entries)
    assert is_docx_artifact_owned_by({}, DOCX_MIME, "user-1") is False
    assert (
        is_docx_artifact_owned_by({"generator": "other"}, DOCX_MIME, "user-1") is False
    )
    # Owner match → allow
    assert is_docx_artifact_owned_by(owned, DOCX_MIME, "user-1") is True
    # Owner mismatch → reject (the IDOR case)
    assert is_docx_artifact_owned_by(owned, DOCX_MIME, "user-2") is False
    assert is_docx_artifact_owned_by(owned, DOCX_MIME, None) is False
    # Ownerless artifact (auth-disabled deployments) → any user may act
    assert is_docx_artifact_owned_by(ownerless, DOCX_MIME, "user-2") is True
    assert is_docx_artifact_owned_by(ownerless, DOCX_MIME, None) is True


def test_tool_run_emits_start_and_result_packets(
    tool: GenerateDocxTool, fake_emitter: MagicMock
) -> None:
    fake_store = MagicMock()
    fake_store.save_file.return_value = "file-id-xyz"
    with (
        patch(
            "onyx.tools.tool_implementations.docx.generate_docx_tool.get_default_file_store",
            return_value=fake_store,
        ),
        patch(
            "onyx.tools.tool_implementations.docx.generate_docx_tool.build_docx_from_markdown",
            return_value=b"PK",
        ),
    ):
        tool.run(
            placement=Placement(turn_index=0),
            override_kwargs=None,
            title="My Doc",
            content="body",
        )

    # Find emitted packet types
    emitted_objs = [call.args[0].obj for call in fake_emitter.emit.call_args_list]
    starts = [o for o in emitted_objs if isinstance(o, GenerateDocxStart)]
    results = [o for o in emitted_objs if isinstance(o, GenerateDocxResult)]
    assert len(starts) >= 1
    assert any(s.title == "My Doc" for s in starts)
    assert len(results) == 1
    assert results[0].file_id == "file-id-xyz"
    assert results[0].title == "My Doc"


def test_tool_run_raises_tool_call_exception_for_empty_title(
    tool: GenerateDocxTool,
) -> None:
    with pytest.raises(ToolCallException) as exc_info:
        tool.run(
            placement=Placement(turn_index=0),
            override_kwargs=None,
            title="   ",
            content="body",
        )
    assert "title" in exc_info.value.llm_facing_message.lower()


def test_tool_run_raises_tool_call_exception_for_empty_content(
    tool: GenerateDocxTool,
) -> None:
    with pytest.raises(ToolCallException) as exc_info:
        tool.run(
            placement=Placement(turn_index=0),
            override_kwargs=None,
            title="My Doc",
            content="",
        )
    assert "content" in exc_info.value.llm_facing_message.lower()


def test_tool_run_wraps_pandoc_errors_as_tool_call_exception(
    tool: GenerateDocxTool,
) -> None:
    with patch(
        "onyx.tools.tool_implementations.docx.generate_docx_tool.build_docx_from_markdown",
        side_effect=RuntimeError("pandoc segfault"),
    ):
        with pytest.raises(ToolCallException) as exc_info:
            tool.run(
                placement=Placement(turn_index=0),
                override_kwargs=None,
                title="Doc",
                content="body",
            )
    assert "Word document" in exc_info.value.llm_facing_message


def test_tool_run_wraps_file_store_errors_as_tool_call_exception(
    tool: GenerateDocxTool,
) -> None:
    fake_store = MagicMock()
    fake_store.save_file.side_effect = RuntimeError("disk full")
    with (
        patch(
            "onyx.tools.tool_implementations.docx.generate_docx_tool.get_default_file_store",
            return_value=fake_store,
        ),
        patch(
            "onyx.tools.tool_implementations.docx.generate_docx_tool.build_docx_from_markdown",
            return_value=b"PK",
        ),
    ):
        with pytest.raises(ToolCallException) as exc_info:
            tool.run(
                placement=Placement(turn_index=0),
                override_kwargs=None,
                title="Doc",
                content="body",
            )
    assert "save" in exc_info.value.llm_facing_message.lower()


def test_tool_passes_reference_template_to_builder(fake_emitter: MagicMock) -> None:
    template_bytes = b"PK-template"
    tool = GenerateDocxTool(
        tool_id=1, emitter=fake_emitter, reference_template_bytes=template_bytes
    )
    fake_store = MagicMock()
    fake_store.save_file.return_value = "fid"
    with (
        patch(
            "onyx.tools.tool_implementations.docx.generate_docx_tool.get_default_file_store",
            return_value=fake_store,
        ),
        patch(
            "onyx.tools.tool_implementations.docx.generate_docx_tool.build_docx_from_markdown",
            return_value=b"PK",
        ) as mock_build,
    ):
        tool.run(
            placement=Placement(turn_index=0),
            override_kwargs=None,
            title="t",
            content="body",
        )
        # The reference template bytes should be forwarded verbatim
        assert mock_build.call_args.kwargs["reference_doc_bytes"] == template_bytes
