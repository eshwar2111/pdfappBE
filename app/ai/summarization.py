"""Summary generation, with a map-reduce path for long documents.

Short document  ->  single pass over the full text.
Long document   ->  section notes per group of chunks (map), then one summary
                    written from the notes (reduce).

The threshold is on estimated tokens, not page count: a 40-page slide deck can
be shorter than a 10-page contract.
"""

from __future__ import annotations

import logging

from app.ai.chunking import Chunk, estimate_tokens
from app.ai.prompts import (
    SECTION_NOTES_SYSTEM_INSTRUCTION,
    SUMMARY_SYSTEM_INSTRUCTION,
    reduce_summary_user_prompt,
    section_notes_user_prompt,
    summary_user_prompt,
)
from app.ai.provider import AIProvider, ChatTurn, GenerationRequest

logger = logging.getLogger(__name__)

#: Comfortably inside the model's window while leaving room for instructions.
#: Above this, the map-reduce path is used.
_SINGLE_PASS_TOKEN_BUDGET = 24_000

#: Chunks per map-step section.
_SECTION_SIZE = 12

_NO_CONTENT_MARKER = "NO_CONTENT"


class SummarizationService:
    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def summarize(self, chunks: list[Chunk], *, filename: str) -> str:
        if not chunks:
            raise ValueError("Cannot summarise a document with no chunks.")

        total_tokens = sum(chunk.token_count for chunk in chunks)
        if total_tokens <= _SINGLE_PASS_TOKEN_BUDGET:
            return await self._single_pass(chunks, filename=filename)

        logger.info(
            "Using map-reduce summarisation (%s estimated tokens across %s chunks)",
            total_tokens,
            len(chunks),
        )
        return await self._map_reduce(chunks, filename=filename)

    # --- strategies --------------------------------------------------------
    async def _single_pass(self, chunks: list[Chunk], *, filename: str) -> str:
        text = "\n\n".join(chunk.content for chunk in chunks)
        return await self._provider.generate(
            GenerationRequest(
                system_instruction=SUMMARY_SYSTEM_INSTRUCTION,
                turns=[
                    ChatTurn(
                        role="user",
                        content=summary_user_prompt(text, filename=filename),
                    )
                ],
                temperature=0.2,
                # Generous relative to a 3-5 sentence answer: current Gemini
                # models spend output tokens on internal reasoning before
                # writing, and a tight cap truncates the prose mid-sentence.
                max_output_tokens=2048,
            )
        )

    async def _map_reduce(self, chunks: list[Chunk], *, filename: str) -> str:
        sections = [
            chunks[i : i + _SECTION_SIZE] for i in range(0, len(chunks), _SECTION_SIZE)
        ]

        # Sections are summarised sequentially rather than concurrently: the
        # free tier rate-limits hard, and a burst of parallel calls fails more
        # often than it saves time at this scale.
        notes: list[str] = []
        for number, section in enumerate(sections, start=1):
            section_text = "\n\n".join(chunk.content for chunk in section)
            result = await self._provider.generate(
                GenerationRequest(
                    system_instruction=SECTION_NOTES_SYSTEM_INSTRUCTION,
                    turns=[
                        ChatTurn(
                            role="user",
                            content=section_notes_user_prompt(
                                section_text,
                                section_number=number,
                                total=len(sections),
                            ),
                        )
                    ],
                    temperature=0.1,
                    max_output_tokens=1536,
                )
            )
            if _NO_CONTENT_MARKER not in result:
                notes.append(f"Section {number}:\n{result}")

        if not notes:
            # Every section came back empty — fall back to the head of the
            # document rather than returning nothing.
            return await self._single_pass(chunks[:_SECTION_SIZE], filename=filename)

        combined = "\n\n".join(notes)

        # Guard against the notes themselves overflowing on a very large
        # document: keep the head, which carries the document's identity.
        if estimate_tokens(combined) > _SINGLE_PASS_TOKEN_BUDGET:
            combined = combined[: _SINGLE_PASS_TOKEN_BUDGET * 4]

        return await self._provider.generate(
            GenerationRequest(
                system_instruction=SUMMARY_SYSTEM_INSTRUCTION,
                turns=[
                    ChatTurn(
                        role="user",
                        content=reduce_summary_user_prompt(combined, filename=filename),
                    )
                ],
                temperature=0.2,
                # Generous relative to a 3-5 sentence answer: current Gemini
                # models spend output tokens on internal reasoning before
                # writing, and a tight cap truncates the prose mid-sentence.
                max_output_tokens=2048,
            )
        )
