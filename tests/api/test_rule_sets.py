"""Saved house-rule sets over HTTP (ROADMAP.md 2.7.1, 2.7.4, DESIGN.md §5.1, §6)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from mypy_boto3_dynamodb.service_resource import Table

from ._helpers import Client


def test_saving_a_set_returns_it(alice: Client) -> None:
    response = alice.post(
        "/players/me/rule-sets",
        {"name": "Thursday nights", "house_rules": {"marks_to_win": 5}},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Thursday nights"
    assert body["house_rules"]["marks_to_win"] == 5
    assert "rule_set_id" in body


def test_an_incoherent_set_is_a_400_not_a_422(alice: Client) -> None:
    """A well-formed body the domain rejects on its own terms - the same distinction
    ``HouseRulesRequest.to_domain`` draws for game creation (DESIGN.md §5.1)."""
    response = alice.post(
        "/players/me/rule-sets",
        {
            "name": "bad",
            "house_rules": {
                "enabled_contracts": ["standard", "plunge"],
                "contract_options": {"plunge": {"minimum_doubles": 8}},
            },
        },
    )

    assert response.status_code == 400


def test_list_shows_only_my_sets(alice: Client, bob: Client) -> None:
    alice.post("/players/me/rule-sets", {"name": "alice's", "house_rules": {}})
    bob.post("/players/me/rule-sets", {"name": "bob's", "house_rules": {}})

    body = alice.get("/players/me/rule-sets").json()

    assert [rs["name"] for rs in body["rule_sets"]] == ["alice's"]


def test_get_round_trips_a_saved_set(alice: Client) -> None:
    created = alice.post(
        "/players/me/rule-sets", {"name": "mine", "house_rules": {"marks_to_win": 3}}
    ).json()

    fetched = alice.get(f"/players/me/rule-sets/{created['rule_set_id']}")

    assert fetched.status_code == 200
    assert fetched.json() == created


def test_getting_an_unknown_id_is_404(alice: Client) -> None:
    response = alice.get("/players/me/rule-sets/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RULE_SET_NOT_FOUND"


def test_one_player_cannot_read_anothers_set(alice: Client, bob: Client) -> None:
    """A foreign id is simply not found - access control needs no check of its own, since sets
    live under the owning player's own partition (DESIGN.md §5.1)."""
    created = alice.post("/players/me/rule-sets", {"name": "alice's", "house_rules": {}}).json()

    response = bob.get(f"/players/me/rule-sets/{created['rule_set_id']}")

    assert response.status_code == 404


def test_update_replaces_name_and_rules(alice: Client) -> None:
    created = alice.post(
        "/players/me/rule-sets", {"name": "old", "house_rules": {"marks_to_win": 7}}
    ).json()

    response = alice.http.put(
        f"/players/me/rule-sets/{created['rule_set_id']}",
        json={"name": "new", "house_rules": {"marks_to_win": 4}},
        headers=alice.auth,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "new"
    assert body["house_rules"]["marks_to_win"] == 4
    assert body["rule_set_id"] == created["rule_set_id"]


def test_updating_someone_elses_set_is_404(alice: Client, bob: Client) -> None:
    created = alice.post("/players/me/rule-sets", {"name": "alice's", "house_rules": {}}).json()

    response = bob.http.put(
        f"/players/me/rule-sets/{created['rule_set_id']}",
        json={"name": "hijacked", "house_rules": {}},
        headers=bob.auth,
    )

    assert response.status_code == 404


def test_deleting_removes_it_from_the_list(alice: Client) -> None:
    created = alice.post("/players/me/rule-sets", {"name": "temp", "house_rules": {}}).json()

    response = alice.http.delete(
        f"/players/me/rule-sets/{created['rule_set_id']}", headers=alice.auth
    )

    assert response.status_code == 204
    assert alice.get("/players/me/rule-sets").json()["rule_sets"] == []


def test_a_game_can_be_created_from_a_saved_set(alice: Client) -> None:
    rule_set = alice.post(
        "/players/me/rule-sets", {"name": "short game", "house_rules": {"marks_to_win": 2}}
    ).json()

    response = alice.post("/games", {"seat": 0, "rule_set_id": rule_set["rule_set_id"]})

    assert response.status_code == 201
    assert response.json()["house_rules"]["marks_to_win"] == 2


def test_editing_a_set_after_the_fact_does_not_change_games_created_from_it(alice: Client) -> None:
    """The snapshot guarantee (DESIGN.md §5.1): applying a set copies it, so a table created from
    one is immune to later edits."""
    rule_set = alice.post(
        "/players/me/rule-sets", {"name": "snapshot", "house_rules": {"marks_to_win": 7}}
    ).json()
    game_id = alice.post("/games", {"seat": 0, "rule_set_id": rule_set["rule_set_id"]}).json()[
        "game_id"
    ]

    alice.http.put(
        f"/players/me/rule-sets/{rule_set['rule_set_id']}",
        json={"name": "snapshot", "house_rules": {"marks_to_win": 3}},
        headers=alice.auth,
    )

    assert alice.view(game_id)["house_rules"]["marks_to_win"] == 7


def test_a_rule_set_reports_the_game_it_is_for(alice: Client) -> None:
    created = alice.post("/players/me/rule-sets", {"name": "x", "house_rules": {}}).json()

    assert created["kind"] == "texas42"
    assert alice.get(f"/players/me/rule-sets/{created['rule_set_id']}").json()["kind"] == "texas42"


def test_a_rule_set_for_another_game_is_refused_at_game_creation(
    alice: Client, table: Table
) -> None:
    """With one registered game this cannot happen through the API, so the item is doctored
    directly. The check still has to exist: it is the only place a stored rule set meets a table
    (DESIGN.md §11)."""
    created = alice.post("/players/me/rule-sets", {"name": "x", "house_rules": {}}).json()
    table.update_item(
        Key={"PK": f"PLAYER#{alice.player_id}", "SK": f"RULESET#{created['rule_set_id']}"},
        UpdateExpression="SET #k = :spades",
        ExpressionAttributeNames={"#k": "kind"},
        ExpressionAttributeValues={":spades": "spades"},
    )

    response = alice.post("/games", {"seat": 0, "rule_set_id": created["rule_set_id"]})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_a_foreign_rule_set_id_at_game_creation_is_404(alice: Client) -> None:
    response = alice.post("/games", {"seat": 0, "rule_set_id": "does-not-exist"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RULE_SET_NOT_FOUND"


def test_supplying_both_house_rules_and_rule_set_id_is_400(alice: Client) -> None:
    rule_set = alice.post("/players/me/rule-sets", {"name": "x", "house_rules": {}}).json()

    response = alice.post(
        "/games",
        {"seat": 0, "rule_set_id": rule_set["rule_set_id"], "house_rules": {"marks_to_win": 5}},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_rule_set_endpoints_require_authentication(client: TestClient) -> None:
    assert client.get("/players/me/rule-sets").status_code == 401
    assert (
        client.post("/players/me/rule-sets", json={"name": "x", "house_rules": {}}).status_code
        == 401
    )
