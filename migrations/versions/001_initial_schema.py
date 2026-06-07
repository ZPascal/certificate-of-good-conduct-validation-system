"""Initial schema: certificate_validations table.

Revision ID: 001
Revises:
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "certificate_validations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("paperless_document_id", sa.Integer(), nullable=False),
        sa.Column("person_name", sa.String(255), nullable=True),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cancellation_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_certificate_validations_paperless_document_id",
        "certificate_validations",
        ["paperless_document_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_certificate_validations_paperless_document_id",
        table_name="certificate_validations",
        if_exists=True,
    )
    op.drop_table("certificate_validations", if_exists=True)
