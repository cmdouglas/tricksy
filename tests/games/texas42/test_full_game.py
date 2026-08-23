"""Phase 0 milestone (ROADMAP.md 0.6): play complete games end to end through apply_move, one per
contract, plus a randomized multi-hand smoke test. Nothing here reaches into engine internals -
every step goes through the public new_game/apply_move/legal_moves entry points, exactly as a
future API handler would call them.
"""

from __future__ import annotations

from random import Random

import pytest

from t42.engine.dominoes import Domino
from t42.engine.game import new_game
from t42.engine.house_rules import HouseRules
from t42.engine.moves import Move
from t42.engine.state import GameState, HandState, Phase, Seat, Team

from ._helpers import PLAYERS, custom_deal, drive_to_game_over, first_option, prefer_contract

DOUBLES = tuple(Domino(n, n) for n in range(7))


def _plunge_or_splash_deal(
    doubles_holder: Seat, *, extra_doubles: int = 0
) -> dict[Seat, tuple[Domino, ...]]:
    non_doubles = [Domino(6, 5), Domino(6, 4), Domino(5, 4), Domino(4, 3)]
    hand = DOUBLES[: 4 + extra_doubles] + tuple(non_doubles[: 3 - extra_doubles])
    return custom_deal(**{doubles_holder.name.lower(): hand})


def _run_single_hand_game(
    hands: dict[Seat, tuple[Domino, ...]],
    *,
    dealer: Seat,
    contract_name: str,
    config: HouseRules | None = None,
) -> GameState:
    state = GameState(
        game_id="full-game",
        config=config or HouseRules(marks_to_win=1),
        players=dict(PLAYERS),
        phase=Phase.BIDDING,
        marks={Team.NORTH_SOUTH: 0, Team.EAST_WEST: 0},
        hand=HandState(dealer=dealer, hands=hands),
        to_act=Seat((dealer + 1) % 4),
    )
    final = drive_to_game_over(state, prefer_contract(contract_name), Random(0))
    assert final.hand is None
    assert final.phase is Phase.GAME_OVER
    return final


def test_full_game_standard() -> None:
    rng = Random(1)
    state = new_game("g-standard", PLAYERS, HouseRules(marks_to_win=1), rng=rng)
    final = drive_to_game_over(state, first_option, rng)
    assert final.phase is Phase.GAME_OVER
    assert final.hand is None
    assert sum(final.marks.values()) >= 1


@pytest.mark.parametrize("contract_name", ["nello", "nello_low", "sevens"])
def test_full_game_no_doubles_requirement_contracts(contract_name: str) -> None:
    rng = Random(2)
    config = HouseRules(
        enabled_contracts=frozenset({"standard", "nello", "nello_low", "sevens"}),
        marks_to_win=1,
    )
    state = new_game(f"g-{contract_name}", PLAYERS, config, rng=rng)
    final = drive_to_game_over(state, prefer_contract(contract_name), rng)
    assert final.phase is Phase.GAME_OVER
    assert final.hand is None
    assert sum(final.marks.values()) >= 1


def test_full_game_plunge() -> None:
    hands = _plunge_or_splash_deal(Seat.NORTH)
    final = _run_single_hand_game(hands, dealer=Seat.WEST, contract_name="plunge")
    assert sum(final.marks.values()) >= 4


def test_full_game_plunge_under_a_stricter_house_rule() -> None:
    hands = _plunge_or_splash_deal(Seat.NORTH, extra_doubles=1)  # 5 doubles, meets a raised bar
    config = HouseRules(
        marks_to_win=1,
        contract_options={"plunge": {"minimum_doubles": 5, "minimum_marks": 5}},
    )
    final = _run_single_hand_game(hands, dealer=Seat.WEST, contract_name="plunge", config=config)
    assert sum(final.marks.values()) >= 5  # the raised minimum_marks, not the contract's default 4


def test_full_game_splash() -> None:
    hands = _plunge_or_splash_deal(Seat.NORTH, extra_doubles=1)  # 5 doubles, well above the 3 min
    config = HouseRules(
        enabled_contracts=frozenset({"standard", "nello", "plunge", "sevens", "splash"}),
        marks_to_win=1,
    )
    final = _run_single_hand_game(hands, dealer=Seat.WEST, contract_name="splash", config=config)
    assert sum(final.marks.values()) >= 2


def test_full_random_game_reaches_game_over_across_several_hands() -> None:
    rng = Random(99)
    state = new_game("g-random", PLAYERS, HouseRules(), rng=rng)  # default marks_to_win=7

    def choose(state: GameState, options: tuple[Move, ...]) -> Move:
        return rng.choice(options)

    final = drive_to_game_over(state, choose, rng, max_moves=5000)
    assert final.phase is Phase.GAME_OVER
    assert final.hand is None
    assert any(m >= final.config.marks_to_win for m in final.marks.values())


def test_full_random_games_never_crash_across_many_seeds() -> None:
    for seed in range(25):
        rng = Random(seed)
        state = new_game(f"g-{seed}", PLAYERS, HouseRules(marks_to_win=2), rng=rng)

        def choose(state: GameState, options: tuple[Move, ...], rng: Random = rng) -> Move:
            return rng.choice(options)

        final = drive_to_game_over(state, choose, rng, max_moves=2000)
        assert final.phase is Phase.GAME_OVER
