"""PDF text extraction.

``pypdf`` (BSD-3) is the primary extractor. Some PDFs — particularly ones with
unusual content-stream layout — yield near-empty text from it while extracting
cleanly with ``pdfplumber`` (MIT), so pages that come back suspiciously empty
are retried there before being written off.

A PDF whose every page is empty is almost always a scan. That is reported as a
typed failure rather than being passed to the LLM, because summarising nothing
produces a confident, wrong summary.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

import pdfplumber
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.exceptions import ValidationError
from app.domain.enums import ProcessingFailureReason

logger = logging.getLogger(__name__)

#: Below this many characters a page is treated as a possible extraction miss
#: and retried with the slower extractor.
_SPARSE_PAGE_THRESHOLD = 40

_PDF_MAGIC = b"%PDF-"

_WHITESPACE_RUN = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_HYPHEN_LINEBREAK = re.compile(r"(\w)-\n(\w)")

#: Control characters that carry no textual meaning and that PostgreSQL will
#: not store at all — a NUL byte raises CharacterNotInRepertoireError on insert.
#: PDFs pick these up from embedded font tables and malformed content streams,
#: so they are stripped at extraction rather than defended against per write.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ExtractionError(Exception):
    """Raised with a typed reason so the pipeline can record *why* it failed."""

    def __init__(self, reason: ProcessingFailureReason, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PageText:
    page_number: int  # 1-based
    text: str


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    pages: list[PageText]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def total_characters(self) -> int:
        return sum(len(page.text) for page in self.pages)


def validate_pdf_magic_bytes(data: bytes) -> None:
    """Content-based type check.

    The client-supplied filename and MIME type are both trivially forged, so
    the actual file header is what decides whether this is a PDF.
    """
    if not data.startswith(_PDF_MAGIC):
        raise ValidationError("That file is not a valid PDF.")


def normalise(text: str) -> str:
    """Collapse extraction noise that would otherwise waste context and
    fragment chunks: control characters, repeated spaces, hard-wrapped
    hyphenation, blank runs."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS.sub("", text)
    text = _HYPHEN_LINEBREAK.sub(r"\1\2", text)
    text = _WHITESPACE_RUN.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def _extract_with_pdfplumber(data: bytes, page_numbers: set[int]) -> dict[int, str]:
    """Second-pass extraction for the specific pages that looked empty."""
    recovered: dict[int, str] = {}
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                if index not in page_numbers:
                    continue
                recovered[index] = normalise(page.extract_text() or "")
    except Exception:  # noqa: BLE001 - fallback is best-effort by design
        logger.warning("pdfplumber fallback failed", exc_info=True)
    return recovered


def extract_text(data: bytes) -> ExtractedDocument:
    validate_pdf_magic_bytes(data)

    try:
        reader = PdfReader(io.BytesIO(data))
    except PdfReadError as exc:
        raise ExtractionError(
            ProcessingFailureReason.CORRUPT_PDF, "The PDF could not be parsed."
        ) from exc

    if reader.is_encrypted:
        # An empty-password decrypt covers the common "protected but not
        # actually locked" case; anything else needs a password we don't have.
        try:
            if reader.decrypt("") == 0:
                raise ExtractionError(
                    ProcessingFailureReason.ENCRYPTED_PDF,
                    "This PDF is password-protected.",
                )
        except ExtractionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(
                ProcessingFailureReason.ENCRYPTED_PDF,
                "This PDF is password-protected.",
            ) from exc

    pages: list[PageText] = []
    sparse: set[int] = set()

    for index, page in enumerate(reader.pages, start=1):
        try:
            text = normalise(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page must not kill the document
            logger.warning("pypdf failed on page %s", index, exc_info=True)
            text = ""
        if len(text) < _SPARSE_PAGE_THRESHOLD:
            sparse.add(index)
        pages.append(PageText(page_number=index, text=text))

    if sparse:
        recovered = _extract_with_pdfplumber(data, sparse)
        pages = [
            PageText(
                page_number=page.page_number,
                text=(
                    recovered[page.page_number]
                    if len(recovered.get(page.page_number, "")) > len(page.text)
                    else page.text
                ),
            )
            for page in pages
        ]

    document = ExtractedDocument(pages=pages)
    if document.total_characters < _SPARSE_PAGE_THRESHOLD:
        raise ExtractionError(
            ProcessingFailureReason.NO_EXTRACTABLE_TEXT,
            "No text could be extracted — this looks like a scanned PDF. "
            "OCR is not supported.",
        )
    return document
