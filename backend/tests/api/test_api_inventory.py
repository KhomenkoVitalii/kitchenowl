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


# --- P1-02: correct, consume, restock, mark-state, remove, location edits ---
# These call the shared service directly (REST/MCP routes for these operations
# are wired in later tickets); they prove the domain rules and the
# revision-conditional writes the service is responsible for.

from app.service import inventory as svc  # noqa: E402


def _actor(name):
    from app import db
    from app.models import User

    return db.session.query(User).filter_by(username=name).first()


def _eggs(env, quantity=12, unit="pcs"):
    entry = _add(env, env.alice, env.default_id, {"name": "eggs", "quantity": quantity, "unit": unit}).get_json()
    return entry


def _base(env, entry, revision):
    return {
        "household_id": env.household_id,
        "inventory_id": env.default_id,
        "item_id": entry["item_id"],
        "expected_revision": revision,
    }


def test_consume_restock_set_total_sequence(env):
    entry = _eggs(env)  # 12 pcs
    actor = _actor("alice")

    after = svc.consume(actor, {**_base(env, entry, entry["revision"]), "quantity": 2, "unit": "pcs"})
    assert after["quantity"] == 10
    assert after["revision"] != entry["revision"]

    after = svc.restock(actor, {**_base(env, entry, after["revision"]), "quantity": 10, "unit": "pcs"})
    assert after["quantity"] == 20

    after = svc.update_pantry_item(
        actor,
        {**_base(env, entry, after["revision"]), "operation": "set_total", "quantity": 8, "unit": "pcs", "quantity_is_estimate": False},
    )
    assert after["quantity"] == 8  # correction replaces, never adds


def test_stale_revision_cannot_deduct_twice(env):
    entry = _eggs(env)
    actor = _actor("alice")
    stale = entry["revision"]

    svc.consume(actor, {**_base(env, entry, stale), "quantity": 2, "unit": "pcs"})
    with pytest.raises(svc.InventoryError) as exc:
        svc.consume(actor, {**_base(env, entry, stale), "quantity": 2, "unit": "pcs"})
    assert exc.value.code == "revision_conflict"


def test_concurrent_conditional_writes_only_one_wins(env):
    from sqlalchemy import update
    from sqlalchemy.orm import Session

    from app import db
    from app.models import InventoryItems

    entry = _eggs(env)
    revision = entry["revision"]
    key = (env.default_id, entry["item_id"])

    # Two independent sessions on the same engine, both holding the same revision.
    s1, s2 = Session(bind=db.engine), Session(bind=db.engine)
    try:
        def swap(session, new_rev, qty):
            return session.execute(
                update(InventoryItems)
                .where(
                    InventoryItems.inventory_id == key[0],
                    InventoryItems.item_id == key[1],
                    InventoryItems.revision == revision,
                )
                .values(quantity=qty, revision=new_rev)
            )

        r1 = swap(s1, "winner", 10)
        s1.commit()
        r2 = swap(s2, "loser", 11)
        s2.commit()
        assert r1.rowcount == 1
        assert r2.rowcount == 0  # the stale revision no longer matches
    finally:
        s1.close()
        s2.close()


def test_arithmetic_error_rules(env):
    actor = _actor("alice")

    milk = _add(env, env.alice, env.default_id, {"name": "milk", "state": "LOW"}).get_json()
    with pytest.raises(svc.InventoryError) as exc:
        svc.consume(actor, {**_base(env, milk, milk["revision"]), "quantity": 1, "unit": "l"})
    assert exc.value.code == "quantity_unknown"

    eggs = _eggs(env)  # 12 pcs
    with pytest.raises(svc.InventoryError) as exc:
        svc.consume(actor, {**_base(env, eggs, eggs["revision"]), "quantity": 99, "unit": "pcs"})
    assert exc.value.code == "insufficient_stock"

    with pytest.raises(svc.InventoryError) as exc:
        svc.consume(actor, {**_base(env, eggs, eggs["revision"]), "quantity": 1, "unit": "kg"})
    assert exc.value.code == "unit_mismatch"


def test_mark_low_clears_count_then_restock_unknown(env):
    entry = _eggs(env)
    actor = _actor("alice")

    low = svc.update_pantry_item(actor, {**_base(env, entry, entry["revision"]), "operation": "mark_low"})
    assert low["state"] == "LOW"
    assert low["quantity"] is None

    with pytest.raises(svc.InventoryError) as exc:
        svc.restock(actor, {**_base(env, entry, low["revision"]), "quantity": 6, "unit": "pcs"})
    assert exc.value.code == "quantity_unknown"


def test_mark_out_then_restock_adopts_unit(env):
    entry = _add(env, env.alice, env.default_id, {"name": "chicken", "state": "OUT"}).get_json()
    actor = _actor("alice")
    assert entry["state"] == "OUT"

    after = svc.restock(actor, {**_base(env, entry, entry["revision"]), "quantity": 3, "unit": "pcs"})
    assert after["quantity"] == 3
    assert after["unit"] == "pcs"


def test_estimation_propagates_through_arithmetic(env):
    entry = _add(
        env, env.alice, env.default_id,
        {"name": "rice", "quantity": 2, "unit": "kg", "quantity_is_estimate": True},
    ).get_json()
    actor = _actor("alice")

    after = svc.consume(actor, {**_base(env, entry, entry["revision"]), "quantity": 1, "unit": "kg"})
    assert after["quantity_is_estimate"] is True  # estimated operand taints the result


def test_remove_yields_untracked(env):
    entry = _eggs(env)
    actor = _actor("alice")

    result = svc.remove_stock(actor, _base(env, entry, entry["revision"]))
    assert result["state"] == "UNTRACKED"
    assert result["revision"] is None

    lookup = env.client.get(
        f"/api/inventory/{env.default_id}/item/{entry['item_id']}", headers=_auth(env.alice)
    ).get_json()
    assert lookup["state"] == "UNTRACKED"


def test_update_metadata_changes_only_note(env):
    entry = _eggs(env)
    actor = _actor("alice")

    after = svc.update_pantry_item(
        actor, {**_base(env, entry, entry["revision"]), "operation": "update_metadata", "description": "top shelf"}
    )
    assert after["description"] == "top shelf"
    assert after["quantity"] == 12  # stock untouched


def test_rename_and_delete_location(env):
    actor = _actor("alice")
    created = svc.create_storage(actor, {"household_id": env.household_id, "name": "Fridge"})

    renamed = svc.rename_storage(
        actor,
        {"household_id": env.household_id, "inventory_id": created["id"], "name": "Cellar", "expected_revision": created["revision"]},
    )
    assert renamed["name"] == "Cellar"

    result = svc.delete_storage(
        actor,
        {"household_id": env.household_id, "inventory_id": created["id"], "expected_revision": renamed["revision"]},
    )
    assert result == {"id": created["id"], "deleted": True}


def test_default_location_cannot_be_deleted(env):
    actor = _actor("alice")
    default = svc.get_pantry(actor, {"household_id": env.household_id})["items"][0]
    with pytest.raises(svc.InventoryError) as exc:
        svc.delete_storage(
            actor, {"household_id": env.household_id, "inventory_id": env.default_id, "expected_revision": default["revision"]}
        )
    assert exc.value.code == "default_location"


def test_nonempty_location_cannot_be_deleted(env):
    actor = _actor("alice")
    created = svc.create_storage(actor, {"household_id": env.household_id, "name": "Fridge"})
    _add(env, env.alice, created["id"], {"name": "butter", "state": "OUT"})  # OUT still counts as tracked
    with pytest.raises(svc.InventoryError) as exc:
        svc.delete_storage(
            actor, {"household_id": env.household_id, "inventory_id": created["id"], "expected_revision": created["revision"]}
        )
    assert exc.value.code == "location_not_empty"


# --- P1-02 transport wiring: REST routes and MCP tools reach the service ---


def test_rest_consume_restock_set_total(env):
    entry = _eggs(env)  # 12 pcs
    base = f"/api/inventory/{env.default_id}/item/{entry['item_id']}"

    r = env.client.post(f"{base}/consume", json={"expected_revision": entry["revision"], "quantity": 2, "unit": "pcs"}, headers=_auth(env.alice))
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["quantity"] == 10

    r = env.client.post(f"{base}/restock", json={"expected_revision": body["revision"], "quantity": 10, "unit": "pcs"}, headers=_auth(env.alice))
    body = r.get_json()
    assert body["quantity"] == 20

    r = env.client.patch(base, json={"operation": "set_total", "expected_revision": body["revision"], "quantity": 8, "unit": "pcs", "quantity_is_estimate": False}, headers=_auth(env.alice))
    assert r.status_code == 200
    assert r.get_json()["quantity"] == 8


def test_rest_mark_and_metadata(env):
    entry = _eggs(env)
    base = f"/api/inventory/{env.default_id}/item/{entry['item_id']}"

    r = env.client.patch(base, json={"operation": "mark_low", "expected_revision": entry["revision"]}, headers=_auth(env.alice))
    low = r.get_json()
    assert low["state"] == "LOW" and low["quantity"] is None

    r = env.client.patch(base, json={"operation": "update_metadata", "expected_revision": low["revision"], "description": "back of shelf"}, headers=_auth(env.alice))
    assert r.get_json()["description"] == "back of shelf"


def test_rest_remove_item(env):
    entry = _eggs(env)
    base = f"/api/inventory/{env.default_id}/item/{entry['item_id']}"
    r = env.client.delete(base, json={"expected_revision": entry["revision"]}, headers=_auth(env.alice))
    assert r.status_code == 200
    assert r.get_json()["state"] == "UNTRACKED"


def test_rest_revision_conflict(env):
    entry = _eggs(env)
    base = f"/api/inventory/{env.default_id}/item/{entry['item_id']}"
    env.client.post(f"{base}/consume", json={"expected_revision": entry["revision"], "quantity": 1, "unit": "pcs"}, headers=_auth(env.alice))
    r = env.client.post(f"{base}/consume", json={"expected_revision": entry["revision"], "quantity": 1, "unit": "pcs"}, headers=_auth(env.alice))
    assert r.status_code == 409
    assert r.get_json()["code"] == "revision_conflict"


def test_rest_update_validation_rejects_mixed_fields(env):
    entry = _eggs(env)
    base = f"/api/inventory/{env.default_id}/item/{entry['item_id']}"
    r = env.client.patch(base, json={"operation": "mark_low", "expected_revision": entry["revision"], "quantity": 5}, headers=_auth(env.alice))
    assert r.status_code == 400
    assert r.get_json()["code"] == "invalid_input"


def test_rest_rename_and_delete_location(env):
    created = env.client.post(f"/api/household/{env.household_id}/inventory", json={"name": "Fridge"}, headers=_auth(env.alice)).get_json()
    r = env.client.post(f"/api/inventory/{created['id']}", json={"name": "Cellar", "expected_revision": created["revision"]}, headers=_auth(env.alice))
    assert r.status_code == 200
    renamed = r.get_json()
    assert renamed["name"] == "Cellar"

    r = env.client.delete(f"/api/inventory/{created['id']}", json={"expected_revision": renamed["revision"]}, headers=_auth(env.alice))
    assert r.status_code == 200
    assert r.get_json() == {"id": created["id"], "deleted": True}


def test_rest_cannot_delete_default_location(env):
    locations = env.client.get(f"/api/household/{env.household_id}/inventory", headers=_auth(env.alice)).get_json()
    default = locations["items"][0]
    r = env.client.delete(f"/api/inventory/{default['id']}", json={"expected_revision": default["revision"]}, headers=_auth(env.alice))
    assert r.status_code == 409
    assert r.get_json()["code"] == "default_location"


def test_mcp_consume_and_set_total(env):
    entry = _eggs(env)
    r = _rpc(env.client, env.alice, "consume_pantry_item", {"household_id": env.household_id, "inventory_id": env.default_id, "item_id": entry["item_id"], "expected_revision": entry["revision"], "quantity": 5, "unit": "pcs"})
    assert r.status_code == 200 and "error" not in r.get_json()
    after = r.get_json()["result"]["structuredContent"]
    assert after["quantity"] == 7

    r = _rpc(env.client, env.alice, "update_pantry_item", {"household_id": env.household_id, "inventory_id": env.default_id, "item_id": entry["item_id"], "expected_revision": after["revision"], "operation": "set_total", "quantity": 3, "unit": "pcs", "quantity_is_estimate": False})
    assert r.get_json()["result"]["structuredContent"]["quantity"] == 3


def test_mcp_remove_returns_untracked(env):
    entry = _eggs(env)
    r = _rpc(env.client, env.alice, "remove_pantry_item", {"household_id": env.household_id, "inventory_id": env.default_id, "item_id": entry["item_id"], "expected_revision": entry["revision"]})
    assert r.status_code == 200
    assert r.get_json()["result"]["structuredContent"]["state"] == "UNTRACKED"


def test_mcp_revision_conflict_is_tool_error(env):
    entry = _eggs(env)
    args = {"household_id": env.household_id, "inventory_id": env.default_id, "item_id": entry["item_id"], "expected_revision": entry["revision"], "quantity": 1, "unit": "pcs"}
    _rpc(env.client, env.alice, "consume_pantry_item", args)
    r = _rpc(env.client, env.alice, "consume_pantry_item", args)  # stale revision
    body = r.get_json()
    assert "error" not in body
    assert body["result"]["isError"] is True
    assert body["result"]["structuredContent"]["code"] == "revision_conflict"


def test_mcp_tools_list_includes_p102_tools(env):
    r = env.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, headers=_auth(env.alice))
    names = {t["name"] for t in r.get_json()["result"]["tools"]}
    assert {"consume_pantry_item", "restock_pantry_item", "update_pantry_item", "remove_pantry_item", "update_pantry_storage", "remove_pantry_storage"} <= names


# --- P1-02 verification: paths not yet exercised (facts, not assumptions) ---


def test_mark_available_sets_unknown_present(env):
    entry = _eggs(env)
    actor = _actor("alice")
    after = svc.update_pantry_item(actor, {**_base(env, entry, entry["revision"]), "operation": "mark_available"})
    assert after["state"] == "AVAILABLE"
    assert after["quantity"] is None
    assert after["quantity_is_estimate"] is False


def test_update_metadata_can_clear_note_to_null(env):
    entry = _eggs(env)
    actor = _actor("alice")
    with_note = svc.update_pantry_item(actor, {**_base(env, entry, entry["revision"]), "operation": "update_metadata", "description": "note"})
    assert with_note["description"] == "note"
    cleared = svc.update_pantry_item(actor, {**_base(env, entry, with_note["revision"]), "operation": "update_metadata", "description": None})
    assert cleared["description"] is None
    assert cleared["quantity"] == 12  # stock untouched


def test_set_total_zero_becomes_out(env):
    entry = _eggs(env)
    actor = _actor("alice")
    after = svc.update_pantry_item(actor, {**_base(env, entry, entry["revision"]), "operation": "set_total", "quantity": 0, "quantity_is_estimate": False})
    assert after["state"] == "OUT"
    assert after["quantity"] == 0


def test_restock_beyond_maximum_is_rejected(env):
    entry = _add(env, env.alice, env.default_id, {"name": "grain", "quantity": 999999999, "unit": "kg"}).get_json()
    actor = _actor("alice")
    with pytest.raises(svc.InventoryError) as exc:
        svc.restock(actor, {**_base(env, entry, entry["revision"]), "quantity": 999, "unit": "kg"})
    assert exc.value.code == "quantity_out_of_range"


def test_rest_location_not_empty_is_rejected(env):
    created = env.client.post(f"/api/household/{env.household_id}/inventory", json={"name": "Fridge"}, headers=_auth(env.alice)).get_json()
    _add(env, env.alice, created["id"], {"name": "cheese", "quantity": 1, "unit": "pcs"})
    r = env.client.delete(f"/api/inventory/{created['id']}", json={"expected_revision": created["revision"]}, headers=_auth(env.alice))
    assert r.status_code == 409
    assert r.get_json()["code"] == "location_not_empty"


def test_mcp_invalid_input_is_tool_error(env):
    entry = _eggs(env)
    # mark_low must not carry a quantity; the service rejects it via marshmallow.
    r = _rpc(env.client, env.alice, "update_pantry_item", {"household_id": env.household_id, "inventory_id": env.default_id, "item_id": entry["item_id"], "expected_revision": entry["revision"], "operation": "mark_low", "quantity": 5})
    body = r.get_json()
    assert "error" not in body
    assert body["result"]["isError"] is True
    assert body["result"]["structuredContent"]["code"] == "invalid_input"
