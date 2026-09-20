from flask import Blueprint, jsonify, request
from flask_jwt_extended import current_user, jwt_required

from app.service import recipe_availability_query as availability_service
from app.service.inventory import InventoryError
from app.service.recipe_shopping_transfer import transfer_missing

recipe_availability = Blueprint("recipe_availability", __name__)
recipe_availability_household = Blueprint("recipe_availability_household", __name__)


@recipe_availability.route("/<int:recipe_id>/availability", methods=["GET"])
@jwt_required()
def get_availability(recipe_id: int):
    return jsonify(availability_service.recipe_availability(current_user, recipe_id))


@recipe_availability.route("/<int:recipe_id>/availability/transfer", methods=["POST"])
@jwt_required()
def transfer_availability(recipe_id: int):
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "shoppinglist_id" not in body or not isinstance(
        body["shoppinglist_id"], int
    ):
        raise InventoryError("invalid_input", "shoppinglist_id is required.", 400)
    return jsonify(transfer_missing(current_user, recipe_id, int(body["shoppinglist_id"])))


@recipe_availability_household.route("/availability", methods=["GET"])
@jwt_required()
def bulk_availability(household_id: int):
    return jsonify(availability_service.household_recipes_availability(current_user, household_id))
