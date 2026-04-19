"""drop_local_auth_tables

Revision ID: d8b3e9f1a702
Revises: c2f1a3d8e901
Create Date: 2026-04-19 16:00:00.000000

Decision 030 cutover: Knuckles is the identity anchor, so Greenroom's
local sign-in tables and columns go away.

Schema changes:

1. ``magic_link_tokens`` — dropped. Knuckles owns magic-link tokens now.
2. ``passkey_credentials`` — dropped. Knuckles owns passkey credentials.
3. ``users.password_hash`` — dropped. Knuckles owns password material.

The ``oauth_provider`` enum keeps ``google``, ``apple``, and ``passkey``
values rather than rewriting the type — Postgres enum-value removal is
multi-step and risky, and carrying unused enum values costs nothing.
Application code treats the enum as {spotify, apple_music, tidal} from
here on (see ``backend.data.models.users.OAuthProvider``).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d8b3e9f1a702"
down_revision: Union[str, None] = "c2f1a3d8e901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop local identity tables and the ``users.password_hash`` column."""
    op.drop_index(
        "ix_passkey_credentials_credential_id", table_name="passkey_credentials"
    )
    op.drop_index("ix_passkey_credentials_user_id", table_name="passkey_credentials")
    op.drop_table("passkey_credentials")

    op.drop_index("ix_magic_link_tokens_user_id", table_name="magic_link_tokens")
    op.drop_index("ix_magic_link_tokens_token_hash", table_name="magic_link_tokens")
    op.drop_index("ix_magic_link_tokens_email", table_name="magic_link_tokens")
    op.drop_table("magic_link_tokens")

    op.drop_column("users", "password_hash")


def downgrade() -> None:
    """Recreate the magic-link and passkey tables and the password column.

    Mirrors the structures from the original ``auth_identity_overhaul``
    migration so a downgrade lands us back in the pre-Knuckles world if
    we ever need to roll back. Enum values on ``oauth_provider`` are
    unaffected by this migration in either direction.
    """
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(length=255), nullable=True),
    )

    op.create_table(
        "magic_link_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_magic_link_tokens_email",
        "magic_link_tokens",
        ["email"],
        unique=False,
    )
    op.create_index(
        "ix_magic_link_tokens_token_hash",
        "magic_link_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_magic_link_tokens_user_id",
        "magic_link_tokens",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "passkey_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("credential_id", sa.Text(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("transports", sa.String(length=200), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_id", name="uq_passkey_credential_id"),
    )
    op.create_index(
        "ix_passkey_credentials_user_id",
        "passkey_credentials",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_passkey_credentials_credential_id",
        "passkey_credentials",
        ["credential_id"],
        unique=False,
    )
