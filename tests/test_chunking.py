import pytest

from knowledge_server.chunking import chunk_text


def test_chunk_text_preserves_line_provenance_and_overlap() -> None:
    text = "eerste regel\ntweede regel\nderde regel\nvierde regel"

    chunks = chunk_text(text, max_chars=28, overlap_chars=14)

    assert len(chunks) >= 2
    assert chunks[0].start_line == 1
    assert chunks[0].end_line >= 2
    assert chunks[1].start_line <= chunks[0].end_line
    assert chunks[-1].end_line == 4


def test_chunk_text_rejects_invalid_sizes() -> None:
    with pytest.raises(ValueError):
        chunk_text("tekst", max_chars=10, overlap_chars=10)


def test_chunk_text_ignores_empty_input() -> None:
    assert chunk_text(" \n\n") == []
