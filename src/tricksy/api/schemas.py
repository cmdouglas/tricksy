"""Request and response bodies (ROADMAP.md 2.3).

Two conventions here are worth knowing before reading the models.

**Requests validate shape, not rules.** Pydantic's job stops at "is this JSON the right shape and
are the primitives in range". Whether a bid is legal, whether a rule set is coherent, whether a
tile is in your hand - all of that belongs to the engine, which already does it and is the only
place it should be done (invariant 4 in particular: no model here may enumerate contract names).
``HouseRulesRequest`` therefore converts to a :class:`~t42.engine.house_rules.HouseRules` and lets
``__post_init__`` and ``contracts.validate_house_rules`` decide, rather than restating any of it.

**Responses do not re-declare the projected view.** :func:`t42.engine.projection.project` is the
one gate hidden information passes through (invariant 5). A pydantic mirror of its output would
be a second definition of that shape, sitting next to the gate and free to drift away from it -
and a drifted mirror is exactly how a field leaks. So ``GameResponse.view`` is typed as an opaque
mapping and passed through untouched. The cost is that the OpenAPI schema describes the view as a
free-form object; the benefit is that there is exactly one description of what a player may see,
and it is executable.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from t42.engine.dominoes import parse as parse_domino
from t42.engine.house_rules import DEFAULT_CONTRACTS, DEFAULT_MARKS_TO_WIN, HouseRules
from t42.engine.moves import ConfirmBid, DeclareContract, Move, Pass, PlaceBid, PlayDomino
from t42.engine.state import PlayerId, Seat
from t42.engine.suits import Suit
from t42.storage.accounts import ContactChannel, Player
from t42.storage.lobby import GameSummary, Lobby
from t42.storage.rule_sets import RuleSet

from .errors import invalid_request

_Username = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")]
_Password = Annotated[str, Field(min_length=8, max_length=256)]


class _Strict(BaseModel):
    """Rejects unknown keys. A misspelled field is a client bug worth surfacing loudly, and it
    is the same reasoning that makes ``HouseRules.options_for`` reject unrecognised option keys
    (DESIGN.md §5.1)."""

    model_config = ConfigDict(extra="forbid")


class ContactChannelModel(_Strict):
    """A way to reach a player. ``kind`` is an open string rather than an enum so Phase 4 can add
    a channel without a schema change here (DESIGN.md §12)."""

    kind: str = Field(max_length=32)
    address: str = Field(max_length=256)

    def to_domain(self) -> ContactChannel:
        return ContactChannel(kind=self.kind, address=self.address)


class RegisterRequest(_Strict):
    username: _Username
    password: _Password
    contacts: list[ContactChannelModel] = Field(default_factory=list)
    device_label: str = Field(default="", max_length=64)


class SignInRequest(_Strict):
    username: _Username
    password: str = Field(max_length=256)
    device_label: str = Field(default="", max_length=64)


class TokenResponse(BaseModel):
    """The one time a token's plaintext exists outside the client (DESIGN.md §6.1)."""

    player_id: PlayerId
    username: str
    token: str


class DeviceResponse(BaseModel):
    token_hash: str
    label: str
    created_at: str
    last_used_at: str


class ContactResponse(BaseModel):
    kind: str
    address: str
    verified: bool
    notify: bool

    @classmethod
    def of(cls, contact: ContactChannel) -> ContactResponse:
        return cls(
            kind=contact.kind,
            address=contact.address,
            verified=contact.verified,
            notify=contact.notify,
        )


class ContactListResponse(BaseModel):
    contacts: list[ContactResponse]


class SetContactNotifyRequest(_Strict):
    """The ``PATCH /players/me/contacts/{address}`` body - the mute toggle (DESIGN.md §6.1)."""

    notify: bool


class VerifyContactRequest(_Strict):
    """The ``POST /contacts/verify`` body. The token is the whole credential (DESIGN.md §6.1),
    so this endpoint takes no other identifying information."""

    token: str


class PasswordResetRequest(_Strict):
    """The ``POST /password-resets`` body (DESIGN.md §6.1). Answered ``202`` unconditionally,
    whether or not the username exists - the same reason sign-in never says which half of a
    credential was wrong."""

    username: _Username


class PasswordResetConfirmRequest(_Strict):
    """The ``POST /password-resets/confirm`` body. The token is the whole credential, the same
    way ``VerifyContactRequest``'s is, so this carries no other identifying information besides
    the new password itself."""

    token: str
    new_password: _Password


class PlayerResponse(BaseModel):
    player_id: PlayerId
    username: str
    contacts: list[ContactResponse]
    created_at: str
    devices: list[DeviceResponse]

    @classmethod
    def of(cls, player: Player, devices: list[DeviceResponse]) -> PlayerResponse:
        return cls(
            player_id=player.player_id,
            username=player.username,
            contacts=[ContactResponse.of(c) for c in player.contacts],
            created_at=player.created_at,
            devices=devices,
        )


class HouseRulesRequest(_Strict):
    """The rule set for a new game (DESIGN.md §5.1).

    ``contract_options`` stays an untyped nested mapping on purpose: the core must never name a
    specific contract (invariant 4), and each contract declares its own option keys, which
    ``options_for`` validates against. Typing them here would move that knowledge into the API.
    """

    enabled_contracts: list[str] = Field(default_factory=lambda: sorted(DEFAULT_CONTRACTS))
    contract_options: dict[str, dict[str, int | bool | str]] = Field(default_factory=dict)
    doubles_are_own_suit: bool = False
    allow_declared_lead: Literal["never", "first_trick", "always"] = "never"
    marks_to_win: int = Field(default=DEFAULT_MARKS_TO_WIN, ge=1, le=99)

    def to_domain(self) -> HouseRules:
        """``HouseRules.__post_init__`` raises ``ValueError`` on a structurally bad rule set;
        that is the client's fault, so it becomes a 400 here rather than escaping as a 500."""
        try:
            return HouseRules(
                enabled_contracts=frozenset(self.enabled_contracts),
                contract_options=self.contract_options,
                doubles_are_own_suit=self.doubles_are_own_suit,
                allow_declared_lead=self.allow_declared_lead,
                marks_to_win=self.marks_to_win,
            )
        except ValueError as exc:
            raise invalid_request(str(exc)) from exc


class RuleSetRequest(_Strict):
    """A named house-rule set to save, or to replace one with (DESIGN.md §5.1)."""

    name: str = Field(min_length=1, max_length=64)
    house_rules: HouseRulesRequest = Field(default_factory=HouseRulesRequest)


class RuleSetResponse(BaseModel):
    rule_set_id: str
    name: str
    kind: str
    house_rules: dict[str, Any]
    created_at: str

    @classmethod
    def of(cls, rule_set: RuleSet) -> RuleSetResponse:
        return cls(
            rule_set_id=rule_set.rule_set_id,
            name=rule_set.name,
            kind=rule_set.kind,
            house_rules=_house_rules_json(rule_set.rules),
            created_at=rule_set.created_at,
        )


class RuleSetListResponse(BaseModel):
    rule_sets: list[RuleSetResponse]


class CreateGameRequest(_Strict):
    """Takes either an inline house-rule body or a saved ``rule_set_id``, never both -
    :func:`t42.api.app._resolve_house_rules` is what tells "not supplied" apart from "supplied as
    the default", since ``house_rules`` has a ``default_factory``."""

    seat: Seat = Seat.NORTH
    house_rules: HouseRulesRequest = Field(default_factory=HouseRulesRequest)
    rule_set_id: str | None = None
    visibility: Literal["public", "invite_only"] = "public"


class JoinGameRequest(_Strict):
    seat: Seat


class InviteRequest(_Strict):
    """Invite a player to a table by username (DESIGN.md §6.2)."""

    username: _Username


class InviteResponse(BaseModel):
    game_id: str
    player_id: PlayerId
    username: str


class InvitedPlayerResponse(BaseModel):
    player_id: PlayerId
    username: str
    created_at: str


class GameInvitesResponse(BaseModel):
    """Who is currently invited to a table (DESIGN.md §6.2) - the host-side counterpart to
    ``InviteListResponse``."""

    invites: list[InvitedPlayerResponse]


class BidBody(_Strict):
    """A numeric bid (30-42) or a mark bid naming its contract. Which combinations are legal is
    the auction's business, not this model's."""

    kind: Literal["BID"] = "BID"
    contract: str | None = None
    points: int | None = Field(default=None, ge=30, le=42)
    marks: int | None = Field(default=None, ge=1)

    def to_move(self, actor: PlayerId) -> Move:
        return PlaceBid(actor=actor, contract=self.contract, points=self.points, marks=self.marks)


class PassBody(_Strict):
    kind: Literal["PASS"] = "PASS"

    def to_move(self, actor: PlayerId) -> Move:
        return Pass(actor=actor)


class ConfirmBidBody(_Strict):
    """The partner's public answer to a pending plunge (DESIGN.md §12). No endpoint of its own,
    because it is just another move."""

    kind: Literal["CONFIRM_BID"] = "CONFIRM_BID"
    accept: bool

    def to_move(self, actor: PlayerId) -> Move:
        return ConfirmBid(actor=actor, accept=self.accept)


class DeclareContractBody(_Strict):
    kind: Literal["DECLARE_CONTRACT"] = "DECLARE_CONTRACT"
    trump: Suit | None = None

    def to_move(self, actor: PlayerId) -> Move:
        return DeclareContract(actor=actor, trump=self.trump)


class PlayDominoBody(_Strict):
    """``domino`` is ``a-b`` notation (DESIGN.md §7); ``declared_suit`` names which end is the
    suit led, when the house rules allow it (DESIGN.md §5.2)."""

    kind: Literal["PLAY_DOMINO"] = "PLAY_DOMINO"
    domino: str = Field(max_length=8)
    declared_suit: Suit | None = None

    def to_move(self, actor: PlayerId) -> Move:
        try:
            domino = parse_domino(self.domino)
        except ValueError as exc:
            raise invalid_request(str(exc)) from exc
        return PlayDomino(actor=actor, domino=domino, declared_suit=self.declared_suit)


#: Every move, discriminated on ``kind`` - the body of the one ``POST /games/{id}/moves`` endpoint
#: (DESIGN.md §6).
#:
#: One endpoint rather than one per phase, because the three it replaced were the same handler
#: three times over: :func:`t42.api.app._submit` never inspects the move, so the URL carried no
#: information the body did not already have. The ``kind`` tags are exactly the ones
#: :func:`t42.engine.projection.project` puts on each entry of ``legal_moves``, so a client can
#: post a legal move straight back without a table mapping kinds to paths - and a game with a
#: different move vocabulary is a different union behind the same route (DESIGN.md §11).
MoveRequest = Annotated[
    BidBody | PassBody | ConfirmBidBody | DeclareContractBody | PlayDominoBody,
    Field(discriminator="kind"),
]


class SeatResponse(BaseModel):
    seat: int
    player_id: PlayerId
    username: str


class GameResponse(BaseModel):
    """A game as one player sees it.

    ``view`` is ``None`` for a caller who is not seated - there is either no state yet to project
    (the game is still filling its lobby) or, per DESIGN.md §6.2, a non-seated caller simply never
    reaches :func:`t42.engine.projection.project`, which stays the one gate hidden information
    passes through even as who may *read* a lobby widens. See this module's docstring for why
    ``view`` is not modelled field by field.
    """

    game_id: str
    kind: str
    status: str
    visibility: str
    seats: list[SeatResponse]
    seat_count: int
    house_rules: dict[str, Any]
    view: dict[str, Any] | None = None

    @classmethod
    def of(cls, lobby: Lobby, view: dict[str, Any] | None) -> GameResponse:
        return cls(
            game_id=lobby.game_id,
            kind=lobby.kind,
            status=lobby.status.value,
            visibility=lobby.visibility.value,
            seats=[
                SeatResponse(seat=seat, player_id=a.player_id, username=a.username)
                for seat, a in sorted(lobby.seats.items())
            ],
            seat_count=lobby.seat_count,
            house_rules=_house_rules_json(lobby.config),
            view=view,
        )


class GameSummaryResponse(BaseModel):
    game_id: str
    kind: str
    status: str
    seat: int
    is_my_turn: bool

    @classmethod
    def of(cls, summary: GameSummary) -> GameSummaryResponse:
        return cls(
            game_id=summary.game_id,
            kind=summary.kind,
            status=summary.status.value,
            seat=summary.seat,
            is_my_turn=summary.is_my_turn,
        )


class GameListResponse(BaseModel):
    games: list[GameSummaryResponse]


class InviteListResponse(BaseModel):
    """ "My pending invites" (DESIGN.md §6). Reuses ``GameResponse`` rather than a parallel shape -
    every listed invite is exactly a lobby the caller isn't seated in yet, so ``view`` is always
    ``None`` on each entry."""

    games: list[GameResponse]


class OpenGamesResponse(BaseModel):
    """The public-tables browse (DESIGN.md §6, §4.1). Same reuse as ``InviteListResponse``: every
    row is a public ``WAITING`` lobby, so ``view`` is always ``None``."""

    games: list[GameResponse]


def _house_rules_json(rules: HouseRules) -> dict[str, Any]:
    """The rule set as plain data for a client. Not shared with
    :func:`t42.storage.codec.encode_house_rules`, for the same reason ``projection`` keeps its own
    encoders: that one is a durable wire format, this is a read-model, and letting a storage
    format leak into responses ties the two together."""
    return {
        "enabled_contracts": sorted(rules.enabled_contracts),
        "contract_options": {
            name: dict(options) for name, options in sorted(rules.contract_options.items())
        },
        "doubles_are_own_suit": rules.doubles_are_own_suit,
        "allow_declared_lead": rules.allow_declared_lead,
        "marks_to_win": rules.marks_to_win,
    }
