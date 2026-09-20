"""Transfer a recipe's missing ingredients onto a shopping list in one atomic write."""

from decimal import Decimal
from typing import Any

from app import db
from app.models import Shoppinglist, ShoppinglistItems, User
from app.service.inventory import InventoryError
from app.service.recipe_availability import parse_requirement
from app.service.recipe_availability_query import evaluate_recipe


def _format_amount(d: Decimal) -> str:
    """Strip trailing zeros from a Decimal, e.g. Decimal('200.000') -> '200'."""
    return format(d.normalize(), "f")


def transfer_missing(actor: User, recipe_id: int, shoppinglist_id: int) -> dict[str, Any]:
    """Move all missing (non-optional, non-available) recipe ingredients onto a shopping list.

    The recipe household and shopping-list household must match. An existing shopping-list
    entry is combined with the new amount when its note parses to the same canonical unit,
    and otherwise replaced. Returns a per-ingredient action log.
    """
    # Step 1 – authorize + fresh availability snapshot (raises InventoryError on failure).
    recipe, availabilities = evaluate_recipe(actor, recipe_id)

    # Step 2 – validate shopping list and household match.
    shoppinglist = db.session.get(Shoppinglist, shoppinglist_id)
    if shoppinglist is None:
        raise InventoryError("not_found", "Shopping list not found.", 404)
    if shoppinglist.household_id != recipe.household_id:
        raise InventoryError(
            "household_mismatch",
            "Shopping list belongs to another household.",
            403,
        )

    actions: list[dict[str, Any]] = []

    try:
        for a in availabilities:
            # Determine action and description text.
            if a.optional:
                actions.append(
                    {
                        "item_id": a.item_id,
                        "name": a.item_name,
                        "action": "skipped_optional",
                        "amount": None,
                        "description": a.source,
                    }
                )
                continue

            if a.state == "AVAILABLE":
                actions.append(
                    {
                        "item_id": a.item_id,
                        "name": a.item_name,
                        "action": "skipped_available",
                        "amount": None,
                        "description": a.source,
                    }
                )
                continue

            if a.state == "UNCERTAIN":
                actions.append(
                    {
                        "item_id": a.item_id,
                        "name": a.item_name,
                        "action": "skipped_uncertain",
                        "amount": None,
                        "description": a.source,
                    }
                )
                continue

            # States: OUT, UNTRACKED, INSUFFICIENT — need to add to shopping list.
            if a.state == "INSUFFICIENT":
                # Both required and available are non-None in the same unit.
                amount: Decimal | None = a.required - a.available  # type: ignore[operator]
            else:
                # OUT or UNTRACKED: want the full required amount.
                amount = a.required if a.required is not None else None

            unit = a.required_unit

            # Upsert the shopping-list entry.
            con = ShoppinglistItems.find_by_ids(shoppinglist_id, a.item_id)
            if con is None:
                con = ShoppinglistItems()
                con.created_by = actor.id
                con.item_id = a.item_id
                con.shoppinglist_id = shoppinglist_id
                action_label = "added"
            else:
                action_label = "updated"
                # Combine with an existing comparable amount instead of overwriting
                # it: sum only when the current note parses to the same canonical
                # unit. A note we cannot parse (or an uncomparable unit) is replaced.
                if amount is not None:
                    existing = parse_requirement(a.item_id, a.item_name, con.description)
                    if existing.amount is not None and existing.unit == unit:
                        amount = existing.amount + amount

            # Build description text (after any combine).
            if amount is not None and unit:
                description = f"{_format_amount(amount)} {unit}"
            elif amount is not None:
                description = _format_amount(amount)
            else:
                # Unknown / incomparable amount — preserve original recipe text.
                description = a.source

            con.description = description
            db.session.add(con)

            actions.append(
                {
                    "item_id": a.item_id,
                    "name": a.item_name,
                    "action": action_label,
                    "amount": description if amount is not None else None,
                    "description": description,
                }
            )

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return {
        "recipe_id": recipe_id,
        "shoppinglist_id": shoppinglist_id,
        "actions": actions,
    }
