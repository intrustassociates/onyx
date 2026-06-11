from __future__ import annotations

import json

from onyx.server.query_and_chat.session_loading import create_generate_docx_packets
from onyx.server.query_and_chat.streaming_models import GenerateDocxResult
from onyx.server.query_and_chat.streaming_models import GenerateDocxStart
from onyx.server.query_and_chat.streaming_models import SectionEnd


def test_create_generate_docx_packets_happy_path() -> None:
    response_json = json.dumps(
        {
            "status": "ok",
            "filename": "Relatorio.docx",
            "file_id": "file-123",
            "message": "Word document generated.",
        }
    )
    packets = create_generate_docx_packets(
        response_json=response_json,
        title="Relatorio",
        turn_index=2,
        tab_index=1,
    )

    assert len(packets) == 3
    start, result, end = (p.obj for p in packets)
    assert isinstance(start, GenerateDocxStart)
    assert start.title == "Relatorio"
    assert isinstance(result, GenerateDocxResult)
    assert result.file_id == "file-123"
    assert result.filename == "Relatorio.docx"
    assert result.title == "Relatorio"
    assert isinstance(end, SectionEnd)
    assert all(p.placement.turn_index == 2 for p in packets)
    assert all(p.placement.tab_index == 1 for p in packets)


def test_create_generate_docx_packets_degrades_on_bad_response() -> None:
    for bad in ["", "not json", json.dumps({"status": "ok"})]:
        packets = create_generate_docx_packets(
            response_json=bad,
            title="Doc",
            turn_index=0,
        )
        # Start + SectionEnd only, no result packet, no crash
        assert len(packets) == 2
        assert isinstance(packets[0].obj, GenerateDocxStart)
        assert isinstance(packets[1].obj, SectionEnd)
