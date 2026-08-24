"""Errors raised by the repository (ROADMAP.md 1.3).

Mirrors ``tricksy.games.texas42.errors``'s style: a base class plus specific subclasses, raised
rather than returned, so callers can catch the one they care about or ``StorageError`` for all of
them.
"""

from __future__ import annotations


class StorageError(Exception):
    """Base class for every rejection the repository can produce."""


class GameNotFound(StorageError):
    """No ``STATE`` item exists for this ``game_id``."""

    def __init__(self, game_id: str) -> None:
        super().__init__(f"no game found with id {game_id!r}")
        self.game_id = game_id


class GameAlreadyExists(StorageError):
    """``create_game`` was called with a ``game_id`` that already has a ``META`` item."""

    def __init__(self, game_id: str) -> None:
        super().__init__(f"a game already exists with id {game_id!r}")
        self.game_id = game_id


class VersionConflict(StorageError):
    """``append``'s ``expected_version`` no longer matches the stored ``STATE`` item - another
    write landed first. The API layer (Phase 2) turns this into a 409."""

    def __init__(self, game_id: str, expected_version: int) -> None:
        super().__init__(
            f"game {game_id!r} is no longer at version {expected_version} - retry with the "
            "latest state"
        )
        self.game_id = game_id
        self.expected_version = expected_version


class SeatTaken(StorageError):
    """Someone is already sitting in the requested seat. A 409 (ROADMAP.md 2.2)."""

    def __init__(self, game_id: str, seat: int) -> None:
        super().__init__(f"seat {seat} in game {game_id!r} is already taken")
        self.game_id = game_id
        self.seat = seat


class GameNotJoinable(StorageError):
    """The game is past its lobby - already dealt, or finished - so no seat can be claimed."""

    def __init__(self, game_id: str, status: str) -> None:
        super().__init__(f"game {game_id!r} is {status} and cannot be joined")
        self.game_id = game_id
        self.status = status


class AlreadySeated(StorageError):
    """The player already holds a different seat in this game. One body, one seat."""

    def __init__(self, game_id: str, seat: int) -> None:
        super().__init__(f"you are already seated at seat {seat} in game {game_id!r}")
        self.game_id = game_id
        self.seat = seat


class GameAlreadyStarted(StorageError):
    """``start_game`` found the game already dealt. Raised when two attempts to deal the same
    full lobby race; the loser sees this, which is a benign outcome rather than an error to
    surface (ROADMAP.md 2.2)."""

    def __init__(self, game_id: str) -> None:
        super().__init__(f"game {game_id!r} has already been dealt")
        self.game_id = game_id


class UsernameTaken(StorageError):
    """The ``USERNAME#`` reservation is already held (ROADMAP.md 2.1)."""

    def __init__(self, username: str) -> None:
        super().__init__(f"the username {username!r} is already taken")
        self.username = username


class InvalidCredentials(StorageError):
    """Sign-in failed.

    Deliberately does not distinguish an unknown username from a wrong password, and carries
    neither in its message: telling the two apart turns the sign-in endpoint into a username
    oracle (DESIGN.md §6.1).
    """

    def __init__(self) -> None:
        super().__init__("invalid username or password")


class InvalidToken(StorageError):
    """The bearer token does not resolve to a player - never issued, or revoked since."""

    def __init__(self) -> None:
        super().__init__("the supplied token is not valid")


class RuleSetNotFound(StorageError):
    """No ``RULESET#`` item exists for this id under this player's partition (ROADMAP.md 2.7.1).

    Also raised for a rule set belonging to a different player: it lives under that other
    player's partition, so from here it is indistinguishable from never having existed, and
    access control needs no check of its own (DESIGN.md §5.1).
    """

    def __init__(self, rule_set_id: str) -> None:
        super().__init__(f"no rule set found with id {rule_set_id!r}")
        self.rule_set_id = rule_set_id


class PlayerNotFound(StorageError):
    """No ``USERNAME#`` reservation resolves to a player (ROADMAP.md 2.7.2). Raised by
    ``accounts.player_for_username`` when inviting someone by a name that doesn't exist."""

    def __init__(self, username: str) -> None:
        super().__init__(f"no player found with username {username!r}")
        self.username = username


class NotInvited(StorageError):
    """``join_seat`` rejected a claim on an ``invite_only`` table because the player holds no
    invite to it (DESIGN.md §6.2)."""

    def __init__(self, game_id: str) -> None:
        super().__init__(f"game {game_id!r} is invite-only and you have not been invited")
        self.game_id = game_id


class ContactNotFound(StorageError):
    """No contact channel with this address exists on the player's profile (ROADMAP.md 4.2)."""

    def __init__(self, address: str) -> None:
        super().__init__(f"no contact channel found with address {address!r}")
        self.address = address


class ContactAlreadyExists(StorageError):
    """``add_contact`` was called with an address already registered. Every other contact
    operation names a channel by its address, so duplicates would make "the" channel ambiguous
    (ROADMAP.md 4.2)."""

    def __init__(self, address: str) -> None:
        super().__init__(f"a contact channel already exists with address {address!r}")
        self.address = address


class TooManyContacts(StorageError):
    """``add_contact`` would push a player's channel count past the cap (ROADMAP.md 5.0) - an
    unbounded ``contacts`` list on ``PROFILE`` can otherwise grow toward DynamoDB's 400KB item
    limit."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"a player may have at most {limit} contact channels")
        self.limit = limit


class TooManyDevices(StorageError):
    """``issue_token`` would push a player's signed-in device count past the cap (ROADMAP.md
    5.0) - nothing expires a bearer token, so an unbounded mint loop can otherwise grow the
    ``PLAYER#`` partition without limit."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"a player may have at most {limit} signed-in devices")
        self.limit = limit


class InvalidVerificationToken(StorageError):
    """The verification token was never issued, has already been redeemed, or has expired
    (ROADMAP.md 4.2). The token is itself the credential (DESIGN.md §6.1), so this carries no
    detail about which of the three it was."""

    def __init__(self) -> None:
        super().__init__("the supplied verification token is not valid")


class InvalidResetToken(StorageError):
    """The password-reset token was never issued, has already been redeemed, or has expired
    (ROADMAP.md 4.3). The token is itself the credential (DESIGN.md §6.1), so this carries no
    detail about which of the three it was."""

    def __init__(self) -> None:
        super().__init__("the supplied reset token is not valid")
