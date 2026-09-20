"""P2: recipe availability and missing-ingredient transfer, over REST and MCP.

Covers single-recipe breakdowns, the bulk roll-up reusing one pantry snapshot,
REST/MCP agreement, authorization/404s, and the transfer amount matrix
(OUT/UNTRACKED full, INSUFFICIENT deficit, UNCERTAIN/optional skipped,
merge-not-duplicate, unknown amount carries the recipe note) plus atomic rollback.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app import db


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _rpc(client, token, name, arguments):
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=_auth(token),
    )
    return res


def _rpc_payload(res):
    """Extract the structuredContent of a successful MCP tool call."""
    body = res.get_json()
    assert "result" in body, body
    assert not body["result"].get("isError"), body
    return body["result"]["structuredContent"]


@contextmanager
def _count_pantry_queries(client):
    """Count SQL statements that read the inventory_items table."""
    counter = {"n": 0}
    with client.application.app_context():
        engine = db.engine

    def before(conn, cursor, statement, parameters, context, executemany):
        if "inventory_items" in statement.lower():
            counter["n"] += 1

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", before)


@pytest.fixture
def env(admin_client):
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
    carol_id, carol = make_user("carol")

    res = client.post(
        "/api/household",
        json={"name": "home", "member": [alice_id]},
        headers=_auth(alice),
    )
    assert res.status_code == 200, res.get_json()
    household_id = client.get("/api/household", headers=_auth(alice)).get_json()[0]["id"]
    default_id = client.get(
        f"/api/household/{household_id}/inventory", headers=_auth(alice)
    ).get_json()["default_inventory_id"]

    # Outsider household for cross-household checks.
    res = client.post(
        "/api/household",
        json={"name": "other", "member": [carol_id]},
        headers=_auth(carol),
    )
    assert res.status_code == 200, res.get_json()
    other_household_id = next(
        h["id"]
        for h in client.get("/api/household", headers=_auth(carol)).get_json()
        if h["name"] == "other"
    )

    return SimpleNamespace(
        client=client,
        alice=alice,
        carol=carol,
        household_id=household_id,
        default_id=default_id,
        other_household_id=other_household_id,
    )


def _add_stock(env, body):
    res = env.client.post(
        f"/api/inventory/{env.default_id}/add-item-by-name",
        json=body,
        headers=_auth(env.alice),
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _recipe(env, name, items, household_id=None, token=None):
    res = env.client.post(
        f"/api/household/{household_id or env.household_id}/recipe",
        json={"name": name, "description": "", "items": items},
        headers=_auth(token or env.alice),
    )
    assert res.status_code == 200, res.get_json()
    return res.get_json()["id"]


def _ingredient(name, description, optional=False):
    return {"name": name, "description": description, "optional": optional}


def _shoppinglist(env):
    res = env.client.post(
        f"/api/household/{env.household_id}/shoppinglist",
        json={"name": "groceries"},
        headers=_auth(env.alice),
    )
    assert res.status_code == 200, res.get_json()
    return res.get_json()["id"]


def _list_items(env, shoppinglist_id):
    lists = env.client.get(
        f"/api/household/{env.household_id}/shoppinglist", headers=_auth(env.alice)
    ).get_json()
    entry = next(sl for sl in lists if sl["id"] == shoppinglist_id)
    return {i["name"]: i.get("description") for i in entry["items"]}


@pytest.fixture
def stew(env):
    """A recipe with one shortage, one uncertain, one untracked and one optional."""
    recipe_id = _recipe(
        env,
        "stew",
        [
            _ingredient("chicken", "400 g"),
            _ingredient("rice", "250 g"),
            _ingredient("onion", "1 pcs"),
            _ingredient("salt", "1 g", optional=True),
        ],
    )
    _add_stock(env, {"name": "chicken", "quantity": 200, "unit": "g"})  # INSUFFICIENT
    _add_stock(env, {"name": "rice", "state": "AVAILABLE"})  # qualitative -> UNCERTAIN
    # onion and salt are never tracked -> UNTRACKED
    return recipe_id


def _by_name(ingredients):
    return {i["name"]: i for i in ingredients}


def test_single_recipe_breakdown(env, stew):
    res = env.client.get(f"/api/recipe/{stew}/availability", headers=_auth(env.alice))
    assert res.status_code == 200, res.get_json()
    body = res.get_json()

    assert body["recipe_id"] == stew
    assert body["status"] == "MISSING_1"
    assert body["missing_count"] == 1
    assert body["uncertain_count"] == 2  # rice (uncertain) + onion (untracked)

    ing = _by_name(body["ingredients"])
    assert ing["chicken"]["status"] == "INSUFFICIENT"
    assert ing["chicken"]["required"] == 400 and ing["chicken"]["available"] == 200
    assert ing["rice"]["status"] == "UNCERTAIN"
    assert ing["onion"]["status"] == "UNTRACKED"
    assert ing["salt"]["status"] == "UNTRACKED" and ing["salt"]["optional"] is True


def test_ready_to_cook_when_all_sufficient(env):
    recipe_id = _recipe(env, "toast", [_ingredient("bread", "2 pcs")])
    _add_stock(env, {"name": "bread", "quantity": 4, "unit": "pcs"})
    body = env.client.get(
        f"/api/recipe/{recipe_id}/availability", headers=_auth(env.alice)
    ).get_json()
    assert body["status"] == "READY_TO_COOK"
    assert body["missing_count"] == 0 and body["uncertain_count"] == 0


def test_two_known_shortages_are_missing_n(env):
    recipe_id = _recipe(
        env,
        "dinner",
        [_ingredient("chicken", "400 g"), _ingredient("eggs", "2 pcs")],
    )
    _add_stock(env, {"name": "chicken", "quantity": 0})  # OUT
    _add_stock(env, {"name": "eggs", "quantity": 1, "unit": "pcs"})  # INSUFFICIENT
    body = env.client.get(
        f"/api/recipe/{recipe_id}/availability", headers=_auth(env.alice)
    ).get_json()
    assert body["status"] == "MISSING_N"
    assert body["missing_count"] == 2
    ing = _by_name(body["ingredients"])
    assert ing["chicken"]["status"] == "OUT"
    assert ing["eggs"]["status"] == "INSUFFICIENT"


def test_bulk_reuses_one_pantry_snapshot(env, stew):
    _recipe(env, "toast", [_ingredient("bread", "2 pcs")])
    _recipe(env, "omelette", [_ingredient("egg", "3 pcs")])

    with _count_pantry_queries(env.client) as counter:
        res = env.client.get(
            f"/api/household/{env.household_id}/recipe/availability",
            headers=_auth(env.alice),
        )
    assert res.status_code == 200, res.get_json()
    rollups = {r["name"]: r for r in res.get_json()}
    assert set(rollups) == {"stew", "toast", "omelette"}
    assert rollups["stew"]["status"] == "MISSING_1"
    # Three recipes, but the pantry is read exactly once.
    assert counter["n"] == 1


def test_bulk_returns_rollups_without_ingredient_detail(env, stew):
    rollups = env.client.get(
        f"/api/household/{env.household_id}/recipe/availability",
        headers=_auth(env.alice),
    ).get_json()
    assert all("ingredients" not in r for r in rollups)
    assert all(
        set(r) == {"recipe_id", "name", "status", "missing_count", "uncertain_count"}
        for r in rollups
    )


def test_rest_and_mcp_agree(env, stew):
    rest = env.client.get(
        f"/api/recipe/{stew}/availability", headers=_auth(env.alice)
    ).get_json()
    mcp = _rpc_payload(_rpc(env.client, env.alice, "check_recipe_availability", {"recipe_id": stew}))
    assert mcp["status"] == rest["status"]
    assert _by_name(mcp["ingredients"]).keys() == _by_name(rest["ingredients"]).keys()
    assert {i["name"]: i["status"] for i in mcp["ingredients"]} == {
        i["name"]: i["status"] for i in rest["ingredients"]
    }


def test_mcp_bulk_wraps_items(env, stew):
    payload = _rpc_payload(
        _rpc(env.client, env.alice, "list_recipe_availability", {"household_id": env.household_id})
    )
    assert [r["name"] for r in payload["items"]] == ["stew"]


def test_availability_unknown_recipe_is_404(env):
    res = env.client.get("/api/recipe/999999/availability", headers=_auth(env.alice))
    assert res.status_code == 404


def test_availability_cross_household_is_forbidden(env, stew):
    res = env.client.get(f"/api/recipe/{stew}/availability", headers=_auth(env.carol))
    assert res.status_code == 403


def test_transfer_amount_matrix(env, stew):
    shoppinglist_id = _shoppinglist(env)
    res = env.client.post(
        f"/api/recipe/{stew}/availability/transfer",
        json={"shoppinglist_id": shoppinglist_id},
        headers=_auth(env.alice),
    )
    assert res.status_code == 200, res.get_json()
    actions = {a["name"]: a for a in res.get_json()["actions"]}

    assert actions["chicken"]["action"] == "added"
    assert actions["chicken"]["amount"] == "200 g"  # deficit 400 - 200
    assert actions["onion"]["action"] == "added"
    assert actions["onion"]["amount"] == "1 pcs"  # full requirement
    assert actions["rice"]["action"] == "skipped_uncertain"
    assert actions["salt"]["action"] == "skipped_optional"

    on_list = _list_items(env, shoppinglist_id)
    assert on_list == {"chicken": "200 g", "onion": "1 pcs"}


def test_transfer_unknown_amount_keeps_recipe_note(env):
    recipe_id = _recipe(env, "soup", [_ingredient("parsley", "to taste")])
    shoppinglist_id = _shoppinglist(env)
    res = env.client.post(
        f"/api/recipe/{recipe_id}/availability/transfer",
        json={"shoppinglist_id": shoppinglist_id},
        headers=_auth(env.alice),
    )
    action = res.get_json()["actions"][0]
    assert action["action"] == "added"
    assert action["amount"] is None
    assert action["description"] == "to taste"
    assert _list_items(env, shoppinglist_id) == {"parsley": "to taste"}


def test_transfer_merges_not_duplicates(env, stew):
    shoppinglist_id = _shoppinglist(env)
    # Pre-add chicken by hand so the transfer must update, not duplicate.
    res = env.client.post(
        f"/api/shoppinglist/{shoppinglist_id}/item/{_chicken_item_id(env)}",
        json={"description": "existing note"},
        headers=_auth(env.alice),
    )
    assert res.status_code == 200, res.get_json()

    env.client.post(
        f"/api/recipe/{stew}/availability/transfer",
        json={"shoppinglist_id": shoppinglist_id},
        headers=_auth(env.alice),
    )
    on_list = _list_items(env, shoppinglist_id)
    # One chicken row, updated to the computed deficit (not duplicated).
    assert list(on_list).count("chicken") == 1
    assert on_list["chicken"] == "200 g"


def test_transfer_combines_comparable_amounts(env, stew):
    shoppinglist_id = _shoppinglist(env)
    # Pre-add chicken with a comparable amount that should be summed, not replaced.
    res = env.client.post(
        f"/api/shoppinglist/{shoppinglist_id}/item/{_chicken_item_id(env)}",
        json={"description": "100 g"},
        headers=_auth(env.alice),
    )
    assert res.status_code == 200, res.get_json()

    env.client.post(
        f"/api/recipe/{stew}/availability/transfer",
        json={"shoppinglist_id": shoppinglist_id},
        headers=_auth(env.alice),
    )
    # stew chicken deficit is 200 g; existing 100 g -> combined 300 g.
    assert _list_items(env, shoppinglist_id)["chicken"] == "300 g"


def _chicken_item_id(env):
    items = env.client.get(
        f"/api/household/{env.household_id}/item", headers=_auth(env.alice)
    ).get_json()
    return next(i["id"] for i in items if i["name"] == "chicken")


def test_transfer_missing_shoppinglist_id_is_400(env, stew):
    res = env.client.post(
        f"/api/recipe/{stew}/availability/transfer",
        json={},
        headers=_auth(env.alice),
    )
    assert res.status_code == 400


def test_transfer_to_other_household_list_forbidden(env, stew):
    other_list = env.client.post(
        f"/api/household/{env.other_household_id}/shoppinglist",
        json={"name": "theirs"},
        headers=_auth(env.carol),
    ).get_json()["id"]
    res = env.client.post(
        f"/api/recipe/{stew}/availability/transfer",
        json={"shoppinglist_id": other_list},
        headers=_auth(env.alice),
    )
    assert res.status_code == 403


def test_transfer_is_atomic_on_failure(env, stew, monkeypatch):
    shoppinglist_id = _shoppinglist(env)
    from app.service import recipe_shopping_transfer as transfer_service

    def boom():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(transfer_service.db.session, "commit", boom)
    with pytest.raises(RuntimeError):
        transfer_service.transfer_missing(
            _alice_user(env), stew, shoppinglist_id
        )
    monkeypatch.undo()
    # Nothing was persisted.
    assert _list_items(env, shoppinglist_id) == {}


def _alice_user(env):
    from app.models import User

    with env.client.application.app_context():
        return User.find_by_username("alice")


def test_transfer_via_mcp(env, stew):
    shoppinglist_id = _shoppinglist(env)
    payload = _rpc_payload(
        _rpc(
            env.client,
            env.alice,
            "add_missing_to_shopping_list",
            {"recipe_id": stew, "shoppinglist_id": shoppinglist_id},
        )
    )
    actions = {a["name"]: a["action"] for a in payload["actions"]}
    assert actions["chicken"] == "added"
    assert actions["rice"] == "skipped_uncertain"
    assert _list_items(env, shoppinglist_id) == {"chicken": "200 g", "onion": "1 pcs"}
