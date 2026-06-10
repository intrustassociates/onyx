from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from onyx.tools.tool_implementations.docx.docx_builder import (
    build_docx_from_markdown,
)
from onyx.tools.tool_implementations.docx.docx_builder import title_to_filename


def _extract_document_xml(docx_bytes: bytes) -> str:
    """A .docx is a zip; ``word/document.xml`` carries the body."""
    with zipfile.ZipFile(BytesIO(docx_bytes), "r") as z:
        return z.read("word/document.xml").decode("utf-8")


def test_title_to_filename_strips_invalid_chars() -> None:
    # Invalid chars are replaced with a space, then runs of whitespace are
    # collapsed back down to a single space.
    assert title_to_filename("Q3 / Report : 2026") == "Q3 Report 2026.docx"


def test_title_to_filename_falls_back_when_empty() -> None:
    assert title_to_filename("///") == "document.docx"


def test_title_to_filename_caps_length() -> None:
    out = title_to_filename("x" * 500)
    assert len(out) <= 190  # 180 stem + ".docx"
    assert out.endswith(".docx")


def test_build_docx_outputs_real_docx_zip() -> None:
    out = build_docx_from_markdown(
        title="Hello World",
        content_markdown="This is a paragraph.",
    )
    assert out.startswith(b"PK")  # ZIP signature
    # Validate the docx contains the expected pieces
    with zipfile.ZipFile(BytesIO(out), "r") as z:
        names = z.namelist()
        assert "word/document.xml" in names
        assert "[Content_Types].xml" in names


def test_build_docx_includes_title_as_heading() -> None:
    out = build_docx_from_markdown(
        title="My Report Title",
        content_markdown="Some body text here.",
    )
    body = _extract_document_xml(out)
    assert "My Report Title" in body
    assert "Some body text here." in body


def test_build_docx_renders_table_from_markdown() -> None:
    table_md = (
        "| Name | Value |\n"
        "|------|-------|\n"
        "| Foo  | 100   |\n"
        "| Bar  | 200   |\n"
    )
    out = build_docx_from_markdown(title="Tabular", content_markdown=table_md)
    body = _extract_document_xml(out)
    # Pandoc emits w:tbl elements for tables — proves table rendering happened
    assert "<w:tbl" in body
    assert "Foo" in body
    assert "200" in body


def test_build_docx_renders_lists() -> None:
    list_md = "- alpha\n- beta\n- gamma\n"
    out = build_docx_from_markdown(title="Items", content_markdown=list_md)
    body = _extract_document_xml(out)
    assert "alpha" in body
    assert "beta" in body
    assert "gamma" in body


def test_build_docx_strips_duplicate_title_h1_from_content() -> None:
    """If the LLM ignores instructions and includes the title as an H1, we
    strip it so the rendered doc doesn't show the title twice."""
    out = build_docx_from_markdown(
        title="Same Title",
        content_markdown="# Same Title\n\nBody paragraph here.\n",
    )
    body = _extract_document_xml(out)
    # The title only appears once
    assert body.count("Same Title") == 1
    assert "Body paragraph here." in body


def test_build_docx_keeps_other_h1s_in_content() -> None:
    out = build_docx_from_markdown(
        title="Document",
        content_markdown="# Different Heading\n\nBody.\n",
    )
    body = _extract_document_xml(out)
    assert "Document" in body
    assert "Different Heading" in body


def test_build_docx_empty_title_uses_fallback() -> None:
    out = build_docx_from_markdown(
        title="",
        content_markdown="content",
    )
    body = _extract_document_xml(out)
    assert "Document" in body  # fallback in build_docx_from_markdown


def test_build_docx_propagates_pypandoc_failures(monkeypatch) -> None:
    """We let exceptions bubble — the tool layer catches them."""
    from onyx.tools.tool_implementations.docx import docx_builder

    def _boom(*_args, **_kwargs):
        raise RuntimeError("Pandoc exploded")

    monkeypatch.setattr(docx_builder.pypandoc, "convert_text", _boom)
    with pytest.raises(RuntimeError, match="Pandoc exploded"):
        build_docx_from_markdown(title="t", content_markdown="x")
