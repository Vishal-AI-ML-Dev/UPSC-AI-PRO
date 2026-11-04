"""study_plans table (latest study plan per user)

Revision ID: 0005_study_plans
Revises: 0004_single_active_session

Phase 08 Slice B (ADR-017). Additive: one new table, no change to existing
tables or data. ``user_id`` is UNIQUE (at most one saved plan per user; the
constraint is also the conflict target of the atomic upsert) and cascades on
user deletion. JSON columns are Text for SQLite/PostgreSQL parity.

PostgreSQL only (release hardening, ADR-019): the table is backend-only, so the
migration also enables RLS (no policies) and revokes all privileges from the
Supabase API roles ``anon`` / ``authenticated`` / ``service_role`` when they
exist. The owner keeps access. No change to existing tables, grants or policies.

Written by Claude, never run by Claude. Production rollback (owner only, after
a backup): ``alembic downgrade 0004_single_active_session`` drops the table and
every saved plan in it; older application code ignores the table.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_study_plans"
down_revision: Union[str, None] = "0004_single_active_session"
branch_labels = None
depends_on = None

_TABLE = "study_plans"

# Revokes every privilege on the table from the Supabase API roles that exist.
# ``%I`` quotes identifiers; a missing role (plain PostgreSQL) is skipped.
_REVOKE_API_ROLES_SQL = f"""
DO $$
DECLARE
    api_role text;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM %I', current_schema(), '{_TABLE}', api_role);
        END IF;
    END LOOP;
END
$$
"""


def _lock_down_for_api_roles() -> None:
    """PostgreSQL only: backend-only table -> RLS on, no Supabase API-role access.

    Enables row level security (no policies, so deny-all for roles that do not own
    the table) and revokes every privilege from the Supabase API roles ``anon``,
    ``authenticated`` and ``service_role``. A role that does not exist (plain
    PostgreSQL) is skipped. The table owner -- the role that runs this migration
    and the application -- keeps normal access; FORCE ROW LEVEL SECURITY is not
    used. The statements run in the same transaction as ``create_table``, so the
    table is never visible with API-role grants. SQLite is unchanged.
    """
    if op.get_context().dialect.name != "postgresql":
        return
    op.execute(sa.text(f'ALTER TABLE "{_TABLE}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(_REVOKE_API_ROLES_SQL))


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("goal", sa.String(length=200), nullable=False),
        sa.Column("inputs", sa.Text(), nullable=False),
        sa.Column("plan_md", sa.Text(), nullable=False),
        sa.Column("meta", sa.Text(), nullable=True),
        sa.Column("progress", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_study_plans_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", name="uq_study_plans_user_id"),
    )
    _lock_down_for_api_roles()


def downgrade() -> None:
    op.drop_table(_TABLE)
