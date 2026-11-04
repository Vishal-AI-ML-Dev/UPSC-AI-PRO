"""evaluation_attempts table (one row per Basic / Mains evaluator attempt)

Revision ID: 0006_evaluation_attempts
Revises: 0005_study_plans

Phase 08 Slice C1 (ADR-017). Additive: one new table and two indexes, no change
to existing tables or data. Stores only the user, mode (``basic``/``mains``),
the optional allow-listed subject, the validated score (NULL = unscored) and
its maximum. **No question or answer text is stored.** ``user_id`` cascades on
user deletion.

PostgreSQL only (release hardening, ADR-019): the table is backend-only, so the
migration also enables RLS (no policies) and revokes all privileges from the
Supabase API roles ``anon`` / ``authenticated`` / ``service_role`` when they
exist. The owner keeps access. No change to existing tables, grants or policies.

Written by Claude, never run by Claude. Production rollback (owner only, after
a backup): ``alembic downgrade 0005_study_plans`` drops the table and every
recorded attempt in it; older application code ignores the table.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_evaluation_attempts"
down_revision: Union[str, None] = "0005_study_plans"
branch_labels = None
depends_on = None

_TABLE = "evaluation_attempts"

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
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("subject", sa.String(length=60), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("max_score", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_evaluation_attempts_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(f"ix_{_TABLE}_user_id", _TABLE, ["user_id"])
    op.create_index(
        "ix_evaluation_attempts_user_subject_created",
        _TABLE,
        ["user_id", "subject", "created_at"],
    )
    _lock_down_for_api_roles()


def downgrade() -> None:
    op.drop_index("ix_evaluation_attempts_user_subject_created", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)
