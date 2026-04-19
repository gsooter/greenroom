"""rename_user_oauth_providers_to_music_service_connections

Revision ID: e3c4f1b2a813
Revises: d8b3e9f1a702
Create Date: 2026-04-19 17:00:00.000000

After Decision 030, ``user_oauth_providers`` holds only connected music
services (identity providers live in Knuckles). Rename the table and its
indexes to match. The Postgres enum type ``oauth_provider`` keeps its
name — renaming it would cascade through every column bind and is not
worth the cost for a purely cosmetic change.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e3c4f1b2a813"
down_revision: Union[str, None] = "d8b3e9f1a702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename the table and its indexes to ``music_service_connections``."""
    op.rename_table("user_oauth_providers", "music_service_connections")
    op.execute(
        "ALTER INDEX ix_user_oauth_providers_user_id "
        "RENAME TO ix_music_service_connections_user_id"
    )
    op.execute(
        "ALTER INDEX ix_user_oauth_providers_provider_user_id "
        "RENAME TO ix_music_service_connections_provider_user_id"
    )


def downgrade() -> None:
    """Reverse rename back to ``user_oauth_providers``."""
    op.execute(
        "ALTER INDEX ix_music_service_connections_provider_user_id "
        "RENAME TO ix_user_oauth_providers_provider_user_id"
    )
    op.execute(
        "ALTER INDEX ix_music_service_connections_user_id "
        "RENAME TO ix_user_oauth_providers_user_id"
    )
    op.rename_table("music_service_connections", "user_oauth_providers")
