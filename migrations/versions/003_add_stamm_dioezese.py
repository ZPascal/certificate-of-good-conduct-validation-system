"""Add stamm_name and dioezese_name columns to certificate_validations.

Revision ID: 003
Revises: 002
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "certificate_validations",
        sa.Column("stamm_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "certificate_validations",
        sa.Column("dioezese_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("certificate_validations", "dioezese_name")
    op.drop_column("certificate_validations", "stamm_name")
