# Phase 2: Recipe availability and missing-ingredient transfer

Status: implementation specification for this fork. Depends on the completed
[Phase 1 Pantry](pantry.md). Scope: `backend/`. Product intent: [Vision](../VISION.md).
Sequence: [Roadmap](../ROADMAP.md). Work order: [implementation checklist](../implementation/recipe-availability.md).

Phase 2 answers two questions against real Pantry state:

> What can I cook with what I have?

> What do I need to buy to cook this recipe?

It adds no new stored domain state. Ingredient statuses and recipe-level tags are
**derived on every read** from Recipe requirements and Pantry observations; they are
never persisted (a stored tag would drift from stock). The one write Phase 2 performs
is an explicit, user-triggered transfer of missing ingredients to a shopping list.

## 1. Release boundary

The first usable release must support this interaction:

1. Ask whether a specific recipe is cookable and see a per-ingredient breakdown.
2. See, across all household recipes, which are `READY_TO_COOK`, `MISSING_1`,
   `MISSING_N` or `UNCERTAIN` — without inspecting every ingredient.
3. Add the missing ingredients of one recipe to a shopping list, with correct
   amounts, without duplicating items already on that list and without silently
   adding things the system only suspects are missing.
4. Uncertainty stays visible throughout: qualitative "AVAILABLE" stock never
   masquerades as "enough" for a quantified requirement.

| Personal MVP: required before daily use | Follow-up integration: required before upstream |
| --- | --- |
| Single-recipe availability with per-ingredient statuses | Serving/`yields` scaling of requirements |
| Bulk recipe roll-up using one pantry snapshot per request | Cross-household / public-recipe availability |
| Missing→shopping transfer with the amount rules below | Live socket events on transfer |
| Shared status vocabulary (`INSUFFICIENT`, `UNCERTAIN` added) | Broader unit conversion / density model |
| REST and MCP over the one shared service | Generated OpenAPI + contribution package |

For the MVP, availability is computed only for recipes in the actor's household.
No Flutter availability UI is promised; MCP and REST are the validated surfaces.

Recipe amount parsing beyond what the comparison needs, cooking/consumption, leftovers
and nutrition belong to later phases. Do not build a universal cooking-unit engine here.

## 2. Decisions for implementation

These are the working decisions for this fork. They do not imply upstream approval.

| Decision | Reason |
| --- | --- |
| Availability is always computed, never stored | Derived-from-stock; a persisted tag drifts the moment Pantry changes. |
| One shared service backs REST and MCP | Status and access rules must be identical, as in Phase 1. |
| The pure comparison core stays Flask-free | `app/service/recipe_availability.py` already parses, compares and rolls up; the adapter only bridges DB models to it. |
| Requirements come from `RecipeItems` as stored | `item_id`, free-text `description` (the amount source) and `optional` map 1:1 to `Requirement`. |
| Observations aggregate every household location for an Item | Stock for one Item may sit in several `Inventory` rows; `_combine` already sums comparable units and flags the rest partial. |
| Bulk loads the pantry **once** per request | `load snapshot → normalize once → evaluate every recipe`. No N× pantry queries. |
| Bulk returns roll-ups only | `{recipe_id, status, missing_count, uncertain_count}`; per-ingredient detail is fetched from the single endpoint on demand. |
| Compare against the recipe as stored (no `yields` scaling) | Serving scaling is a follow-up; the first cut proves the comparison itself. |
| Transfer is explicit and never automatic | Selecting or viewing a recipe must not mutate a shopping list (roadmap rule). |
| Matching-unit arithmetic only; no implicit conversion | Same rule as Phase 1; unknown/uncomparable amounts transfer without a structured quantity. |

### Shared status vocabulary (Phase 2 additions)

Phase 1 defined `AVAILABLE`, `LOW`, `OUT`, `UNTRACKED` as Pantry stock states.
Phase 2 introduces two **comparison** states — they describe a requirement vs. stock,
are computed, and are never writable Pantry states.

| Ingredient status | Meaning in a recipe comparison |
| --- | --- |
| `AVAILABLE` | Both sides are comparable and stock ≥ requirement. Proven sufficient. |
| `INSUFFICIENT` | Both sides comparable and stock < requirement. Proven shortage; deficit is known. |
| `OUT` | Item tracked and known absent (exact zero). Proven shortage; full amount missing. |
| `UNTRACKED` | No stock row for the Item anywhere in the household. Treated as a shortage for transfer. |
| `UNCERTAIN` | Cannot prove sufficiency or shortage — qualitative stock, unknown/estimated quantity, or uncomparable units. Preserve; never auto-resolve. |

`UNTRACKED != OUT`, `UNCERTAIN != INSUFFICIENT`, `LOW != INSUFFICIENT`. A Pantry
`LOW`/qualitative-`AVAILABLE` row has an unknown quantity, so against a quantified
requirement it yields `UNCERTAIN`, not a proof either way.

### Recipe-level derived tags

Roll up **required** (non-optional) ingredients only; optional ingredients never block.

| Tag | Rule |
| --- | --- |
| `READY_TO_COOK` | Every required ingredient is demonstrably `AVAILABLE`. |
| `MISSING_1` | Exactly one required ingredient is a known shortage (`OUT`/`INSUFFICIENT`). |
| `MISSING_N` | Two or more required ingredients are known shortages. |
| `UNCERTAIN` | No known shortage, but at least one required ingredient is `UNCERTAIN`/`UNTRACKED`. |

Known-missing counts cover proven shortages only. `MISSING_1` alongside two uncertain
ingredients does **not** promise that buying one item makes the recipe cookable, so
`uncertain_count` is always returned beside the count. (These rules already live in
`recipe_status`; `UNTRACKED` handling for the bulk roll-up is fixed here: an untracked
required ingredient makes the recipe at best `UNCERTAIN`, and it is a transfer target.)

## 3. Availability computation

The pure core (`app/service/recipe_availability.py`) is unchanged. Phase 2 adds an
adapter, `app/service/recipe_availability_query.py`:

- **Requirements from a recipe.** For each `RecipeItems` row:
  `parse_requirement(item_id, item._name, description, optional)`. Free text that is
  not a supported amount becomes an amount-less requirement (never a guessed zero).
- **Pantry snapshot.** `pantry_snapshot(household_id) -> Mapping[item_id, list[Observation]]`
  runs **one** query over the household's `InventoryItems` (all locations) and maps each
  row to `Observation(item_id, quantity, unit, quantity_is_estimate)`. Qualitative rows
  (`quantity is None`) map to `Observation(quantity=None, ...)`, which the core treats as
  uncertain. This snapshot is built once and reused for every recipe in a bulk request.
- **Single recipe** → authorize household, build requirements, look up each Item's
  observations in the snapshot, `compare_requirements` + `recipe_status`, serialize the
  full per-ingredient breakdown plus the recipe status.
- **Bulk** → one snapshot, then evaluate every household recipe, returning
  `{recipe_id, name, status, missing_count, uncertain_count}` per recipe — no ingredient
  detail. Supports the existing recipe filtering/paging conventions where practical.

Authorization reuses the Phase 1 household check. Invalid recipe IDs return 404;
recipes outside the actor's household are not evaluated (MVP is same-household only).

## 4. Missing → shopping-list transfer

`app/service/recipe_shopping_transfer.py` provides the service contract; REST and MCP
expose it. Input: recipe ID + target shopping-list ID (both household-authorized).
It recomputes availability from a fresh snapshot (never trusts a client-supplied
status) and transfers by status:

| Ingredient status | Transferred? | Amount added |
| --- | --- | --- |
| `OUT` | Yes | Full recipe requirement (or no structured amount if the requirement has none). |
| `UNTRACKED` | Yes | Full recipe requirement (or no structured amount if none). |
| `INSUFFICIENT` | Yes | Deficit only: `required − available`, computed in matching units. |
| `UNCERTAIN` | **No** | — (preserve uncertainty; report as skipped). |
| `AVAILABLE` | No | — |
| optional ingredients | **No** by default | — (regardless of status). |

Rules fixed by this spec:

1. **`UNTRACKED` is a first-class transfer target.** A never-tracked ingredient is the
   most obvious thing to buy; omitting it would defeat the feature.
2. **No implicit conversion.** If the requirement amount is unknown, or its unit is not
   comparable to stock, add the item **without a structured quantity**, preserving the
   original recipe `description` as the shopping item's note.
3. **`optional=true` is not transferred by default.**
4. **Existing shopping-list item → merge/update, never duplicate.** If the Item is
   already on the target list, update it (combine amount where comparable) rather than
   adding a second row.
5. **Structured response for explainability.** Return, per ingredient, one of
   `added` / `updated` / `skipped_uncertain` / `skipped_optional` / `skipped_available`,
   so an MCP client can explain exactly what it did and what it deliberately left out.

The transfer is the only Phase 2 write. It commits atomically; a failure adds nothing.

## 5. REST contracts

| Method & path | Purpose | Response |
| --- | --- | --- |
| `GET /recipe/<id>/availability` | Single-recipe breakdown | recipe status + per-ingredient `[{item_id, name, status, required, available, optional, ...}]`. |
| `GET /household/<id>/recipes/availability` | Bulk roll-up | `[{recipe_id, name, status, missing_count, uncertain_count}]` from one snapshot. |
| `POST /recipe/<id>/availability/transfer` | Missing→shopping | body: `{shoppinglist_id}`; returns the per-ingredient action report of §4. |

Paths mirror existing recipe/household controller conventions; exact final routing is
settled in P2-02 against `app/controller/recipe/` and the shopping-list blueprint.

## 6. MCP tools and agent behavior

Mirror the Phase 1 pattern (`app/mcp/pantry.py`): one shared service, own commit,
domain failures returned as `isError`. Tools:

- `check_recipe_availability` — single recipe, full breakdown.
- `list_recipe_availability` — bulk roll-up (the "what can I cook?" tool).
- `add_missing_to_shopping_list` — the transfer, returning the §4 action report.

Agent-behavior principles carry over from Phase 1 (see pantry.md §10): preserve
uncertainty, infer intent not tool name, keep mutations conservative and retry-safe.
Specifically: never let an agent "resolve" an `UNCERTAIN` ingredient into a purchase on
its own; surface it and let the user decide. Tool descriptions must state that bulk
returns roll-ups only and that ingredient detail requires the single-recipe tool.

## 7. Testing

Follow Phase 1 practice: real behavior in proper test files, not ad-hoc scripts.

- **Unit** (`tests/util/test_recipe_availability.py`, already present): the pure core.
  Extend only if the adapter surfaces a new comparison case.
- **Integration** (`tests/api/test_api_recipe_availability.py`, new): single + bulk over
  REST and MCP; snapshot-reuse in bulk (one pantry read serves many recipes); the full
  transfer matrix (OUT/UNTRACKED full amount, INSUFFICIENT deficit, UNCERTAIN skipped,
  optional skipped, merge-not-duplicate, unknown-amount item with note); authorization
  and 404s; atomic transfer rollback on failure.

Establish the pre-existing baseline first (the known planner timezone failure). Run via
`backend/scripts/test.sh` so the venv/`cd` conventions from Phase 1 hold.

## 8. MVP vs upstream boundary

Personal MVP (this phase): single + bulk availability, the transfer with its amount and
merge rules, shared service, REST + MCP, tests, a real MCP dogfooding session.

Upstream follow-ups (do not block daily use): `yields`/serving scaling, public/cross-
household recipe availability, socket events on transfer, a broader unit model, and the
OpenAPI/contribution package. Record them under U-tasks in the checklist as they arise.

## 9. Open questions (resolve before they cause ambiguity)

- Bulk paging/filtering: reuse the recipe list's existing paging, or return the whole
  household set in one snapshot pass? (Lean: whole set for the MVP; revisit if slow.)
- Which shopping list is the default when the client names none? (Lean: require an
  explicit `shoppinglist_id`; no implicit default.)
- Should `LOW` ever bias toward transfer? (Lean: no — `LOW` is unknown-quantity →
  `UNCERTAIN` → skipped; revisit only if dogfooding shows friction.)
