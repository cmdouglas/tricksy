from __future__ import annotations

from t42.engine.contracts import get
from t42.engine.dominoes import Domino
from t42.engine.house_rules import HouseRules
from t42.engine.state import PlayedDomino, Seat, Trick

NELLO_LOW = get("nello_low")


def test_doubles_rank_low_in_their_number_suit_regardless_of_the_table_config() -> None:
    # Even with the game's doubles_are_own_suit turned on, nello_low still keeps doubles in their
    # number suit and ranks them lowest there - intrinsic to the contract, not HouseRules-driven.
    on_config = HouseRules(doubles_are_own_suit=True)
    trick = Trick(
        plays=(
            PlayedDomino(seat=Seat.NORTH, domino=Domino(5, 3)),
            PlayedDomino(seat=Seat.EAST, domino=Domino(5, 5)),
        )
    )
    # 5-5 stays in the fives suit here (not its own doubles suit) and ranks below 5-3.
    assert NELLO_LOW.trick_winner(trick, None, on_config) is Seat.NORTH


def test_a_non_double_still_beats_nothing_when_alone_but_loses_to_a_higher_non_double() -> None:
    trick = Trick(
        plays=(
            PlayedDomino(seat=Seat.NORTH, domino=Domino(5, 1)),
            PlayedDomino(seat=Seat.EAST, domino=Domino(5, 3)),
        )
    )
    assert NELLO_LOW.trick_winner(trick, None, HouseRules()) is Seat.EAST
