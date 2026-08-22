"""Scaffolding for the API tests: one authenticated player, and a driver that plays a whole game
over HTTP. Not collected as a test module (no test_ prefix).

Everything here goes through the public API only. No test reaches past it into the repository to
set something up, so what these tests prove is true of the API and not merely of the layers under
it - which is the point of a contract test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response

PASSWORD = "correct-horse-battery"

#: Seats in turn order, matching ``Seat``.
SEATS = (0, 1, 2, 3)


@dataclass(frozen=True, slots=True)
class Client:
    """One signed-in player, holding the token every request of theirs carries."""

    http: TestClient
    player_id: str
    username: str
    token: str

    @classmethod
    def register(cls, http: TestClient, username: str, **extra: Any) -> Client:
        response = http.post("/players", json={"username": username, "password": PASSWORD, **extra})
        assert response.status_code == 201, response.text
        body = response.json()
        return cls(http=http, player_id=body["player_id"], username=username, token=body["token"])

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, path: str) -> Response:
        return self.http.get(path, headers=self.auth)

    def post(self, path: str, json: Any = None, *, idempotency_key: str | None = None) -> Response:
        headers = dict(self.auth)
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return self.http.post(path, json=json, headers=headers)

    def delete(self, path: str) -> Response:
        return self.http.delete(path, headers=self.auth)

    def patch(self, path: str, json: Any = None) -> Response:
        return self.http.patch(path, json=json, headers=self.auth)

    # -- game shortcuts, each asserting the happy path so tests read as intent ------------------

    def create_game(self, seat: int = 0, visibility: str = "public", **house_rules: Any) -> str:
        body: dict[str, Any] = {"seat": seat, "visibility": visibility}
        if house_rules:
            body["house_rules"] = house_rules
        response = self.post("/games", body)
        assert response.status_code == 201, response.text
        return str(response.json()["game_id"])

    def join(self, game_id: str, seat: int) -> Response:
        return self.post(f"/games/{game_id}/join", {"seat": seat})

    def view(self, game_id: str) -> dict[str, Any]:
        response = self.get(f"/games/{game_id}")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def invite(self, game_id: str, username: str) -> Response:
        return self.post(f"/games/{game_id}/invites", {"username": username})

    def decline_or_revoke_invite(self, game_id: str, player_id: str) -> Response:
        return self.http.delete(f"/games/{game_id}/invites/{player_id}", headers=self.auth)

    def my_invites(self) -> dict[str, Any]:
        response = self.get("/players/me/invites")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def list_invites(self, game_id: str) -> Response:
        return self.get(f"/games/{game_id}/invites")


def seated_game(players: list[Client], **house_rules: Any) -> str:
    """A dealt game with ``players`` in seats 0-3, reached the way a real one is: one creates a
    lobby and the other three join it, the last join dealing the first hand."""
    game_id = players[0].create_game(seat=0, **house_rules)
    for seat, player in zip(SEATS[1:], players[1:], strict=True):
        response = player.join(game_id, seat)
        assert response.status_code == 200, response.text
    return game_id


def whose_turn(players: list[Client], game_id: str) -> tuple[Client, dict[str, Any]]:
    """The player to act and their current view. Found by asking each player what they can see,
    rather than by reading the state directly - a client only ever knows its own view."""
    for player in players:
        view = player.view(game_id)["view"]
        if view is not None and view["legal_moves"]:
            return player, view
    raise AssertionError(f"nobody has a legal move in game {game_id}")


def submit(player: Client, game_id: str, move: dict[str, Any], **kwargs: Any) -> Response:
    """Submit one of the moves ``project`` said was legal, posted back **verbatim**.

    There is nothing to translate: every move goes to one endpoint and the ``kind`` tags on
    ``MoveRequest`` are the ones ``project`` emits, so the projected entry *is* the request body.
    This helper used to carry a kind-to-path table; that it no longer needs one is the point of
    collapsing the three move endpoints into ``POST /games/{id}/moves`` (DESIGN.md §6, §11).
    """
    return player.post(f"/games/{game_id}/moves", move, **kwargs)


def play_until(
    players: list[Client],
    game_id: str,
    *,
    max_moves: int = 5000,
    choose: Any = None,
    on_response: Any = None,
) -> dict[str, Any]:
    """Drive a game to ``GAME_OVER`` entirely over HTTP.

    ``choose`` picks from the legal moves offered (default: the first, which is deterministic
    given a fixed deal). ``on_response`` sees every response, so a test can assert something of
    each one - the hidden-information sweep uses it.
    """
    pick = choose or (lambda moves: moves[0])
    for _ in range(max_moves):
        player, view = whose_turn(players, game_id)
        response = submit(player, game_id, pick(view["legal_moves"]))
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        if on_response is not None:
            on_response(player, body)
        finished = body["status"] == "COMPLETE" or (
            body["view"] is not None and body["view"]["phase"] == "GAME_OVER"
        )
        if finished:
            return body
    raise AssertionError(f"game {game_id} did not finish within {max_moves} moves")
