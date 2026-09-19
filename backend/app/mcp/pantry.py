"""Pantry tool metadata; all behavior is shared with REST in the service."""

from dataclasses import dataclass
from typing import Any, Callable

from app.models import User
from app.service import inventory

ID = {"type": "integer", "minimum": 1}
NAME = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"\S"}
UNIT = {"type": ["string", "null"], "minLength": 1, "maxLength": 32, "pattern": r"\S"}
QUANTITY = {"type": "number", "minimum": 0, "maximum": 999999999.999, "multipleOf": 0.001}

LOCATION = {
    "type": "object",
    "properties": {
        "id": ID, "household_id": ID, "name": NAME,
        "revision": {"type": "string"},
        "created_at": {"type": "integer"}, "updated_at": {"type": "integer"},
    },
    "required": ["id", "household_id", "name", "revision", "created_at", "updated_at"],
    "additionalProperties": False,
}
ITEM = {
    "type": "object", "properties": {"id": ID, "name": {"type": "string"}},
    "required": ["id", "name"], "additionalProperties": False,
}
ENTRY = {
    "type": "object",
    "properties": {
        "inventory_id": ID, "item_id": ID, "item": ITEM,
        "description": {"type": ["string", "null"]},
        "quantity": {"type": ["number", "null"]}, "unit": UNIT,
        "quantity_is_estimate": {"type": "boolean"},
        "state": {"enum": ["AVAILABLE", "LOW", "OUT"]},
        "revision": {"type": "string"}, "created_by": {"type": ["integer", "null"]},
        "created_at": {"type": "integer"}, "updated_at": {"type": "integer"},
    },
    "required": ["inventory_id", "item_id", "item", "description", "quantity", "unit",
                 "quantity_is_estimate", "state", "revision", "created_by", "created_at", "updated_at"],
    "additionalProperties": False,
}
UNTRACKED = {
    "type": "object",
    "properties": {"inventory_id": ID, "item_id": ID, "item": ITEM,
                   "state": {"const": "UNTRACKED"}, "revision": {"type": "null"}},
    "required": ["inventory_id", "item_id", "item", "state", "revision"],
    "additionalProperties": False,
}
STOCK_PAGE = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": ENTRY},
                   "next_cursor": {"type": ["string", "null"]}},
    "required": ["items", "next_cursor"], "additionalProperties": False,
}
LOCATIONS = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": LOCATION},
                   "default_inventory_id": {"type": ["integer", "null"]},
                   "next_cursor": {"type": "null"}},
    "required": ["items", "default_inventory_id", "next_cursor"], "additionalProperties": False,
}


@dataclass(frozen=True)
class PantryTool:
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
    "get_pantry": PantryTool(
        description=(
            "Read Pantry in an explicitly selected household. Without inventory_id, list locations "
            "and the default ID. With inventory_id, list stock; follow next_cursor to the end. "
            "With item_id as well, read one observation, including UNTRACKED. AVAILABLE means "
            "present, not necessarily sufficient for a recipe. Null quantity means unknown. "
            "Reads never create stock or locations."
        ),
        input_schema={
            "type": "object", "properties": {
                "household_id": ID, "inventory_id": ID, "item_id": ID,
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "cursor": {"type": "string", "minLength": 1, "maxLength": 1024},
                "state": {"enum": ["AVAILABLE", "LOW", "OUT"]}, "name": NAME,
            },
            "required": ["household_id"], "additionalProperties": False,
            "allOf": [
                {"if": {"anyOf": [{"required": [key]} for key in ("item_id", "limit", "cursor", "state", "name")]},
                 "then": {"required": ["inventory_id"]}},
                {"if": {"required": ["item_id"]},
                 "then": {"not": {"anyOf": [{"required": [key]} for key in ("limit", "cursor", "state", "name")]}}},
            ],
        },
        output_schema={"type": "object", "oneOf": [LOCATIONS, STOCK_PAGE, ENTRY, UNTRACKED]},
        handler=inventory.get_pantry,
        read_only=True,
    ),
    "create_pantry_storage": PantryTool(
        description="Create a named location such as Fridge in the selected household. Use returned IDs for later operations; names are not unique identifiers.",
        input_schema={"type": "object", "properties": {"household_id": ID, "name": NAME},
                      "required": ["household_id", "name"], "additionalProperties": False},
        output_schema=LOCATION,
        handler=inventory.create_storage,
    ),
    "add_pantry_item": PantryTool(
        description=(
            "Start tracking an Item in a location, only if absent. Choose item_id OR an exact "
            "catalog name; a missing name creates an Item atomically with stock. Record an "
            "observed total (quantity, unit, optional estimate) OR state AVAILABLE/LOW/OUT. "
            "Quantities support three decimals. OUT is zero; LOW/AVAILABLE have unknown quantity. "
            "This never increments or overwrites existing stock. After a timeout, read the "
            "entry before retrying. Household, Item and location must belong together."
        ),
        input_schema={
            "type": "object", "properties": {
                "household_id": ID, "inventory_id": ID, "item_id": ID, "name": NAME,
                "quantity": QUANTITY, "unit": UNIT, "quantity_is_estimate": {"type": "boolean"},
                "state": {"enum": ["AVAILABLE", "LOW", "OUT"]},
                "description": {"type": ["string", "null"]},
            },
            "required": ["household_id", "inventory_id"], "additionalProperties": False,
            "allOf": [
                {"oneOf": [{"required": ["item_id"]}, {"required": ["name"]}]},
                {"oneOf": [{"required": ["quantity"]}, {"required": ["state"]}]},
                {"if": {"properties": {"quantity": {"exclusiveMinimum": 0}}, "required": ["quantity"]},
                 "then": {"properties": {"unit": {"type": "string"}}, "required": ["unit"]}},
                {"if": {"required": ["state"]}, "then": {"properties": {"quantity_is_estimate": {"const": False}}}},
            ],
        },
        output_schema=ENTRY,
        handler=inventory.add_stock,
    ),
}
