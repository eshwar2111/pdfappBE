"""Chunking.

Strategy: pack whole paragraphs up to a token budget, carry a fixed overlap
into the next chunk, and record the page range each chunk spans.

Why paragraphs rather than a fixed character window — splitting mid-sentence
produces chunks that embed poorly and read badly when shown as a citation.
Why overlap — a fact that straddles a boundary would otherwise be retrievable
from neither side.

Token counts are estimated from character length rather than measured with a
tokenizer. The estimate only sizes chunks, so a few percent of drift costs
nothing, and it avoids shipping a tokenizer that must stay in sync with the
provider's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.extraction import ExtractedDocument
from app.core.config import settings

#: Empirically ~4 characters per token for English prose.
_CHARS_PER_TOKEN = 4

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    content: str
    page_start: int
    page_end: int
    token_count: int


@dataclass(frozen=True, slots=True)
class _Block:
    """A paragraph tagged with the page it came from."""

    text: str
    page: int
    tokens: int


def _split_oversized(block: _Block, limit: int) -> list[_Block]:
    """A single paragraph longer than the budget (tables, dense contract
    clauses) is split on sentence boundaries so it still fits."""
    if block.tokens <= limit:
        return [block]

    parts: list[_Block] = []
    buffer = ""
    for sentence in _SENTENCE_BREAK.split(block.text):
        candidate = f"{buffer} {sentence}".strip()
        if estimate_tokens(candidate) > limit and buffer:
            parts.append(_Block(buffer, block.page, estimate_tokens(buffer)))
            buffer = sentence
        else:
            buffer = candidate
    if buffer:
        parts.append(_Block(buffer, block.page, estimate_tokens(buffer)))
    return parts


def _to_blocks(document: ExtractedDocument, limit: int) -> list[_Block]:
    blocks: list[_Block] = []
    for page in document.pages:
        if not page.text.strip():
            continue
        for paragraph in _PARAGRAPH_BREAK.split(page.text):
            cleaned = paragraph.strip()
            if not cleaned:
                continue
            blocks.extend(
                _split_oversized(
                    _Block(cleaned, page.page_number, estimate_tokens(cleaned)), limit
                )
            )
    return blocks


def _overlap_tail(blocks: list[_Block], overlap_tokens: int) -> list[_Block]:
    """The trailing blocks of a chunk, up to the overlap budget, to prepend to
    the next one."""
    tail: list[_Block] = []
    budget = overlap_tokens
    for block in reversed(blocks):
        if block.tokens > budget:
            break
        tail.insert(0, block)
        budget -= block.tokens
    return tail


def chunk_document(
    document: ExtractedDocument,
    *,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[Chunk]:
    target = target_tokens or settings.chunk_target_tokens
    overlap = overlap_tokens or settings.chunk_overlap_tokens

    blocks = _to_blocks(document, target)
    if not blocks:
        return []

    chunks: list[Chunk] = []
    current: list[_Block] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        chunks.append(
            Chunk(
                index=len(chunks),
                content="\n\n".join(block.text for block in current),
                page_start=min(block.page for block in current),
                page_end=max(block.page for block in current),
                token_count=current_tokens,
            )
        )
        carry = _overlap_tail(current, overlap)
        current = list(carry)
        current_tokens = sum(block.tokens for block in carry)

    for block in blocks:
        if current and current_tokens + block.tokens > target:
            flush()
        current.append(block)
        current_tokens += block.tokens

    # Final flush, without seeding an overlap that has nothing to follow it.
    if current:
        chunks.append(
            Chunk(
                index=len(chunks),
                content="\n\n".join(block.text for block in current),
                page_start=min(block.page for block in current),
                page_end=max(block.page for block in current),
                token_count=current_tokens,
            )
        )

    return chunks
