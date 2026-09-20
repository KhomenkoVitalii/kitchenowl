"""Shared Pantry operations. Each write owns one commit, including new Items."""

import base64
import binascii
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any, Callable, TypeVar
from uuid import uuid4

from marshmallow import Schema, ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import contains_eager, joinedload

from app import db
from app.models import Household, HouseholdMember, Inventory, InventoryItems, Item, User
from app.service.inventory_schemas import (
    AddStock,
    Adjustment,
    CreateStorage,
    DeleteStorage,
    ReadPantry,
    RemoveStock,
    RenameStorage,
    UpdatePantryItem,
)

MAX_QUANTITY = Decimal("999999999.999")


class InventoryError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 409,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": self.details}


def validate_input(schema: type[Schema], data: Any) -> dict[str, Any]:
    try:
        return schema().load(data)
    except ValidationError as exc:
        raise InventoryError(
            "invalid_input", "Invalid Pantry request.", 400, {"fields": exc.messages}
        ) from exc


def authorize(household_id: int, actor: User) -> Household:
    if not actor:
        raise InventoryError("unauthenticated", "Authentication required.", 401)
    household = db.session.get(Household, household_id)
    if household is None:
        raise InventoryError("not_found", "Household not found.", 404)
    if not actor.admin and HouseholdMember.find_by_ids(household_id, actor.id) is None:
        raise InventoryError("forbidden", "Household access required.", 403)
    return household


def lock_household(household_id: int) -> None:
    """Serialize Pantry creation and Item merges on both SQLite and PostgreSQL.

    A no-op write acquires a DB lock until commit/rollback without changing the
    household's timestamp. Never use an in-process lock for this invariant.
    """
    with db.session.no_autoflush:
        db.session.execute(
            update(Household)
            .where(Household.id == household_id)
            .values(id=Household.id, updated_at=Household.updated_at)
            .execution_options(synchronize_session=False)
        )


def guard_item_merge(item: Item, other: Item) -> None:
    with db.session.no_autoflush:
        if item.household_id != other.household_id:
            raise InventoryError("household_mismatch", "Items belong to different households.")
        lock_household(item.household_id)
        tracked = db.session.scalar(
            select(InventoryItems.item_id)
            .where(InventoryItems.item_id.in_([item.id, other.id]))
            .limit(1)
        )
        if tracked is not None:
            raise InventoryError(
                "inventory_merge_requires_reconciliation",
                "Items tracked in Pantry must be reconciled before merging.",
            )


def _inventory(inventory_id: int, household_id: int) -> Inventory:
    inventory = db.session.get(Inventory, inventory_id)
    if inventory is None:
        raise InventoryError("not_found", "Inventory not found.", 404)
    if inventory.household_id != household_id:
        raise InventoryError("household_mismatch", "Inventory belongs to another household.", 403)
    return inventory


def _item(item_id: int, household_id: int) -> Item:
    item = db.session.get(Item, item_id)
    if item is None:
        raise InventoryError("not_found", "Item not found.", 404)
    if item.household_id != household_id:
        raise InventoryError("household_mismatch", "Item belongs to another household.", 403)
    return item


def household_for_inventory(inventory_id: int, actor: User) -> int:
    inventory = db.session.get(Inventory, inventory_id)
    if inventory is None:
        raise InventoryError("not_found", "Inventory not found.", 404)
    authorize(inventory.household_id, actor)
    return inventory.household_id


def _milliseconds(value: datetime) -> int:
    # SQLite returns naive values; timestamps in this application are UTC.
    return round(value.replace(tzinfo=timezone.utc).timestamp() * 1000)


def serialize_inventory(inventory: Inventory) -> dict[str, Any]:
    return {
        "id": inventory.id,
        "household_id": inventory.household_id,
        "name": inventory.name,
        "revision": inventory.revision,
        "created_at": _milliseconds(inventory.created_at),
        "updated_at": _milliseconds(inventory.updated_at),
    }


def serialize_entry(entry: InventoryItems) -> dict[str, Any]:
    return {
        "inventory_id": entry.inventory_id,
        "item_id": entry.item_id,
        "item": {"id": entry.item.id, "name": entry.item.name},
        "description": entry.description,
        "quantity": float(entry.quantity) if entry.quantity is not None else None,
        "unit": entry.unit,
        "quantity_is_estimate": entry.quantity_is_estimate,
        "state": entry.state,
        "revision": entry.revision,
        "created_by": entry.created_by,
        "created_at": _milliseconds(entry.created_at),
        "updated_at": _milliseconds(entry.updated_at),
    }


def _untracked(inventory_id: int, item: Item) -> dict[str, Any]:
    return {
        "inventory_id": inventory_id,
        "item_id": item.id,
        "item": {"id": item.id, "name": item.name},
        "state": "UNTRACKED",
        "revision": None,
    }


def get_pantry(actor: User, data: Any) -> dict[str, Any]:
    args = validate_input(ReadPantry, data)
    household_id = args["household_id"]
    authorize(household_id, actor)
    if "inventory_id" not in args:
        locations = db.session.scalars(
            select(Inventory).where(Inventory.household_id == household_id).order_by(Inventory.id)
        ).all()
        return {
            "items": [serialize_inventory(location) for location in locations],
            "default_inventory_id": locations[0].id if locations else None,
            "next_cursor": None,
        }
    inventory = _inventory(args["inventory_id"], household_id)
    if "item_id" in args:
        item = _item(args["item_id"], household_id)
        entry = db.session.get(InventoryItems, (inventory.id, item.id))
        return serialize_entry(entry) if entry else _untracked(inventory.id, item)

    context = {"inventory_id": inventory.id, "state": args.get("state"), "name": args.get("name")}
    after = 0
    if "cursor" in args:
        try:
            cursor = json.loads(base64.urlsafe_b64decode(args["cursor"]).decode())
            if (
                not isinstance(cursor, dict)
                or set(cursor) != {"context", "after"}
                or cursor["context"] != context
                or type(cursor["after"]) is not int
                or cursor["after"] < 1
            ):
                raise ValueError()
            after = cursor["after"]
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise InventoryError("invalid_cursor", "Cursor does not match this stock query.", 400) from exc
    query = (
        select(InventoryItems)
        .where(InventoryItems.inventory_id == inventory.id, InventoryItems.item_id > after)
        .order_by(InventoryItems.item_id)
    )
    match args.get("state"):
        case "AVAILABLE":
            query = query.where((InventoryItems.quantity > 0) | (InventoryItems.stock_state == "AVAILABLE"))
        case "LOW":
            query = query.where(InventoryItems.stock_state == "LOW")
        case "OUT":
            query = query.where(InventoryItems.quantity == 0)
    if "name" in args:
        # Filter on the joined Item and reuse that join to eager-load it.
        query = (
            query.join(InventoryItems.item)
            .where(func.lower(Item.name).contains(args["name"].lower(), autoescape=True))
            .options(contains_eager(InventoryItems.item))
        )
    else:
        query = query.options(joinedload(InventoryItems.item))
    limit = args.get("limit", 100)
    entries = db.session.scalars(query.limit(limit + 1)).all()
    next_cursor = None
    if len(entries) > limit:
        next_cursor = base64.urlsafe_b64encode(
            json.dumps({"context": context, "after": entries[limit - 1].item_id}).encode()
        ).decode()
    return {"items": [serialize_entry(entry) for entry in entries[:limit]], "next_cursor": next_cursor}


T = TypeVar("T")


def _write(operation: Callable[[], T]) -> T:
    try:
        result = operation()
        db.session.commit()
        return result
    except IntegrityError as exc:
        db.session.rollback()
        raise InventoryError("write_conflict", "Stock or its references changed; refresh before retrying.") from exc
    except OperationalError as exc:
        db.session.rollback()
        raise InventoryError("database_unavailable", "The database could not complete the operation; refresh before retrying.", 503) from exc
    except Exception:
        db.session.rollback()
        raise


def create_storage(actor: User, data: Any) -> dict[str, Any]:
    def apply() -> dict[str, Any]:
        args = validate_input(CreateStorage, data)
        authorize(args["household_id"], actor)
        lock_household(args["household_id"])
        inventory = Inventory(household_id=args["household_id"], name=args["name"])
        db.session.add(inventory)
        db.session.flush()
        return serialize_inventory(inventory)

    return _write(apply)


def add_stock(actor: User, data: Any) -> dict[str, Any]:
    def apply() -> dict[str, Any]:
        args = validate_input(AddStock, data)
        household_id = args["household_id"]
        authorize(household_id, actor)
        lock_household(household_id)
        inventory = _inventory(args["inventory_id"], household_id)
        if "item_id" in args:
            item = _item(args["item_id"], household_id)
        else:
            matches = db.session.scalars(
                select(Item).where(
                    Item.household_id == household_id,
                    func.lower(Item.name) == func.lower(args["name"]),
                )
            ).all()
            if len(matches) > 1:
                raise InventoryError("ambiguous_item", "Several Items have this name; use an Item ID.")
            if matches:
                item = matches[0]
            else:
                item = Item(household_id=household_id, name=args["name"])
                db.session.add(item)
                db.session.flush()
        if db.session.get(InventoryItems, (inventory.id, item.id)) is not None:
            raise InventoryError("already_tracked", "Item is already tracked in this inventory.")
        state = args.get("state")
        entry = InventoryItems(
            inventory_id=inventory.id,
            item=item,
            quantity=0 if state == "OUT" else args.get("quantity"),
            unit=args.get("unit"),
            quantity_is_estimate=args["quantity_is_estimate"],
            stock_state=state if state != "OUT" else None,
            description=args.get("description"),
            created_by=actor.id,
        )
        db.session.add(entry)
        db.session.flush()
        return serialize_entry(entry)

    return _write(apply)


def _resolve_entry(args: dict[str, Any], actor: User) -> tuple[Inventory, Item, InventoryItems]:
    household_id = args["household_id"]
    authorize(household_id, actor)
    inventory = _inventory(args["inventory_id"], household_id)
    item = _item(args["item_id"], household_id)
    entry = db.session.get(InventoryItems, (inventory.id, item.id))
    if entry is None:
        raise InventoryError("not_found", "Item is not tracked in this inventory.", 404)
    return inventory, item, entry


def _conditional_update(
    entry: InventoryItems, expected_revision: str, values: dict[str, Any]
) -> dict[str, Any]:
    """Compare-and-swap on the stored revision, enforced in the database.

    Zero affected rows means the row's revision moved (or it was deleted and its
    UUID never repeats), so a stale write cannot apply twice.
    """
    result = db.session.execute(
        update(InventoryItems)
        .where(
            InventoryItems.inventory_id == entry.inventory_id,
            InventoryItems.item_id == entry.item_id,
            InventoryItems.revision == expected_revision,
        )
        .values(**values, revision=str(uuid4()))
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise InventoryError(
            "revision_conflict", "Stock changed since it was read; refresh and retry."
        )
    db.session.expire(entry)  # drop stale ORM state; reload within this transaction
    return serialize_entry(entry)


def consume(actor: User, data: Any) -> dict[str, Any]:
    def apply() -> dict[str, Any]:
        args = validate_input(Adjustment, data)
        _, _, entry = _resolve_entry(args, actor)
        if entry.quantity is None:
            raise InventoryError("quantity_unknown", "The stored quantity is unknown; set a total first.")
        if entry.unit is not None and entry.unit != args["unit"]:
            raise InventoryError("unit_mismatch", "Use the same unit as the stored quantity.")
        new_quantity = entry.quantity - args["quantity"]
        if new_quantity < 0:
            raise InventoryError("insufficient_stock", "Not enough stock to consume that amount.")
        return _conditional_update(entry, args["expected_revision"], {
            "quantity": new_quantity,
            "unit": entry.unit,
            "stock_state": None,
            "quantity_is_estimate": entry.quantity_is_estimate or args["quantity_is_estimate"],
        })

    return _write(apply)


def restock(actor: User, data: Any) -> dict[str, Any]:
    def apply() -> dict[str, Any]:
        args = validate_input(Adjustment, data)
        _, _, entry = _resolve_entry(args, actor)
        if entry.quantity is None:
            raise InventoryError("quantity_unknown", "The stored quantity is unknown; set a total first.")
        unit = entry.unit
        if unit is None:
            unit = args["unit"]  # an OUT entry with no unit adopts the supplied one
        elif unit != args["unit"]:
            raise InventoryError("unit_mismatch", "Use the same unit as the stored quantity.")
        new_quantity = entry.quantity + args["quantity"]
        if new_quantity > MAX_QUANTITY:
            raise InventoryError("quantity_out_of_range", "The resulting quantity exceeds the maximum.")
        return _conditional_update(entry, args["expected_revision"], {
            "quantity": new_quantity,
            "unit": unit,
            "stock_state": None,
            "quantity_is_estimate": entry.quantity_is_estimate or args["quantity_is_estimate"],
        })

    return _write(apply)


def update_pantry_item(actor: User, data: Any) -> dict[str, Any]:
    def apply() -> dict[str, Any]:
        args = validate_input(UpdatePantryItem, data)
        _, _, entry = _resolve_entry(args, actor)
        operation = args["operation"]
        if operation == "set_total":
            values = {
                "quantity": args["quantity"],
                "unit": args.get("unit"),
                "stock_state": None,
                "quantity_is_estimate": args["quantity_is_estimate"],
            }
        elif operation == "mark_available":
            values = {"quantity": None, "unit": entry.unit, "stock_state": "AVAILABLE", "quantity_is_estimate": False}
        elif operation == "mark_low":
            values = {"quantity": None, "unit": entry.unit, "stock_state": "LOW", "quantity_is_estimate": False}
        elif operation == "mark_out":
            values = {"quantity": Decimal(0), "unit": entry.unit, "stock_state": None, "quantity_is_estimate": False}
        else:  # update_metadata
            values = {"description": args.get("description")}
        return _conditional_update(entry, args["expected_revision"], values)

    return _write(apply)


def remove_stock(actor: User, data: Any) -> dict[str, Any]:
    def apply() -> dict[str, Any]:
        args = validate_input(RemoveStock, data)
        inventory, item, entry = _resolve_entry(args, actor)
        result = db.session.execute(
            delete(InventoryItems)
            .where(
                InventoryItems.inventory_id == inventory.id,
                InventoryItems.item_id == item.id,
                InventoryItems.revision == args["expected_revision"],
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            raise InventoryError("revision_conflict", "Stock changed since it was read; refresh and retry.")
        db.session.expunge(entry)
        return _untracked(inventory.id, item)

    return _write(apply)


def rename_storage(actor: User, data: Any) -> dict[str, Any]:
    def apply() -> dict[str, Any]:
        args = validate_input(RenameStorage, data)
        authorize(args["household_id"], actor)
        inventory = _inventory(args["inventory_id"], args["household_id"])
        result = db.session.execute(
            update(Inventory)
            .where(Inventory.id == inventory.id, Inventory.revision == args["expected_revision"])
            .values(name=args["name"], revision=str(uuid4()))
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            raise InventoryError("revision_conflict", "Location changed since it was read; refresh and retry.")
        db.session.expire(inventory)
        return serialize_inventory(inventory)

    return _write(apply)


def delete_storage(actor: User, data: Any) -> dict[str, Any]:
    def apply() -> dict[str, Any]:
        args = validate_input(DeleteStorage, data)
        household_id = args["household_id"]
        authorize(household_id, actor)
        lock_household(household_id)  # serialize against concurrent stock creation
        inventory = _inventory(args["inventory_id"], household_id)
        inventory_id = inventory.id
        default_id = db.session.scalar(
            select(func.min(Inventory.id)).where(Inventory.household_id == household_id)
        )
        if inventory_id == default_id:
            raise InventoryError("default_location", "The default location cannot be deleted.")
        tracked = db.session.scalar(
            select(InventoryItems.item_id).where(InventoryItems.inventory_id == inventory_id).limit(1)
        )
        if tracked is not None:
            raise InventoryError("location_not_empty", "Remove all tracked items before deleting this location.")
        result = db.session.execute(
            delete(Inventory)
            .where(Inventory.id == inventory_id, Inventory.revision == args["expected_revision"])
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            raise InventoryError("revision_conflict", "Location changed since it was read; refresh and retry.")
        db.session.expunge(inventory)
        return {"id": inventory_id, "deleted": True}

    return _write(apply)
