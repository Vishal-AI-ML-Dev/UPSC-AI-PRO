"""single active session: users.active_session_id + refresh_tokens.session_id

Revision ID: 0004_single_active_session
Revises: 0003_jobs

Adds the server-side session identity used to enforce one active session per
account. Both columns are nullable and no existing data is changed: legacy
refresh tokens (session_id NULL) and access tokens without a ``sid`` claim are
rejected by the application, so every user signs in once after deployment.
Idempotent (skips columns that already exist), like 0002.
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_single_active_session"
down_revision: Union[str, None] = "0003_jobs"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "active_session_id" not in _columns("users"):
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("active_session_id", sa.String(length=32), nullable=True))
    if "session_id" not in _columns("refresh_tokens"):
        with op.batch_alter_table("refresh_tokens") as batch:
            batch.add_column(sa.Column("session_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    # Dropping these columns returns the app to stateless access tokens; see the
    # recovery notes in the deployment docs before ever running this in production.
    if "session_id" in _columns("refresh_tokens"):
        with op.batch_alter_table("refresh_tokens") as batch:
            batch.drop_column("session_id")
    if "active_session_id" in _columns("users"):
        with op.batch_alter_table("users") as batch:
            batch.drop_column("active_session_id")
