# Recipe-availability implementation checklist

Status: **P2 planned**; pure comparison core landed (`e9b83725`). Contract:
[Phase 2 specification](../design/recipe-availability.md). Product sequence:
[Roadmap](../ROADMAP.md). Depends on the completed [Phase 1 Pantry](../design/pantry.md).

Branch: `phase/02-recipe-availability`. Paths below are relative to `backend/`.

Orchestration: Opus owns the spec, the DB→core adapter and the transfer service
(the semantic core, plus the one-snapshot-per-request invariant and matching-unit
deficit math — where lighter models err). Sonnet 4.5 workers take the disjoint-file
REST and MCP tickets against exact contracts, then Opus runs the suite and reviews.

## Tasks

### P2-00 — Specification and checklist — **done**

- [x] Write [recipe-availability.md](../design/recipe-availability.md): status vocabulary
  additions, computation model, transfer amount/merge rules, REST/MCP contracts, MVP vs
  upstream boundary.
- [x] This checklist.

### P2-01 — DB→core adapter and pantry snapshot (Opus) — **done**

Depends on: the pure core `app/service/recipe_availability.py`. New file
`app/service/recipe_availability_query.py`.

- [x] `pantry_snapshot(household_id) -> dict[int, list[Observation]]`: one query over
  the household's `InventoryItems` across all locations; map each row to
  `Observation(item_id, quantity, unit, quantity_is_estimate)`.
- [x] `recipe_requirements(recipe) -> list[Requirement]` from `RecipeItems`
  (`parse_requirement(item_id, item.name, description, optional)`).
- [x] `recipe_availability(actor, recipe_id) -> dict`: authorize household, build
  requirements, look up observations in a snapshot, `compare_requirements` +
  `recipe_status`, serialize full per-ingredient breakdown + recipe status.
- [x] `household_recipes_availability(actor, household_id) -> list[dict]`: **one** snapshot,
  evaluate every household recipe, return `{recipe_id, name, status, missing_count,
  uncertain_count}` — roll-ups only, no ingredient detail.
- [x] Invalid recipe → 404 (`InventoryError`); cross-household recipe blocked by `authorize`.

**Acceptance:** a bulk call over N recipes issues exactly one `InventoryItems` query;
single and bulk agree on each recipe's status. (Behavior verified in P2-05.)

**Frozen contract for P2-02/03/04** — all return plain JSON-able dicts, raise
`app.service.inventory.InventoryError` (has `.code`, `.status`, `.payload()`):
- `recipe_availability(actor: User, recipe_id: int) -> dict` → `{recipe_id, name, status,
  missing_count, uncertain_count, ingredients: [{item_id, name, status, required,
  required_unit, available, available_unit, available_is_partial, optional, description}]}`.
- `household_recipes_availability(actor: User, household_id: int) -> list[dict]` → each
  `{recipe_id, name, status, missing_count, uncertain_count}`.
- `pantry_snapshot(household_id: int) -> dict[int, list[Observation]]` (internal reuse).

### P2-04 — Transfer service contract only (Opus, or Sonnet with exact contract)

Depends on: P2-01. New file `app/service/recipe_shopping_transfer.py`. **No REST/MCP here**
— kept disjoint so P2-02/P2-03 own the transports.

- [ ] `transfer_missing(actor, recipe_id, shoppinglist_id) -> dict`: recompute availability
  from a fresh snapshot (never trust client status); authorize both objects to the household.
- [ ] Amount rules: `OUT`/`UNTRACKED` → full requirement; `INSUFFICIENT` → deficit
  (`required − available`, matching units only); unknown/uncomparable → item with no
  structured quantity, keep recipe `description` as the note.
- [ ] `UNCERTAIN` skipped; `optional=true` skipped by default; `AVAILABLE` skipped.
- [ ] Existing shopping-list item → merge/update (combine amount where comparable), never
  duplicate.
- [ ] Return per-ingredient action report: `added` / `updated` / `skipped_uncertain` /
  `skipped_optional` / `skipped_available`. Atomic: any failure transfers nothing.

**Acceptance:** the §4 matrix; merge-not-duplicate; atomic rollback; no implicit conversion.

### P2-02 — REST endpoints (Sonnet 4.5)

Depends on: P2-01, P2-04. New `app/controller/recipe/availability_controller.py` +
blueprint registration. Mirror existing recipe/household controllers.

- [ ] `GET /recipe/<id>/availability` → P2-01 single.
- [ ] `GET /household/<id>/recipes/availability` → P2-01 bulk.
- [ ] `POST /recipe/<id>/availability/transfer` (`{shoppinglist_id}`) → P2-04.
- [ ] `@jwt_required` + household authorization; domain errors → correct HTTP codes.

Contract handed to the worker: exact service function names, input dicts and return
shapes from P2-01/P2-04, plus the controller file to mirror.

### P2-03 — MCP tools (Sonnet 4.5)

Depends on: P2-01, P2-04. New `app/mcp/recipe_availability.py` + dispatch registration.
Mirror `app/mcp/pantry.py` (own commit, `InventoryError`/domain error → `isError`).

- [ ] `check_recipe_availability` (single), `list_recipe_availability` (bulk roll-up),
  `add_missing_to_shopping_list` (transfer, returns the action report).
- [ ] Tool descriptions state bulk = roll-ups only; detail via the single tool; and the
  agent-behavior principles (preserve uncertainty; never auto-resolve `UNCERTAIN`).

### P2-05 — Integration tests (Opus runs/verifies; workers may draft)

New `tests/api/test_api_recipe_availability.py`.

- [ ] Single + bulk over REST and MCP; agreement between them.
- [ ] Bulk snapshot reuse: one pantry read serves many recipes.
- [ ] Transfer matrix: OUT/UNTRACKED full amount, INSUFFICIENT deficit, UNCERTAIN skipped,
  optional skipped, merge-not-duplicate, unknown-amount item carries the recipe note.
- [ ] Authorization, 404s, atomic transfer rollback.

**Demonstration:** a recipe needing 400 g chicken (200 g in pantry), 250 g rice
(qualitative AVAILABLE) and 1 onion (untracked) reports chicken `INSUFFICIENT`, rice
`UNCERTAIN`, onion `UNTRACKED`; the recipe is `MISSING_1` with `uncertain_count=1`.
Transfer adds 200 g chicken (deficit) and 1 onion (full), skips rice as uncertain, and
merges rather than duplicating if chicken is already on the list.

## Validation

Same conventions as Phase 1: `backend/scripts/test.sh` (handles venv/`cd`); establish the
pre-existing planner timezone failure as baseline; Ruff/Pyright on touched modules. Real
behavior goes in the test files above, not ad-hoc scripts.

## Evidence and Phase 3 handoff

Add real entries during implementation; empty fields mean no evidence yet.

| Evidence | Result |
| --- | --- |
| Implementation commits / PRs | `e9b83725` pure comparison core + Phase 1 close-out. |
| Baseline and release test results | Pending P2-01…P2-05. |
| Client, transport and demonstrated workflow | Pending real MCP dogfooding session. |
| Usage notes: friction, repairs, decisions changed | Pending. |

After the dogfooding gate, write the Phase 3 (recipe acquisition/normalization) spec
using the amount-parsing gaps this phase actually surfaces. Do not pre-design later
cooking/consumption/nutrition domains at this gate.
