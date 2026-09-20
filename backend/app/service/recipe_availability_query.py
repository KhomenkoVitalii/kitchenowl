"""Bridge Recipe/Pantry database models to the pure availability comparison.

The comparison itself lives in ``app.service.recipe_availability`` and is
Flask-free. This adapter authorizes the household, turns ``RecipeItems`` into
``Requirement`` values and a household's ``InventoryItems`` into ``Observation``
values, then serializes the result for REST and MCP.

Bulk evaluation loads the household's Pantry exactly once (``pantry_snapshot``)
and reuses that snapshot for every recipe; it never issues one pantry query per
recipe. Availability is always computed here, never stored.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select

from app import db
from app.models import Inventory, InventoryItems, Recipe, User
from app.service.inventory import InventoryError, authorize
from app.service.recipe_availability import (
    Availability,
    Observation,
    Requirement,
    compare_requirements,
    parse_requirement,
    recipe_status,
)

_KNOWN_SHORTAGE = {"OUT", "INSUFFICIENT"}
_UNCERTAIN = {"UNCERTAIN", "UNTRACKED"}


def pantry_snapshot(household_id: int) -> dict[int, list[Observation]]:
    """One query over every location in the household, grouped by Item.

    Qualitative rows (null quantity) map to an amount-less Observation, which the
    comparison core treats as uncertain. Callers pass the whole snapshot to
    ``compare_requirements``; a bulk request builds it once and reuses it.
    """
    rows = db.session.scalars(
        select(InventoryItems)
        .join(Inventory, InventoryItems.inventory_id == Inventory.id)
        .where(Inventory.household_id == household_id)
    ).all()
    snapshot: dict[int, list[Observation]] = {}
    for row in rows:
        snapshot.setdefault(row.item_id, []).append(
            Observation(
                item_id=row.item_id,
                quantity=row.quantity,
                unit=row.unit,
                quantity_is_estimate=row.quantity_is_estimate,
            )
        )
    return snapshot


def recipe_requirements(recipe: Recipe) -> list[Requirement]:
    """Build requirements from a recipe's stored ingredients.

    ``description`` is the free-text amount source; unsupported text becomes an
    amount-less requirement rather than a guessed zero.
    """
    return [
        parse_requirement(
            entry.item_id, entry.item.name, entry.description, entry.optional
        )
        for entry in recipe.items
    ]


def _serialize_ingredient(availability: Availability) -> dict[str, Any]:
    return {
        "item_id": availability.item_id,
        "name": availability.item_name,
        "status": availability.state,
        "required": (
            float(availability.required) if availability.required is not None else None
        ),
        "required_unit": availability.required_unit,
        "available": (
            float(availability.available)
            if availability.available is not None
            else None
        ),
        "available_unit": availability.available_unit,
        "available_is_partial": availability.available_is_partial,
        "optional": availability.optional,
        "description": availability.source,
    }


def _counts(availabilities: Iterable[Availability]) -> tuple[int, int]:
    """Known-shortage and uncertain counts over required ingredients only."""
    required = [row for row in availabilities if not row.optional]
    missing = sum(1 for row in required if row.state in _KNOWN_SHORTAGE)
    uncertain = sum(1 for row in required if row.state in _UNCERTAIN)
    return missing, uncertain


def _evaluate(
    recipe: Recipe, snapshot: Mapping[int, Iterable[Observation]]
) -> list[Availability]:
    return compare_requirements(recipe_requirements(recipe), snapshot)


def _rollup(recipe: Recipe, availabilities: list[Availability]) -> dict[str, Any]:
    missing, uncertain = _counts(availabilities)
    return {
        "recipe_id": recipe.id,
        "name": recipe.name,
        "status": recipe_status(availabilities),
        "missing_count": missing,
        "uncertain_count": uncertain,
    }


def _authorized_recipe(recipe_id: int, actor: User) -> Recipe:
    recipe = db.session.get(Recipe, recipe_id)
    if recipe is None:
        raise InventoryError("not_found", "Recipe not found.", 404)
    authorize(recipe.household_id, actor)
    return recipe


def evaluate_recipe(actor: User, recipe_id: int) -> tuple[Recipe, list[Availability]]:
    """Authorize and compute a recipe's per-ingredient ``Availability`` objects.

    Returns the ORM ``Recipe`` and the raw comparison rows (Decimals intact), for
    callers that need the amounts — e.g. the shopping-list transfer's deficit math.
    ``recipe_availability`` serializes these; this returns them unserialized.
    """
    recipe = _authorized_recipe(recipe_id, actor)
    return recipe, _evaluate(recipe, pantry_snapshot(recipe.household_id))


def recipe_availability(actor: User, recipe_id: int) -> dict[str, Any]:
    """Single recipe: full per-ingredient breakdown plus the recipe roll-up."""
    recipe, availabilities = evaluate_recipe(actor, recipe_id)
    result = _rollup(recipe, availabilities)
    result["ingredients"] = [_serialize_ingredient(row) for row in availabilities]
    return result


def household_recipes_availability(
    actor: User, household_id: int
) -> list[dict[str, Any]]:
    """Bulk roll-up over every household recipe using one pantry snapshot.

    Returns roll-ups only; per-ingredient detail is fetched from
    ``recipe_availability`` on demand.
    """
    authorize(household_id, actor)
    snapshot = pantry_snapshot(household_id)
    recipes = db.session.scalars(
        select(Recipe).where(Recipe.household_id == household_id).order_by(Recipe.name)
    ).all()
    return [_rollup(recipe, _evaluate(recipe, snapshot)) for recipe in recipes]
