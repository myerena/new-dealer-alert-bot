"""Keyword matching rules for dealer activity signals.

These patterns are intentionally loose to maximize recall.
False positives are expected and acceptable — the goal is
to surface anything that smells like expansion, launch,
relocation, hiring, or a new rooftop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Signal categories ───────────────────────────────────────────────

# Strong signals — high confidence of new/expanding dealership
STRONG_SIGNALS = [
    r"grand\s+opening",
    r"ribbon\s+cutting",
    r"ribbon-cutting",
    r"now\s+open",
    r"opening\s+soon",
    r"new\s+location",
    r"new\s+dealership",
    r"new\s+rooftop",
    r"new\s+store\s+open",
    r"relocated\s+to",
    r"second\s+location",
    r"third\s+location",
    r"new\s+franchise",
    r"franchise\s+agreement",
    r"groundbreaking\s+ceremony",
    r"broke\s+ground",
    r"under\s+construction",
    r"construction\s+begins",
    r"building\s+new",
    r"acquired\s+by",
    r"acquisition\s+of",
    r"new\s+owner",
    r"change\s+of\s+ownership",
    r"dealer\s+agreement",
    r"point\s+agreement",
    r"open\s+point",
]

# Medium signals — staffing and operational changes
MEDIUM_SIGNALS = [
    r"now\s+hiring",
    r"hiring\s+(general\s+manager|sales\s+manager|finance\s+manager|service\s+manager|f&i)",
    r"welcome\s+our\s+new\s+(gm|general\s+manager|dealer\s+principal)",
    r"new\s+gm\b",
    r"new\s+general\s+manager",
    r"appointed\s+(dealer|general\s+manager|president)",
    r"joins\s+as\s+(general\s+manager|dealer|president)",
    r"named\s+(general\s+manager|dealer\s+principal)",
    r"promoted\s+to\s+(general\s+manager|dealer)",
    r"expansion\s+plan",
    r"expanding\s+into",
    r"coming\s+soon",
    r"plans\s+to\s+open",
    r"set\s+to\s+open",
    r"will\s+open",
    r"slated\s+to\s+open",
    r"expected\s+to\s+open",
    r"renovation",
    r"remodel",
    r"adding\s+a\s+brand",
    r"new\s+brand\s+added",
    r"dual\s+dealership",
    r"multi-brand",
]

# Weak signals — worth tracking but noisy
WEAK_SIGNALS = [
    r"dealer\s+of\s+the\s+year",
    r"new\s+inventory",
    r"pre-owned\s+expansion",
    r"fleet\s+expansion",
    r"new\s+service\s+center",
    r"service\s+department\s+expansion",
    r"parts\s+department",
    r"body\s+shop\s+open",
    r"detail\s+center",
    r"looking\s+for\s+sales",
    r"join\s+our\s+team",
    r"career\s+opportunities",
    r"we.re\s+growing",
    r"growing\s+team",
    r"new\s+member\s+spotlight",  # Chamber directories
    r"new\s+business\s+member",
    r"welcome\s+new\s+member",
    r"ribbon\s+cutting\s+calendar",  # Chamber event pages
    r"economic\s+development",
    r"commercial\s+real\s+estate",
    r"zoning\s+approval",
    r"building\s+permit",
    r"site\s+plan\s+approval",
]


@dataclass
class KeywordMatch:
    """A keyword match found in text."""

    keyword: str
    signal_strength: str  # "strong", "medium", "weak"
    match_text: str  # The actual matched text
    position: int  # Character position in the text
    context: str  # Surrounding text for snippet


def _compile_patterns(patterns: list[str]) -> re.Pattern:
    """Compile a list of regex patterns into a single compiled pattern."""
    combined = "|".join(f"({p})" for p in patterns)
    return re.compile(combined, re.IGNORECASE)


# Pre-compiled pattern sets
_STRONG_RE = _compile_patterns(STRONG_SIGNALS)
_MEDIUM_RE = _compile_patterns(MEDIUM_SIGNALS)
_WEAK_RE = _compile_patterns(WEAK_SIGNALS)


def find_keyword_matches(text: str) -> list[KeywordMatch]:
    """Scan text for all dealer activity signal keywords.

    Returns matches sorted by signal strength (strong first).
    """
    matches = []

    for pattern, strength in [
        (_STRONG_RE, "strong"),
        (_MEDIUM_RE, "medium"),
        (_WEAK_RE, "weak"),
    ]:
        for m in pattern.finditer(text):
            # Extract surrounding context (up to 150 chars each side)
            start = max(0, m.start() - 150)
            end = min(len(text), m.end() + 150)
            context = text[start:end].strip()

            matches.append(
                KeywordMatch(
                    keyword=m.group(0).lower().strip(),
                    signal_strength=strength,
                    match_text=m.group(0),
                    position=m.start(),
                    context=context,
                )
            )

    # Sort: strong first, then medium, then weak
    order = {"strong": 0, "medium": 1, "weak": 2}
    matches.sort(key=lambda m: (order[m.signal_strength], m.position))
    return matches
