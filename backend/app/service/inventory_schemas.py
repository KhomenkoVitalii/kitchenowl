"""Transport-independent validation for Pantry commands and observations."""

from decimal import Decimal, InvalidOperation
from typing import Any

from marshmallow import Schema, ValidationError, fields, validate, validates_schema


class Identifier(fields.Integer):
    def __init__(self, **kwargs: Any):
        super().__init__(strict=True, validate=validate.Range(min=1), **kwargs)


class Name(fields.String):
    def _deserialize(self, value: Any, attr: Any, data: Any, **kwargs: Any) -> str:
        result = super()._deserialize(value, attr, data, **kwargs).strip()
        if not 1 <= len(result) <= 128:
            raise ValidationError("Use 1–128 nonblank characters.")
        return result


class Unit(fields.String):
    def _deserialize(self, value: Any, attr: Any, data: Any, **kwargs: Any) -> str:
        result = super()._deserialize(value, attr, data, **kwargs).strip().lower()
        if not 1 <= len(result) <= 32:
            raise ValidationError("Use 1–32 nonblank characters.")
        return "pcs" if result in {"piece", "pieces"} else result


class Estimate(fields.Boolean):
    def _deserialize(self, value: Any, attr: Any, data: Any, **kwargs: Any) -> bool:
        if type(value) is not bool:
            raise ValidationError("Use a boolean.")
        return value


class Amount(fields.Field):
    def _deserialize(self, value: Any, attr: Any, data: Any, **kwargs: Any) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValidationError("Use a JSON number.")
        try:
            amount = Decimal(str(value))
            if not amount.is_finite() or not 0 <= amount <= Decimal("999999999.999"):
                raise ValidationError("Quantity must be between 0 and 999999999.999.")
            if amount != amount.quantize(Decimal("0.001")):
                raise ValidationError("Quantity supports at most three decimal places.")
            return amount
        except InvalidOperation as exc:
            raise ValidationError("Invalid quantity.") from exc


class ReadPantry(Schema):
    household_id = Identifier(required=True)
    inventory_id = Identifier()
    item_id = Identifier()
    limit = fields.Integer(strict=True, validate=validate.Range(min=1, max=200))
    cursor = fields.String(validate=validate.Length(min=1, max=1024))
    state = fields.String(validate=validate.OneOf(["AVAILABLE", "LOW", "OUT"]))
    name = Name()

    @validates_schema
    def read_mode(self, data: dict[str, Any], **kwargs: Any) -> None:
        filters = {"limit", "cursor", "state", "name"} & data.keys()
        if "inventory_id" not in data and ("item_id" in data or filters):
            raise ValidationError("Select an inventory before an Item or filters.")
        if "item_id" in data and filters:
            raise ValidationError("Item lookup does not accept list filters.")


class CreateStorage(Schema):
    household_id = Identifier(required=True)
    name = Name(required=True)


class AddStock(Schema):
    household_id = Identifier(required=True)
    inventory_id = Identifier(required=True)
    item_id = Identifier()
    name = Name()
    quantity = Amount()
    unit = Unit(allow_none=True)
    quantity_is_estimate = Estimate(load_default=False)
    state = fields.String(validate=validate.OneOf(["AVAILABLE", "LOW", "OUT"]))
    description = fields.String(allow_none=True)

    @validates_schema
    def observation(self, data: dict[str, Any], **kwargs: Any) -> None:
        if ("item_id" in data) == ("name" in data):
            raise ValidationError("Supply exactly one of item_id or name.")
        if ("quantity" in data) == ("state" in data):
            raise ValidationError("Supply exactly one of quantity or state.")
        if data.get("quantity", 0) > 0 and not data.get("unit"):
            raise ValidationError("Positive quantities require a unit.")
        if "state" in data and data["quantity_is_estimate"]:
            raise ValidationError("Qualitative observations cannot be estimated.")
