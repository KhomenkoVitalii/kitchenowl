"""P1-01: record and read Pantry stock through REST and MCP.

Covers location discovery, stock creation, reads (including UNTRACKED), household
isolation, duplicate protection, the qualitative/estimated states from the release
scenario, and the tracked-Item merge guard.
"""

from types import SimpleNamespace

import pytest


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _rpc(client, token, name, arguments):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=_auth(token),
    )


@pytest.fixture
def env(admin_client):
    """Two members of one household plus an outsider who owns another household."""
    client = admin_client

    def make_user(username):
        res = client.post(
            "/api/user/new",
            json={"username": username, "name": username, "password": "pw123456"},
        )
        assert res.status_code == 200, res.get_json()
        res = client.post(
            "/api/auth", json={"username": username, "password": "pw123456"}
        )
        assert res.status_code == 200, res.get_json()
        token = res.get_json()["access_token"]
        uid = client.get("/api/user", headers=_auth(token)).get_json()["id"]
        return uid, token

    alice_id, alice = make_user("alice")
    bob_id, bob = make_user("bob")
    carol_id, carol = make_user("carol")

    res = client.post(
        "/api/household", json={"name": "home", "member": [alice_id]}, headers=_auth(alice)
    )
    assert res.status_code == 200, res.get_json()
    household_id = client.get("/api/household", headers=_auth(alice)).get_json()[0]["id"]

    res = client.put(
        f"/api/household/{household_id}/member/{bob_id}",
        json={"admin": True},
        headers=_auth(alice),
    )
    assert res.status_code == 200, res.get_json()

    default_id = client.get(
        f"/api/household/{household_id}/inventory", headers=_auth(alice)
    ).get_json()["default_inventory_id"]

    # Outsider household with its own catalog Item, for cross-household checks.
    res = client.post(
        "/api/household", json={"name": "other", "member": [carol_id]}, headers=_auth(carol)
    )
    assert res.status_code == 200, res.get_json()
    other_household_id = next(
        h["id"]
        for h in client.get("/api/household", headers=_auth(carol)).get_json()
        if h["name"] == "other"
    )
    res = client.post(
        f"/api/household/{other_household_id}/item",
        json={"name": "foreign"},
        headers=_auth(carol),
    )
    assert res.status_code == 200, res.get_json()
    foreign_item_id = res.get_json()["id"]

    return SimpleNamespace(
        client=client,
        alice=alice,
        bob=bob,
        carol=carol,
        household_id=household_id,
        default_id=default_id,
        other_household_id=other_household_id,
        foreign_item_id=foreign_item_id,
    )


def _add(env, token, inventory_id, body):
    return env.client.post(
        f"/api/inventory/{inventory_id}/add-item-by-name", json=body, headers=_auth(token)
    )


def test_household_gets_default_pantry(env):
    res = env.client.get(
        f"/api/household/{env.household_id}/inventory", headers=_auth(env.alice)
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["default_inventory_id"] == env.default_id
    assert body["next_cursor"] is None
    assert [loc["name"] for loc in body["items"]] == ["Pantry"]


def test_add_stock_then_read_through_rest(env):
    res = _add(env, env.alice, env.default_id, {"name": "eggs", "quantity": 12, "unit": "pcs"})
    assert res.status_code == 201, res.get_json()
    entry = res.get_json()
    assert entry["quantity"] == 12
    assert entry["unit"] == "pcs"
    assert entry["state"] == "AVAILABLE"
    assert entry["quantity_is_estimate"] is False
    item_id = entry["item_id"]

    res = env.client.get(
        f"/api/inventory/{env.default_id}/item/{item_id}", headers=_auth(env.alice)
    )
    assert res.status_code == 200
    assert res.get_json()["revision"] == entry["revision"]

    res = env.client.get(
        f"/api/inventory/{env.default_id}/items", headers=_auth(env.alice)
    )
    assert res.status_code == 200
    listed = res.get_json()
    assert listed["next_cursor"] is None
    assert [e["item"]["name"] for e in listed["items"]] == ["eggs"]


def test_add_through_mcp_read_through_rest(env):
    res = _rpc(
        env.client,
        env.alice,
        "add_pantry_item",
        {
            "household_id": env.household_id,
            "inventory_id": env.default_id,
            "name": "eggs",
            "quantity": 12,
            "unit": "pcs",
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert "error" not in body
    entry = body["result"]["structuredContent"]
    assert entry["state"] == "AVAILABLE"

    res = env.client.get(
        f"/api/inventory/{env.default_id}/item/{entry['item_id']}", headers=_auth(env.alice)
    )
    assert res.status_code == 200
    assert res.get_json()["revision"] == entry["revision"]


def test_second_member_reads_nonmember_cannot(env):
    entry = _add(
        env, env.alice, env.default_id, {"name": "eggs", "quantity": 12, "unit": "pcs"}
    ).get_json()
    path = f"/api/inventory/{env.default_id}/item/{entry['item_id']}"

    assert env.client.get(path, headers=_auth(env.bob)).status_code == 200
    assert env.client.get(path, headers=_auth(env.carol)).status_code == 403


def test_duplicate_add_is_rejected_and_changes_nothing(env):
    first = _add(
        env, env.alice, env.default_id, {"name": "eggs", "quantity": 12, "unit": "pcs"}
    ).get_json()

    res = _add(env, env.alice, env.default_id, {"name": "eggs", "quantity": 99, "unit": "pcs"})
    assert res.status_code == 409
    assert res.get_json()["code"] == "already_tracked"

    current = env.client.get(
        f"/api/inventory/{env.default_id}/item/{first['item_id']}", headers=_auth(env.alice)
    ).get_json()
    assert current["quantity"] == 12
    assert current["revision"] == first["revision"]


def test_low_estimated_and_out_states(env):
    milk = _add(env, env.alice, env.default_id, {"name": "milk", "state": "LOW"}).get_json()
    assert milk["state"] == "LOW"
    assert milk["quantity"] is None

    rice = _add(
        env,
        env.alice,
        env.default_id,
        {"name": "rice", "quantity": 0.5, "unit": "bag", "quantity_is_estimate": True},
    ).get_json()
    assert rice["quantity"] == 0.5
    assert rice["quantity_is_estimate"] is True
    assert rice["state"] == "AVAILABLE"

    chicken = _add(env, env.alice, env.default_id, {"name": "chicken", "state": "OUT"}).get_json()
    assert chicken["state"] == "OUT"
    assert chicken["quantity"] == 0


def test_untracked_lookup_returns_null_revision(env):
    eggs = _add(
        env, env.alice, env.default_id, {"name": "eggs", "quantity": 12, "unit": "pcs"}
    ).get_json()

    res = env.client.post(
        f"/api/household/{env.household_id}/inventory",
        json={"name": "Fridge"},
        headers=_auth(env.alice),
    )
    assert res.status_code == 201, res.get_json()
    fridge_id = res.get_json()["id"]

    res = env.client.get(
        f"/api/inventory/{fridge_id}/item/{eggs['item_id']}", headers=_auth(env.alice)
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["state"] == "UNTRACKED"
    assert body["revision"] is None


def test_cross_household_item_cannot_be_attached(env):
    res = env.client.put(
        f"/api/inventory/{env.default_id}/item/{env.foreign_item_id}",
        json={"quantity": 1, "unit": "pcs"},
        headers=_auth(env.alice),
    )
    assert res.status_code == 403
    assert res.get_json()["code"] == "household_mismatch"


def test_mcp_tools_list_includes_pantry_tools(env):
    res = env.client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=_auth(env.alice),
    )
    assert res.status_code == 200
    names = {t["name"] for t in res.get_json()["result"]["tools"]}
    assert {"get_pantry", "add_pantry_item", "create_pantry_storage"} <= names


def test_mcp_domain_error_is_tool_error(env):
    _add(env, env.alice, env.default_id, {"name": "eggs", "quantity": 12, "unit": "pcs"})
    res = _rpc(
        env.client,
        env.alice,
        "add_pantry_item",
        {
            "household_id": env.household_id,
            "inventory_id": env.default_id,
            "name": "eggs",
            "quantity": 1,
            "unit": "pcs",
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert "error" not in body  # expected failures are tool errors, not protocol errors
    assert body["result"]["isError"] is True
    assert body["result"]["structuredContent"]["code"] == "already_tracked"


def test_deletion_integrity(env):
    from app import db
    from app.models import Household, Inventory, InventoryItems, Item

    eggs = _add(
        env, env.alice, env.default_id, {"name": "eggs", "quantity": 12, "unit": "pcs"}
    ).get_json()
    item_id = eggs["item_id"]

    # Deleting a stock entry keeps the catalog Item.
    db.session.delete(db.session.get(InventoryItems, (env.default_id, item_id)))
    db.session.commit()
    assert db.session.get(Item, item_id) is not None

    # Deleting the catalog Item cascades to its remaining stock.
    _add(env, env.alice, env.default_id, {"name": "eggs", "quantity": 5, "unit": "pcs"})
    db.session.delete(db.session.get(Item, item_id))
    db.session.commit()
    assert db.session.get(InventoryItems, (env.default_id, item_id)) is None

    # Deleting the household cascades inventories and their stock.
    _add(env, env.alice, env.default_id, {"name": "flour", "quantity": 1, "unit": "kg"})
    db.session.delete(db.session.get(Household, env.household_id))
    db.session.commit()
    assert db.session.get(Inventory, env.default_id) is None
    assert (
        db.session.query(InventoryItems)
        .filter(InventoryItems.inventory_id == env.default_id)
        .count()
        == 0
    )


def test_deleting_user_clears_attribution(env):
    from app import db
    from app.models import InventoryItems, User

    eggs = _add(
        env, env.alice, env.default_id, {"name": "eggs", "quantity": 12, "unit": "pcs"}
    ).get_json()
    entry = db.session.get(InventoryItems, (env.default_id, eggs["item_id"]))
    assert entry.created_by is not None

    db.session.delete(db.session.get(User, entry.created_by))
    db.session.commit()

    db.session.refresh(entry)
    assert entry.created_by is None


def test_item_merge_is_blocked_when_stock_exists(env):
    keep = env.client.post(
        f"/api/household/{env.household_id}/item", json={"name": "keep"}, headers=_auth(env.alice)
    ).get_json()
    drop = env.client.post(
        f"/api/household/{env.household_id}/item", json={"name": "drop"}, headers=_auth(env.alice)
    ).get_json()

    # Give the merge target stock, then attempt to merge the other Item into it.
    env.client.put(
        f"/api/inventory/{env.default_id}/item/{keep['id']}",
        json={"quantity": 3, "unit": "pcs"},
        headers=_auth(env.alice),
    )

    res = env.client.post(
        f"/api/item/{keep['id']}",
        json={"merge_item_id": drop["id"], "name": "keep-renamed"},
        headers=_auth(env.alice),
    )
    assert res.status_code == 409
    assert res.get_json()["code"] == "inventory_merge_requires_reconciliation"

    # Rejected before any edit committed: the name is unchanged and stock intact.
    item = env.client.get(f"/api/item/{keep['id']}", headers=_auth(env.alice)).get_json()
    assert item["name"] == "keep"
    stock = env.client.get(
        f"/api/inventory/{env.default_id}/item/{keep['id']}", headers=_auth(env.alice)
    ).get_json()
    assert stock["quantity"] == 3


def test_item_merge_blocked_when_only_source_has_stock(env):
    keep = env.client.post(
        f"/api/household/{env.household_id}/item", json={"name": "keep"}, headers=_auth(env.alice)
    ).get_json()
    drop = env.client.post(
        f"/api/household/{env.household_id}/item", json={"name": "drop"}, headers=_auth(env.alice)
    ).get_json()
    # Stock on the Item being merged away must also block the merge.
    env.client.put(
        f"/api/inventory/{env.default_id}/item/{drop['id']}",
        json={"quantity": 1, "unit": "pcs"},
        headers=_auth(env.alice),
    )
    res = env.client.post(
        f"/api/item/{keep['id']}",
        json={"merge_item_id": drop["id"]},
        headers=_auth(env.alice),
    )
    assert res.status_code == 409
    assert res.get_json()["code"] == "inventory_merge_requires_reconciliation"


def test_create_stock_by_item_id(env):
    item = env.client.post(
        f"/api/household/{env.household_id}/item", json={"name": "sugar"}, headers=_auth(env.alice)
    ).get_json()
    res = env.client.put(
        f"/api/inventory/{env.default_id}/item/{item['id']}",
        json={"quantity": 2, "unit": "kg"},
        headers=_auth(env.alice),
    )
    assert res.status_code == 201, res.get_json()
    entry = res.get_json()
    assert entry["item_id"] == item["id"]
    assert entry["quantity"] == 2
    assert entry["unit"] == "kg"


def test_stock_list_pagination_covers_every_entry(env):
    names = ["aaa", "bbb", "ccc", "ddd", "eee"]
    for n in names:
        _add(env, env.alice, env.default_id, {"name": n, "quantity": 1, "unit": "pcs"})

    seen = []
    cursor = None
    pages = 0
    while True:
        url = f"/api/inventory/{env.default_id}/items?limit=2"
        if cursor:
            url += f"&cursor={cursor}"
        body = env.client.get(url, headers=_auth(env.alice)).get_json()
        seen += [e["item"]["name"] for e in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages < 10  # guard against a cursor that never terminates

    assert sorted(seen) == sorted(names)
    assert len(seen) == len(set(seen))  # no entry served twice


def test_stock_list_state_filter(env):
    _add(env, env.alice, env.default_id, {"name": "apple", "quantity": 3, "unit": "pcs"})
    _add(env, env.alice, env.default_id, {"name": "banana", "state": "LOW"})
    _add(env, env.alice, env.default_id, {"name": "carrot", "state": "OUT"})

    def names(state):
        body = env.client.get(
            f"/api/inventory/{env.default_id}/items?state={state}", headers=_auth(env.alice)
        ).get_json()
        return sorted(e["item"]["name"] for e in body["items"])

    assert names("AVAILABLE") == ["apple"]
    assert names("LOW") == ["banana"]
    assert names("OUT") == ["carrot"]


def test_stock_list_name_filter(env):
    _add(env, env.alice, env.default_id, {"name": "green apple", "quantity": 1, "unit": "pcs"})
    _add(env, env.alice, env.default_id, {"name": "banana", "quantity": 1, "unit": "pcs"})

    body = env.client.get(
        f"/api/inventory/{env.default_id}/items?name=APP", headers=_auth(env.alice)
    ).get_json()
    assert [e["item"]["name"] for e in body["items"]] == ["green apple"]


def test_invalid_cursor_is_rejected(env):
    res = env.client.get(
        f"/api/inventory/{env.default_id}/items?cursor=not-a-real-cursor", headers=_auth(env.alice)
    )
    assert res.status_code == 400
    assert res.get_json()["code"] == "invalid_cursor"


def test_ambiguous_item_name_is_rejected(env):
    # Catalog names are not unique; two same-named Items make a name target ambiguous.
    from app import db
    from app.models import Item

    db.session.add(Item(household_id=env.household_id, name="twin"))
    db.session.add(Item(household_id=env.household_id, name="twin"))
    db.session.commit()

    res = _add(env, env.alice, env.default_id, {"name": "twin", "quantity": 1, "unit": "pcs"})
    assert res.status_code == 409
    assert res.get_json()["code"] == "ambiguous_item"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "quantity": -1, "unit": "pcs"},  # negative
        {"name": "x", "quantity": 5},  # positive amount without a unit
        {"name": "x", "quantity": 1.2345, "unit": "pcs"},  # too many decimals
        {"name": "x", "quantity": 1, "unit": "pcs", "state": "LOW"},  # quantity and state
        {"name": "x", "state": "LOW", "quantity_is_estimate": True},  # estimated qualitative
        {"name": "x"},  # neither quantity nor state
        {"quantity": 1, "unit": "pcs"},  # neither item_id nor name (name added by helper? no)
    ],
)
def test_add_stock_rejects_invalid_input(env, body):
    res = _add(env, env.alice, env.default_id, body)
    assert res.status_code == 400, res.get_json()
    assert res.get_json()["code"] in {"invalid_input", "ambiguous_item"}


def test_reads_return_404_for_missing_resources(env):
    assert (
        env.client.get(
            "/api/inventory/999999/item/1", headers=_auth(env.alice)
        ).status_code
        == 404
    )
    eggs = _add(
        env, env.alice, env.default_id, {"name": "eggs", "quantity": 1, "unit": "pcs"}
    ).get_json()
    assert eggs  # sanity
    res = env.client.get(
        f"/api/inventory/{env.default_id}/item/999999", headers=_auth(env.alice)
    )
    assert res.status_code == 404


def test_mcp_create_storage_and_list(env):
    res = _rpc(
        env.client,
        env.alice,
        "create_pantry_storage",
        {"household_id": env.household_id, "name": "Freezer"},
    )
    assert res.status_code == 200
    created = res.get_json()["result"]["structuredContent"]
    assert created["name"] == "Freezer"

    res = _rpc(env.client, env.alice, "get_pantry", {"household_id": env.household_id})
    assert res.status_code == 200
    locations = res.get_json()["result"]["structuredContent"]
    assert "Freezer" in [loc["name"] for loc in locations["items"]]
    assert locations["default_inventory_id"] == env.default_id
