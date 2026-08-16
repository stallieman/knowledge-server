from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    """A source fragment with stable line provenance."""

    index: int
    text: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class _TextUnit:
    text: str
    line_number: int


def _text_units(text: str, max_chars: int) -> list[_TextUnit]:
    units: list[_TextUnit] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        normalized_line = line.rstrip()
        if not normalized_line:
            normalized_line = " "

        for offset in range(0, len(normalized_line), max_chars):
            units.append(
                _TextUnit(
                    text=normalized_line[offset : offset + max_chars],
                    line_number=line_number,
                )
            )

    return units


def _joined_length(units: list[_TextUnit]) -> int:
    return sum(len(unit.text) for unit in units) + max(0, len(units) - 1)


def _overlap_tail(units: list[_TextUnit], overlap_chars: int) -> list[_TextUnit]:
    if overlap_chars <= 0:
        return []

    tail: list[_TextUnit] = []
    tail_size = 0

    for unit in reversed(units):
        if tail and tail_size + len(unit.text) + 1 > overlap_chars:
            break
        tail.append(unit)
        tail_size += len(unit.text) + (1 if tail_size else 0)

    tail.reverse()
    return tail


def chunk_text(
    text: str,
    *,
    max_chars: int = 1800,
    overlap_chars: int = 250,
) -> list[TextChunk]:
    """Split text into overlapping, line-aware chunks."""
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be between zero and max_chars")
    if not text.strip():
        return []

    chunks: list[TextChunk] = []
    buffer: list[_TextUnit] = []

    def emit() -> None:
        chunk_text_value = "\n".join(unit.text for unit in buffer).strip()
        if not chunk_text_value:
            return
        chunks.append(
            TextChunk(
                index=len(chunks),
                text=chunk_text_value,
                start_line=buffer[0].line_number,
                end_line=buffer[-1].line_number,
            )
        )

    for unit in _text_units(text, max_chars):
        prospective_length = _joined_length([*buffer, unit])
        if buffer and prospective_length > max_chars:
            emit()
            buffer = _overlap_tail(buffer, overlap_chars)

            while buffer and _joined_length([*buffer, unit]) > max_chars:
                buffer.pop(0)

        buffer.append(unit)

    if buffer:
        emit()

    return chunks
