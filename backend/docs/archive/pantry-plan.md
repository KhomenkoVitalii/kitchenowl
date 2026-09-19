# Pantry + MCP implementation plan

> Superseded draft. Use the [Phase 1 specification](../design/pantry.md) and
> [implementation checklist](../implementation/pantry.md). This file preserves
> earlier research; its release scope and unresolved recommendations are historical.

Status: proposed design for discussion; application implementation has not started.
Scope: `backend/` only.
Product direction: [Food Management Extension](../VISION.md).
Research date: 2026-09-19.

## 1. The first useful release

Let a household record what it has, inspect it, and change it through REST and MCP.
An MCP client should be able to handle this interaction using deterministic tools:

> I have twelve eggs, half a bag of rice, a little milk, and no chicken left.

The resulting pantry contains counted eggs, an estimated quantity of rice, milk
marked LOW, and chicken marked OUT. A later request to consume two eggs leaves ten.
Other household members see the committed changes. Retrying a request after a
connection failure cannot consume another two eggs accidentally.

The first release includes storage locations, quantities and stock states, explicit
consume/restock operations, an atomic bulk update, authorization, migrations,
export/import, API documentation, and MCP tools. It is useful before a Flutter
pantry screen exists.

Missing-ingredient calculations, automatic shopping transfers, cooking batches,
meal logs, and nutrition follow this release. They need separate behavior and
tests; pantry data should provide a stable foundation for them.

## 2. What the repository and upstream actually contain

### Local integration points

| Area | Current behavior | Consequence for Pantry |
| --- | --- | --- |
| `app/models/item.py` | Items belong to households; merging rewrites recipe, shopping-list, and history references. | Pantry must reuse Item and participate in merge/delete behavior. |
| `app/helpers/db_model_base.py` | `save()` and `delete()` commit immediately. | A multi-step pantry command must use add/flush/delete within one transaction instead. |
| `app/helpers/authorize_household.py` | Members can access household resources; server admins have an explicit override. | Apply the same policy through REST and MCP and validate referenced objects separately. |
| `app/controller/mcp_controller.py` | A tool registry, two transports, direct model mutations, generic descriptions, and a final commit in dispatch. | Add thin pantry adapters and explicit tool metadata; arrange a single pantry transaction boundary. |
| `app/sockets/connection_socket.py` | Authenticated users join `household/<id>` rooms. | Send pantry notifications to the existing household rooms after commit. |
| `app/models/household.py` and `controller/exportimport/` | Household exports and imports enumerate supported domains explicitly. | Adding a model alone will not preserve pantry data in exports. |
| `app/models/recipe.py` | Ingredient amounts are stored in free-text descriptions. | A numeric pantry does not immediately enable reliable recipe subtraction. |
| `app/controller/auth/auth_controller.py` | `/api/auth/llt` creates revocable long-lived bearer tokens. | Reuse this for compatible MCP clients; a new token system is unnecessary. |
| `tests/api/conftest.py` | Tests create/drop tables against the configured application database. | Test runs must set an isolated database before any application import. |

The local migration head is `0b10d67750be`; its parent is `bd383e73ef4d`.
The README says Python 3.12+, but `pyproject.toml` requires Python 3.14+; use the
declared project requirement when setting up implementation validation.

### Upstream alignment

[Issue #1040](https://github.com/TomBursch/kitchenowl/issues/1040) proposes optional
quantities, storage locations, reuse of Item, export/import, and real-time updates.
It is a contributor proposal, not an approved specification; the GitHub API returned
no comments when checked. Its suggested zero-stock deletion and conflict handling
are decisions we should discuss rather than silently adopt.

The maintainer already has a [pantry branch](https://github.com/TomBursch/kitchenowl/tree/pantry),
at commit [`371bdd0801291e26e5984a74d845e354961469ed`](https://github.com/TomBursch/kitchenowl/commit/371bdd0801291e26e5984a74d845e354961469ed).
Its backend uses `Inventory` for a named location and `InventoryItems` for the
association to Item, with a composite key `(inventory_id, item_id)`. It has initial
REST routes and socket events, but only free-text stock descriptions. This model
was absent from the upstream main branch's model directory when checked.

**Recommendation: extend those Inventory concepts and names.** Treat Fridge,
Freezer, and Pantry as named Inventory records. Avoid introducing a competing
InventoryStorage model. Review and credit the maintainer's work when adapting it.
The complete commit includes frontend changes, so it is not a direct cherry-pick
within our current backend-only scope.

Before presenting a PR, prepare a short design comment for the existing discussion:
ask which branch to target, whether this model extension is welcome, and whether
the maintainer wants backend and MCP changes together. Posting it is a separate
action for the user. Local development can continue independently.

Upstream requests focused PRs, rebasing, squashing before merge, and conventional
commit messages in its [contribution guide](https://docs.kitchenowl.org/latest/reference/contributing/).
These practices improve reviewability; they cannot guarantee acceptance.

## 3. Recommended domain model

### Inventory: a storage location

Preserve the draft's `id`, `household_id`, `name`, relationships, and timestamps.
Add a revision token for conditional edits. Keep names editable and household-scoped;
use stable IDs in mutations. Reject blank or overlong names. Name lookup must
report ambiguity rather than pick an arbitrary record.

Provision a single default location named Pantry for existing and new households.
Fridge, Freezer, and custom locations can be created on demand. Preserve the draft's
lowest-ID default convention initially, protect that default from deletion, and
reject deletion of a nonempty location. This avoids silently deleting its stock.
Reading a pantry must never create locations or other data.

Add `Household.inventory_feature`, defaulting to false for existing households and
new households until explicitly enabled. It follows the existing feature flags as
a presentation preference; it is not an authorization boundary. API access depends
on permissions, so MCP can still operate while a client hides the feature.

### InventoryItems: stock for an existing catalog item

Preserve `(inventory_id, item_id)` as the primary key. Household ownership comes
from Inventory and Item; the service must require both to belong to the same
household, even when the caller is a server admin or belongs to both households.

| Field | Proposed representation | Meaning |
| --- | --- | --- |
| `description` | Existing text field | Optional human note; never the arithmetic source of truth. |
| `quantity` | Nullable `Numeric(12, 3)` | Known or estimated nonnegative amount; null means unquantified. |
| `unit` | Nullable string, max 32 characters | Examples: `pcs`, `g`, `kg`, `ml`, `l`, `bag`, `package`, `portion`. |
| `quantity_is_estimate` | Boolean, default false | Allows the client to distinguish an estimate such as half a bag. |
| `stock_state` | Nullable bounded string | AVAILABLE or LOW when quantity is null; otherwise derived from quantity. |
| `low_stock_threshold` | Nullable `Numeric(12, 3)` | Threshold in the entry's unit; requires a unit. |
| `expires_at` | Nullable SQL Date | Earliest known expiry represented by this aggregate entry. |
| `revision` | UUID string | Changes on every mutation, including quantity and metadata changes. |
| `created_by` | Existing nullable user FK | Attribution; deleting a user must not delete stock. |
| `created_at`, `updated_at` | Existing timestamp mixin | Creation and latest modification times. |

Use Decimal for arithmetic and bound values to the declared precision. Reject
excess precision instead of silently rounding a user's input. Serialize quantities
as JSON numbers through an explicit response serializer shared by REST and MCP;
do not rely on each transport's default Decimal/date conversions.

Expose a computed `state` field with these rules:

| Stored information | Returned state |
| --- | --- |
| Quantity is zero | OUT |
| Quantity is positive and at or below a configured threshold | LOW |
| Quantity is positive otherwise | AVAILABLE |
| Quantity is null | Explicit `stock_state` of AVAILABLE or LOW |
| Entry does not exist | Untracked; do not claim the item is available or OUT. |

Database checks should enforce nonnegative quantities/thresholds, valid states,
and the relationship between quantity and stock_state. Application validation
also rejects booleans masquerading as numbers, nonfinite numbers, empty units,
invalid dates, overlong strings, and contradictory fields. Positive quantities
require a unit; zero can be recorded without knowing a unit.

One item in the fridge and the same item in the freezer are separate entries.
Version one deliberately aggregates packages within a location. It cannot track
two expiry dates or two different units for the same item in that location. A
future lot table can attach to the existing association without replacing Item.

## 4. Commands and exact behavior

| Command | Behavior |
| --- | --- |
| Add | Create an entry only if absent; an existing entry produces a conflict, never an implicit increment. |
| Set quantity | Replace the total: “I have 12 eggs” means 12, regardless of the previous amount. |
| Consume | Subtract a positive amount from a quantified entry in the same unit. |
| Restock | Add a positive amount to a quantified entry in the same unit. |
| Mark LOW / AVAILABLE | Clear the numeric quantity and its estimate flag; record the qualitative state. Retain the preferred unit and threshold. |
| Mark OUT | Set quantity to zero; retain the entry, unit, and threshold for later restocking. |
| Update metadata | Edit description, expiry, threshold, or estimate flag without changing unrelated fields. |
| Remove | Explicitly stop tracking the entry; this differs from marking it OUT. |

Additional rules:

- Consume beyond the recorded amount returns `insufficient_stock`; it does not
  clamp to zero. The user can instead explicitly set a corrected total or mark OUT.
- Arithmetic on an unquantified entry returns `quantity_unknown`. “Some left plus
  five” has no known total. Use a state update or an explicitly observed total.
- Quantity and qualitative state setters are mutually exclusive in one command.
  An omitted field means unchanged; explicit null clears an optional value where
  allowed. Clearing quantity requires an explicit qualitative state.
- Normalize unit whitespace/case and a small documented alias list, such as
  `pieces` to `pcs`. Arithmetic requires the same normalized unit in version one.
  Do not guess package size, density, or ingredient weight. Changing a unit requires
  an explicit new total and resetting/replacing the threshold in the same command.
- Arithmetic preserves `quantity_is_estimate` if either input is an estimate.
- Restocking a nonempty entry preserves its existing expiry; a supplied earlier
  date can move it earlier. Replacing that date with a later date is an explicit
  metadata correction. Restocking an OUT entry starts with the supplied date or
  null, so the previous stock's expiry does not follow the new food.
- Past expiry dates are accepted as information. Expiry never automatically
  consumes or deletes stock.
- The simple LOW action intentionally abandons a stale numeric estimate. Tool
  descriptions must explain this effect so the client can choose correctly.

Keeping OUT entries differs from #1040's proposed deletion-at-zero behavior.
It preserves units and thresholds and supports the user's explicit “no chicken
left” example. This is a product decision to raise with the maintainer.

## 5. Shared implementation and transactions

```text
REST controllers       MCP tools
       \                 /
        input validation
                |
       inventory command service
       permissions + state rules
       transaction + revision checks
                |
        Inventory / InventoryItems / Item
                |
         commit, then socket events
```

Put business rules in `app/service/inventory.py`. Both transports pass a typed,
validated command and the authenticated actor. Services must not invoke REST
controllers or make HTTP requests back to the same server.

Use a single transaction for each command or explicit bulk command. Catalog item
creation, stock updates, and revision changes succeed or roll back together.
Avoid the committing `save()`, `delete()`, and `Item.create_by_name()` helpers
inside this transaction. A small execution wrapper commits once and emits its
collected events only after success. Adapt MCP dispatch for pantry tools so its
existing final commit does not create a second pantry transaction boundary.

Authorize the household and every referenced object before mutations. Follow the
existing member/server-admin policy consistently in both adapters. Require admin
rights for household settings as the current API does. Prevent body parameters
from changing household ownership. Recheck membership for each tool execution.

Mutations identify entries by inventory ID and item ID. Optional name-based add
resolves an exact normalized catalog name within the household, rejects ambiguous
matches, and can create a missing catalog item in the same transaction. It must
never use fuzzy matching as an automatic write target. Serialize simultaneous
pantry name creation within a household and test it on both databases. Existing
catalog write paths do not have a normalized-name uniqueness constraint; do not
claim global name uniqueness or add that constraint without a separate legacy-data
review. IDs remain the authoritative identity.

### Concurrent requests and uncertain retries

Require `expected_revision` for mutations of an existing entry or location. Read
responses provide the token. Perform updates/deletes with a database comparison
against that token, not a Python check followed by an unconditional write. A stale
token returns `revision_conflict` with no mutation; return the current authorized
record so the client can reconcile. A new entry must satisfy an absent-row check
and the composite primary key. Use fresh UUID revisions to prevent a deleted and
recreated entry from accidentally accepting an old revision.

This also prevents repeating the same consume/restock request after a lost
response: its old revision cannot apply twice. It does not replay the original
response or provide an exactly-once delivery guarantee. On a conflict after a
timeout, the client must inspect the record and ask for clarification if the
outcome is ambiguous; it must not substitute the new revision and retry the same
delta automatically. A durable operation receipt can be added later if automatic
replay recovery proves necessary.

Use the same conditional-write path on SQLite and PostgreSQL. Surface database
contention as a retryable failure after rollback, with bounded retry only when the
transaction is known not to have committed. In-process locks alone are insufficient
for multiple workers.

### Bulk updates

Provide one explicit `apply_pantry_changes` operation, limited to 50 commands in
one household. Validate and authorize the whole batch before changes; reject
duplicate targets and inconsistent references. Apply it atomically and return
results in input order. One invalid/stale command rolls everything back and
identifies its input index. JSON-RPC batching remains a collection of independent
requests, not a substitute for this transaction.

## 6. REST contract

Follow the maintainer draft's household inventory routes and inventory/item IDs.
Use normal blueprint registration and Marshmallow schemas. Preserve useful draft
entry points as adapters where practical; document any tightened validation.

| Route | Operation |
| --- | --- |
| `GET /api/household/<household_id>/inventory` | List locations; include their IDs and revisions. |
| `POST /api/household/<household_id>/inventory` | Create a location. |
| `POST /api/inventory/<inventory_id>` | Rename a location with its revision. |
| `DELETE /api/inventory/<inventory_id>` | Delete an empty, nondefault location with its revision. |
| `GET /api/inventory/<inventory_id>/items` | List entries with bounded pagination and state/name filters. |
| `POST /api/inventory/<inventory_id>/add-item-by-name` | Resolve/create Item and add stock only if absent. |
| `PUT /api/inventory/<inventory_id>/item/<item_id>` | Create absent stock for a known Item. |
| `PATCH /api/inventory/<inventory_id>/item/<item_id>` | Set quantity/state or edit metadata with its revision. |
| `POST /api/inventory/<inventory_id>/item/<item_id>/consume` | Consume a positive amount with its revision. |
| `POST /api/inventory/<inventory_id>/item/<item_id>/restock` | Restock a positive amount with its revision. |
| `DELETE /api/inventory/<inventory_id>/item/<item_id>` | Stop tracking, conditional on revision. |
| `POST /api/household/<household_id>/inventory/changes` | Atomic bulk command. |

For GET pagination, default to 100 entries and cap at 200, ordered by stable IDs;
return an explicit continuation cursor. MCP can traverse locations and pages
without silently losing entries. Reads should eagerly load related Item data to
avoid a database query for each returned entry.

Successful entry responses include both IDs, a compact Item representation,
quantity/unit/estimate, computed state, threshold, expiry, revision, and timestamps.
Return expiry as `YYYY-MM-DD` and timestamps as the existing epoch milliseconds.

Use 400 for input validation, existing 401/403 conventions for authentication and
authorization, 404 for absent resources, and 409 for conflicts such as stale
revision, incompatible unit, insufficient stock, or a nonempty storage location.
Provide stable machine-readable `code`, `message`, and field/index details for
pantry errors. Register a pantry-specific error handler; the current global
exception handler returns generic text and does not provide this contract.

Document request and response schemas, permissions, errors, pagination, and units
in `/api/openapi` and the backend README. Preserve existing endpoint responses.

## 7. MCP contract

Keep `/mcp` and `/mcp/sse` behind `KITCHENOWL_MCP_ENABLED=true`. Add pantry handlers
in `app/mcp/pantry.py` and register them with the existing server. Avoid expanding
the already large controller with another set of business-rule implementations.

| Tool | Purpose |
| --- | --- |
| `get_pantry` | List locations and/or a page of stock, with revisions and explicit continuation information. |
| `create_pantry_storage` | Create a named location. |
| `update_pantry_storage` | Rename a location with an expected revision. |
| `remove_pantry_storage` | Delete an empty, nondefault location with an expected revision. |
| `add_pantry_item` | Create stock using an Item ID or an unambiguous exact name. |
| `update_pantry_item` | Set total, set LOW/AVAILABLE/OUT, or edit metadata. |
| `consume_pantry_item` | Subtract a specified amount. |
| `restock_pantry_item` | Add a specified amount. |
| `remove_pantry_item` | Stop tracking an entry. |
| `apply_pantry_changes` | Apply a bounded, atomic set of explicit commands. |

Require explicit household context in tool inputs and verify it against inventory
and item IDs. A tool must never choose the user's first household silently. Server
instructions should explain: call `list_households`, inspect `get_pantry`, resolve
identity, distinguish observed totals from additions, then supply revisions.

Use descriptive input schemas, shared Marshmallow validation, and output schemas.
JSON Schema definitions must correctly express nullable fields and unknown-field
rejection; OpenAPI's `nullable` syntax cannot simply be copied into an MCP schema.
Contract tests should cover both the advertised schema and actual validation.

Return the same serialized data in `structuredContent` and the text content block.
Describe read-only and destructive behavior using tool annotations; delta tools
must not be advertised as unconditionally idempotent. Treat validation/protocol
failures separately from expected domain failures: pantry domain failures return
`isError: true` with useful, sanitized error data. Keep legacy tool behavior stable
and cover it with regression tests. These choices follow the
[MCP tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).

Test tool discovery and execution on both existing transports. Prefer stateless
HTTP for the live demonstration; current SSE sessions are per worker and require
session affinity. Document setup with existing revocable long-lived tokens and
verify revocation. Compatibility with the user's chosen client is a release check;
support for bearer-authenticated MCP does not by itself establish compatibility
with every hosted MCP client or its authentication requirements.

## 8. Integration that must ship with the feature

### Item merges and deletion

Extend Item's relationships so catalog and household deletion clean up inventory
associations. Deleting stock itself must never delete the shared catalog Item.
User deletion should clear creator attribution.

Handle stock when `Item.merge()` runs. Nonoverlapping associations move to the
surviving Item. Colliding entries may combine quantified amounts only when units
and thresholds are compatible; preserve the earliest expiry and estimate flag.
Two unquantified entries can combine their state conservatively (LOW wins).
Mixed quantified/unquantified entries, incompatible units, conflicting thresholds,
and arithmetic overflow return a conflict for explicit reconciliation.

Preflight the entire merge and complete it atomically, including existing recipe,
shopping-list, and history references. The existing merge path commits in more
than one place, and its controller saves before merging: this requires a focused
transaction adjustment, not just another loop that eventually deletes stock.
Failed merges must leave every affected domain intact.

### Export/import

Extend household export with inventories and all their stock fields. Use names
and document-local references, not database IDs, for portable relationships.
Allocate fresh revisions and server timestamps on import. Resolve creator names
when available; otherwise preserve stock with null attribution.

Existing exports without inventory remain valid. Define inventory restore as
merge-by-explicit-storage-and-item mapping with totals replaced, never added;
re-importing the same export must not double stock. Reject ambiguous name mappings.
Provide a pantry-only import transaction for the new section, validating all of it
before any pantry writes. Document that the legacy combined importer is not a
single transaction across recipes, expenses, and inventory.

### Live updates

Retain the draft's `inventory:add`, `inventory:delete`, `inventory_item:add`, and
`inventory_item:remove` event names; add an explicit location-update event.
Include revisions and both IDs in entry notifications. Events for mutations via
REST and MCP must have the same shape and go only to the owning household room.

Publish after commit. A failed transaction emits nothing. A socket delivery failure
after commit does not turn a successful stock change into a retryable mutation
failure: log it and let clients refresh from REST/MCP. Event delivery is best-effort;
reconnecting clients reload authoritative state. A durable outbox is unnecessary
for this first release unless delivery guarantees become a product requirement.

## 9. Migration strategy

The draft migration `f5a5571d9a06` and local `0b10d67750be` both descend from
`bd383e73ef4d`. Copying the draft migration blindly would introduce two heads.

For this fork, write a new migration after the actual local head, using the draft's
table names and compatible keys, and attribute the adapted design. Include the
feature flag, stock fields/checks, indexes, defaults, and Pantry location backfill.
Do not import live application models into the migration. Use migration-local
tables and the Alembic transaction. Keep fresh and migrated schemas consistent.

Before preparing the upstream PR, regenerate/rebase the unpublished migration for
the maintainer's chosen base. If contributing on the pantry branch, extend its
existing revision rather than creating its tables again. If support for already
deployed pantry-branch databases is requested, add and test an explicit merge/
upgrade path; it is not covered merely by having matching table names. Never
rewrite a migration that users have already applied.

Verify fresh install, upgrade from a populated local-head database, and downgrade
then upgrade on disposable databases for both SQLite and PostgreSQL. Existing
users, recipes, shopping lists, and token indexes must survive. Document that a
downgrade removes the new pantry data.

## 10. Tests that define “done”

| Area | Required evidence |
| --- | --- |
| Everyday flow | Bulk-record eggs/rice/milk/chicken, consume eggs, restock, list through REST and MCP, export and restore. |
| Qualitative stock | Null differs from zero; LOW clears stale quantity; OUT retains preferences; threshold boundaries work. |
| Quantity rules | Fractions, estimates, Decimal arithmetic, bounds, invalid units, unknown totals, over-consumption, and precision limits. |
| Isolation | Unauthenticated, nonmember, member, household admin, and server admin cases; cross-household references rejected even for a user in both. |
| Transactions | Failure after new catalog item creation leaves no partial item/stock; failing bulk command leaves no changes or events. |
| Concurrency | Two independent sessions consume with one revision: exactly one succeeds; duplicate add does not create two stock rows. |
| Retry behavior | Repeat a successful delta with its old revision; verify no second mutation. Delete/recreate must reject the old token. |
| Lifecycle | Catalog merge with/without collisions; conflict rollback; item, user, inventory, and household deletion. |
| Backup | Old exports accepted; pantry export/import round-trip; repeated import does not add quantities; ambiguous mappings rejected. |
| MCP | Discoverable schemas/descriptions; result and error shapes; both transports; invalid arguments; revoked token; existing tools still work. |
| Notifications | Both interfaces produce identical committed events; rollback emits none; wrong household receives none. |
| Migrations | Fresh and populated upgrades, downgrade/upgrade, one expected head, ORM/migration schema agreement on both databases. |
| Compatibility | Existing backend API tests pass; feature defaults do not alter existing shopping or recipe behavior. |

Run tests with an explicitly disposable DB and storage directory configured before
`app` is imported. A file-backed SQLite database is necessary for meaningful
multi-session tests. PostgreSQL tests use a separate test database. Establish the
existing suite's baseline first so unrelated failures are reported accurately.

Use Ruff/format checks on changed Python files and Pyright for the affected modules,
recording any existing typing baseline. Migration and integration tests must execute
actual database behavior rather than mock away commits, constraints, and locks.
End with a real MCP client session; mocked tool dispatch alone is insufficient.

## 11. Implementation sequence and review boundaries

These are implementation milestones. The personal release includes all of them;
the maintainer can choose whether to review them as stacked PRs or one feature PR.

| Step | Work | Completion condition |
| --- | --- | --- |
| 1. Establish the base | Record upstream comparison, test baseline, target migration head, and agreed behavior. | Reproducible isolated test commands and settled model/command contract. |
| 2. Inventory foundation | Adapt Inventory/InventoryItems, relationships, feature flag, migration, storage APIs, lifecycle integration. | Migration and ownership tests pass; existing data is preserved. |
| 3. Stock commands | Quantities/states, shared schemas/service, revision checks, REST operations, atomic bulk changes. | Domain, rollback, and real concurrency tests pass on both databases. |
| 4. Complete integration | Merge handling, portable exports/imports, committed socket events, OpenAPI documentation. | Full lifecycle/backup/event tests and existing API regressions pass. |
| 5. MCP delivery | Thin tools, descriptions/schemas, error mapping, shared execution, setup documentation. | Same scenario works through REST and a real MCP client, including failed/retried writes. |
| 6. Contribution package | Focused diff, attribution, design notes, migration notes, test evidence, concise PR description. | A reviewer can understand behavior and reproduce the tests without this chat. |

Expected file map (all paths relative to `backend/`):

- New: `app/models/inventory.py`, `app/service/inventory.py`,
  `app/controller/inventory/{__init__,schemas,inventory_controller}.py`,
  `app/mcp/{__init__,pantry}.py`, an Alembic revision, and inventory tests.
- Extend: model/controller registration, household model/schemas/controller,
  Item's merge and relationships, relevant User relationships, export/import,
  MCP tool metadata/dispatch, and `README.md`.
- Keep unrelated refactors and MCP-wide protocol upgrades in separate changes.
  Add no new runtime library unless implementation demonstrates a concrete need.

Prepare local commits around working, tested behavior rather than one commit per
technical layer. Attribute adapted upstream code. Keep this personal roadmap out
of an upstream code PR unless the maintainer wants it as design documentation.

## 12. How this grows into the full product

| Next release | Concrete outcome | Design dependency |
| --- | --- | --- |
| Ingredient availability | Explain what is present, missing, or uncertain; explicitly add missing items to shopping. | A tested parser for existing recipe descriptions and limited unit conversion; never silently treat unparseable amounts as zero. |
| Import quality | Improve URL imports and retain useful source data. | Extend the existing scraper/NLP/optional LLM pipeline; recipe importing already exists. |
| Cooking and leftovers | One cooking command consumes ingredients and creates a batch; eating portions reduces the batch. | Reuse pantry transaction/revision rules; consuming ingredients must happen once. |
| Consumption log | Record what a person ate, including standalone meals and batch portions. | Keep household stock distinct from personal consumption; capture overrides as snapshots. |
| Nutrition | Compute approximate totals with source/provenance and missing-data indicators. | Snapshot recipe/batch nutrition so later recipe edits cannot rewrite historical meals. |
| Planning and quick UI | Pantry-aware meal suggestions and one-action updates. | Stable APIs and explicit uncertainty; Flutter work requires expanding the current scope. |

Cooked batches and meal logging should be designed together when that stage starts,
even if their implementations are split. That avoids double-consuming ingredients
when a recipe is cooked once and eaten over several days.

## 13. Decisions to discuss before coding

Recommended defaults are specified above so implementation has a concrete starting
point. The product decisions with the largest downstream consequences are:

1. Use the maintainer's Inventory/location model and one Item per location.
2. Keep OUT entries; distinguish clearing stock from stopping tracking.
3. A LOW/AVAILABLE action replaces a numeric estimate with qualitative information.
4. Use matching-unit arithmetic initially; add conversions with recipe availability.
5. Require revisions for writes and report conflicts instead of overwriting newer stock.
6. Include atomic bulk updates, backups, and lifecycle behavior in the usable release.

Upstream questions are the target branch, migration base, these behavior differences,
and preferred PR boundaries. Client-specific authentication compatibility must be
verified before declaring the personal MCP workflow ready.
