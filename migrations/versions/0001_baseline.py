"""baseline schema: users, conversations, messages, feedback, reset + verification tokens

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-08

Initial Alembic baseline. It reflects the schema previously created by
SQLAlchemy's ``create_all()``. ``upgrade()`` is intentionally *idempotent*: it
only creates tables that do not already exist. That makes it safe to run against
EITHER a fresh database OR an existing one that was bootstrapped with
``create_all()`` -- just run ``alembic upgrade head`` once and Alembic stamps
this revision without duplicating tables. Every schema change after this must
get its own migration (``alembic revision --autogenerate -m "..."``).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TOKEN_TABLES = ("password_reset_tokens", "email_verification_tokens")


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("hashed_password", sa.String(length=255), nullable=False),
            sa.Column("email_verified", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)

    if "conversations" not in existing:
        op.create_table(
            "conversations",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=32), nullable=False),
            sa.Column("agent", sa.String(length=50), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
        op.create_index("ix_conversations_agent", "conversations", ["agent"])

    if "messages" not in existing:
        op.create_table(
            "messages",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("conversation_id", sa.String(length=32), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_messages_conversation_id", "messages", ["conversation_id"]
        )

    if "feedback" not in existing:
        op.create_table(
            "feedback",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=32), nullable=False),
            sa.Column("agent", sa.String(length=50), nullable=False),
            sa.Column("rating", sa.String(length=10), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("answer", sa.Text(), nullable=False),
            sa.Column("comment", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_feedback_user_id", "feedback", ["user_id"])
        op.create_index("ix_feedback_agent", "feedback", ["agent"])

    for tbl in _TOKEN_TABLES:
        if tbl not in existing:
            op.create_table(
                tbl,
                sa.Column("id", sa.String(length=32), nullable=False),
                sa.Column("user_id", sa.String(length=32), nullable=False),
                sa.Column("token_hash", sa.String(length=64), nullable=False),
                sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
                sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
                sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
                sa.PrimaryKeyConstraint("id"),
            )
            op.create_index(f"ix_{tbl}_user_id", tbl, ["user_id"])
            op.create_index(f"ix_{tbl}_token_hash", tbl, ["token_hash"], unique=True)


def downgrade() -> None:
    for tbl in _TOKEN_TABLES:
        op.drop_index(f"ix_{tbl}_token_hash", table_name=tbl)
        op.drop_index(f"ix_{tbl}_user_id", table_name=tbl)
        op.drop_table(tbl)

    op.drop_index("ix_feedback_agent", table_name="feedback")
    op.drop_index("ix_feedback_user_id", table_name="feedback")
    op.drop_table("feedback")

    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_conversations_agent", table_name="conversations")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
