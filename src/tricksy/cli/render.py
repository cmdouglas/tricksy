"""Response data to text (ROADMAP.md 3.3).

Every function here is a pure ``dict -> str``: no HTTP, no ``argparse``, and - like every module
under ``t42.cli`` - no import from ``t42.engine``, ``t42.storage`` or boto3 (DESIGN.md §7). That
last constraint is why ``_SEATS`` and ``_SUITS`` below duplicate what ``Seat``/``Suit`` already
encode as integers on the wire: a CLI that reached for the engine's enums to print "fives" would be
a client a web or chatbot front end couldn't be written the same way as, and a double-six set does
not grow an eighth suit, so the duplication risk is bounded (DESIGN.md §7).

The other governing rule is that this module never derives anything the server didn't already say.
``render_legal_moves`` does not decide what's legal - it formats ``view["legal_moves"]``, which the
server already computed, as the literal command a player would type to submit each one. That is
formatting, not rules knowledge (DESIGN.md §7).

``--json`` never reaches this module: ``main.py`` prints the raw response body and returns before
any of these functions are called (ROADMAP.md 3.3).
"""

from __future__ import annotations

from typing import Any, Final

_SEATS: Final[dict[int, str]] = {0: "north", 1: "east", 2: "south", 3: "west"}
_SUITS: Final[dict[int, str]] = {
    0: "blanks",
    1: "aces",
    2: "deuces",
    3: "treys",
    4: "fours",
    5: "fives",
    6: "sixes",
    7: "doubles",
}


def _seat_name(seat: int | None) -> str:
    return "-" if seat is None else _SEATS[seat]


def _suit_name(suit: int | None) -> str:
    return "-" if suit is None else _SUITS[suit]


def _trump_label(trump: int | None) -> str:
    """Prose spelling, e.g. ``trump: no trump``."""
    return "no trump" if trump is None else _suit_name(trump)


def _trump_value(trump: int | None) -> str:
    """The ``trump=<x>`` token spelling a rendered ``declare`` command uses (DESIGN.md §7:
    ``t42 declare <code> trump=none``) - distinct from :func:`_trump_label` because prose and a
    command token spell "no trump" differently."""
    return "none" if trump is None else _suit_name(trump)


def render_game(game: dict[str, Any]) -> str:
    """A ``GameResponse``: the lobby (seats, status, visibility, house rules), plus the full
    table - hand, trump, current trick, whose turn, legal moves as commands - when ``view`` is
    present. ``view`` is ``None`` for a lobby still filling, and for a caller who isn't seated
    (browsing an open game or an invite); render.py doesn't need to know which - it renders the
    same lobby-only block either way."""
    sections = [_render_lobby(game)]
    view = game.get("view")
    if view is not None:
        sections.append(_render_view(view))
    return "\n\n".join(sections)


def _render_lobby(game: dict[str, Any]) -> str:
    seats_by_index = {seat["seat"]: seat for seat in game["seats"]}
    view = game.get("view")
    my_seat = view["seat"] if view is not None else None

    lines = [f"Game {game['game_id']} - {game['status']} - {game['visibility']}"]
    for seat in range(4):
        entry = seats_by_index.get(seat)
        name = entry["username"] if entry is not None else "(open)"
        suffix = " (you)" if seat == my_seat else ""
        lines.append(f"  {(_seat_name(seat) + ':'):<7}{name}{suffix}")
    lines.append(f"house rules: {_render_house_rules(game['house_rules'])}")
    return "\n".join(lines)


def _render_house_rules(rules: dict[str, Any]) -> str:
    parts = [
        f"contracts={','.join(rules['enabled_contracts'])}",
        f"marks_to_win={rules['marks_to_win']}",
        f"doubles_are_own_suit={rules['doubles_are_own_suit']}",
        f"allow_declared_lead={rules['allow_declared_lead']}",
    ]
    for contract_name, options in sorted(rules.get("contract_options", {}).items()):
        for key, value in sorted(options.items()):
            parts.append(f"{contract_name}.{key}={value}")
    return ", ".join(parts)


def _render_view(view: dict[str, Any]) -> str:
    marks = view["marks"]
    to_act = view["to_act"]
    suffix = " (you)" if to_act is not None and to_act == view["seat"] else ""
    contract = view["contract"] if view["contract"] is not None else "-"

    lines = [
        f"phase: {view['phase']}",
        f"dealer: {_seat_name(view['dealer'])}",
        f"marks: NS {marks['north_south']} - EW {marks['east_west']}",
        f"declarer: {_seat_name(view['declarer'])}"
        f"  contract: {contract}"
        f"  trump: {_trump_label(view['trump'])}",
        f"to_act: {_seat_name(to_act)}{suffix}",
        "",
        f"hand: {_render_hand(view['hand'])}",
        "",
        "current trick:",
        _render_trick(view["current_trick"]),
        f"completed tricks: {len(view['completed_tricks'])}",
        "",
        "legal moves:",
        render_legal_moves(view["legal_moves"], view["game_id"]),
    ]
    return "\n".join(lines)


def _render_hand(hand: list[str] | None) -> str:
    if not hand:
        return "(no hand dealt yet)"
    return "  ".join(hand)


def _render_trick(trick: dict[str, Any] | None) -> str:
    if trick is None:
        return "  (no hand in progress)"
    plays = trick["plays"]
    if not plays:
        return "  (no plays yet)"

    lines = []
    for index, play in enumerate(plays):
        suffix = ""
        if index == 0 and trick["declared_suit"] is not None:
            suffix = f"  (led: {_suit_name(trick['declared_suit'])})"
        lines.append(f"  {_seat_name(play['seat'])}: {play['domino']}{suffix}")
    if trick["winner"] is not None:
        lines.append(f"  -> {_seat_name(trick['winner'])} wins")
    return "\n".join(lines)


def render_legal_moves(moves: list[dict[str, Any]], game_id: str) -> str:
    if not moves:
        return "  (nothing to do - not your turn)"
    return "\n".join(f"  {_render_move_command(move, game_id)}" for move in moves)


def _render_move_command(move: dict[str, Any], game_id: str) -> str:
    kind = move["kind"]

    if kind == "BID":
        if move["points"] is not None:
            return f"t42 bid {game_id} {move['points']}"
        marks = move["marks"]
        unit = "mark" if marks == 1 else "marks"
        contract_flag = f" --contract {move['contract']}" if move["contract"] is not None else ""
        return f"t42 bid {game_id} {marks}-{unit}{contract_flag}"
    if kind == "PASS":
        return f"t42 bid {game_id} pass"
    if kind == "CONFIRM_BID":
        return f"t42 bid {game_id} {'confirm' if move['accept'] else 'decline'}"
    if kind == "DECLARE_CONTRACT":
        return f"t42 declare {game_id} trump={_trump_value(move['trump'])}"
    if kind == "PLAY_DOMINO":
        declare_flag = (
            f" --declare {_suit_name(move['declared_suit'])}"
            if move["declared_suit"] is not None
            else ""
        )
        return f"t42 play {game_id} {move['domino']}{declare_flag}"
    raise ValueError(f"unknown move kind: {kind!r}")


def render_games_list(response: dict[str, Any]) -> str:
    games = response["games"]
    if not games:
        return "(no games)"
    lines = []
    for summary in games:
        marker = " *" if summary["is_my_turn"] else ""
        lines.append(
            f"{summary['game_id']}  {summary['status']}  {_seat_name(summary['seat'])}{marker}"
        )
    return "\n".join(lines)


def render_open_games(response: dict[str, Any]) -> str:
    return _render_lobby_rows(response["games"], empty="(no open games)")


def render_invite_list(response: dict[str, Any]) -> str:
    """The invitee's own pending invites (``InviteListResponse``) - every row is a ``GameResponse``
    with ``view: null``, the same shape :func:`render_open_games` renders."""
    return _render_lobby_rows(response["games"], empty="(no pending invites)")


def _render_lobby_rows(games: list[dict[str, Any]], *, empty: str) -> str:
    if not games:
        return empty
    return "\n\n".join(_render_lobby(game) for game in games)


def render_game_invites(response: dict[str, Any]) -> str:
    """Who is currently invited to a table (``GameInvitesResponse``), the host side of the pair
    :func:`render_invite_list` renders the invitee's side of."""
    invites = response["invites"]
    if not invites:
        return "(no pending invites)"
    return "\n".join(
        f"{invite['username']}  (invited {invite['created_at']})" for invite in invites
    )


def render_rule_set(rule_set: dict[str, Any]) -> str:
    lines = [
        f"{rule_set['name']} ({rule_set['rule_set_id']})",
        f"created: {rule_set['created_at']}",
        _render_house_rules(rule_set["house_rules"]),
    ]
    return "\n".join(lines)


def render_rule_set_list(response: dict[str, Any]) -> str:
    rule_sets = response["rule_sets"]
    if not rule_sets:
        return "(no saved rule sets)"
    lines = []
    for rule_set in rule_sets:
        contracts = ",".join(rule_set["house_rules"]["enabled_contracts"])
        lines.append(f"{rule_set['rule_set_id']}  {rule_set['name']}  ({contracts})")
    return "\n".join(lines)


def render_token(response: dict[str, Any]) -> str:
    """A ``TokenResponse`` from ``register``/``login`` - the token itself is deliberately not
    printed, since it is already saved to the profile file and printing it would just be another
    way for it to end up in a terminal scrollback or a script's captured output."""
    return f"signed in as {response['username']} ({response['player_id']})"


def _render_contact_line(contact: dict[str, Any]) -> str:
    verified = "verified" if contact["verified"] else "unverified"
    notify = "notifying" if contact["notify"] else "muted"
    return f"{contact['kind']}: {contact['address']} ({verified}, {notify})"


def render_profile(player: dict[str, Any]) -> str:
    lines = [
        f"{player['username']} ({player['player_id']})",
        f"created: {player['created_at']}",
    ]

    contacts = player["contacts"]
    if contacts:
        lines.append("contacts:")
        for contact in contacts:
            lines.append(f"  {_render_contact_line(contact)}")
    else:
        lines.append("contacts: (none)")

    devices = player["devices"]
    if devices:
        lines.append("devices:")
        for device in devices:
            label = device["label"] or "(unlabeled)"
            lines.append(f"  {label}  last used {device['last_used_at']}")
    else:
        lines.append("devices: (none)")

    return "\n".join(lines)


def render_contact(contact: dict[str, Any]) -> str:
    return _render_contact_line(contact)


def render_contact_list(response: dict[str, Any]) -> str:
    contacts = response["contacts"]
    if not contacts:
        return "(no contact channels)"
    return "\n".join(_render_contact_line(c) for c in contacts)
