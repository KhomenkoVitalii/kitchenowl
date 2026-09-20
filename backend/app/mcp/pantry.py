"""Pantry tool metadata; all behavior is shared with REST in the service."""

from dataclasses import dataclass
from typing import Any, Callable

from app.models import User
from app.service import inventory

ID = {"type": "integer", "minimum": 1}
NAME = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"\S"}
UNIT = {"type": ["string", "null"], "minLength": 1, "maxLength": 32, "pattern": r"\S"}
QUANTITY = {"type": "number", "minimum": 0, "maximum": 999999999.999, "multipleOf": 0.001}
REVISION = {"type": "string", "minLength": 1, "maxLength": 36}

# A JSON Schema `false` subschema forbids the property. Bulk mark_*/remove take
# only item_id + expected_revision; these keep the schema in step with the service.
_NO_STOCK_FIELDS = {
    "quantity": False, "unit": False, "quantity_is_estimate": False,
    "state": False, "description": False, "name": False,
}
_STRICT_AMOUNT = {"type": "number", "exclusiveMinimum": 0, "maximum": 999999999.999, "multipleOf": 0.001}

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
    "consume_pantry_item": PantryTool(
        description=(
            "Deduct a strictly positive amount of an Item from its tracked total. The quantity "
            "argument is added to the running deduction — do NOT call this twice on a timeout; "
            "re-read the entry and verify whether the first call landed before retrying. The unit "
            "must match the unit stored on the entry; passing a different unit is rejected. If the "
            "stored quantity is unknown (null) or qualitative (AVAILABLE/LOW), call "
            "update_pantry_item with operation=set_total first. On a revision conflict the client "
            "MUST re-read the entry and retry — never blindly resend the deduction, as the stored "
            "quantity may have already been updated."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "household_id": ID,
                "inventory_id": ID,
                "item_id": ID,
                "expected_revision": REVISION,
                "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 999999999.999, "multipleOf": 0.001},
                "unit": {"type": "string", "minLength": 1, "maxLength": 32},
                "quantity_is_estimate": {"type": "boolean"},
            },
            "required": ["household_id", "inventory_id", "item_id", "expected_revision", "quantity", "unit"],
            "additionalProperties": False,
        },
        output_schema=ENTRY,
        handler=inventory.consume,
    ),
    "restock_pantry_item": PantryTool(
        description=(
            "Add a strictly positive amount to an Item's tracked total. This increments the "
            "existing quantity — it does NOT replace it (use update_pantry_item with "
            "operation=set_total to replace). The unit must match the unit stored on the entry; "
            "an OUT entry with no unit adopts the supplied one. If the stored quantity is unknown "
            "(null) or qualitative (AVAILABLE/LOW), call update_pantry_item with "
            "operation=set_total first. On a revision conflict the client MUST re-read the entry "
            "and retry — never blindly resend the increment, as the stored quantity may have "
            "already been updated."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "household_id": ID,
                "inventory_id": ID,
                "item_id": ID,
                "expected_revision": REVISION,
                "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 999999999.999, "multipleOf": 0.001},
                "unit": {"type": "string", "minLength": 1, "maxLength": 32},
                "quantity_is_estimate": {"type": "boolean"},
            },
            "required": ["household_id", "inventory_id", "item_id", "expected_revision", "quantity", "unit"],
            "additionalProperties": False,
        },
        output_schema=ENTRY,
        handler=inventory.restock,
    ),
    "update_pantry_item": PantryTool(
        description=(
            "Mutate an existing tracked entry using one of five operations. "
            "set_total REPLACES the stored quantity in full — it does not add to or subtract from "
            "it; contrast with restock_pantry_item and consume_pantry_item. Positive quantities "
            "require a unit; zero is permitted without one. mark_available and mark_low CLEAR the "
            "numeric quantity and record a qualitative state (unknown amount, present or low). "
            "mark_out sets quantity to zero. update_metadata edits the free-text description "
            "only; pass null to clear it. Each operation is discriminated — you cannot mix "
            "quantity fields with a mark_* or update_metadata call. On a revision conflict the "
            "client MUST re-read the entry and retry with the current revision."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "household_id": ID,
                "inventory_id": ID,
                "item_id": ID,
                "expected_revision": REVISION,
                "operation": {"enum": ["set_total", "mark_available", "mark_low", "mark_out", "update_metadata"]},
                "quantity": QUANTITY,
                "unit": UNIT,
                "quantity_is_estimate": {"type": "boolean"},
                "description": {"type": ["string", "null"]},
            },
            "required": ["household_id", "inventory_id", "item_id", "expected_revision", "operation"],
            "additionalProperties": False,
            "allOf": [
                {
                    "if": {"properties": {"operation": {"const": "set_total"}}, "required": ["operation"]},
                    "then": {
                        "required": ["quantity", "quantity_is_estimate"],
                        "allOf": [
                            {
                                "if": {"properties": {"quantity": {"exclusiveMinimum": 0}}, "required": ["quantity"]},
                                "then": {"properties": {"unit": {"type": "string"}}, "required": ["unit"]},
                            }
                        ],
                    },
                },
                {
                    "if": {"properties": {"operation": {"const": "update_metadata"}}, "required": ["operation"]},
                    "then": {"required": ["description"]},
                },
            ],
        },
        output_schema=ENTRY,
        handler=inventory.update_pantry_item,
    ),
    "remove_pantry_item": PantryTool(
        description=(
            "Stop tracking an Item in a location. This removes the stock observation entirely; "
            "the Item itself is not deleted from the household catalog. The call returns an "
            "UNTRACKED observation confirming the item is no longer followed in this location. "
            "expected_revision guards against concurrent edits: on a conflict, re-read the entry "
            "and retry. To track the item again, use add_pantry_item."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "household_id": ID,
                "inventory_id": ID,
                "item_id": ID,
                "expected_revision": REVISION,
            },
            "required": ["household_id", "inventory_id", "item_id", "expected_revision"],
            "additionalProperties": False,
        },
        output_schema=UNTRACKED,
        handler=inventory.remove_stock,
    ),
    "update_pantry_storage": PantryTool(
        description=(
            "Rename an existing storage location. Names are display labels only — they are not "
            "unique identifiers. Use the returned id for all future operations; the id never "
            "changes. expected_revision guards against concurrent renames: on a conflict, "
            "re-read the location list and retry with the current revision."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "household_id": ID,
                "inventory_id": ID,
                "name": NAME,
                "expected_revision": REVISION,
            },
            "required": ["household_id", "inventory_id", "name", "expected_revision"],
            "additionalProperties": False,
        },
        output_schema=LOCATION,
        handler=inventory.rename_storage,
    ),
    "apply_pantry_changes": PantryTool(
        description=(
            "Apply 1–50 pantry-entry changes to ONE household ATOMICALLY. Any invalid or stale "
            "command rolls the ENTIRE batch back — including catalog Items that earlier add "
            "commands in the same batch would have created — and returns the failing command's "
            "zero-based index in the error details. Duplicate targets (including two name strings "
            "that resolve to the same catalog Item) are detected and rejected before any write "
            "occurs. Results are returned in the same order as the input commands. This is NOT "
            "JSON-RPC batching: it is a single transactional operation. Location management "
            "(create/rename/delete storage) is excluded — use the dedicated storage tools instead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "household_id": ID,
                "commands": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "properties": {
                            "command": {"enum": ["add", "consume", "restock", "set_total",
                                                 "mark_available", "mark_low", "mark_out",
                                                 "update_metadata", "remove"]},
                            "inventory_id": ID,
                            "item_id": ID,
                            "name": NAME,
                            "expected_revision": REVISION,
                            "quantity": QUANTITY,
                            "unit": UNIT,
                            "quantity_is_estimate": {"type": "boolean"},
                            "state": {"enum": ["AVAILABLE", "LOW", "OUT"]},
                            "description": {"type": ["string", "null"]},
                        },
                        "required": ["command", "inventory_id"],
                        "additionalProperties": False,
                        "allOf": [
                            {
                                "if": {"properties": {"command": {"const": "add"}}, "required": ["command"]},
                                "then": {
                                    "properties": {"expected_revision": False},
                                    "allOf": [
                                        {"oneOf": [{"required": ["item_id"]}, {"required": ["name"]}]},
                                        {"oneOf": [{"required": ["quantity"]}, {"required": ["state"]}]},
                                        {
                                            "if": {"properties": {"quantity": {"exclusiveMinimum": 0}}, "required": ["quantity"]},
                                            "then": {"properties": {"unit": {"type": "string"}}, "required": ["unit"]},
                                        },
                                        {
                                            "if": {"required": ["state"]},
                                            "then": {"properties": {"quantity_is_estimate": {"const": False}}},
                                        },
                                    ],
                                },
                            },
                            {
                                "if": {"properties": {"command": {"const": "consume"}}, "required": ["command"]},
                                "then": {
                                    "required": ["item_id", "expected_revision", "quantity", "unit"],
                                    "properties": {"quantity": _STRICT_AMOUNT, "unit": {"type": "string"},
                                                   "name": False, "state": False, "description": False},
                                },
                            },
                            {
                                "if": {"properties": {"command": {"const": "restock"}}, "required": ["command"]},
                                "then": {
                                    "required": ["item_id", "expected_revision", "quantity", "unit"],
                                    "properties": {"quantity": _STRICT_AMOUNT, "unit": {"type": "string"},
                                                   "name": False, "state": False, "description": False},
                                },
                            },
                            {
                                "if": {"properties": {"command": {"const": "set_total"}}, "required": ["command"]},
                                "then": {
                                    "required": ["item_id", "expected_revision", "quantity", "quantity_is_estimate"],
                                    "properties": {"name": False, "state": False, "description": False},
                                    "allOf": [
                                        {
                                            "if": {"properties": {"quantity": {"exclusiveMinimum": 0}}, "required": ["quantity"]},
                                            "then": {"properties": {"unit": {"type": "string"}}, "required": ["unit"]},
                                        }
                                    ],
                                },
                            },
                            {
                                "if": {"properties": {"command": {"const": "mark_available"}}, "required": ["command"]},
                                "then": {"required": ["item_id", "expected_revision"], "properties": _NO_STOCK_FIELDS},
                            },
                            {
                                "if": {"properties": {"command": {"const": "mark_low"}}, "required": ["command"]},
                                "then": {"required": ["item_id", "expected_revision"], "properties": _NO_STOCK_FIELDS},
                            },
                            {
                                "if": {"properties": {"command": {"const": "mark_out"}}, "required": ["command"]},
                                "then": {"required": ["item_id", "expected_revision"], "properties": _NO_STOCK_FIELDS},
                            },
                            {
                                "if": {"properties": {"command": {"const": "update_metadata"}}, "required": ["command"]},
                                "then": {
                                    "required": ["item_id", "expected_revision", "description"],
                                    "properties": {"quantity": False, "unit": False, "quantity_is_estimate": False,
                                                   "state": False, "name": False},
                                },
                            },
                            {
                                "if": {"properties": {"command": {"const": "remove"}}, "required": ["command"]},
                                "then": {"required": ["item_id", "expected_revision"], "properties": _NO_STOCK_FIELDS},
                            },
                        ],
                    },
                },
            },
            "required": ["household_id", "commands"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {"oneOf": [ENTRY, UNTRACKED]},
                }
            },
            "required": ["results"],
            "additionalProperties": False,
        },
        handler=inventory.apply_pantry_changes,
    ),
    "remove_pantry_storage": PantryTool(
        description=(
            "Delete an empty storage location. The location must contain no tracked items; call "
            "remove_pantry_item for each tracked entry first. The default location (the one with "
            "the lowest id in the household) cannot be deleted. expected_revision guards against "
            "concurrent edits: on a conflict, re-read the location list and retry with the "
            "current revision. Returns the deleted location id and a deleted confirmation flag."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "household_id": ID,
                "inventory_id": ID,
                "expected_revision": REVISION,
            },
            "required": ["household_id", "inventory_id", "expected_revision"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "id": ID,
                "deleted": {"const": True},
            },
            "required": ["id", "deleted"],
            "additionalProperties": False,
        },
        handler=inventory.delete_storage,
    ),
}
