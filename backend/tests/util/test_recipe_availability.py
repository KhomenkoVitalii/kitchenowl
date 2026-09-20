from decimal import Decimal

import pytest

from app.service.recipe_availability import (
    Observation,
    compare_requirements,
    parse_requirement,
    recipe_status,
)


def test_parse_recipe_amounts_and_normalize_units():
    assert parse_requirement(1, "rice", "0.5 kg").amount == Decimal("500.0")
    assert parse_requirement(1, "eggs", "2 pieces").unit == "pcs"
    assert parse_requirement(1, "salt", "to taste").amount is None


@pytest.mark.parametrize(
    "description", [None, "", "1/2 kg", "1-2 pcs", "2 bags", "0 g", "-1 g"]
)
def test_unsupported_amounts_never_become_guessed_requirements(description):
    requirement = parse_requirement(1, "rice", description)
    assert requirement.amount is None
    assert requirement.unit is None
    rows = compare_requirements([requirement], {1: [Observation(1, Decimal(500), "g")]})
    assert rows[0].state == "UNCERTAIN"


def test_volume_conversion_preserves_decimal_comma_and_source():
    requirement = parse_requirement(1, "milk", " 0,5 L ")
    row = compare_requirements(
        [requirement], {1: [Observation(1, Decimal(500), "ml")]}
    )[0]
    assert row.state == "AVAILABLE"
    assert row.required == row.available == Decimal(500)
    assert row.required_unit == row.available_unit == "ml"
    assert row.source == "0,5 L"


def test_numeric_pantry_is_compared_after_unit_conversion():
    requirement = parse_requirement(1, "rice", "500 g")
    rows = compare_requirements(
        [requirement],
        {1: [Observation(1, Decimal("0.5"), "kg")]},
    )
    assert rows[0].state == "AVAILABLE"


def test_matching_units_and_shortage_states():
    requirements = [
        parse_requirement(1, "eggs", "2 pcs"),
        parse_requirement(2, "milk", "1 l"),
        parse_requirement(3, "salt", "to taste"),
    ]
    rows = compare_requirements(
        requirements,
        {
            1: [Observation(1, Decimal(4), "pcs")],
            2: [Observation(2, Decimal(500), "ml")],
        },
    )
    assert [row.state for row in rows] == ["AVAILABLE", "INSUFFICIENT", "UNTRACKED"]


def test_qualitative_and_missing_stock_remain_distinct():
    requirements = [
        parse_requirement(1, "rice", "250 g"),
        parse_requirement(2, "oil", "1 l"),
    ]
    rows = compare_requirements(
        requirements,
        {1: [Observation(1, None, None)]},
    )
    assert [row.state for row in rows] == ["UNCERTAIN", "UNTRACKED"]


@pytest.mark.parametrize(
    "stock, state",
    [([], "UNTRACKED"), ([Observation(2, Decimal(0), None)], "OUT")],
)
def test_optional_ingredient_does_not_block_recipe(stock, state):
    required = parse_requirement(1, "rice", "250 g")
    optional = parse_requirement(2, "parsley", "1 pcs", optional=True)
    rows = compare_requirements(
        [required, optional],
        {1: [Observation(1, Decimal(250), "g")], 2: stock},
    )
    assert [row.state for row in rows] == ["AVAILABLE", state]
    assert recipe_status(rows) == "READY_TO_COOK"


def test_recipe_status_preserves_uncertainty():
    requirement = parse_requirement(1, "rice", "250 g")
    rows = compare_requirements([requirement], {1: [Observation(1, None, None)]})
    assert recipe_status(rows) == "UNCERTAIN"


def test_recipe_status_counts_one_known_shortage():
    requirements = [
        parse_requirement(1, "rice", "250 g"),
        parse_requirement(2, "eggs", "2 pcs"),
    ]
    rows = compare_requirements(
        requirements,
        {
            1: [Observation(1, Decimal(0), "g")],
            2: [Observation(2, Decimal(2), "pcs")],
        },
    )
    assert recipe_status(rows) == "MISSING_1"


@pytest.mark.parametrize("quantity", [Decimal(0), Decimal(100), Decimal(500)])
def test_estimates_cannot_prove_absence_shortage_or_sufficiency(quantity):
    requirement = parse_requirement(1, "rice", "250 g")
    observation = Observation(
        1,
        quantity,
        "g",
        quantity_is_estimate=True,
    )
    rows = compare_requirements([requirement], {1: [observation]})
    assert rows[0].state == "UNCERTAIN"
    assert recipe_status(rows) == "UNCERTAIN"


@pytest.mark.parametrize(
    ("description", "unit"),
    [("250 g", None), ("250 g", "bag"), ("250 g", "ml"), ("to taste", "pcs")],
)
def test_known_absence_does_not_depend_on_units_or_recipe_parsing(description, unit):
    rows = compare_requirements(
        [parse_requirement(1, "rice", description)],
        {1: [Observation(1, Decimal(0), unit)]},
    )
    assert rows[0].state == "OUT"
    assert rows[0].available == 0
    assert recipe_status(rows) == "MISSING_1"


def test_stock_from_multiple_locations_is_summed_without_rounding():
    rows = compare_requirements(
        [parse_requirement(1, "rice", "0.3 kg")],
        {
            1: [
                Observation(1, Decimal("0.1"), "kg"),
                Observation(1, Decimal(200), "g"),
                Observation(1, Decimal(0), "package"),
            ]
        },
    )
    row = rows[0]
    assert row.state == "AVAILABLE"
    assert (row.required, row.available) == (Decimal(300), Decimal(300))
    assert row.required_unit == row.available_unit == "g"
    assert not row.available_is_partial


@pytest.mark.parametrize(
    "extra",
    [
        Observation(1, None, None),
        Observation(1, Decimal(1), "ml"),
        Observation(1, Decimal(1), "bag"),
        Observation(1, Decimal(1), None),
        Observation(1, Decimal(500), "g", quantity_is_estimate=True),
    ],
)
def test_extra_unknown_stock_cannot_hide_proven_sufficiency_or_prove_a_shortage(extra):
    requirement = parse_requirement(1, "rice", "250 g")
    enough = Observation(1, Decimal(250), "g")
    less = Observation(1, Decimal(100), "g")
    sufficient = compare_requirements([requirement], {1: [enough, extra]})[0]
    uncertain = compare_requirements([requirement], {1: [less, extra]})[0]
    assert sufficient.state == "AVAILABLE"
    assert uncertain.state == "UNCERTAIN"
    assert sufficient.available == 250
    assert uncertain.available == 100
    assert sufficient.available_is_partial and uncertain.available_is_partial


def test_one_known_shortage_is_not_several_because_another_amount_is_unknown():
    rows = compare_requirements(
        [parse_requirement(1, "rice", "250 g"), parse_requirement(2, "milk", "200 ml")],
        {
            1: [Observation(1, Decimal(0), "g")],
            2: [Observation(2, None, None)],
        },
    )
    assert [row.state for row in rows] == ["OUT", "UNCERTAIN"]
    assert recipe_status(rows) == "MISSING_1"


def test_empty_recipe_does_not_claim_ready_to_cook():
    assert recipe_status(compare_requirements([], {})) == "UNCERTAIN"


def test_all_optional_recipe_is_ready_even_when_stock_is_out_or_untracked():
    requirements = [
        parse_requirement(1, "salt", "1 g", optional=True),
        parse_requirement(2, "pepper", "1 g", optional=True),
    ]
    rows = compare_requirements(requirements, {1: [Observation(1, Decimal(0), None)]})
    assert [row.state for row in rows] == ["OUT", "UNTRACKED"]
    assert recipe_status(rows) == "READY_TO_COOK"


def test_pantry_derived_out_does_not_override_an_estimated_quantity():
    from app.models import InventoryItems

    pantry_entry = InventoryItems(
        item_id=1, quantity=Decimal(0), unit="g", quantity_is_estimate=True
    )
    assert pantry_entry.state == "OUT"
    observation = Observation(
        item_id=pantry_entry.item_id,
        quantity=pantry_entry.quantity,
        unit=pantry_entry.unit,
        quantity_is_estimate=pantry_entry.quantity_is_estimate,
    )
    rows = compare_requirements(
        [parse_requirement(1, "rice", "250 g")], {1: [observation]}
    )
    assert rows[0].state == "UNCERTAIN"
    assert recipe_status(rows) == "UNCERTAIN"


def test_several_known_shortages_count_only_required_ingredients():
    requirements = [
        parse_requirement(1, "rice", "250 g"),
        parse_requirement(2, "eggs", "2 pcs"),
    ]
    observations = {
        1: [Observation(1, Decimal(0), None)],
        2: [Observation(2, Decimal(1), "pcs")],
    }
    rows = compare_requirements(requirements, observations)
    assert [row.state for row in rows] == ["OUT", "INSUFFICIENT"]
    assert recipe_status(rows) == "MISSING_FEW"
