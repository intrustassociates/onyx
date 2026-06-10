"""Markdown → .docx conversion using Pandoc (via pypandoc).

Onyx already ships ``pypandoc_binary`` (see ``pyproject.toml``), which bundles
the Pandoc binary so no extra OS dependency is needed. The pattern below
mirrors ``backend/onyx/server/features/build/session/manager.py:1703``.
"""

from __future__ import annotations

import os
import re
import tempfile

import pypandoc

from onyx.utils.logger import setup_logger


logger = setup_logger()


_TITLE_INVALID = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def title_to_filename(title: str, *, fallback: str = "document") -> str:
    """Build a safe ``.docx`` filename from a free-text document title."""
    cleaned = _TITLE_INVALID.sub(" ", title or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = fallback
    if len(cleaned) > 180:
        cleaned = cleaned[:180].rstrip()
    return f"{cleaned}.docx"


def build_docx_from_markdown(
    *,
    title: str,
    content_markdown: str,
    reference_doc_bytes: bytes | None = None,
) -> bytes:
    """Render the given Markdown content as a Word document.

    The document gets a synthetic top-level heading derived from ``title`` so
    callers don't need to repeat the title in their markdown payload.

    ``reference_doc_bytes`` is a serialized ``.docx`` whose styles Pandoc copies
    (fonts, heading colors, page setup). When ``None``, Pandoc uses its default
    styling. The reference is supplied as bytes (rather than a path) so the
    caller can fetch it from SharePoint at runtime and pass it through without
    persisting to disk longer than this call.
    """
    title_stripped = (title or "").strip() or "Document"
    body = (content_markdown or "").lstrip()

    # The tool description tells the LLM not to include the title in content,
    # but if it does anyway we don't want to render it twice. Strip a leading
    # H1 if its text matches the title.
    leading_h1 = re.match(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    if leading_h1 and leading_h1.group(1).strip().lower() == title_stripped.lower():
        body = body[leading_h1.end() :].lstrip()

    full_markdown = f"# {title_stripped}\n\n{body}".rstrip() + "\n"

    extra_args: list[str] = ["--standalone"]
    reference_path: str | None = None
    try:
        if reference_doc_bytes:
            ref_fd, reference_path = tempfile.mkstemp(suffix=".docx")
            with os.fdopen(ref_fd, "wb") as fh:
                fh.write(reference_doc_bytes)
            extra_args.append(f"--reference-doc={reference_path}")

        out_fd, out_path = tempfile.mkstemp(suffix=".docx")
        os.close(out_fd)
        try:
            pypandoc.convert_text(
                full_markdown,
                to="docx",
                format="markdown",
                outputfile=out_path,
                extra_args=extra_args,
            )
            with open(out_path, "rb") as fh:
                return fh.read()
        finally:
            try:
                os.remove(out_path)
            except OSError as exc:
                logger.debug("Failed to remove temp docx %s: %s", out_path, exc)
    finally:
        if reference_path:
            try:
                os.remove(reference_path)
            except OSError as exc:
                logger.debug(
                    "Failed to remove temp reference doc %s: %s", reference_path, exc
                )
