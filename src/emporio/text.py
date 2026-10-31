"""Text normalisation shared by catalog search and policy parsing."""

from __future__ import annotations

import re
import unicodedata

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Plural endings that matter for catalog vocabulary ("violões" -> "violao").
_PLURAL_RULES = (
    ("oes", "ao"),
    ("aes", "ao"),
    ("ais", "al"),
    ("eis", "el"),
    ("res", "r"),
    ("zes", "z"),
)


def fold(text: str) -> str:
    """Lowercase and strip accents: "Violão Elétrico" -> "violao eletrico"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def tokens(text: str) -> list[str]:
    """Accent-insensitive alphanumeric tokens."""
    return _TOKEN_RE.findall(fold(text))


def singular(token: str) -> str:
    """Tiny Portuguese singulariser, applied to both queries and documents.

    It only has to be consistent, not linguistically perfect: the same rule runs
    on both sides of every comparison.
    """
    for suffix, replacement in _PLURAL_RULES:
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            return token[: -len(suffix)] + replacement
    if token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
        return token[:-1]
    return token


def slugify(text: str) -> str:
    return "-".join(tokens(text))


def contains_phrase(haystack_tokens: list[str], phrase: str) -> bool:
    """True when the folded tokens of ``phrase`` appear consecutively in the haystack."""
    needle = f" {' '.join(tokens(phrase))} "
    return needle in f" {' '.join(haystack_tokens)} "
