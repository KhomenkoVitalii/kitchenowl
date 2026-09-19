"""Household storage locations and observations about their stock."""

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, event
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app import db

if TYPE_CHECKING:
    from app.helpers.db_model_base import DbModelBase
    from app.models import Household, Item, User

    Model = DbModelBase
else:
    Model = db.Model


def new_revision() -> str:
    return str(uuid4())


class Inventory(Model):
    __tablename__ = "inventory"
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 128", name="name_length"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("household.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(db.String(128))
    revision: Mapped[str] = mapped_column(db.String(36), default=new_revision)

    household: Mapped["Household"] = relationship(back_populates="inventories")
    items: Mapped[list["InventoryItems"]] = relationship(
        back_populates="inventory", cascade="all, delete-orphan"
    )


class InventoryItems(Model):
    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint(
            "quantity IS NULL OR (quantity >= 0 AND quantity <= 999999999.999)",
            name="quantity_range",
        ),
        CheckConstraint(
            "(quantity IS NULL AND stock_state IS NOT NULL "
            "AND stock_state IN ('AVAILABLE', 'LOW') AND NOT quantity_is_estimate) "
            "OR (quantity IS NOT NULL AND stock_state IS NULL)",
            name="stock_observation",
        ),
        CheckConstraint(
            "unit IS NULL OR length(trim(unit)) BETWEEN 1 AND 32",
            name="unit_length",
        ),
        CheckConstraint(
            "quantity IS NULL OR quantity = 0 OR unit IS NOT NULL",
            name="quantity_unit",
        ),
    )

    inventory_id: Mapped[int] = mapped_column(
        ForeignKey("inventory.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("item.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    description: Mapped[str | None] = mapped_column(db.String)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    unit: Mapped[str | None] = mapped_column(db.String(32))
    quantity_is_estimate: Mapped[bool] = mapped_column(default=False)
    stock_state: Mapped[str | None] = mapped_column(db.String(9))
    revision: Mapped[str] = mapped_column(db.String(36), default=new_revision)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )

    inventory: Mapped[Inventory] = relationship(back_populates="items")
    item: Mapped["Item"] = relationship(back_populates="inventory_entries")
    created_by_user: Mapped["User | None"] = relationship(
        back_populates="inventory_entries"
    )

    @property
    def state(self) -> str:
        if self.quantity is not None:
            return "OUT" if self.quantity == 0 else "AVAILABLE"
        if self.stock_state is None:
            raise ValueError("Stock row has neither a quantity nor a stock_state.")
        return self.stock_state


@event.listens_for(Session, "before_flush")
def provision_pantry(session: Session, _context: object, _instances: object) -> None:
    # Covers every ORM household creation path in the same transaction. Reads
    # never provision storage; existing households are backfilled by migration.
    from app.models import Household

    for obj in list(session.new):
        if isinstance(obj, Household) and not obj.inventories:
            obj.inventories.append(Inventory(name="Pantry"))
