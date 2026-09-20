# Pantry — setup and usage

User-facing reference for the Pantry feature (Phase 1). It covers enabling the
API, authentication, the REST routes and MCP tools, quantity and retry rules,
error codes, and the current backup limitation. For the product rationale see
[Vision](VISION.md); for the exact contract see [design spec](design/pantry.md);
for agent behavior see design spec §10.

## What Pantry is

Each household owns one or more **storage locations** (a default "Pantry" is
created automatically; you can add "Fridge", "Freezer", etc.). Each location
tracks **stock entries**: one entry per (location, catalog Item). An entry holds
either an exact quantity + unit, or a qualitative state, and reports one of:

| State | Meaning |
| --- | --- |
| `AVAILABLE` | A positive quantity, or a qualitative "present, amount unknown". |
| `LOW` | Qualitative "running low, amount unknown". |
| `OUT` | Tracked and known empty (quantity 0). |
| `UNTRACKED` | No entry exists for this Item/location (never persisted; derived on lookup). |

## Enabling the API and authenticating

Pantry REST routes are always registered. The MCP transports require the server
to be started with:

```
KITCHENOWL_MCP_ENABLED=true
```

MCP is served over two transports (pick per client):

- **Stateless HTTP** at `POST /mcp` — works across multiple workers; preferred.
- **HTTP+SSE** at `GET /mcp/sse` (+ `POST /mcp/messages?session_id=…`) — the
  session is worker-local.

All Pantry access (REST and MCP) is **authenticated** and scoped to households
you are a member of (server admins additionally pass, but every location/Item is
still checked against the requested household). MCP clients authenticate with a
**bearer long-lived token**:

```
POST /api/auth/llt        # create a long-lived token; send it as: Authorization: Bearer <token>
DELETE /api/auth/llt/<id> # revoke it
```

A revoked or absent token is rejected with `401` on REST, `/mcp` and `/mcp/sse`.

## Quantity and unit rules

- Quantities are decimals with **at most 3 decimal places**, from `0` to
  `999999999.999`. Booleans, non-finite and out-of-range values are rejected.
- A **positive** quantity requires a **unit**; a zero quantity may omit it.
- Units are trimmed and lower-cased; `piece`/`pieces` normalize to `pcs`.
  Otherwise the unit token is stored as given — **Pantry does not convert units**
  (e.g. `kg` is not converted to `g`). Arithmetic requires **matching units**.
- `quantity_is_estimate` marks a total as approximate; it must be `false`/absent
  for qualitative (`LOW`/`AVAILABLE`) entries and propagates through arithmetic
  (an estimated operand yields an estimated result).

## Commands / operations

| Intent | REST | MCP tool |
| --- | --- | --- |
| List locations + default id | `GET /api/household/<h>/inventory` | `get_pantry` (no inventory id) |
| Create a location | `POST /api/household/<h>/inventory` | `create_pantry_storage` |
| Rename a location | `POST /api/inventory/<v>` | `update_pantry_storage` |
| Delete an empty, non-default location | `DELETE /api/inventory/<v>` | `remove_pantry_storage` |
| List stock (paged, filterable) | `GET /api/inventory/<v>/items` | `get_pantry` (inventory id) |
| Read one entry (or UNTRACKED) | `GET /api/inventory/<v>/item/<i>` | `get_pantry` (inventory + Item id) |
| Start tracking by Item id | `PUT /api/inventory/<v>/item/<i>` | `add_pantry_item` (item_id) |
| Start tracking by name (resolve/create Item) | `POST /api/inventory/<v>/add-item-by-name` | `add_pantry_item` (name) |
| Set total / mark state / edit note | `PATCH /api/inventory/<v>/item/<i>` | `update_pantry_item` |
| Consume an amount | `POST /api/inventory/<v>/item/<i>/consume` | `consume_pantry_item` |
| Restock an amount | `POST /api/inventory/<v>/item/<i>/restock` | `restock_pantry_item` |
| Stop tracking | `DELETE /api/inventory/<v>/item/<i>` | `remove_pantry_item` |
| Apply many changes atomically | `POST /api/household/<h>/inventory/changes` | `apply_pantry_changes` |

`update_pantry_item` takes a discriminated `operation`: `set_total`,
`mark_available`, `mark_low`, `mark_out`, or `update_metadata`. Key semantics:

- **`set_total` replaces** the quantity; **`restock`/`consume` adjust** it — pick
  by intent, not by the word "add". Read current state first when unsure.
- `mark_available`/`mark_low` **clear** the numeric quantity (amount becomes
  unknown); `mark_out` sets it to 0; `update_metadata` changes only the note
  (`null` clears it).
- Consuming or restocking an entry whose quantity is unknown returns
  `quantity_unknown`; over-consumption returns `insufficient_stock`; a different
  unit returns `unit_mismatch`. Restocking an `OUT` entry with no stored unit
  adopts the supplied one.

## Revisions and retry behavior

Every entry and location has a `revision` (a UUID). **All updates to an existing
record require `expected_revision`** in the JSON body (including `DELETE`). The
check is enforced in the database write, so a stale write affects zero rows and
returns `revision_conflict`.

On `revision_conflict`, **re-read the record and resolve the outcome — do not
blindly resend** a consume/restock. Because revisions are fresh UUIDs, a lost
response cannot be safely replayed; the change may already have applied.

## Atomic bulk changes

`apply_pantry_changes` (REST `POST /api/household/<h>/inventory/changes`) applies
**1–50 commands** to one household in a single transaction:

- Each command carries `command` (`add`, `consume`, `restock`, `set_total`,
  `mark_available`, `mark_low`, `mark_out`, `update_metadata`, `remove`),
  `inventory_id`, a target (`item_id`, or `name` for `add`) and, for existing
  records, `expected_revision`.
- Duplicate targets (including two names resolving to the same Item) are rejected
  before anything is written.
- Any invalid or stale command **rolls the whole batch back** — including catalog
  Items an earlier `add` would have created — and returns the failing command's
  zero-based `index` in the error details.
- Results are returned in input order as `{ "results": [ … ] }`. This is not the
  same as JSON-RPC batching, which has no atomic guarantee.

## Errors

REST returns `{ "code": "…", "message": "…", "details": {} }`; bulk errors put the
failing command's `index` in `details`. MCP returns expected domain failures as
tool results with `isError: true` and the same payload as `structuredContent`.

| Code | HTTP | When |
| --- | --- | --- |
| `invalid_input` | 400 | Malformed request or unknown/contradictory fields. |
| `invalid_cursor` | 400 | A stock-list cursor does not match this query. |
| `unauthenticated` | 401 | Missing/invalid/revoked credentials. |
| `forbidden` | 403 | Not a member of the household. |
| `household_mismatch` | 403 | Location/Item belongs to another household. |
| `not_found` | 404 | Household, location, Item, or entry does not exist. |
| `already_tracked` | 409 | Adding an Item already tracked in the location. |
| `ambiguous_item` | 409 | A name matches several Items — use an Item id. |
| `revision_conflict` | 409 | `expected_revision` no longer matches; re-read and retry. |
| `unit_mismatch` | 409 | Arithmetic unit differs from the stored unit. |
| `quantity_unknown` | 409 | Arithmetic on an unknown (LOW/AVAILABLE) quantity. |
| `insufficient_stock` | 409 | Consuming more than is available. |
| `quantity_out_of_range` | 409 | A restock would exceed the maximum quantity. |
| `duplicate_target` | 409 | A bulk request targets the same entry twice. |
| `default_location` | 409 | Attempt to delete the default location. |
| `location_not_empty` | 409 | Deleting a location that still has entries (OUT counts). |
| `write_conflict` | 409 | A concurrent write changed referenced rows; refresh. |
| `database_unavailable` | 503 | The database could not complete the operation; retry. |

## Export / import

Household **export includes Pantry** — the export JSON has a `pantry` array of
locations, each with its stock entries (Items referenced by name; revisions and
attribution are regenerated on import). Importing that JSON into another household
recreates the locations and stock.

- **Re-import replaces per-entry totals; it never adds** — importing the same file
  twice is idempotent (no doubled quantities, no duplicated locations).
- The whole Pantry section is **validated before anything is written**, and
  ambiguous references (a name matching several Items, or duplicate location names)
  are rejected without partial writes.
- Older exports without a `pantry` section still import cleanly.

A database backup/restore remains a valid recovery path, but is no longer the only
one for Pantry data.
