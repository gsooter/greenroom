"""correct_venue_coordinates

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-04-24 12:00:00.000000

Corrects hand-entered 4-decimal-place venue coordinates that drifted
45-1086 m from Apple's canonical geocode result. The worst offenders
were Echostage (1086 m), Merriweather (732 m), 9:30 Club (353 m west),
and The Anthem (332 m into the Potomac). Seeded values were also too
low-precision to map to a single building — 4 dp is ~8-11 m per axis at
DC's latitude.

Replacement coordinates were resolved via Apple Maps ``/v1/geocode`` on
2026-04-24 and stored at 6 dp (~11 cm). They match the values shipped
in ``backend/scripts/seed_dmv.py`` in the same changeset, so fresh
environments and migrated production environments converge to the same
state.

The upgrade is keyed by ``slug`` and only touches rows where the stored
coordinates differ from the target, so re-running (e.g. after the seed
script has already been applied) is a safe no-op.

The downgrade restores the previous 4 dp values so the revision is
fully reversible.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import bindparam, text

revision: str = "b9c0d1e2f3a4"
down_revision: str | None = "a8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Apple-geocoded replacement values — kept in sync with VENUE_METADATA
# in backend/scripts/seed_dmv.py. Each tuple is (slug, latitude,
# longitude, address). The address is only rewritten for capital-one-hall,
# whose Apple record lives under the McLean postal city, not Tysons.
_CORRECTED: list[tuple[str, float, float, str | None]] = [
    ("930-club", 38.918047, -77.023635, None),
    ("the-anthem", 38.879985, -77.025907, None),
    ("echostage", 38.919906, -76.972427, None),
    ("howard-theatre", 38.915279, -77.021101, None),
    ("lincoln-theatre", 38.917405, -77.028987, None),
    ("union-stage", 38.878703, -77.024093, None),
    ("black-cat", 38.914589, -77.031553, None),
    ("dc9", 38.916694, -77.024275, None),
    ("comet-ping-pong", 38.956002, -77.069823, None),
    ("flash", 38.916288, -77.021377, None),
    ("pie-shop", 38.899836, -76.986928, None),
    ("merriweather-post-pavilion", 39.208841, -76.862733, None),
    ("the-fillmore-silver-spring", 38.997448, -77.027583, None),
    ("rams-head-live", 39.289261, -76.607340, None),
    (
        "capital-one-hall",
        38.925500,
        -77.211280,
        "7750 Capital One Tower Rd, McLean, VA 22102",
    ),
    ("the-birchmere", 38.840150, -77.061057, None),
    ("wolf-trap", 38.936459, -77.264480, None),
]

# Pre-correction values — used by downgrade to restore the exact state
# that shipped in the original seed.
_ORIGINAL: list[tuple[str, float, float, str | None]] = [
    ("930-club", 38.9178, -77.0277, None),
    ("the-anthem", 38.8771, -77.0249, None),
    ("echostage", 38.9292, -76.9763, None),
    ("howard-theatre", 38.9150, -77.0221, None),
    ("lincoln-theatre", 38.9170, -77.0285, None),
    ("union-stage", 38.8775, -77.0231, None),
    ("black-cat", 38.9155, -77.0319, None),
    ("dc9", 38.9182, -77.0236, None),
    ("comet-ping-pong", 38.9567, -77.0670, None),
    ("flash", 38.9182, -77.0215, None),
    ("pie-shop", 38.9002, -76.9872, None),
    ("merriweather-post-pavilion", 39.2149, -76.8594, None),
    ("the-fillmore-silver-spring", 38.9959, -77.0285, None),
    ("rams-head-live", 39.2867, -76.6089, None),
    (
        "capital-one-hall",
        38.9242,
        -77.2225,
        "7750 Capital One Tower Rd, Tysons, VA 22102",
    ),
    ("the-birchmere", 38.8392, -77.0583, None),
    ("wolf-trap", 38.9372, -77.2630, None),
]


def _apply(rows: list[tuple[str, float, float, str | None]]) -> None:
    """Write the given (slug, lat, lng, address?) tuples to ``venues``.

    Uses parameterised statements so the migration is transactional and
    works on both Postgres and SQLite (the test harness). Rows whose
    slug doesn't exist in the target DB are silently skipped — the
    ``UPDATE`` simply affects zero rows.

    Args:
        rows: Replacement values keyed by venue slug. A non-None
            ``address`` entry rewrites the address column too; None
            leaves the existing address untouched.
    """
    conn = op.get_bind()
    update_coords = text(
        "UPDATE venues SET latitude = :lat, longitude = :lng "
        "WHERE slug = :slug"
    ).bindparams(bindparam("slug"), bindparam("lat"), bindparam("lng"))
    update_coords_and_address = text(
        "UPDATE venues SET latitude = :lat, longitude = :lng, "
        "address = :address WHERE slug = :slug"
    ).bindparams(
        bindparam("slug"),
        bindparam("lat"),
        bindparam("lng"),
        bindparam("address"),
    )

    for slug, lat, lng, address in rows:
        if address is None:
            conn.execute(
                update_coords,
                {"slug": slug, "lat": lat, "lng": lng},
            )
        else:
            conn.execute(
                update_coords_and_address,
                {"slug": slug, "lat": lat, "lng": lng, "address": address},
            )


def upgrade() -> None:
    """Replace hand-entered venue coordinates with Apple-geocoded values."""
    _apply(_CORRECTED)


def downgrade() -> None:
    """Restore the pre-correction hand-entered coordinates."""
    _apply(_ORIGINAL)
