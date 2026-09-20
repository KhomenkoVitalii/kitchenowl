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
- [x] `UNCERTAIN` skipped; `optional=true` skipped by default; `AVAILABLE` skipped.
- [x] Existing shopping-list item → merge/update, never duplicate. When the current note
  parses to the same canonical unit as the new amount they are SUMMED (e.g. list "100 g" +
  recipe deficit "200 g" → "300 g"); a note we cannot parse, or an uncomparable unit, is
  replaced. Shopping items store free text (no structured quantity), so combining is limited
  to parseable, same-unit notes.
- [x] Return per-ingredient action report: `added` / `updated` / `skipped_uncertain` /
  `skipped_optional` / `skipped_available`. Atomic: single commit at the end, rollback on
  any failure.

**Acceptance:** the §4 matrix; merge sums comparable amounts (replaces otherwise); atomic
rollback; no implicit conversion. Built by a Sonnet 4.5 worker; combine-on-merge and review by Opus.

### P2-02 — REST endpoints — **done**

New `app/controller/recipe/availability_controller.py` + blueprint registration. Built by a
Sonnet 4.5 worker (mirrors `inventory_controller.py`); reviewed by Opus.

- [x] `GET /recipe/<id>/availability` → P2-01 single.
- [x] `GET /household/<id>/recipe/availability` → P2-01 bulk.
- [x] `POST /recipe/<id>/availability/transfer` (`{shoppinglist_id}`) → P2-04.
- [x] `@jwt_required`; InventoryError handled app-wide by the existing inventory error
  handler → correct HTTP codes. `recipe/__init__.py` given an `__all__` (Opus).

### P2-03 — MCP tools — **done**

New `app/mcp/recipe_availability.py` + dispatch registration in `mcp_controller.py`. Built by
a Sonnet 4.5 worker (mirrors `app/mcp/pantry.py`); reviewed by Opus.

- [x] `check_recipe_availability` (single), `list_recipe_availability` (bulk roll-up wrapped
  as `{items:[...]}`), `add_missing_to_shopping_list` (transfer, returns the action report).
- [x] Tool descriptions state bulk = roll-ups only; detail via the single tool; and never
  treat `UNCERTAIN` as available. Dispatch mirrors the pantry branch (InventoryError→isError,
  service owns its commit).

### P2-05 — Integration tests — **done**

New `tests/api/test_api_recipe_availability.py` (15 tests, Opus).

- [x] Single + bulk over REST and MCP; agreement between them.
- [x] Bulk snapshot reuse: asserted exactly one `inventory_items` query for three recipes.
- [x] Transfer matrix: OUT/UNTRACKED full amount, INSUFFICIENT deficit, UNCERTAIN skipped,
  optional skipped, merge-not-duplicate, unknown-amount item carries the recipe note.
- [x] Authorization (403 cross-household for both read and transfer), 404s, missing
  `shoppinglist_id` → 400, atomic transfer rollback (monkeypatched commit failure adds nothing).

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
| Implementation commits / PRs | `e9b83725` pure core + Phase 1 close-out; `651adde1` spec; `89600ad5`/`(this)` adapter+evaluate_recipe; `(this)` transfer + REST + MCP + tests (P2-02..P2-05). |
| Baseline and release test results | Full `tests/api tests/util`: **255 passed, 1 failed** — the one failure is the pre-existing planner timezone baseline (`test_meal_planning_cooking_date_field`); zero Phase 2 regressions. `test_api_recipe_availability.py`: 15 passed (incl. one-query bulk snapshot, transfer matrix, atomic rollback, REST/MCP agreement). New files ruff-clean; MCP files match the existing `pantry.py` style. |
| Client, transport and demonstrated workflow | REST + MCP exercised in tests. Real MCP dogfooding session pending (the Phase 2 validation gate). |
| Usage notes: friction, repairs, decisions changed | Pending dogfooding. |

After the dogfooding gate, write the Phase 3 (recipe acquisition/normalization) spec
using the amount-parsing gaps this phase actually surfaces. Do not pre-design later
cooking/consumption/nutrition domains at this gate.
