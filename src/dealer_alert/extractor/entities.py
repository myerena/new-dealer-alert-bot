"""Light entity extraction for dealer names, locations, and people.

Uses regex-based heuristics rather than full NER to keep dependencies light.
This is intentionally loose — recall over precision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Dealer name patterns ────────────────────────────────────────────

# Common dealer name suffixes
_DEALER_SUFFIXES = (
    r"(?:auto(?:motive)?|motors?|cars?|dealer(?:ship)?|group|"
    r"ford|chevrolet|chevy|toyota|honda|nissan|hyundai|kia|"
    r"dodge|ram|jeep|chrysler|subaru|mazda|bmw|mercedes|audi|"
    r"volkswagen|vw|gmc|buick|cadillac|lincoln|volvo|"
    r"acura|lexus|infiniti|genesis|"
    r"pre-owned|used\s+cars?|certified)"
)

_DEALER_NAME_RE = re.compile(
    rf"\b([A-Z][A-Za-z&'.\-]+(?:\s+[A-Z][A-Za-z&'.\-]+){{0,4}})\s+{_DEALER_SUFFIXES}\b",
    re.IGNORECASE,
)

# Blacklist of false-positive dealer name words (e.g., article headlines, common words)
_DEALER_NAME_BLACKLIST = {
    "don't",
    "the",
    "read",
    "view",
    "click",
    "don",
    "scout",
    "biggest",
    "latest",
    "today",
    "tomorrow",
    "miss",
    "exclusive",
    "new",
    "breaking",
    "top",
    "best",
    "great",
    "amazing",
    "incredible",
    "fantastic",
}

# ── Location patterns ───────────────────────────────────────────────

US_STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}

STATE_NAMES = {
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
    "district of columbia",
}

STATE_NAME_TO_ABBR = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
}

# City, State pattern: "Springfield, IL" or "Springfield, Illinois"
_CITY_STATE_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}),\s*("
    + "|".join(US_STATES)
    + "|"
    + "|".join(re.escape(s.title()) for s in STATE_NAMES)
    + r")\b"
)

# ── People patterns ─────────────────────────────────────────────────

# Title + Name: "GM John Smith", "General Manager Jane Doe"
# Updated to require clear word boundary (no concatenated text like "SaysByCaleb")
_TITLE_NAME_RE = re.compile(
    r"(?:general\s+manager|gm|dealer\s+principal|president|owner|"
    r"sales\s+manager|finance\s+manager|service\s+manager|"
    r"managing\s+partner|partner|director|vp|ceo|coo)\s+"
    r"([A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
    re.IGNORECASE,
)

# Name + title: "John Smith, General Manager"
# Updated to require clear word boundary between names
_NAME_TITLE_RE = re.compile(
    r"\b([A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?),?\s+"
    r"(?:general\s+manager|gm|dealer\s+principal|president|owner|"
    r"sales\s+manager|finance\s+manager|service\s+manager)\b",
    re.IGNORECASE,
)


@dataclass
class ExtractedEntities:
    """Entities extracted from a text fragment."""

    dealer_names: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)


def extract_entities(text: str) -> ExtractedEntities:
    """Extract dealer names, locations, and people from text.

    Uses regex heuristics — optimized for recall, not precision.
    """
    entities = ExtractedEntities()

    # Dealer names
    for m in _DEALER_NAME_RE.finditer(text):
        name = m.group(0).strip()
        # Require at least 2 words and not in blacklist
        words = name.split()
        if (
            len(words) >= 2
            and name.lower() not in _DEALER_NAME_BLACKLIST
            and words[0].lower() not in _DEALER_NAME_BLACKLIST
        ):
            entities.dealer_names.append(name)

    # City/State
    for m in _CITY_STATE_RE.finditer(text):
        city = m.group(1).strip()
        state = m.group(2).strip()
        # Normalize state to abbreviation
        if state.lower() in STATE_NAME_TO_ABBR:
            state = STATE_NAME_TO_ABBR[state.lower()]
        entities.cities.append(city)
        if state not in entities.states:
            entities.states.append(state)

    # People (deduplicate)
    seen_people = set()
    for pattern in [_TITLE_NAME_RE, _NAME_TITLE_RE]:
        for m in pattern.finditer(text):
            name = m.group(1).strip()
            if name.lower() not in seen_people and len(name) > 3:
                seen_people.add(name.lower())
                entities.people.append(name)

    # Deduplicate dealer names
    entities.dealer_names = list(dict.fromkeys(entities.dealer_names))
    entities.cities = list(dict.fromkeys(entities.cities))

    return entities
