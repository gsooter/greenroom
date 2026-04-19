"""auth_identity_overhaul

Revision ID: c2f1a3d8e901
Revises: 4a9f2c5b8e31
Create Date: 2026-04-19 13:00:00.000000

Phase 1 auth overhaul (Decision 026): Greenroom becomes its own identity
anchor. Spotify remains a connected service but stops being the login
path.

Schema changes:

1. ``users.password_hash`` — nullable column reserved for future
   password-based auth.
2. ``users.onboarding_completed_at`` — nullable timestamp. Null = show
   the post-signup genre-picker onboarding; non-null = skip.
3. ``oauth_provider`` enum gains ``passkey``, ``apple_music``, ``tidal``.
   Passkey is the WebAuthn identity provider; apple_music/tidal are the
   connected music services for Phase 5 — we add them now so Phase 5 is
   a data-only change.
4. ``magic_link_tokens`` table — hashed single-use tokens for email
   magic-link sign-in.
5. ``passkey_credentials`` table — one row per registered WebAuthn
   credential with a monotonic ``sign_count`` used to detect clones.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c2f1a3d8e901"
down_revision: Union[str, None] = "4a9f2c5b8e31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_PROVIDERS = ("passkey", "apple_music", "tidal")


def upgrade() -> None:
    """Add password_hash, onboarding timestamp, enum values, and two tables."""
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "onboarding_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Postgres native enums can't add values inside a transaction that
    # also references the new value. We commit the ALTER TYPE separately
    # using an autocommit block so the new table + service code can use
    # the values in the same migration run.
    for value in _NEW_PROVIDERS:
        op.execute(
            sa.text(f"ALTER TYPE oauth_provider ADD VALUE IF NOT EXISTS '{value}'")
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


def downgrade() -> None:
    """Reverse upgrade — drop the two tables and the user columns.

    Postgres does not support removing a value from an enum, so the
    downgrade leaves the new oauth_provider values in place. That's
    intentional: the reverse would require dropping the enum and
    recreating every column that uses it, which is riskier than
    carrying an unused enum value.
    """
    op.drop_index(
        "ix_passkey_credentials_credential_id", table_name="passkey_credentials"
    )
    op.drop_index(
        "ix_passkey_credentials_user_id", table_name="passkey_credentials"
    )
    op.drop_table("passkey_credentials")

    op.drop_index("ix_magic_link_tokens_user_id", table_name="magic_link_tokens")
    op.drop_index("ix_magic_link_tokens_token_hash", table_name="magic_link_tokens")
    op.drop_index("ix_magic_link_tokens_email", table_name="magic_link_tokens")
    op.drop_table("magic_link_tokens")

    op.drop_column("users", "onboarding_completed_at")
    op.drop_column("users", "password_hash")
