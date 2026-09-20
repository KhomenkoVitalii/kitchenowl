"""Deterministic comparison of a recipe's requirements with Pantry observations.

This module is deliberately independent of Flask and SQLAlchemy. REST, MCP and
the planner can use the same comparison once their transport work is ready.
Unknown amounts remain unknown; the service never treats qualitative AVAILABLE as
enough for a quantified recipe requirement.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

_AMOUNT = re.compile(r"^\s*(?P<value>\d+(?:[.,]\d+)?|\.\d+)\s*(?P<unit>[A-Za-z]+)?\s*$")
_UNITS = {
    "piece": ("pcs", Decimal(1)),
    "pieces": ("pcs", Decimal(1)),
    "pc": ("pcs", Decimal(1)),
    "pcs": ("pcs", Decimal(1)),
    "x": ("pcs", Decimal(1)),
    "g": ("g", Decimal(1)),
    "kg": ("g", Decimal(1000)),
    "ml": ("ml", Decimal(1)),
    "l": ("ml", Decimal(1000)),
}


@dataclass(frozen=True)
class Requirement:
    """A parsed requirement in canonical g/ml/pcs, or an unknown amount.

    Use parse_requirement for recipe text; direct callers must supply canonical
    amounts and units. Comparison does not convert requirements again.
    """

    item_id: int
    item_name: str
    amount: Decimal | None
    unit: str | None
    optional: bool = False
    source: str = ""


@dataclass(frozen=True)
class Observation:
    """Quantity and estimate flag are authoritative for recipe comparison.

    Map these fields directly from Pantry observations. Exact zero is empty;
    estimated zero and null quantities cannot prove absence or sufficiency.
    Pantry's derived state is intentionally excluded: OUT also describes an
    estimated zero, and qualitative AVAILABLE/LOW both have unknown quantity.
    """

    item_id: int
    quantity: Decimal | None
    unit: str | None
    quantity_is_estimate: bool = False


@dataclass(frozen=True)
class Availability:
    """Comparison with an exact total, or a known subtotal when partial."""

    item_id: int
    item_name: str
    state: str
    required: Decimal | None
    required_unit: str | None
    available: Decimal | None
    available_unit: str | None
    optional: bool
    source: str = ""
    available_is_partial: bool = False


def parse_requirement(
    item_id: int, item_name: str, description: str | None, optional: bool = False
) -> Requirement:
    """Parse the small amount vocabulary already stored by KitchenOwl recipes.

    Descriptions are free text, so unsupported text intentionally produces an
    amount-less requirement instead of a guessed zero.
    """
    source = (description or "").strip()
    match = _AMOUNT.match(source)
    if not match:
        return Requirement(item_id, item_name, None, None, optional, source)
    # The regex admits only valid decimal syntax after comma normalization.
    value = Decimal(match.group("value").replace(",", "."))
    raw_unit = (match.group("unit") or "pcs").lower()
    normalized = _UNITS.get(raw_unit)
    if normalized is None or value <= 0:
        return Requirement(item_id, item_name, None, None, optional, source)
    unit, multiplier = normalized
    return Requirement(item_id, item_name, value * multiplier, unit, optional, source)


def _canonical(quantity: Decimal, unit: str | None) -> tuple[Decimal, str] | None:
    if unit is None:
        return None
    normalized = _UNITS.get(unit.strip().lower())
    if normalized is None:
        return None
    canonical_unit, multiplier = normalized
    return quantity * multiplier, canonical_unit


def _combine(
    observations: Iterable[Observation], unit: str | None
) -> tuple[Decimal | None, bool, str]:
    rows = list(observations)
    if not rows:
        return None, False, "UNTRACKED"
    if all(row.quantity == 0 and not row.quantity_is_estimate for row in rows):
        return Decimal(0), False, "OUT"
    total = None
    partial = False
    for row in rows:
        if row.quantity == 0 and not row.quantity_is_estimate:
            continue
        converted = (
            _canonical(row.quantity, row.unit)
            if row.quantity is not None and not row.quantity_is_estimate
            else None
        )
        if converted is None or converted[1] != unit:
            partial = True
        else:
            total = (total if total is not None else Decimal(0)) + converted[0]
    return total, partial, "UNCERTAIN" if partial else "AVAILABLE"


def compare_requirements(
    requirements: Iterable[Requirement],
    observations: Mapping[int, Iterable[Observation]],
) -> list[Availability]:
    """Return one status per requirement.

    `AVAILABLE` is sufficient only when both sides have comparable quantities.
    Unknown stock cannot prove sufficiency or shortage. An exact comparable
    subtotal can prove sufficiency even if other locations are uncertain.
    Optional ingredients retain their actual status; aggregation skips them.
    """
    result: list[Availability] = []
    for requirement in requirements:
        available_unit = requirement.unit
        available, partial, observed_state = _combine(
            observations.get(requirement.item_id, ()), available_unit
        )
        if observed_state in {"UNTRACKED", "OUT"}:
            state = observed_state
        elif (
            requirement.amount is None or requirement.unit is None or available is None
        ):
            state = "UNCERTAIN"
        elif available >= requirement.amount:
            state = "AVAILABLE"
        else:
            state = "UNCERTAIN" if partial else "INSUFFICIENT"
        result.append(
            Availability(
                requirement.item_id,
                requirement.item_name,
                state,
                requirement.amount,
                requirement.unit,
                available,
                available_unit if available is not None else None,
                requirement.optional,
                requirement.source,
                partial,
            )
        )
    return result


def recipe_status(availabilities: Iterable[Availability]) -> str:
    """Count known shortages; missing tags do not rule out unknown ingredients.

    MISSING_1 means one known shortage, not that buying one item guarantees
    readiness. MISSING_N means two or more known shortages. Consumers must
    retain ingredient states to expose remaining uncertainty.
    """
    all_rows = list(availabilities)
    if not all_rows:
        return "UNCERTAIN"
    rows = [row for row in all_rows if not row.optional]
    if not rows or all(row.state == "AVAILABLE" for row in rows):
        return "READY_TO_COOK"
    known_missing = sum(row.state in {"OUT", "INSUFFICIENT"} for row in rows)
    if known_missing == 1:
        return "MISSING_1"
    if known_missing > 0:
        return "MISSING_N"
    return "UNCERTAIN"
