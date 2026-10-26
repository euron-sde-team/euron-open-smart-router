"""One default routing policy, enforced by the database; drop an unused column.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The service already clears the old default before setting a new one; the partial unique
    # index makes two concurrent "make default" requests unable to leave two defaults behind.
    op.create_index(
        "uq_routing_policies_single_default",
        "routing_policies",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    # Inherited from the fork: alerts are raised for model health, never for budgets.
    op.drop_column("alerts", "budget_id")


def downgrade() -> None:
    op.add_column("alerts", sa.Column("budget_id", sa.UUID(), nullable=True))
    op.drop_index("uq_routing_policies_single_default", table_name="routing_policies")
