"""rename_sendgrid_message_id_to_provider_message_id

Revision ID: a1b2c3d4e5f6
Revises: f4d92a0c1e51
Create Date: 2026-04-20 13:00:00.000000

Greenroom swapped SendGrid for Resend as its transactional-email
provider. The ``email_digest_log.sendgrid_message_id`` column name is
now a lie — it stores whatever message id the active provider returns.
Rename it to ``provider_message_id`` so future provider swaps do not
require another migration.

Downgrade restores the old name verbatim.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f4d92a0c1e51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename ``sendgrid_message_id`` to ``provider_message_id``."""
    op.alter_column(
        "email_digest_log",
        "sendgrid_message_id",
        new_column_name="provider_message_id",
    )


def downgrade() -> None:
    """Restore the original ``sendgrid_message_id`` column name."""
    op.alter_column(
        "email_digest_log",
        "provider_message_id",
        new_column_name="sendgrid_message_id",
    )
