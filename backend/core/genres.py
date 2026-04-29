"""Canonical genre catalog used by the ``/welcome`` taste step.

The list is small and hand-curated — these are the twelve buckets the
taste step renders as tiles. The backend owns the canonical list (label,
slug, emoji) so the frontend never ships a genre the backend can't
validate against, and so a future change (add "Country", rename a tile)
is a backend-only deploy.

The ``slug`` is the value persisted on :attr:`User.genre_preferences`
and validated against :data:`GENRE_SLUGS` at write time by the
:mod:`backend.services.users` patch path.
"""

from __future__ import annotations

from typing import TypedDict


class Genre(TypedDict):
    """Serialized genre entry returned by ``GET /api/v1/genres``.

    Attributes:
        slug: Stable machine identifier persisted on the user row.
        label: Display name rendered on the onboarding tile.
        emoji: One glyph shown above the label on the tile.
    """

    slug: str
    label: str
    emoji: str


GENRES: tuple[Genre, ...] = (
    {"slug": "indie-rock", "label": "Indie Rock", "emoji": "🎸"},
    {"slug": "hip-hop", "label": "Hip Hop", "emoji": "🎤"},
    {"slug": "electronic", "label": "Electronic", "emoji": "🎛️"},
    {"slug": "jazz", "label": "Jazz", "emoji": "🎷"},
    {"slug": "r-and-b", "label": "R&B", "emoji": "🎶"},
    {"slug": "folk", "label": "Folk", "emoji": "🪕"},
    {"slug": "metal", "label": "Metal", "emoji": "🤘"},
    {"slug": "pop", "label": "Pop", "emoji": "✨"},
    {"slug": "funk-soul", "label": "Funk/Soul", "emoji": "🕺"},
    {"slug": "classical", "label": "Classical", "emoji": "🎻"},
    {"slug": "punk", "label": "Punk", "emoji": "💥"},
    {"slug": "alternative", "label": "Alternative", "emoji": "🎚️"},
)

GENRE_SLUGS: frozenset[str] = frozenset(g["slug"] for g in GENRES)

GENRE_LABELS: dict[str, str] = {g["slug"]: g["label"] for g in GENRES}

# Substring aliases used to match a user's onboarding genre picks against
# the free-form genre tags that scrapers and Spotify attach to events and
# artists. Matching is case-insensitive substring containment — an event
# genre of "indie rock/pop" matches the alias "indie rock", and "post-
# punk" matches the alias "punk". Aliases are deliberately specific
# multi-word phrases where possible so that a broad tag like "rock"
# doesn't sweep every rock-leaning slug into every event.
#
# Keep this list conservative: false positives dilute the "Because you
# like X" chip on the For-You page faster than missing an occasional
# match does.
GENRE_SPOTIFY_ALIASES: dict[str, tuple[str, ...]] = {
    "indie-rock": (
        "indie rock",
        "indie pop",
        "alternative rock",
        "alt-rock",
        "alt rock",
        "shoegaze",
    ),
    "hip-hop": ("hip hop", "hip-hop", "rap", "trap", "drill"),
    "electronic": (
        "electronic",
        "edm",
        "house",
        "techno",
        "trance",
        "dubstep",
        "drum and bass",
        "dnb",
    ),
    "jazz": ("jazz", "bebop", "swing", "fusion"),
    "r-and-b": ("r&b", "rnb", "soul", "neo soul", "rhythm and blues"),
    "folk": (
        "folk",
        "singer-songwriter",
        "singer/songwriter",
        "americana",
        "bluegrass",
    ),
    "metal": ("metal", "thrash", "doom"),
    "pop": ("pop",),
    "funk-soul": ("funk", "soul", "disco"),
    "classical": ("classical", "orchestral", "baroque", "chamber", "opera"),
    "punk": ("punk", "emo", "post-punk", "hardcore"),
    "alternative": (
        "alternative",
        "alt rock",
        "alt-rock",
        "new wave",
        "post-rock",
    ),
}
