"""The HTTP surface (ROADMAP.md 2.4, DESIGN.md §6).

Every handler here is thin by design: it authenticates, converts a request body into an engine
``Move``, and hands off. All the deciding happens below it - the engine says whether a move is
legal, the repository says whether the write won its race, and ``project`` says what the caller
may see. Nothing in this module reimplements any of that, and no handler reaches into
``GameState`` to build a response.

``_submit`` is the whole of the move endpoint, and is worth reading first.
"""

from __future__ import annotations

from random import Random
from typing import Any

from fastapi import FastAPI, status
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.games.texas42 import GAME_KIND
from tricksy.games.texas42.errors import RulesError
from tricksy.games.texas42.game import apply_move
from tricksy.games.texas42.house_rules import HouseRules
from tricksy.games.texas42.moves import Move
from tricksy.games.texas42.projection import project
from tricksy.games.texas42.state import GameId, PlayerId
from tricksy.notifications import render_password_reset, render_verify_contact
from tricksy.storage.accounts import (
    ContactChannel,
    Player,
    add_contact,
    authenticate,
    begin_password_reset,
    begin_verification,
    complete_password_reset,
    complete_verification,
    create_player,
    get_player,
    hash_token,
    issue_token,
    list_tokens,
    player_for_username,
    remove_contact,
    revoke_token,
    set_contact_notify,
)
from tricksy.storage.errors import (
    AlreadySeated,
    GameAlreadyExists,
    GameNotFound,
    GameNotJoinable,
    PlayerNotFound,
    VersionConflict,
)
from tricksy.storage.events import events_for_move
from tricksy.storage.invites import (
    find_invite,
    invite_player,
    list_invites_for_game,
    list_invites_for_player,
    revoke_invite,
)
from tricksy.storage.lobby import (
    Lobby,
    Visibility,
    create_pending_game,
    get_lobby,
    join_seat,
    list_games_for_player,
    list_open_games,
    new_game_code,
)
from tricksy.storage.repository import GameStatus, append, find_request, get_state
from tricksy.storage.rule_sets import (
    create_rule_set,
    delete_rule_set,
    get_rule_set,
    list_rule_sets,
    update_rule_set,
)

from .deps import BearerToken, CurrentPlayer, EmailSenderDep, IdempotencyKey, TableDep
from .errors import install_error_handlers, invalid_request, not_a_player, not_started
from .schemas import (
    ContactChannelModel,
    ContactListResponse,
    ContactResponse,
    CreateGameRequest,
    DeviceResponse,
    GameInvitesResponse,
    GameListResponse,
    GameResponse,
    GameSummaryResponse,
    InvitedPlayerResponse,
    InviteListResponse,
    InviteRequest,
    InviteResponse,
    JoinGameRequest,
    MoveRequest,
    OpenGamesResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    PlayerResponse,
    RegisterRequest,
    RuleSetListResponse,
    RuleSetRequest,
    RuleSetResponse,
    SetContactNotifyRequest,
    SignInRequest,
    TokenResponse,
    VerifyContactRequest,
)

app = FastAPI(
    title="Tricksy",
    summary="Server-authoritative asynchronous trick-taking games. Texas 42 (DESIGN.md §6).",
    version="0.1.0",
)
install_error_handlers(app)


#: How many times to retry a game-code collision before giving up. Collisions are ~1 in 730
#: million, so any number above one is really guarding against a pathological RNG rather than
#: expected contention.
_CODE_ATTEMPTS = 5

#: DESIGN.md §6 lists no query parameters for the open-games browse, so this is a fixed cap
#: rather than a client-tunable one.
_OPEN_GAMES_LIMIT = 50


def _contact(player: Player, address: str) -> ContactChannel:
    """The one channel a contact-mutating handler just touched, picked back out of the ``Player``
    those functions return, so the response can echo it without a second read."""
    return next(c for c in player.contacts if c.address == address)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# --------------------------------------------------------------------------- players & sessions


@app.post("/players", status_code=status.HTTP_201_CREATED)
def register(table: TableDep, body: RegisterRequest) -> TokenResponse:
    """Creates an account and signs in the device that created it, so registering takes one
    round trip rather than two."""
    player = create_player(
        table,
        body.username,
        body.password,
        [contact.to_domain() for contact in body.contacts],
    )
    token = issue_token(table, player.player_id, body.device_label)
    return TokenResponse(player_id=player.player_id, username=player.username, token=token)


@app.post("/sessions", status_code=status.HTTP_201_CREATED)
def sign_in(table: TableDep, body: SignInRequest) -> TokenResponse:
    """Signs in one device. Each call mints a new token, so signing in on a phone leaves any
    other device signed in (DESIGN.md §6.1)."""
    player_id = authenticate(table, body.username, body.password)
    token = issue_token(table, player_id, body.device_label)
    player = get_player(table, player_id)
    return TokenResponse(player_id=player_id, username=player.username, token=token)


@app.delete("/sessions/current", status_code=status.HTTP_204_NO_CONTENT)
def sign_out(table: TableDep, player_id: CurrentPlayer, token: BearerToken) -> None:
    """Revokes the token this request arrived with, and only that one, so signing out on one
    device leaves the others alone."""
    revoke_token(table, player_id, hash_token(token))


@app.get("/players/me")
def me(table: TableDep, player_id: CurrentPlayer) -> PlayerResponse:
    player = get_player(table, player_id)
    devices = [
        DeviceResponse(
            token_hash=d.token_hash,
            label=d.label,
            created_at=d.created_at,
            last_used_at=d.last_used_at,
        )
        for d in list_tokens(table, player_id)
    ]
    return PlayerResponse.of(player, devices)


@app.post("/players/me/contacts", status_code=status.HTTP_201_CREATED)
def add_contact_channel(
    table: TableDep, player_id: CurrentPlayer, body: ContactChannelModel
) -> ContactResponse:
    player = add_contact(table, player_id, body.kind, body.address)
    return ContactResponse.of(_contact(player, body.address))


@app.get("/players/me/contacts")
def list_contact_channels(table: TableDep, player_id: CurrentPlayer) -> ContactListResponse:
    player = get_player(table, player_id)
    return ContactListResponse(contacts=[ContactResponse.of(c) for c in player.contacts])


@app.patch("/players/me/contacts/{address}")
def mute_contact_channel(
    table: TableDep, player_id: CurrentPlayer, address: str, body: SetContactNotifyRequest
) -> ContactResponse:
    player = set_contact_notify(table, player_id, address, body.notify)
    return ContactResponse.of(_contact(player, address))


@app.delete("/players/me/contacts/{address}", status_code=status.HTTP_204_NO_CONTENT)
def remove_contact_channel(table: TableDep, player_id: CurrentPlayer, address: str) -> None:
    remove_contact(table, player_id, address)


@app.post("/players/me/contacts/{address}/verification", status_code=status.HTTP_202_ACCEPTED)
def begin_contact_verification(
    table: TableDep, player_id: CurrentPlayer, address: str, sender: EmailSenderDep
) -> None:
    """Mails a single-use token to ``address`` (DESIGN.md §6.1). This is the one place the API
    layer sends an email itself, rather than through the Streams-driven notifier (ROADMAP.md
    4.5): verification has to happen synchronously, since the token it mails is the only proof
    the player is told to redeem."""
    token = begin_verification(table, player_id, address)
    subject, body = render_verify_contact({"address": address, "token": token})
    sender.send(address, subject, body)


@app.post("/contacts/verify", status_code=status.HTTP_204_NO_CONTENT)
def verify_contact(table: TableDep, body: VerifyContactRequest) -> None:
    """Deliberately takes no bearer token: the token in the body is itself the credential
    (DESIGN.md §6.1), and it arrives in an email the player may open on a device that has never
    signed in."""
    complete_verification(table, body.token)


@app.post("/password-resets", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    table: TableDep, sender: EmailSenderDep, body: PasswordResetRequest
) -> None:
    """Always answers ``202``, whether or not ``username`` exists or has a verified channel
    (DESIGN.md §6.1) - the same refusal to distinguish cases that sign-in already makes. Mails
    the first **verified** email contact, since an unverified address may belong to someone
    other than the account owner - and only one, so a mid-flight send failure can never leave
    the caller with a raised exception after a token has already reached some, but not all, of a
    multi-contact player's addresses.

    This is not fully timing-safe: unlike ``authenticate``'s constant-time dummy hash, a request
    for an unknown username returns after one read while a real one does more work (a further
    read, minting a token, and a synchronous email send), so response latency alone is a
    residual, low-severity username-existence signal. Closing it fully would mean queuing the
    send instead of making it inline, which is more machinery than this endpoint's risk profile
    currently justifies.
    """
    try:
        player_id = player_for_username(table, body.username)
    except PlayerNotFound:
        return
    player = get_player(table, player_id)
    verified = next((c for c in player.contacts if c.kind == "email" and c.verified), None)
    if verified is None:
        return
    token = begin_password_reset(table, player_id)
    subject, body_text = render_password_reset({"token": token})
    sender.send(verified.address, subject, body_text)


@app.post("/password-resets/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(table: TableDep, body: PasswordResetConfirmRequest) -> None:
    """Deliberately takes no bearer token, the same reason ``verify_contact`` doesn't: the token in
    the body is itself the credential. Setting the new password and revoking every device both
    happen inside :func:`~tricksy.storage.accounts.complete_password_reset` (DESIGN.md §6.1)."""
    complete_password_reset(table, body.token, body.new_password)


@app.get("/players/me/games")
def my_games(table: TableDep, player_id: CurrentPlayer) -> GameListResponse:
    return GameListResponse(
        games=[GameSummaryResponse.of(s) for s in list_games_for_player(table, player_id)]
    )


# -------------------------------------------------------------------------------------- rule sets


@app.post("/players/me/rule-sets", status_code=status.HTTP_201_CREATED)
def save_rule_set(
    table: TableDep, player_id: CurrentPlayer, body: RuleSetRequest
) -> RuleSetResponse:
    rule_set = create_rule_set(table, player_id, body.name, body.house_rules.to_domain())
    return RuleSetResponse.of(rule_set)


@app.get("/players/me/rule-sets")
def my_rule_sets(table: TableDep, player_id: CurrentPlayer) -> RuleSetListResponse:
    return RuleSetListResponse(
        rule_sets=[RuleSetResponse.of(rs) for rs in list_rule_sets(table, player_id)]
    )


@app.get("/players/me/rule-sets/{rule_set_id}")
def read_rule_set(table: TableDep, player_id: CurrentPlayer, rule_set_id: str) -> RuleSetResponse:
    return RuleSetResponse.of(get_rule_set(table, player_id, rule_set_id))


@app.put("/players/me/rule-sets/{rule_set_id}")
def replace_rule_set(
    table: TableDep, player_id: CurrentPlayer, rule_set_id: str, body: RuleSetRequest
) -> RuleSetResponse:
    rule_set = update_rule_set(
        table, player_id, rule_set_id, body.name, body.house_rules.to_domain()
    )
    return RuleSetResponse.of(rule_set)


@app.delete("/players/me/rule-sets/{rule_set_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_rule_set(table: TableDep, player_id: CurrentPlayer, rule_set_id: str) -> None:
    delete_rule_set(table, player_id, rule_set_id)


# ------------------------------------------------------------------------------ game lifecycle


@app.post("/games", status_code=status.HTTP_201_CREATED)
def create_game(table: TableDep, player_id: CurrentPlayer, body: CreateGameRequest) -> GameResponse:
    """Opens a lobby with the caller seated. The returned ``game_id`` is the join code others
    need (DESIGN.md §4.1)."""
    config = _resolve_house_rules(table, player_id, body)
    username = get_player(table, player_id).username

    for _ in range(_CODE_ATTEMPTS):
        try:
            lobby = create_pending_game(
                table,
                new_game_code(),
                player_id,
                username,
                body.seat,
                config,
                visibility=Visibility(body.visibility),
            )
        except GameAlreadyExists:
            continue
        return _game_response(table, lobby, player_id)
    raise RuntimeError("could not allocate an unused game code")


@app.post("/games/{game_id}/join")
def join_game(
    table: TableDep, player_id: CurrentPlayer, game_id: GameId, body: JoinGameRequest
) -> GameResponse:
    """Takes a seat. The join that fills the fourth seat deals the first hand, so this is where
    a game usually becomes playable."""
    username = get_player(table, player_id).username
    lobby = join_seat(table, game_id, player_id, username, body.seat, rng=Random())
    return _game_response(table, lobby, player_id)


@app.get("/games/open")
def open_games(table: TableDep, player_id: CurrentPlayer) -> OpenGamesResponse:
    """Public tables still ``WAITING`` with a seat free, newest first (DESIGN.md §4.1, §6.2).
    Filters out the caller's own tables - free, since the ``OpenGames`` GSI already projected the
    seats map, so this needs no extra read per row.

    Registered *before* ``/games/{game_id}`` below: Starlette matches routes in registration
    order, and ``{game_id}`` would otherwise swallow the literal ``open`` path first.
    """
    lobbies = list_open_games(table, limit=_OPEN_GAMES_LIMIT)
    games = [GameResponse.of(lobby, None) for lobby in lobbies if lobby.seat_of(player_id) is None]
    return OpenGamesResponse(games=games)


@app.get("/games/{game_id}")
def read_game(table: TableDep, player_id: CurrentPlayer, game_id: GameId) -> GameResponse:
    """A caller need not be seated to read a lobby: an invitee weighing whether to join, or
    anyone with the code to a public table still filling up, may see the lobby fields (with
    ``view: null``) without taking a seat (DESIGN.md §6.2). Anyone else gets a 403.
    """
    lobby = get_lobby(table, game_id)
    if lobby.seat_of(player_id) is None:
        publicly_visible = lobby.visibility is Visibility.PUBLIC and lobby.status is (
            GameStatus.WAITING
        )
        if not publicly_visible and not find_invite(table, game_id, player_id):
            raise not_a_player(game_id)
    return _game_response(table, lobby, player_id)


# -------------------------------------------------------------------------------------- invites


@app.post("/games/{game_id}/invites", status_code=status.HTTP_201_CREATED)
def create_invite(
    table: TableDep, player_id: CurrentPlayer, game_id: GameId, body: InviteRequest
) -> InviteResponse:
    """Any seated player may invite - there is no host role (DESIGN.md §6.2)."""
    lobby = get_lobby(table, game_id)
    _require_seat(lobby, player_id)
    if lobby.status is not GameStatus.WAITING:
        raise GameNotJoinable(game_id, lobby.status.value)
    invitee_id = player_for_username(table, body.username)
    seat = lobby.seat_of(invitee_id)
    if seat is not None:
        raise AlreadySeated(game_id, seat)
    inviter_seat = lobby.seat_of(player_id)
    assert inviter_seat is not None  # _require_seat already proved this
    invite_player(
        table,
        game_id,
        invitee_id,
        body.username,
        inviter_username=lobby.seats[inviter_seat].username,
    )
    return InviteResponse(game_id=game_id, player_id=invitee_id, username=body.username)


@app.get("/games/{game_id}/invites")
def game_invites(table: TableDep, player_id: CurrentPlayer, game_id: GameId) -> GameInvitesResponse:
    """Who is currently invited to this table - the host-side counterpart to
    ``GET /players/me/invites`` (DESIGN.md §6.2). Seated players only, the same gate
    ``POST .../invites`` already applies."""
    lobby = get_lobby(table, game_id)
    _require_seat(lobby, player_id)
    invites = [
        InvitedPlayerResponse(player_id=i.player_id, username=i.username, created_at=i.created_at)
        for i in list_invites_for_game(table, game_id)
    ]
    return GameInvitesResponse(invites=invites)


@app.delete("/games/{game_id}/invites/{target_player_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invite(
    table: TableDep, player_id: CurrentPlayer, game_id: GameId, target_player_id: PlayerId
) -> None:
    """A seated player revokes somebody else's invite, or the invitee declines their own - the
    same delete either way, gated on the caller being one or the other (DESIGN.md §6.2)."""
    lobby = get_lobby(table, game_id)
    if lobby.seat_of(player_id) is None and player_id != target_player_id:
        raise not_a_player(game_id)
    revoke_invite(table, game_id, target_player_id)


@app.get("/players/me/invites")
def my_invites(table: TableDep, player_id: CurrentPlayer) -> InviteListResponse:
    """Enriches each pending invite with a lobby read and drops any game no longer ``WAITING`` -
    a bounded fan-out over a handful of rows in exchange for a list that's never stale
    (ROADMAP.md 2.7.2). ``invites.py`` itself stays a dumb row read."""
    games = []
    for pending in list_invites_for_player(table, player_id):
        try:
            lobby = get_lobby(table, pending.game_id)
        except GameNotFound:
            continue
        if lobby.status is not GameStatus.WAITING:
            continue
        games.append(GameResponse.of(lobby, None))
    return InviteListResponse(games=games)


# ---------------------------------------------------------------------------------------- moves


@app.post("/games/{game_id}/moves")
def submit_move(
    table: TableDep,
    player_id: CurrentPlayer,
    game_id: GameId,
    body: MoveRequest,
    request_id: IdempotencyKey,
) -> GameResponse:
    """Any move: a bid, a pass, a plunge answer, a declaration or a play - discriminated on
    ``kind`` (DESIGN.md §6).

    One endpoint for all of them because ``_submit`` never inspects the move, so a per-phase URL
    said nothing the body did not. The ``kind`` values match the ones ``legal_moves`` reports in the
    projected view, so a client can post back what the server just offered it.
    """
    return _submit(table, game_id, player_id, body.to_move(player_id), request_id)


# --------------------------------------------------------------------------------------- shared


def _submit(
    table: Table,
    game_id: GameId,
    player_id: PlayerId,
    move: Move,
    request_id: str | None,
) -> GameResponse:
    """Apply one move and persist it. The single write path behind every move.

    Read the current state, let the engine accept or reject the move, turn the accepted move into
    events, and write them conditioned on the version we read. If somebody else wrote in between,
    ``append`` raises ``VersionConflict`` and the client gets a 409 - deliberately with no retry
    here. Contention is not a real scenario in a turn-based game: another player moving first
    means this move is out of turn anyway, and this player submitting twice is what ``request_id``
    absorbs. A retry loop would re-apply a move against a state the client never saw.

    **An idempotency key is checked twice: before, and again on failure.** ``append`` also
    recognises a duplicate ``request_id``, but on its own that is too late to help a real retry -
    by the time a client resends a move whose response it never got, the turn has usually moved
    on, and the engine rejects the move as out-of-turn long before ``append`` sees the marker. So
    the marker is read up front. That check races too, though: two retries in flight at once can
    both miss it, and the loser then fails in the engine. Hence the second look, after a
    rejection. Under an idempotency key a failure only counts if the marker *still* does not
    exist; if it appeared meanwhile, a sibling request applied this exact move and the honest
    answer is the state it produced, not an error.

    Two requests that are genuinely simultaneous can still both find nothing and one can still
    lose - the marker is written by the transaction it is racing. That is as far as this goes
    without a lock, and a client that retries once more will find the marker settled.
    """
    lobby = get_lobby(table, game_id)
    _require_seat(lobby, player_id)
    if lobby.status is GameStatus.WAITING:
        raise not_started(game_id)

    if request_id is not None and find_request(table, game_id, request_id) is not None:
        return _game_response(table, lobby, player_id)

    try:
        stored = get_state(table, game_id)
        new_state = apply_move(stored.state, move, rng=Random())
        events = events_for_move(stored.state, move, new_state)
        append(
            table,
            game_id,
            events,
            new_state,
            expected_version=stored.version,
            request_id=request_id,
        )
    except (RulesError, VersionConflict):
        if request_id is None or find_request(table, game_id, request_id) is None:
            raise
    return _game_response(table, get_lobby(table, game_id), player_id)


def _resolve_house_rules(table: Table, player_id: PlayerId, body: CreateGameRequest) -> HouseRules:
    """Either a saved set or an inline body, never both (DESIGN.md §5.1).

    ``model_fields_set`` is how "``house_rules`` was sent" is told apart from "``house_rules``
    defaulted": ``HouseRulesRequest`` has a ``default_factory``, so an absent field and an
    explicitly-sent default value are otherwise indistinguishable on the model itself.

    A saved set's ``kind`` must match the game being created. With one registered game that check
    cannot fail, which is precisely why it belongs here now rather than in a comment: this is the
    only place a stored rule set meets a table, so it is the only place the invariant can live.
    """
    if body.rule_set_id is not None:
        if "house_rules" in body.model_fields_set:
            raise invalid_request("supply either house_rules or rule_set_id, not both")
        rule_set = get_rule_set(table, player_id, body.rule_set_id)
        if rule_set.kind != GAME_KIND:
            raise invalid_request(
                f"rule set {body.rule_set_id!r} is for {rule_set.kind}, not {GAME_KIND}"
            )
        return rule_set.rules
    return body.house_rules.to_domain()


def _require_seat(lobby: Lobby, player_id: PlayerId) -> None:
    """Nobody sees a game they are not playing in. Spectators are out of scope for the MVP
    (DESIGN.md §2), so seating is the whole of the authorization model."""
    if lobby.seat_of(player_id) is None:
        raise not_a_player(lobby.game_id)


def _game_response(table: Table, lobby: Lobby, player_id: PlayerId) -> GameResponse:
    """A game as ``player_id`` may see it.

    The projected view is present only for a caller who is seated (DESIGN.md §6.2) - replacing the
    older test of whether the game had been dealt, now that a non-seated caller can reach this
    too. ``project`` is still called unconditionally on whatever state exists for a seated caller,
    never bypassed or partially reimplemented - it is the one gate hidden information passes
    through (invariant 5).
    """
    view: dict[str, Any] | None = None
    if lobby.seat_of(player_id) is not None:
        try:
            view = project(get_state(table, lobby.game_id).state, player_id)
        except GameNotFound:  # pragma: no cover - only if META and STATE disagree
            view = None
    return GameResponse.of(lobby, view)
