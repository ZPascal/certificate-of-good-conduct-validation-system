"""Add last_expiry_alert_at column to certificate_validations.

Revision ID: 002
Revises: 001
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "certificate_validations",
        sa.Column("last_expiry_alert_at", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("certificate_validations", "last_expiry_alert_at")
