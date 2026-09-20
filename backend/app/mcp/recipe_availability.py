"""Recipe availability tool metadata; all behavior is shared with REST in the service."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.models import User
from app.service import recipe_availability_query as availability_service
from app.service.recipe_shopping_transfer import transfer_missing

ID = {"type": "integer", "minimum": 1}


@dataclass(frozen=True)
class AvailabilityTool:
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler: Callable[[User, Any], dict[str, Any]]
    read_only: bool = False

    def metadata(self, name: str) -> dict[str, Any]:
        return {
            "name": name, "description": self.description,
            "inputSchema": self.input_schema, "outputSchema": self.output_schema,
            "annotations": {"readOnlyHint": self.read_only, "destructiveHint": False,
                            "idempotentHint": self.read_only, "openWorldHint": False},
        }


TOOLS = {
    "check_recipe_availability": AvailabilityTool(
        description=(
            "Return the full per-ingredient availability for ONE recipe, plus a recipe-level "
            "status tag. Each ingredient carries one of five statuses: AVAILABLE (present in "
            "pantry), INSUFFICIENT (tracked but quantity is below what the recipe needs), "
            "OUT (tracked at zero), UNTRACKED (no pantry observation exists for the item), or "
            "UNCERTAIN (the system cannot prove sufficiency — e.g. qualitative stock or "
            "unit mismatch). Never treat UNCERTAIN as available; it means the pantry data "
            "is not conclusive and a human should verify."
        ),
        input_schema={
            "type": "object",
            "properties": {"recipe_id": ID},
            "required": ["recipe_id"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        handler=lambda user, args: availability_service.recipe_availability(
            user, int(args["recipe_id"])
        ),
        read_only=True,
    ),
    "list_recipe_availability": AvailabilityTool(
        description=(
            "Return a roll-up availability summary for EVERY recipe in a household, computed "
            "from a single pantry snapshot. Each entry contains recipe_id, name, status, "
            "missing_count, and uncertain_count. Status is one of: READY_TO_COOK (all "
            "required ingredients are AVAILABLE or sufficient), MISSING_1 (exactly one "
            "ingredient is missing), MISSING_N (two or more ingredients are missing), or "
            "UNCERTAIN (at least one ingredient is UNCERTAIN with no missing ingredients). "
            "This tool returns roll-ups ONLY — it does not include per-ingredient detail. "
            "To inspect individual ingredient statuses for a specific recipe, call "
            "check_recipe_availability with that recipe_id."
        ),
        input_schema={
            "type": "object",
            "properties": {"household_id": ID},
            "required": ["household_id"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        handler=lambda user, args: {
            "items": availability_service.household_recipes_availability(
                user, int(args["household_id"])
            )
        },
        read_only=True,
    ),
    "add_missing_to_shopping_list": AvailabilityTool(
        description=(
            "Transfer a recipe's missing ingredients to a shopping list. Ingredients with "
            "status OUT or UNTRACKED have the full required amount added; INSUFFICIENT "
            "ingredients contribute only the deficit (required minus available). UNCERTAIN "
            "and optional ingredients are NOT added — uncertainty is preserved and not "
            "resolved by assumption. If an item is already present on the shopping list it "
            "is updated rather than duplicated. Returns a per-ingredient action report "
            "describing what was added, updated, or skipped."
        ),
        input_schema={
            "type": "object",
            "properties": {"recipe_id": ID, "shoppinglist_id": ID},
            "required": ["recipe_id", "shoppinglist_id"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        handler=lambda user, args: transfer_missing(
            user, int(args["recipe_id"]), int(args["shoppinglist_id"])
        ),
        read_only=False,
    ),
}
