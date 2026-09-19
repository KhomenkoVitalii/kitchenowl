from typing import Any

from flask import Blueprint, jsonify, request
from flask_jwt_extended import current_user, jwt_required

from app import db
from app.service import inventory as service

inventory = Blueprint("inventory", __name__)
inventory_household = Blueprint("inventory_household", __name__)


@inventory.app_errorhandler(service.InventoryError)
def inventory_error(error: service.InventoryError):
    # Also handles a Pantry merge guard raised through the existing Item API.
    db.session.rollback()
    return jsonify(error.payload()), error.status


def _arguments(context: dict[str, int], *, query: bool = False) -> dict[str, Any]:
    args = request.args.to_dict() if query else request.get_json(silent=True)
    if not isinstance(args, dict):
        raise service.InventoryError("invalid_input", "Expected a JSON object.", 400)
    if context.keys() & args.keys():
        raise service.InventoryError("invalid_input", "Resource IDs must come from the URL.", 400)
    if query:
        if any(len(values) != 1 for _, values in request.args.lists()):
            raise service.InventoryError("invalid_input", "Duplicate query parameters.", 400)
        if "limit" in args:
            try:
                args["limit"] = int(args["limit"])
            except ValueError as exc:
                raise service.InventoryError("invalid_input", "Limit must be an integer.", 400) from exc
    return {**args, **context}


def _context(inventory_id: int) -> dict[str, int]:
    return {
        "household_id": service.household_for_inventory(inventory_id, current_user),
        "inventory_id": inventory_id,
    }


@inventory_household.route("", methods=["GET"])
@jwt_required()
def list_locations(household_id: int):
    return jsonify(service.get_pantry(current_user, _arguments({"household_id": household_id}, query=True)))


@inventory_household.route("", methods=["POST"])
@jwt_required()
def create_location(household_id: int):
    return jsonify(service.create_storage(current_user, _arguments({"household_id": household_id}))), 201


@inventory.route("/<int:inventory_id>/items", methods=["GET"])
@jwt_required()
def list_stock(inventory_id: int):
    return jsonify(service.get_pantry(current_user, _arguments(_context(inventory_id), query=True)))


@inventory.route("/<int:inventory_id>/item/<int:item_id>", methods=["GET"])
@jwt_required()
def get_stock(inventory_id: int, item_id: int):
    context = {**_context(inventory_id), "item_id": item_id}
    return jsonify(service.get_pantry(current_user, _arguments(context, query=True)))


@inventory.route("/<int:inventory_id>/item/<int:item_id>", methods=["PUT"])
@jwt_required()
def create_stock(inventory_id: int, item_id: int):
    context = {**_context(inventory_id), "item_id": item_id}
    return jsonify(service.add_stock(current_user, _arguments(context))), 201


@inventory.route("/<int:inventory_id>/add-item-by-name", methods=["POST"])
@jwt_required()
def create_stock_by_name(inventory_id: int):
    args = _arguments(_context(inventory_id))
    if "name" not in args:
        raise service.InventoryError("invalid_input", "Item name is required.", 400)
    return jsonify(service.add_stock(current_user, args)), 201
