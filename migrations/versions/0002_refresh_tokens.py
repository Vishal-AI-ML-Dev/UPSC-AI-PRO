"""refresh_tokens table (rotating, revocable session tokens)

Revision ID: 0002_refresh_tokens
Revises: 0001_baseline
Create Date: 2026-07-08

Adds the ``refresh_tokens`` table introduced with refresh-token rotation
(#9 Security). Kept idempotent (create only if missing) for the same reason as
the baseline: it must be safe to run against an existing production database
whose other tables were bootstrapped by ``create_all()``. This is the first
real *incremental* migration -- exactly the workflow Alembic (#10) unlocked.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_refresh_tokens"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "refresh_tokens"


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if _TABLE in _existing_tables():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(f"ix_{_TABLE}_user_id", _TABLE, ["user_id"])
    op.create_index(f"ix_{_TABLE}_token_hash", _TABLE, ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index(f"ix_{_TABLE}_token_hash", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)
