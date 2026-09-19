"""Add Pantry locations and stock observations.

Revision ID: a82c914e6d30
Revises: 0b10d67750be
"""

from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "a82c914e6d30"
down_revision = "0b10d67750be"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("household_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("revision", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 128", name="name_length"),
        sa.ForeignKeyConstraint(["household_id"], ["household.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_household_id", "inventory", ["household_id"])
    op.create_table(
        "inventory_items",
        sa.Column("inventory_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("quantity_is_estimate", sa.Boolean(), nullable=False),
        sa.Column("stock_state", sa.String(9), nullable=True),
        sa.Column("revision", sa.String(36), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "quantity IS NULL OR (quantity >= 0 AND quantity <= 999999999.999)",
            name="quantity_range",
        ),
        sa.CheckConstraint(
            "(quantity IS NULL AND stock_state IS NOT NULL "
            "AND stock_state IN ('AVAILABLE', 'LOW') AND NOT quantity_is_estimate) "
            "OR (quantity IS NOT NULL AND stock_state IS NULL)",
            name="stock_observation",
        ),
        sa.CheckConstraint("unit IS NULL OR length(trim(unit)) BETWEEN 1 AND 32", name="unit_length"),
        sa.CheckConstraint("quantity IS NULL OR quantity = 0 OR unit IS NOT NULL", name="quantity_unit"),
        sa.ForeignKeyConstraint(["inventory_id"], ["inventory.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("inventory_id", "item_id"),
    )
    op.create_index("ix_inventory_items_item_id", "inventory_items", ["item_id"])
    op.create_index("ix_inventory_items_created_by", "inventory_items", ["created_by"])

    # Migration-local definitions keep this backfill independent of future ORM code.
    household = sa.table("household", sa.column("id", sa.Integer()))
    inventory = sa.table(
        "inventory", sa.column("household_id", sa.Integer()),
        sa.column("name", sa.String()), sa.column("revision", sa.String()),
        sa.column("created_at", sa.DateTime()), sa.column("updated_at", sa.DateTime()),
    )
    connection = op.get_bind()
    now = datetime.now(timezone.utc)
    for household_id in connection.execute(sa.select(household.c.id)).scalars():
        connection.execute(inventory.insert().values(
            household_id=household_id, name="Pantry", revision=str(uuid4()),
            created_at=now, updated_at=now,
        ))


def downgrade():
    op.drop_table("inventory_items")
    op.drop_table("inventory")
