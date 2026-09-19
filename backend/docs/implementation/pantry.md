# Pantry implementation checklist

Status: **P1-01 implemented**; P1-02 is next. Contract:
[Phase 1 specification](../design/pantry.md). Product sequence: [Roadmap](../ROADMAP.md).

**Next task: P1-02.** P1-01 built one working REST/MCP path (locations, create and
read stock) with the shared service, merge guard and both transports wired in.
Paths below are relative to `backend/`.

## Personal release

### P1-01 — Record and read stock through REST and MCP

Depends on: no earlier implementation task.

- [x] Establish isolated API test baseline using Python 3.14+ and disposable storage.
  Ran the API suite against a disposable SQLite DB/storage (see "Validation during
  implementation"); baseline before this task was 101 passed, 1 pre-existing planner
  failure (`test_meal_planning_cooking_date_field`).
- [x] Add Inventory/InventoryItems, relationships, Pantry backfill and household
  creation support. Models registered in `app/models/__init__.py`; reciprocal
  relationships on `Household`/`Item`/`User`; migration `a82c914e6d30` (head
  `0b10d67750be`) backfills one Pantry per household; a `before_flush` listener
  provisions a Pantry on ORM household creation.
- [x] Add the shared service, permission checks, explicit serialization and
  transactional name resolution/Item creation (`app/service/inventory.py`).
- [x] Implement location discovery, create stock and read stock through both REST
  and MCP. Positive/estimated quantity, AVAILABLE, LOW, OUT and UNTRACKED covered
  by `tests/api/test_api_inventory.py`.
- [x] Add deletion integrity and the tracked-Item merge guard before real stock
  can be created through exposed routes. Guard wired into `Item.merge` and the item
  controller; rollback and cascade/attribution deletion verified by tests.
  Concurrent creation is serialized by a DB-level household lock (`lock_household`).
- [x] Add Pantry-specific tool metadata/error handling and separate its transaction
  ownership from legacy MCP dispatch. Pantry tools dispatch through the shared
  service (own commit), bypass the legacy dispatch commit, and report domain
  failures as `isError` tool results.

Likely files: new `app/models/inventory.py`, `app/service/inventory.py`,
`app/controller/inventory/{__init__,schemas,inventory_controller}.py`,
`app/mcp/{__init__,pantry}.py`, migration and tests; extend model/blueprint
registration, household creation, Item lifecycle and MCP registry/dispatch.

**Demonstration:** create “eggs, 12 pcs” through MCP, read the same entry through
REST, then inspect as another member. A nonmember cannot read it; a cross-household
Item cannot be attached. Duplicate create changes nothing. Repeat with LOW milk
and estimated rice. Fresh/populated migration preserves existing data.

This is an engineering checkpoint; daily-use release requires P1-02 through P1-04.

### P1-02 — Correct, consume and restock

Depends on: P1-01.

- [ ] Add set-total, consume/restock, mark-state, note edit and remove in the shared
  service; expose each through REST and MCP.
- [ ] Enforce Decimal bounds, matching units and all state-transition rules.
- [ ] Add database conditional writes using expected revisions, including deletion.
- [ ] Complete create/rename/delete location operations with default/nonempty guards.
- [ ] Verify real multi-session conflict handling and reuse of stale revisions.

**Demonstration:** 12 eggs → consume 2 → 10 → restock 10 → 20 → set total 8 → 8.
Replay the deduction with its old revision: stock stays unchanged. Two concurrent
deductions using one revision yield one success. Mark LOW clears the count; a
numeric restock now returns `quantity_unknown`. OUT remains tracked; remove yields
UNTRACKED. Invalid arithmetic and failed location deletion make no changes.

### P1-03 — Update a whole pantry in one interaction

Depends on: P1-02.

- [ ] Add `apply_pantry_changes` to REST and MCP with 1–50 commands and indexed errors.
- [ ] Resolve/validate all targets, reject duplicates and apply in one transaction.
- [ ] Cover rollback after catalog Item creation and after earlier valid commands.
- [ ] Complete stock pagination/filtering and explicit Item lookup; verify no entries
  disappear silently when more than one page is needed.
- [ ] Test discoverable tool schemas against actual accepted/rejected arguments,
  including nullable values, both transports and revoked credentials.

**Demonstration:** one call records eggs, half a bag of rice, LOW milk and OUT
chicken. A second call with one stale/invalid command changes none of them and
creates no orphan Item. A corrected request succeeds. REST and MCP return the same
states and revisions.

### P1-04 — Make it usable in the real household

Depends on: P1-03. This is the personal release gate.

- [ ] Confirm deployment database and intended MCP client/transport; run the scenario
  with real authentication. Record concrete compatibility failures if any.
- [ ] Verify fresh and populated upgrades, downgrade/upgrade, schema parity and a
  consistent database backup/restore on the deployment engine.
- [ ] Run existing backend regressions and focused validation listed below.
- [ ] Document setup, tools/routes/errors, quantity rules, retry behavior and the
  temporary limitation that application export/import excludes Pantry.
- [ ] Use Pantry for seven days, including an initial inventory, a shopping/restock
  update, ingredient use and a correction after the recorded state becomes stale.
- [ ] Record friction and fixes below. Advance when there is no unresolved data-loss,
  partial-update or repeated-deduction defect, and common updates need no manual API
  repair. If the workflow is abandoned, simplify it before starting Phase 2.

**Demonstration:** a real client completes specification section 1; another member
reads the result. Restore a backup into a disposable instance and inspect the same
Pantry. Usage notes show whether set/add/LOW and unit handling match everyday input.

The seven-day trial is a proposed evaluation window, not a delivery-time estimate.

## Validation during implementation

Test behavior as each task adds it. Do not defer authorization, atomicity or retry
tests to the contribution track. Use file-backed SQLite for multiple-session tests;
use a separate disposable database for PostgreSQL. Configure the environment before
importing the app because existing fixtures create and drop tables.

Example baseline from `backend/` (requires installed project dependencies):

```sh
pantry_test_dir=$(mktemp -d /tmp/kitchenowl-pantry-tests.XXXXXX)
env -u DB_USER -u DB_PASSWORD -u DB_PASSWORD_FILE -u DB_USER_FILE \
  -u DB_HOST -u DB_PORT \
  STORAGE_PATH="$pantry_test_dir" DB_DRIVER=sqlite \
  DB_NAME="$pantry_test_dir/test.db" \
  uv run pytest tests/api tests/util
```

Keep migration/backup tests in separate disposable databases. Establish failures
that already exist before changing code. Run focused Ruff checks/format checks and
Pyright for affected modules, recording the existing type-check baseline. Repeat
the full API regression suite at the release gate. Do not install dependencies or
run database tests merely to validate changes to these planning documents.

## Follow-up integration and upstream readiness

These tasks do not block P1-04. They also need not delay Phase 2 after its Pantry
dependency has passed daily-use validation. An upstream contribution requires all
applicable integration tasks; maintainer acceptance is a separate decision.

| ID | Work | Completion evidence |
| --- | --- | --- |
| U1 | Full tracked Item merges replacing the MVP guard | Noncolliding rows move, compatible stock combines, conflicts roll back stock and legacy references together; concurrency verified. |
| U2 | Portable export/import | Old export works; Pantry round-trip preserves stock; repeated import does not double it; ambiguous references fail atomically. |
| U3 | Live notifications | REST/MCP send identical post-commit events to the correct household; rollback sends none; delivery failure does not retry a committed mutation. |
| U4 | Compatibility matrix and API documentation | SQLite/PostgreSQL migrations, concurrency, constraints and lifecycle tests; generated OpenAPI matches schemas; setup is reproducible. |
| U5 | Upstream package | Reverified upstream code/base, attributed adaptations, migration strategy, focused diff and validation notes; branch/PR expectations resolved before submission. |

Optional additions driven by usage: quantity-based LOW thresholds, expiry metadata
and a presentation feature flag. Their behavior is recorded in the specification;
none is silently bundled into U1–U5. No automatic shopping transfer in this phase.

## Evidence and Phase 2 handoff

Add real entries here during implementation; empty fields mean no evidence yet.

| Evidence | Result |
| --- | --- |
| Implementation commits / PRs | P1-01 wired on `phase/01-pantry` (models, service, REST/MCP, merge guard, migration, tests). |
| Baseline and release test results | Full suite 130 passed, 1 pre-existing planner failure. `tests/api/test_api_inventory.py`: 29 passed (create/read, states, isolation, pagination, filters, validation, merge guard, deletion integrity, MCP). |
| Deployment database and successful migration/restore | SQLite verified: populated upgrade backfills Pantry, downgrade drops pantry tables and preserves households, fresh upgrade recreates schema. Deployment engine + restore still to confirm (P1-04). |
| Client, transport and demonstrated workflow | REST + MCP stateless HTTP exercised in tests; real MCP client session pending (P1-04). |
| Seven-day usage notes: repeated friction, repairs, decisions changed | Pending (P1-04). |

After P1-04, write the Phase 2 recipe-availability spec using actual Pantry data and
a small set of real recipes. Its first decisions are comparable ingredient amounts,
uncertainty, aggregation across locations and explicit missing-to-shopping behavior.
Do not redesign the later cooking, consumption or nutrition domains at this gate.
