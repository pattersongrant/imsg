"""Detect and filter iMessage tapback / reaction messages."""

from __future__ import annotations

import re

# associated_message_type values for tapbacks (2000-2006) and removals (3000-3006).
REACTION_TYPE_MIN = 2000
REACTION_TYPE_MAX = 3006

REACTION_LABELS = {
    2000: "loved",
    2001: "liked",
    2002: "disliked",
    2003: "laughed",
    2004: "emphasized",
    2005: "questioned",
    2006: "reacted",
}

# Words that leak into keyword analysis when reaction text is decoded.
REACTION_WORDS = frozenset(
    {
        "loved",
        "liked",
        "disliked",
        "laughed",
        "emphasized",
        "questioned",
        "reacted",
    }
)

# iMessage reaction rows often decode to e.g. 'Loved "hey there"'.
_REACTION_TEXT_RE = re.compile(
    r"^(?:loved|liked|disliked|laughed at|emphasized|questioned)\b",
    re.I,
)


def is_reaction_type(associated_message_type: int | None) -> bool:
    if associated_message_type is None:
        return False
    return REACTION_TYPE_MIN <= associated_message_type <= REACTION_TYPE_MAX


def looks_like_reaction_text(text: str) -> bool:
    """True when plain text matches an iMessage tapback prefix."""
    if not text:
        return False
    return bool(_REACTION_TEXT_RE.match(text.strip()))


def is_reaction_message(
    text: str | None,
    *,
    associated_message_type: int | None = None,
) -> bool:
    if is_reaction_type(associated_message_type):
        return True
    return looks_like_reaction_text(text or "")


def strip_reaction_prefix(text: str) -> str:
    """Remove tapback prefix so quoted original text can be analyzed if needed."""
    stripped = text.strip()
    match = _REACTION_TEXT_RE.match(stripped)
    if not match:
        return stripped
    remainder = stripped[match.end() :].strip()
    if remainder.startswith('"') and remainder.endswith('"') and len(remainder) > 1:
        remainder = remainder[1:-1].strip()
    return remainder
