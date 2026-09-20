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


def _revision() -> fields.String:
    """A fresh field instance; marshmallow fields must not be shared across schemas."""
    return fields.String(required=True, validate=validate.Length(min=1, max=36))


class Adjustment(Schema):
    """Shared by consume and restock: a strictly positive amount in a stated unit."""

    household_id = Identifier(required=True)
    inventory_id = Identifier(required=True)
    item_id = Identifier(required=True)
    expected_revision = _revision()
    quantity = Amount(required=True)
    unit = Unit(required=True)
    quantity_is_estimate = Estimate(load_default=False)

    @validates_schema
    def strictly_positive(self, data: dict[str, Any], **kwargs: Any) -> None:
        if data["quantity"] <= 0:
            raise ValidationError("Amount must be greater than zero.")


class UpdatePantryItem(Schema):
    """A discriminated setter/metadata edit so conflicting instructions can't be sent."""

    household_id = Identifier(required=True)
    inventory_id = Identifier(required=True)
    item_id = Identifier(required=True)
    expected_revision = _revision()
    operation = fields.String(
        required=True,
        validate=validate.OneOf(
            ["set_total", "mark_available", "mark_low", "mark_out", "update_metadata"]
        ),
    )
    quantity = Amount()
    unit = Unit(allow_none=True)
    quantity_is_estimate = Estimate()
    description = fields.String(allow_none=True)

    @validates_schema
    def per_operation(self, data: dict[str, Any], **kwargs: Any) -> None:
        operation = data["operation"]
        extras = {"quantity", "unit", "quantity_is_estimate", "description"} & data.keys()
        if operation == "set_total":
            if "quantity" not in data:
                raise ValidationError("set_total requires a quantity.")
            if "quantity_is_estimate" not in data:
                raise ValidationError("set_total requires an explicit estimate flag.")
            if data["quantity"] > 0 and not data.get("unit"):
                raise ValidationError("Positive quantities require a unit.")
            if "description" in data:
                raise ValidationError("set_total does not change the note; use update_metadata.")
        elif operation == "update_metadata":
            if "description" not in data:
                raise ValidationError("update_metadata requires a description (null clears it).")
            if extras - {"description"}:
                raise ValidationError("update_metadata only changes the note.")
        elif extras:
            raise ValidationError(f"{operation} takes no additional fields.")


class RemoveStock(Schema):
    household_id = Identifier(required=True)
    inventory_id = Identifier(required=True)
    item_id = Identifier(required=True)
    expected_revision = _revision()


class RenameStorage(Schema):
    household_id = Identifier(required=True)
    inventory_id = Identifier(required=True)
    name = Name(required=True)
    expected_revision = _revision()


class DeleteStorage(Schema):
    household_id = Identifier(required=True)
    inventory_id = Identifier(required=True)
    expected_revision = _revision()


class ApplyChanges(Schema):
    """Envelope only; each command is validated per-command so errors carry an index.

    Commands are accepted as raw values (including null) rather than validated as
    dicts here: shape errors must surface from the indexed per-command loop with a
    ``details.index``, not as nested envelope field errors.
    """

    household_id = Identifier(required=True)
    commands = fields.List(
        fields.Raw(allow_none=True), required=True, validate=validate.Length(min=1, max=50)
    )
