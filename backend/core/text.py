"""Text normalization helpers shared across layers.

Kept in :mod:`backend.core` so the data and recommendation layers can
both depend on it without reaching across the import hierarchy defined
in ``CLAUDE.md``.
"""

from __future__ import annotations

import unicodedata


def normalize_artist_name(name: str) -> str:
    """Collapse casing, diacritics, and whitespace for artist lookup.

    Used everywhere an artist name is used as a key — ``artists``
    table upserts, artist-match scoring, and any future join that
    matches scraped performer strings against Spotify catalog names.
    Output is ASCII, lowercase, single-spaced, and stripped so
    "Beyoncé", "beyonce", and "  BEYONCE  " all collapse to the same
    key.

    Args:
        name: Raw artist name string.

    Returns:
        A normalized lookup key.
    """
    stripped = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in stripped if not unicodedata.combining(c))
    return " ".join(ascii_only.lower().split())
