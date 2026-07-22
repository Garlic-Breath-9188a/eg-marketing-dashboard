"""Resolve free-text task titles to canonical HubSpot company names.

Ported from the Roland Express server (`server.js`), which enriched Asana tasks
and Slack messages with a company so they could be grouped alongside HubSpot
deals. Two passes, in order:

1. **Canonical names** — match any known HubSpot company name appearing in the
   text. Longest name first, so "Franklin Templeton" wins over "Franklin".
2. **Aliases** — the shorthand Craig actually types ("wfa", "northwestern").

Both passes anchor on word boundaries. The Node original used a bare substring
test for canonical names, which mis-tagged anything containing a short company
name as a substring — a task reading "Conference (March, Vegas)" resolved to a
company called "Arch". With ~1,700 names in the cache that is not an edge case,
so `\b` anchoring applies to canonical names too, not just aliases.
"""
from __future__ import annotations

import re

# Companies that are us, or work for us — never a prospect, never a useful tag.
# Single source of truth: `app.py` imports this as EXCLUDED_DOMAINS.
EXCLUDED_COMPANY_DOMAINS = {
    "ezragroup.com",      # Own company
    "ezragroupllc.com",   # Own company (second domain, also used for email)
    "grimmandco.org",     # Fractional CMO firm
    "streetcredpr.com",   # PR firm
    "mochadesigns.co",    # External development partner
}

# Names never worth resolving a task to. Domain-based exclusion alone is not
# enough — roughly a fifth of cached HubSpot companies have no domain at all, so
# a self-named or junk record with a NULL domain would otherwise pass through.
# Compared case-insensitively against the full name.
UNMATCHABLE_COMPANY_NAMES = {
    "ezra group",
    "ezra group llc",
    "test",
    "unknown company",
}

# Shorthand → canonical HubSpot company name.
COMPANY_ALIASES: dict[str, str] = {
    "wfa": "Wells Fargo",
    "wells fargo advisors": "Wells Fargo",
    "northwestern": "Northwestern Mutual",
    "lincoln": "Lincoln Financial Group",
    "lincoln financial": "Lincoln Financial Group",
    "lfg": "Lincoln Financial Group",
    "nitrogen": "Nitrogen Wealth",
    "cetera": "Cetera Financial Group",
    "voya": "Voya Financial Advisors",
    "right capital": "Right Capital, Inc.",
    "smartx": "SmartX Advisory Solutions, Inc.",
    "docupace": "Docupace Technologies",
    "advisorengine": "AdvisorEngine, Inc.",
    "advisor engine": "AdvisorEngine, Inc.",
    "allspring": "Allspring Global Investments",
    "franklin": "Franklin Templeton",
    "franklin templeton": "Franklin Templeton",
    "marlin": "Marlin Equity",
    "pwam": "Private Wealth Asset Management",
    "nerdwallet": "Nerdwallet Wealth Partners",
    "nexhelm": "Nexhelm AI",
    "summit trail": "Summit Trail Advisors",
    "steel grove": "Steel Grove Capital Advisors",
    "hilltop": "Hilltop Securities",
    "canoe": "Canoe Intelligence",
    "envestnet": "Envestnet",
    "vestmark": "Vestmark",
    "orion": "Orion",
    "fidelity": "Fidelity",
    "robinhood": "Robinhood",
    "wilmington": "Wilmington Trust",
    "wilmington trust": "Wilmington Trust",
    "investcloud": "Investcloud",
    "intelliflo": "Intelliflo",
    "laserfiche": "Laserfiche",
    "coldstream": "Coldstream",
    "aidentified": "Aidentified",
    "amplify": "Amplify Platform",
    "westfuller": "Westfuller Advisors",
    "transcend": "Transcend Capital",
    "editorial calendar": "Editorial Calendar",
    "editorial": "Editorial Calendar",
}

def _boundary_pattern(term: str) -> re.Pattern:
    r"""Compile `term` anchored on word boundaries.

    `\b` is only meaningful next to a word character, so it is applied
    conditionally — a name like "Right Capital, Inc." ends in punctuation, and a
    trailing `\b` there would demand a following word character and never match.
    """
    esc = re.escape(term)
    prefix = r"\b" if term[:1].isalnum() else ""
    suffix = r"\b" if term[-1:].isalnum() else ""
    return re.compile(f"{prefix}{esc}{suffix}", re.IGNORECASE)


# Longest alias first so "franklin templeton" is tried before "franklin".
_ALIAS_PATTERNS = [
    (_boundary_pattern(alias), canonical)
    for alias, canonical in sorted(COMPANY_ALIASES.items(), key=lambda kv: -len(kv[0]))
]


class CompanyMatcher:
    """Resolves text to a canonical company name.

    Built once per refresh: with ~1,700 company names, compiling the patterns
    per task would mean hundreds of thousands of redundant compiles.
    """

    def __init__(self, names: list[str]):
        # Longest first so a partial name can't win — without it "Franklin"
        # could claim text that actually names "Franklin Templeton".
        unique = sorted({n for n in names if n}, key=len, reverse=True)
        self._patterns = [(_boundary_pattern(n), n) for n in unique]

    def resolve(self, texts: list[str | None]) -> str | None:
        """Return the company for the first text that matches, else None.

        `texts` is tried in priority order — for an Asana task that is the task
        name first, then the project name, so a task explicitly naming a client
        beats the board it happens to sit on.
        """
        for text in texts:
            if not text:
                continue
            for pattern, canonical in self._patterns:
                if pattern.search(text):
                    return canonical
            for pattern, canonical in _ALIAS_PATTERNS:
                if pattern.search(text):
                    return canonical
        return None
