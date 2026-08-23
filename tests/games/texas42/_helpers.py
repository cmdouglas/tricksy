"""Shared scaffolding for scripting games directly, bypassing new_game's shuffle so tests control
exactly which dominoes each seat holds. Not collected as a test module (no test_ prefix)."""

from __future__ import annotations

from collections.abc import Callable
from random import Random

from t42.engine.dominoes import FULL_SET, Domino
from t42.engine.game import apply_move, legal_moves
from t42.engine.house_rules import STANDARD_CONTRACT, HouseRules
from t42.engine.moves import ConfirmBid, Move, Pass, PlaceBid
from t42.engine.state import GameState, HandState, Phase, PlayerId, Seat, Team

PLAYERS: dict[Seat, PlayerId] = {
    Seat.NORTH: "north",
    Seat.EAST: "east",
    Seat.SOUTH: "south",
    Seat.WEST: "west",
}


def deal(seed: int) -> dict[Seat, tuple[Domino, ...]]:
    """A reproducible 7-each deal, for tests where the exact tiles don't matter."""
    tiles = list(FULL_SET)
    Random(seed).shuffle(tiles)
    return {seat: tuple(tiles[i * 7 : (i + 1) * 7]) for i, seat in enumerate(Seat)}


def make_game(
    hands: dict[Seat, tuple[Domino, ...]],
    *,
    dealer: Seat = Seat.NORTH,
    config: HouseRules | None = None,
    to_act: Seat | None = None,
    marks: dict[Team, int] | None = None,
) -> GameState:
    """A game sitting in Phase.BIDDING with a pre-set deal, ready to script an auction onto."""
    hand = HandState(dealer=dealer, hands=hands)
    return GameState(
        game_id="test-game",
        config=config or HouseRules(),
        players=dict(PLAYERS),
        phase=Phase.BIDDING,
        marks=marks or {Team.NORTH_SOUTH: 0, Team.EAST_WEST: 0},
        hand=hand,
        to_act=to_act if to_act is not None else Seat((dealer + 1) % 4),
    )


def player_of(seat: Seat) -> PlayerId:
    return PLAYERS[seat]


def custom_deal(**explicit_by_seat_name: tuple[Domino, ...]) -> dict[Seat, tuple[Domino, ...]]:
    """A valid deal with some seats pinned to specific tiles and the rest filled from whatever's
    left, in FULL_SET order. Seat names are lowercase: north=(...), east=(...), etc."""
    explicit = {Seat[name.upper()]: tiles for name, tiles in explicit_by_seat_name.items()}
    used = {domino for tiles in explicit.values() for domino in tiles}
    leftover = [domino for domino in FULL_SET if domino not in used]
    hands: dict[Seat, tuple[Domino, ...]] = {}
    for seat in Seat:
        if seat in explicit:
            hands[seat] = explicit[seat]
        else:
            hands[seat] = tuple(leftover[:7])
            leftover = leftover[7:]
    return hands


def first_option(state: GameState, options: tuple[Move, ...]) -> Move:
    return options[0]


Chooser = Callable[[GameState, tuple[Move, ...]], Move]


def prefer_contract(contract_name: str) -> Chooser:
    """Steer bidding toward ``contract_name``. A numeric point bid's ``Bid.contract`` is ``None``,
    not ``"standard"`` (the engine only fills in the standard name once bidding resolves), so
    ``"standard"`` is normalized to that ``None`` target here."""
    target = None if contract_name == STANDARD_CONTRACT else contract_name

    def choose(state: GameState, options: tuple[Move, ...]) -> Move:
        if state.phase is Phase.BIDDING:
            for_target = [o for o in options if isinstance(o, PlaceBid) and o.contract == target]
            if for_target:
                return min(for_target, key=lambda o: o.marks or 0)
            confirms = [o for o in options if isinstance(o, ConfirmBid)]
            if confirms:
                return next(o for o in confirms if o.accept)
            passes = [o for o in options if isinstance(o, Pass)]
            if passes:
                return passes[0]
        return options[0]

    return choose


def drive_to_game_over(
    state: GameState,
    choose: Chooser,
    rng: Random,
    *,
    max_moves: int = 500,
    on_state: Callable[[GameState], None] | None = None,
    on_transition: Callable[[GameState, Move, GameState], None] | None = None,
) -> GameState:
    """Play ``state`` to ``GAME_OVER``. ``on_state``, if given, is called with the state produced
    by every move - the hook the codec round-trip test uses to snapshot every phase a real game
    passes through, rather than just the final one. ``on_transition``, if given, is called with
    ``(state_before, move, state_after)`` - the replay test uses this to build the event log a
    move produces, which needs the state the move was applied *to* as well as its result."""
    for _ in range(max_moves):
        if state.phase is Phase.GAME_OVER:
            return state
        seat = state.to_act
        assert seat is not None
        options = legal_moves(state, player_of(seat))
        assert options, f"{player_of(seat)} has no legal moves in {state.phase}"
        move = choose(state, options)
        state_before = state
        state = apply_move(state, move, rng=rng)
        if on_state is not None:
            on_state(state)
        if on_transition is not None:
            on_transition(state_before, move, state)
    raise AssertionError(f"game did not reach GAME_OVER within {max_moves} moves")
