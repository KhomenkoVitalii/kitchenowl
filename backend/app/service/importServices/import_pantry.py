"""Import Pantry locations and stock from a portable household export.

Re-import replaces per-entry totals (it never adds), so importing the same file
twice is idempotent. The whole section is validated before anything is written,
and ambiguous references (duplicate location/Item names) are rejected. Item and
location references are by name; revisions are regenerated and attribution is
cleared, matching the rest of the export/import machinery.
"""

from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import func, select

from app import db
from app.models import Household, Inventory, InventoryItems, Item
from app.service.inventory import MAX_QUANTITY, InventoryError


def _entry_values(raw: dict) -> dict:
    quantity = raw.get("quantity")
    unit = raw.get("unit")
    if unit is not None:
        unit = unit.strip().lower() or None
        unit = {"piece": "pcs", "pieces": "pcs"}.get(unit, unit)
    estimate = bool(raw.get("quantity_is_estimate", False))
    description = raw.get("description")

    if quantity is not None:
        try:
            quantity = Decimal(str(quantity))
        except (InvalidOperation, ValueError) as exc:
            raise InventoryError("invalid_input", "Invalid Pantry quantity in import.", 400) from exc
        if (
            not quantity.is_finite()
            or not (0 <= quantity <= MAX_QUANTITY)
            or quantity != quantity.quantize(Decimal("0.001"))
        ):
            raise InventoryError("invalid_input", "Pantry quantity out of range in import.", 400)
        if quantity > 0 and not unit:
            raise InventoryError("invalid_input", "A positive Pantry quantity needs a unit in import.", 400)
        return {"quantity": quantity, "unit": unit, "quantity_is_estimate": estimate,
                "stock_state": None, "description": description}

    if raw.get("stock_state") not in ("AVAILABLE", "LOW"):
        raise InventoryError(
            "invalid_input", "A Pantry entry needs a quantity or an AVAILABLE/LOW state in import.", 400
        )
    if estimate:
        raise InventoryError("invalid_input", "A qualitative Pantry entry cannot be estimated in import.", 400)
    return {"quantity": None, "unit": unit, "quantity_is_estimate": False,
            "stock_state": raw["stock_state"], "description": description}


def importPantry(household: Household, pantry: list[dict]) -> None:
    if not pantry:
        return

    # Phase 1: validate and resolve every reference before writing anything.
    prepared: list[tuple[str, Inventory | None, list[tuple[str, dict]]]] = []
    seen_locations: set[str] = set()
    for location in pantry:
        name = location["name"].strip()[:128]
        if name.lower() in seen_locations:
            raise InventoryError("invalid_input", f"Duplicate location '{name}' in import.", 400)
        seen_locations.add(name.lower())

        location_matches = [inv for inv in household.inventories if inv.name == name]
        if len(location_matches) > 1:
            raise InventoryError("ambiguous_item", f"Several locations are named '{name}'.", 409)

        entries: list[tuple[str, dict]] = []
        seen_items: set[str] = set()
        for raw in location.get("items", []):
            item_name = raw["item"].strip()
            if item_name.lower() in seen_items:
                raise InventoryError(
                    "invalid_input", f"Duplicate item '{item_name}' in location '{name}'.", 400
                )
            seen_items.add(item_name.lower())
            item_count = db.session.scalar(
                select(func.count())
                .select_from(Item)
                .where(Item.household_id == household.id, func.lower(Item.name) == item_name.lower())
            )
            if item_count and item_count > 1:
                raise InventoryError("ambiguous_item", f"Several Items are named '{item_name}'.", 409)
            entries.append((item_name, _entry_values(raw)))
        prepared.append((name, location_matches[0] if location_matches else None, entries))

    # Phase 2: apply. Any new Item/location is created once and reused by name.
    for name, location, entries in prepared:
        if location is None:
            location = Inventory(household_id=household.id, name=name)
            db.session.add(location)
            db.session.flush()
        for item_name, values in entries:
            item = Item.find_by_name(household.id, item_name)
            if item is None:
                item = Item(household_id=household.id, name=item_name[:128])
                db.session.add(item)
                db.session.flush()
            entry = db.session.get(InventoryItems, (location.id, item.id))
            if entry is None:
                entry = InventoryItems(inventory_id=location.id, item_id=item.id, created_by=None)
                db.session.add(entry)
            entry.quantity = values["quantity"]
            entry.unit = values["unit"]
            entry.quantity_is_estimate = values["quantity_is_estimate"]
            entry.stock_state = values["stock_state"]
            entry.description = values["description"]
            entry.revision = str(uuid4())
    db.session.commit()
