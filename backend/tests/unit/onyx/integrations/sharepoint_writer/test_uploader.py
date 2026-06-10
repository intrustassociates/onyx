from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from onyx.integrations.sharepoint_writer.graph_client import GraphClientError
from onyx.integrations.sharepoint_writer.uploader import download_item_bytes
from onyx.integrations.sharepoint_writer.uploader import sanitize_filename
from onyx.integrations.sharepoint_writer.uploader import SIMPLE_UPLOAD_MAX_BYTES
from onyx.integrations.sharepoint_writer.uploader import upload_docx


class _FakeTokenProvider:
    graph_host = "https://graph.microsoft.com"
    graph_base = "https://graph.microsoft.com/v1.0"

    def acquire(self) -> str:
        return "tok"


def test_sanitize_filename_strips_invalid_chars() -> None:
    assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j.docx') == "a b c d e f g h i j.docx"


def test_sanitize_filename_adds_docx_extension() -> None:
    assert sanitize_filename("report") == "report.docx"


def test_sanitize_filename_preserves_existing_docx() -> None:
    assert sanitize_filename("report.docx") == "report.docx"


def test_sanitize_filename_collapses_whitespace() -> None:
    assert sanitize_filename("foo    bar\t\nbaz") == "foo bar baz.docx"


def test_sanitize_filename_falls_back_when_empty() -> None:
    assert sanitize_filename("///") == "document.docx"


def test_sanitize_filename_caps_length() -> None:
    long_name = "x" * 500
    result = sanitize_filename(long_name)
    assert len(result) <= 200
    assert result.endswith(".docx")


def test_upload_docx_uses_simple_put_for_small_files() -> None:
    response = {
        "id": "new-item-id",
        "webUrl": "https://t.sharepoint.com/sites/m/Documents/foo.docx",
        "name": "foo.docx",
    }
    with (
        patch(
            "onyx.integrations.sharepoint_writer.uploader.graph_put_bytes",
            return_value=response,
        ) as mock_put,
        patch(
            "onyx.integrations.sharepoint_writer.uploader.graph_post_json"
        ) as mock_post,
    ):
        result = upload_docx(
            _FakeTokenProvider(),
            drive_id="drive-1",
            folder_id="folder-1",
            filename="foo.docx",
            content_bytes=b"small",
        )
        assert result.item_id == "new-item-id"
        assert result.web_url == response["webUrl"]
        assert result.filename == "foo.docx"
        url_called = mock_put.call_args.args[0]
        assert "conflictBehavior=rename" in url_called
        assert mock_post.call_count == 0  # session not used for small files


def test_upload_docx_raises_when_response_missing_id() -> None:
    response = {"webUrl": "https://x", "name": "foo.docx"}  # no id
    with patch(
        "onyx.integrations.sharepoint_writer.uploader.graph_put_bytes",
        return_value=response,
    ):
        with pytest.raises(GraphClientError, match="missing id"):
            upload_docx(
                _FakeTokenProvider(),
                drive_id="drive-1",
                folder_id="folder-1",
                filename="foo.docx",
                content_bytes=b"x",
            )


def test_upload_docx_uses_session_for_large_files() -> None:
    large_bytes = b"x" * (SIMPLE_UPLOAD_MAX_BYTES + 10)
    session_response = {"uploadUrl": "https://upload.example.com/sess?key=abc"}
    chunk_response = MagicMock()
    chunk_response.status_code = 201
    chunk_response.content = b'{"id":"new-id","webUrl":"https://w","name":"f.docx"}'
    chunk_response.json.return_value = {
        "id": "new-id",
        "webUrl": "https://w",
        "name": "f.docx",
    }

    with (
        patch(
            "onyx.integrations.sharepoint_writer.uploader.graph_post_json",
            return_value=session_response,
        ) as mock_post,
        patch(
            "onyx.integrations.sharepoint_writer.uploader.requests.put",
            return_value=chunk_response,
        ) as mock_put,
    ):
        result = upload_docx(
            _FakeTokenProvider(),
            drive_id="drive-1",
            folder_id="folder-1",
            filename="f.docx",
            content_bytes=large_bytes,
        )
        assert result.item_id == "new-id"
        assert mock_post.call_count == 1
        # At least one chunk PUT
        assert mock_put.call_count >= 1


def test_chunked_upload_retries_transient_chunk_failure() -> None:
    large_bytes = b"x" * (SIMPLE_UPLOAD_MAX_BYTES + 10)
    session_response = {"uploadUrl": "https://upload.example.com/sess?key=abc"}

    flaky = MagicMock()
    flaky.status_code = 503
    flaky.text = "service unavailable"

    success = MagicMock()
    success.status_code = 201
    success.content = b'{"id":"new-id","webUrl":"https://w","name":"f.docx"}'
    success.json.return_value = {
        "id": "new-id",
        "webUrl": "https://w",
        "name": "f.docx",
    }

    with (
        patch(
            "onyx.integrations.sharepoint_writer.uploader.graph_post_json",
            return_value=session_response,
        ),
        patch(
            "onyx.integrations.sharepoint_writer.uploader.requests.put",
            side_effect=[flaky, success],
        ) as mock_put,
        patch("onyx.integrations.sharepoint_writer.uploader.time.sleep"),
    ):
        result = upload_docx(
            _FakeTokenProvider(),
            drive_id="drive-1",
            folder_id="folder-1",
            filename="f.docx",
            content_bytes=large_bytes,
        )
        assert result.item_id == "new-id"
        assert mock_put.call_count == 2  # one retry, same byte range


def test_chunked_upload_raises_on_non_retryable_chunk_status() -> None:
    large_bytes = b"x" * (SIMPLE_UPLOAD_MAX_BYTES + 10)
    session_response = {"uploadUrl": "https://upload.example.com/sess?key=abc"}

    denied = MagicMock()
    denied.status_code = 403
    denied.text = "forbidden"

    with (
        patch(
            "onyx.integrations.sharepoint_writer.uploader.graph_post_json",
            return_value=session_response,
        ),
        patch(
            "onyx.integrations.sharepoint_writer.uploader.requests.put",
            return_value=denied,
        ),
    ):
        with pytest.raises(GraphClientError, match="403") as exc_info:
            upload_docx(
                _FakeTokenProvider(),
                drive_id="drive-1",
                folder_id="folder-1",
                filename="f.docx",
                content_bytes=large_bytes,
            )
        assert exc_info.value.status_code == 403


def test_download_item_bytes_returns_content() -> None:
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.content = b"template-bytes"
    with patch(
        "onyx.integrations.sharepoint_writer.uploader.requests.get",
        return_value=resp,
    ) as mock_get:
        data = download_item_bytes(_FakeTokenProvider(), "drive-1", "item-1")
        assert data == b"template-bytes"
        # Auth header sent
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer tok"


def test_download_item_bytes_raises_on_404() -> None:
    resp = MagicMock()
    resp.ok = False
    resp.status_code = 404
    resp.text = "not found"
    with patch(
        "onyx.integrations.sharepoint_writer.uploader.requests.get",
        return_value=resp,
    ):
        with pytest.raises(GraphClientError, match="404"):
            download_item_bytes(_FakeTokenProvider(), "drive-1", "item-1")
