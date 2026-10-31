"""The store's policy manual (PDF) parsed into sections the agent can consult.

Retrieval is by section rather than by embedding similarity: the manual is
8 pages with 10 numbered sections, so the model reads the table of contents
(exposed as an enum in the ``consult_policy`` tool) and asks for whole
sections. No chunking, no recall misses, and every answer is traceable to a
section number. See README, "Why no vector database".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from emporio.text import slugify

POLICY_FILE = "políticas_da_loja.pdf"

# Written for the staff, not for customers: section 7 (how to serve customers on
# WhatsApp) is the basis of the system prompt and section 10 is an internal note.
INTERNAL_SECTIONS = frozenset({7, 10})

# The company table spells the e-mail domain with an accent ("empório..."), the
# cover does not. The cover's version is used; see docs/data-quality.md.
ERRATA = {"contato@empóriodamusica.com.br": "contato@emporiodamusica.com.br"}

_PAGE_HEADER = "Empório da Música Manual de Políticas e Procedimentos"
_FOOTER_RE = re.compile(r"^Página \d+$")
_SECTION_RE = re.compile(r"^(\d{1,2})\. (\S.*)$")
_SUBSECTION_RE = re.compile(r"^\d{1,2}\.\d{1,2} \S")
_BULLET_RE = re.compile(r"^•\s+")
_NUMBERED_RE = re.compile(r"^(\d+)\.\s{2,}")
_CELL_GAP_RE = re.compile(r"\s{3,}")


@dataclass(frozen=True)
class PolicySection:
    number: int
    title: str
    text: str

    @property
    def slug(self) -> str:
        return slugify(self.title)

    @property
    def heading(self) -> str:
        return f"{self.number}. {self.title}"


class PolicyManual:
    def __init__(self, sections: list[PolicySection]) -> None:
        self.sections = sections

    @classmethod
    def from_pdf(cls, path: Path) -> PolicyManual:
        pages = PdfReader(path).pages[1:]  # page 1 is the cover
        lines = [line for page in pages for line in page.extract_text(extraction_mode="layout").splitlines()]
        return cls(_split_sections(lines))

    @property
    def customer_sections(self) -> list[PolicySection]:
        return [s for s in self.sections if s.number not in INTERNAL_SECTIONS]

    def get(self, slug: str) -> PolicySection | None:
        return next((s for s in self.sections if s.slug == slug), None)

    def number(self, number: int) -> PolicySection:
        return next(s for s in self.sections if s.number == number)


def _split_sections(raw_lines: list[str]) -> list[PolicySection]:
    sections: list[PolicySection] = []
    current: tuple[int, str] | None = None
    body = _Reflow()

    def flush() -> None:
        if current:
            text = body.text()
            for wrong, right in ERRATA.items():
                text = text.replace(wrong, right)
            sections.append(PolicySection(current[0], current[1], text))

    for raw in raw_lines:
        stripped = raw.strip()
        if stripped == _PAGE_HEADER or _FOOTER_RE.match(stripped):
            continue
        heading = _SECTION_RE.match(raw)  # top-level headings start at column 0
        expected = (current[0] + 1) if current else 1
        if heading and int(heading.group(1)) == expected:
            flush()
            current, body = (expected, heading.group(2).strip()), _Reflow()
        elif current:
            body.add(raw)
    flush()
    return sections


class _Reflow:
    """Rebuild readable text from the PDF's layout lines: paragraphs and bullets
    wrapped by the page width are re-joined, table rows become "a | b | c"."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.kinds: list[str] = []

    def add(self, raw: str) -> None:
        stripped = raw.strip()
        last = self.kinds[-1] if self.kinds else "blank"
        if not stripped:
            if last != "blank":
                self._push("", "blank")
            return
        indent = len(raw) - len(raw.lstrip())
        has_cells = bool(_CELL_GAP_RE.search(stripped))
        if _BULLET_RE.match(stripped):
            self._push("- " + _BULLET_RE.sub("", stripped), "item")
        elif _NUMBERED_RE.match(stripped):
            self._push(_NUMBERED_RE.sub(r"\1. ", stripped), "item")
        elif indent >= 5 and not has_cells and last in ("item", "row", "text"):
            self.lines[-1] += " " + stripped  # wrapped bullet or table cell
        elif has_cells:
            self._push(_CELL_GAP_RE.sub(" | ", stripped), "row")
        elif indent == 0 and _SUBSECTION_RE.match(stripped):
            self._push(stripped, "heading")
        elif last == "text" and not self.lines[-1].endswith((".", ":", ";", "!", "?")):
            self.lines[-1] += " " + stripped  # paragraph wrapped by the page width
        else:
            self._push(stripped, "text")

    def _push(self, line: str, kind: str) -> None:
        self.lines.append(line)
        self.kinds.append(kind)

    def text(self) -> str:
        return "\n".join(self.lines).strip()
