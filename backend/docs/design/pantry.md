# Phase 1: Pantry backend and MCP

Status: implementation specification for this fork; the full section-1 workflow is
implemented (P1-01 create/read; P1-02 correct/consume/restock/mark/remove and location
edits; P1-03 atomic `apply_pantry_changes`, pagination/filter, both MCP transports), all
with revision-conditional writes. Remaining Phase 1 work is the P1-04 real-deployment gate.
Scope: `backend/`. Product intent: [Vision](../VISION.md).
Sequence: [Roadmap](../ROADMAP.md). Work order: [implementation checklist](../implementation/pantry.md).

## 1. Release boundary

The first usable release must support this complete interaction:

1. Choose a household and its Pantry location.
2. Record 12 eggs (`pcs`), estimated 0.5 bag of rice, milk LOW and chicken OUT
   in one atomic operation. Resolve or create existing-catalog Items by name.
3. Consume 2 eggs: 10 remain. Restock 10 eggs: 20 remain.
4. Correct the observed egg total to 8: 8 remain, not 28.
5. Read the same committed state through REST and MCP as another household member.
6. Repeat a successful deduction with its old revision: no second deduction.
7. Stop tracking chicken: its state becomes UNTRACKED rather than OUT.

| Personal MVP: required before daily use | Follow-up integration: required before an upstream contribution |
| --- | --- |
| Named locations; exact, estimated and qualitative stock | Portable household export/import |
| Set, consume, restock, mark state, remove, atomic bulk | Socket notifications after commit |
| Shared service, household authorization, conditional writes | Full Item merge with stock reconciliation |
| REST and MCP, including a real client session | Full SQLite/PostgreSQL validation matrix |
| Fresh/populated migration on the deployment database | Migration alignment with the chosen upstream branch |
| Deletion integrity and a guard against unsafe Item merges | Feature flag if needed by an inventory-aware client |
| Tested database backup/restore and explicit export limitation | Generated OpenAPI coverage and contribution package |

For the MVP, reads refresh state; no Pantry UI or push subscription is promised.
Application exports do not yet back up Pantry: use a consistent database backup
and verify a restore before relying on real stock data. Document this limitation
in the backend's user-facing setup instructions when implementing the feature.

Low thresholds and expiry metadata are optional follow-ups, not prerequisites
for qualitative LOW or basic stock tracking. Their intended semantics are retained
below. Recipe availability, shopping transfers, cooking, meals and nutrition belong
to later phases. No shared tag engine or natural-language parser is needed here.

## 2. Decisions for implementation

These are the working decisions for this fork. They do not imply upstream approval.

| Decision | Reason |
| --- | --- |
| `Inventory` is a location; `InventoryItems` links an Item to a location | Reuse the previously researched upstream model shape and the existing Item catalog. |
| One entry per `(inventory_id, item_id)` | Simple household tracking; individual packages/lots can follow later. |
| Keep zero stock as OUT; absence means UNTRACKED | “No chicken left” differs from never tracking chicken. |
| LOW/AVAILABLE actions clear the numeric quantity | A fresh qualitative observation replaces a stale count. |
| Matching-unit arithmetic only | No guessed package sizes, density or portion weights. |
| Require revisions for existing-record writes | Concurrent edits and retries must not silently consume stock twice. |
| Bulk updates are all-or-nothing | A spoken inventory update has one inspectable outcome. |
| Use one command service for REST and MCP | State rules and access checks must be identical. |
| Guard tracked Item merges in MVP; implement full reconciliation later | Avoid losing Pantry data without making a broad merge refactor the first task. |

### Shared status vocabulary

| Stored stock | Response `state` | Meaning |
| --- | --- | --- |
| Positive quantity | AVAILABLE | Some quantified food exists, not a promise that it satisfies any recipe. |
| Zero quantity | OUT | Tracked and known absent. |
| Null quantity and `stock_state=AVAILABLE` | AVAILABLE | Present; amount unknown. |
| Null quantity and `stock_state=LOW` | LOW | Running low; amount unknown. |
| No entry for an Item/location | UNTRACKED | No observation is stored. |

UNTRACKED is derived, never persisted as a stock row. A stock-list response only
contains tracked entries; an explicit Item/location lookup returns an UNTRACKED
observation with `revision: null` if both referenced resources exist but no entry
does. Invalid resource IDs still return 404. Removal returns this same observation.

`INSUFFICIENT` and `UNCERTAIN` belong to Phase 2's comparison against a requirement.
They are not writable Pantry states. Existing recipe labels stay in the existing
Tag model; computed recipe badges will not become manually editable tags.

## 3. Data and validation

`Inventory`: ID, household ID, name (1–128 trimmed characters), UUID revision and
existing timestamp fields. Names are editable; IDs are authoritative. Create one
default Pantry during household creation and migration backfill. The lowest-ID
location is the default and cannot be deleted. Reads never create data. Other
locations can be created, renamed and deleted when empty. OUT entries count as
tracked entries, so a location containing them is not empty.

`InventoryItems` MVP fields:

| Field | Contract |
| --- | --- |
| `inventory_id`, `item_id` | Composite primary key; both objects must belong to the same household. |
| `description` | Optional human note, never parsed as the arithmetic source. |
| `quantity` | Nullable `Numeric(12, 3)`; nonnegative, at most 999999999.999. |
| `unit` | Nullable normalized string, 1–32 characters when present. |
| `quantity_is_estimate` | Boolean; default false; must be false when quantity is null. |
| `stock_state` | AVAILABLE or LOW only when quantity is null; null when quantified. |
| `revision` | Fresh UUID on creation and every change. |
| `created_by` | Nullable User FK; deleting the user clears attribution. |
| `created_at`, `updated_at` | Existing timestamp convention. |

Use Decimal arithmetic, explicit range/precision validation and database checks
for quantity/state invariants. Reject negatives, nonfinite numbers, booleans as
amounts, excess precision, blank units, unknown input fields and contradictory
quantity/state inputs. A positive quantity requires a unit; zero may omit it.
REST and MCP use the same serializer: JSON numeric quantities, boolean estimate
flags, null for unknown values and existing epoch-millisecond timestamps.

Normalize unit whitespace and lowercase; initial aliases are `piece`/`pieces` →
`pcs`. Otherwise preserve the normalized unit token. `bag` and `package` are
distinct. Do not convert `kg` to `g` in Phase 1. A changed unit requires a new
observed total. Never aggregate incompatible units across locations into a number.

One entry aggregates food in one location. It cannot represent two units or two
expiry dates for the same Item there. A note can preserve extra context.

## 4. Commands

All existing-record commands require `expected_revision`; create requires absence.
Every mutation returns the committed record, or an explicit deletion result.

| Command | Behavior |
| --- | --- |
| `add` | Create stock with a total or qualitative state. Existing entry → `already_tracked`. |
| `set_total` | Replace quantity, unit and estimate flag using a fresh observation. |
| `consume` | Subtract a strictly positive amount from a known quantity in the same unit. |
| `restock` | Add a strictly positive amount to a known quantity in the same unit. |
| `mark_available`, `mark_low` | Clear quantity/estimate; retain preferred unit; set qualitative state. |
| `mark_out` | Set quantity to 0, clear estimate/state; retain preferred unit and entry. |
| `update_metadata` | Change description without changing stock. |
| `remove` | Delete tracking for this location; keep catalog Item. |

Arithmetic on unknown quantity → `quantity_unknown`; over-consumption →
`insufficient_stock`; mismatching units → `unit_mismatch`. Never clamp a negative
result or guess an unknown total. To correct an inaccurate count, set a new total.
Restocking an OUT entry with no unit adopts the supplied unit; a retained unit
must match. Arithmetic propagates estimation if either operand is estimated,
including an estimated zero; an explicit `mark_out` records known absence.

`set_total` requires quantity and an explicit estimate flag (default false for
new entries); unit can be omitted only for zero. Qualitative creation requires
`state=AVAILABLE|LOW|OUT`; OUT is stored as zero. `update_pantry_item` uses a
discriminated `operation` to choose one setter or metadata update, so the client
cannot submit simultaneous conflicting stock instructions. Omission leaves
metadata unchanged; explicit null clears a nullable note.

Resolve an add target by exactly one of `item_id` or `name`. A name is trimmed
and matched case-insensitively within the household; zero matches creates a
catalog Item inside the stock transaction, multiple matches return `ambiguous_item`.
Reject names over 128 characters rather than truncate. Never use fuzzy results as
automatic write targets. Updates/removals use IDs returned by reads.

`apply_pantry_changes` accepts 1–50 entry commands in one household, each with an
inventory ID and appropriate target/revision. It excludes location management.
Reject duplicate targets, including names resolving to the same Item, before
applying any changes. An invalid or stale command rolls back everything, including
new catalog Items, and returns its zero-based `index`. Results retain input order.
JSON-RPC request batching does not provide these atomic semantics.

## 5. Transactions, access and existing lifecycle

Put domain behavior in `app/service/inventory.py`. An execution wrapper authorizes,
validates, applies a command or bulk command, and commits once. Use session
add/flush/delete within it; do not call helpers that commit. Adapt MCP dispatch
so Pantry tools bypass its legacy extra commit. Both transports call the service
directly; neither calls the other over HTTP. Roll back on any failure.

Follow the existing REST member/server-admin policy for all Pantry operations.
Check membership at each execution and verify every Inventory/Item belongs to
the requested household, even for server admins or users belonging to both homes.
Do not reuse MCP's narrower membership-only helper as the Pantry policy. Keep
legacy tool behavior unchanged. No household selection by “first household”.

Revisions are checked in conditional database UPDATE/DELETE predicates, not just
in Python. Zero affected rows → `revision_conflict`. Return the current record
only after authorization. UUIDs prevent delete/recreate accepting an old token.
Insert conflicts are enforced by the composite primary key. Use database-level
serialization for simultaneous Pantry name creation within a household; existing
catalog routes lack normalized-name uniqueness, so do not claim global uniqueness.
Recheck ambiguity on subsequent name writes; IDs remain definitive.

A repeated delta with the old revision cannot apply twice. This does not prove
which client changed the row after a lost response. The client must reread and
resolve an ambiguous outcome; it must never fetch a fresh revision and blindly
repeat a deduction. Durable request receipts are deferred. Roll back on contention;
retry only when the transaction is known not to have committed. In-process locks
are insufficient for multiple workers.

Deleting a catalog Item or household must clean up stock through relationships;
deleting stock must not delete the Item. Deleting a user clears attribution.
For MVP, reject Item merges involving stock on either Item with
`inventory_merge_requires_reconciliation`, before the controller saves any edits
or the merge changes other domains. Cover all callers, including direct model use;
make the guard and merge safe against concurrent stock creation. A check followed
by an unprotected legacy merge is not enough. Unrelated untracked merges retain
their behavior. Full tracked merges are a separate integration task.

## 6. REST and MCP contract

Routes are new fork contracts, inspired by earlier upstream research. No current
Pantry route compatibility is claimed. Use existing blueprint and schema patterns.

| REST route | MCP tool | Operation |
| --- | --- | --- |
| `GET /api/household/<h>/inventory` | `get_pantry` (no inventory ID) | List locations, default ID and revisions. |
| `POST /api/household/<h>/inventory` | `create_pantry_storage` | Create location. |
| `POST /api/inventory/<v>` | `update_pantry_storage` | Rename with revision. |
| `DELETE /api/inventory/<v>` | `remove_pantry_storage` | Delete empty nondefault with revision. |
| `GET /api/inventory/<v>/items` | `get_pantry` (inventory ID) | Page of stock. |
| `GET /api/inventory/<v>/item/<i>` | `get_pantry` (inventory and Item IDs) | Entry or UNTRACKED observation. |
| `POST /api/inventory/<v>/add-item-by-name` | `add_pantry_item` (name) | Resolve/create Item and add entry. |
| `PUT /api/inventory/<v>/item/<i>` | `add_pantry_item` (Item ID) | Create absent entry. |
| `PATCH /api/inventory/<v>/item/<i>` | `update_pantry_item` | Set total, mark state, edit note. |
| `POST /api/inventory/<v>/item/<i>/consume` | `consume_pantry_item` | Deduct amount. |
| `POST /api/inventory/<v>/item/<i>/restock` | `restock_pantry_item` | Add amount. |
| `DELETE /api/inventory/<v>/item/<i>` | `remove_pantry_item` | Stop tracking. |
| `POST /api/household/<h>/inventory/changes` | `apply_pantry_changes` | Atomic entry commands. |

MCP requires `household_id` explicitly and checks it against resource IDs. REST
derives the household from the location when absent from the route. Mutating
requests supply revisions in the JSON body, including DELETE. Stock pages use
stable Item-ID order, default limit 100, max 200, and `next_cursor` (null at end).
Filters: state and Item name. A cursor applies to the same inventory and filters;
pagination is a current-state scan, not a frozen snapshot. Load related Items
eagerly. No household-wide numeric aggregation in Phase 1.

Entry payload example (IDs and revision are illustrative):

```json
{
  "inventory_id": 3,
  "item_id": 42,
  "item": {"id": 42, "name": "eggs"},
  "description": null,
  "quantity": 12,
  "unit": "pcs",
  "quantity_is_estimate": false,
  "state": "AVAILABLE",
  "revision": "58472a79-57b2-46bb-925b-54743bd25091",
  "created_at": 1789776000000,
  "updated_at": 1789776000000
}
```

For consume/restock: `{ "expected_revision": "<read token>", "quantity": 2,
"unit": "pcs", "quantity_is_estimate": false }`. For setters add `operation`,
e.g. `set_total` or `mark_low`; bulk commands also carry `inventory_id` and
`item_id` (or `name` for add). List responses use `{ "items": [], "next_cursor":
null }`; single/mutation responses use the entry object; bulk uses `{ "results":
[] }`. Location discovery additionally includes `default_inventory_id`.

REST errors: 400 invalid input; 401 unauthenticated; 403 unauthorized; 404 absent
resource; 409 stock, unit, revision, duplicate or location conflict. Use a Pantry
error handler returning `{ "code": "...", "message": "...", "details": {} }`;
bulk errors include `index` in details. Do not expose raw exceptions.

New MCP adapters live in `app/mcp/pantry.py`. Describe set-vs-add, unknown amounts,
revision retries and the quantity-clearing LOW action in tool descriptions. Use
input/output schemas and annotations; nullable JSON Schema fields must not copy
OpenAPI's `nullable` syntax. Return identical serialized data as `structuredContent`
and JSON text. Expected domain failures use `isError: true`; malformed protocol
requests remain JSON-RPC errors. These choices follow the
[MCP tool contract](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).

Retain `KITCHENOWL_MCP_ENABLED=true`, `/mcp` and `/mcp/sse`. Test both existing
transports and revoked tokens. A real client demonstration preferably uses
stateless HTTP; SSE sessions are worker-local. Existing long-lived tokens provide
bearer authentication, but compatibility with a particular hosted client remains
to be demonstrated. Do not add a new auth system speculatively.

## 7. Migration and release checks

Create a new migration after the actual local head (currently `0b10d67750be`).
Include tables, constraints, indexes, relationships and one Pantry per existing
household. Use migration-local definitions, not imports of live models. Verify
fresh setup, populated upgrade, and downgrade/upgrade on disposable databases;
downgrade deletes Pantry data. Check schema parity with ORM-created test tables.

MVP tests must exercise state transitions, quantity validation, household isolation,
multi-session conditional writes, duplicate/stale retries, bulk rollback including
Item creation, name ambiguity, deletion, merge guards, both transports and existing
API regressions. Run against the actual deployment database before daily use.
If that is PostgreSQL, its validation is a release requirement, not deferred.
The full two-database matrix is required before an upstream contribution.

Dogfood the release with the scenario in section 1 and the checklist's usage gate.
Passing API tests alone does not establish that the voice/text interaction works.

## 8. Follow-up integration decisions

These preserve useful decisions from the earlier plan without blocking the first
Pantry workflow. Implement only when their checklist task is taken up.

| Area | Intended behavior |
| --- | --- |
| Threshold | Nullable nonnegative Decimal in entry unit. Positive quantity at/below threshold yields LOW; zero remains OUT. Unit changes reset/replace threshold. Qualitative marking retains it. |
| Expiry | Nullable date, earliest known expiry of aggregate stock, not a lot system. Past dates allowed; no automatic consumption. Restock nonempty stock keeps earliest date; restock OUT resets to supplied date or null. Later-date corrections must be explicit. |
| Feature flag | `Household.inventory_feature`, false by default if added; presentation only, never authorization. Add when a client needs it. |
| Item merge | One transaction across stock and existing references. Move noncolliding rows; sum compatible quantities/thresholds, propagate estimate and earliest expiry; qualitative LOW wins. Mixed/ambiguous quantities, incompatible units/thresholds or overflow conflict without changing anything. Refresh revisions. |
| Export/import | Portable storage/Item references; validate the entire Pantry section before writing. Re-import replaces totals, never adds. Fresh revisions/timestamps; null unresolved attribution; old exports remain valid; reject ambiguous mappings. Legacy whole-household import is not globally atomic. |
| Live events | Household rooms only; identical REST/MCP payloads, committed revisions. Preserve researched `inventory:add`, `inventory:delete`, `inventory_item:add`, `inventory_item:remove` names where compatible; add location-update event. Emit after commit; rollback emits nothing. Delivery failure does not turn committed stock into a retryable mutation. |
| Contribution | Recheck target branch and migration ancestry; attribute adapted code. No external comment or PR is sent as part of this plan. Never rewrite an already-deployed migration. |

## 9. Repository evidence and open questions

Local code inspected on 2026-09-19 at `ab4530e1`; paths below are relative to
`backend/`. Recheck these integration points when implementation starts.

| Evidence | Implementation consequence |
| --- | --- |
| `app/models/item.py`, `controller/item/item_controller.py` | Household Items; merge commits internally and controller saves first. Guard before either can change data. |
| `app/helpers/db_model_base.py` | `save()`/`delete()` commit. Pantry needs a separate transaction boundary. |
| `app/helpers/authorize_household.py`, `controller/mcp_controller.py` | REST has a server-admin override; MCP's household helper does not. Pantry service must unify its own policy. |
| `app/controller/mcp_controller.py` | Generic tool metadata, direct writes, final dispatch commit, HTTP and SSE transports. |
| `app/models/household.py`, `controller/exportimport/` | Exports enumerate domains; new models are not included automatically. |
| `app/models/recipe.py`, `service/recipe_scraping.py`, `service/ingredient_parsing.py` | Ingredient descriptions are text; scraping/parsing already exists. Phase 2 needs comparable amounts; Phase 3 improves existing import. |
| `app/sockets/connection_socket.py` | Existing authenticated `household/<id>` rooms can carry future events. |
| `app/controller/auth/auth_controller.py` | Existing creation/revocation of long-lived tokens. |
| `tests/api/conftest.py`, `app/config.py` | Tests create/drop configured tables. Set disposable DB/storage environment before importing app. |
| `pyproject.toml`, `README.md` | Project requires Python 3.14+; backend README still says 3.12+. Use project requirement and correct setup docs during implementation. |

[Issue #1040](https://github.com/TomBursch/kitchenowl/issues/1040) is a contributor
proposal, not an approved specification. It includes storage, optional quantities,
export/import and live updates; its deletion-at-zero choice differs from this fork.

Earlier research recorded upstream Pantry commit
[`371bdd0801291e26e5984a74d845e354961469ed`](https://github.com/TomBursch/kitchenowl/commit/371bdd0801291e26e5984a74d845e354961469ed),
`Inventory`/`InventoryItems`, and migration `f5a5571d9a06` sharing parent
`bd383e73ef4d` with our local head. That commit was unavailable locally and could
not be fetched through the research tool during this review. Preserve this as
historical evidence, not a verified current branch state. Verify before copying
code; create the fork migration from the actual local head regardless.

| Open question | Needed by | Working approach |
| --- | --- | --- |
| Which MCP client/auth setup will be used daily? | Real-client release check | Reuse bearer-token transport; prove client compatibility, then address a concrete gap if one exists. |
| Which database is the personal deployment using? | Migration/release validation | Begin isolated SQLite development; confirm deployment engine before daily use. |
| Are quantity/LOW semantics comfortable in practice? | Phase 1 usage gate | Implement recorded rules, log friction, amend decisions before Phase 2. |
| Which upstream branch and PR boundaries are wanted? | Contribution only | Develop locally; resolve when preparing a contribution. |

There are no unresolved product choices that block the first local implementation
task. Client/deployment checks are delivery tasks, not assumptions of compatibility.

## 10. Client and agent behavior

These principles govern how a natural-language agent should drive the Pantry tools.
They were confirmed through MCP testing. The service enforces data integrity; the
agent is responsible for intent and for not persisting invented facts.

1. **Preserve uncertainty; never silently invent persistent facts.** Use existing
   context first. If a required assumption materially affects persisted Pantry state,
   ask at most one low-friction clarification question. Prefer categorical
   clarification ("a full bag or nearly empty?") over demanding exact measurements.
   Mark estimated amounts as estimated (`quantity_is_estimate`), never as exact.

2. **Infer the user's intent, not the tool name.** A spoken "add" may mean create,
   restock, or set a new total. Read current Pantry state when needed, then choose
   the matching operation (`add_pantry_item` vs `restock` vs `set_total`). Do not
   create persistent catalog Items from vague or unresolved product identities.

3. **Mutations must be conservative and retry-safe.** Never blindly repeat a write
   after a `revision_conflict`: reread the entry and resolve the ambiguity first. Do
   not fold unresolved input into an atomic `apply_pantry_changes` batch when it could
   persist bad data — resolve the target first, then commit.
