# Roadmap

Execution breakdown for the phases in [DESIGN.md](DESIGN.md) §10. Phases 0 through 5 are broken
down in full; Phases 6 and 7 are sketched and will be expanded as they come up.

Scaffolding is committed: toolchain, engine module layout, and the implemented primitives listed
under "Done" below.

---

## Phase 0: Rules engine — complete

Goal: a pure library that can run a complete 4-player game in memory, with no network and no AWS.
All six contracts (standard, nello, nello_low, sevens, plunge, splash) are implemented; a full
game per contract runs end to end through `new_game`/`apply_move`/`legal_moves` in
`tests/games/texas42/test_full_game.py`, the Phase 0 milestone demo. The rule-variant decisions this
phase depended on (0.1, below) are recorded in DESIGN.md §12.

### Done

- `dominoes.py`: tile representation, normalized ends, `a-b` notation
- `suits.py`: trump membership, led suit, follow legality, rank within a suit, doubles-as-own-suit
  variant, doubles-rank-low variant (for `nello_low`)
- `scoring.py`: count values, 35 count + 7 tricks = 42
- `config.py`: per-game `RuleConfig`
- `trick_rules.py`: shared follow-suit legality and highest-trump-or-led-suit winner, reused by
  every contract except sevens (its own pip-distance winner)
- `contracts/`: registry plus all six strategies (`standard`, `nello`, `nello_low`, `sevens`,
  `plunge`, `splash`)
- `state.py` / `events.py` / `moves.py`: data shapes, including `PendingBid`/`ConfirmBid` for the
  plunge confirmation sub-flow
- `bidding.py`: full auction state machine - numeric and mark bids, plunge confirmation (public,
  not a private channel - see DESIGN.md §12), the dealer-must-bid rule that replaces an all-pass
  redeal
- `tricks.py`: trick legality and resolution, active-seat-aware so nello's 3-handed hand closes
  tricks at 3 plays instead of 4
- `game.py`: `new_game`, `apply_move`, `legal_moves` - dealing, hand lifecycle, phase dispatch

### 0.1 Rule variants — resolved, see DESIGN.md §12

Plunge/splash doubles-and-marks minimums, nello's two doubles-ranking contracts, sevens'
tie-breaking, and the dealer-must-bid all-pass rule are all recorded there.

### Exit criteria — met

- A whole game runs end to end in memory, for every one of the six contracts
- Illegal moves raise `RulesError`, never corrupt state (state is immutable throughout; verified)
- No I/O, no AWS import anywhere under `tricksy.games.texas42`
- The rules modules carry the heaviest test investment: scoring boundaries, follow-suit edge
  cases, and a property test that a trick always has exactly one winner

### Still open (tracked, not blocking)

- `projection.py` is a Phase 1 stub - the player-specific view lands with persistence, since it's
  naturally exercised against a real game log (see Phase 1, 1.5 below).
- Each contract's bid entry bar (plunge's 4 doubles / 4 marks, splash's 3 / 2, nello's and sevens'
  1 mark) is a class attribute or module constant rather than per-game data. Phase 0.5 lifts these
  into house-rule options.

---

## Phase 0.5: House rules

Goal: a game is created with an explicit, validated house-rule set, and no contract-specific rule
value survives as a global. Everything a table might rule differently is data on one `HouseRules`
value; an incoherent rule set is rejected at creation rather than surfacing mid-auction.

This lands before Phase 1 on purpose: 1.1's codec encodes the rule config to DynamoDB and §4.1
stores it on the `META` item, so settling the shape afterwards would be a data migration.

Model and validation tiers are defined in DESIGN.md §5.1 - read that first.

### 0.5.1 Rename `RuleConfig` to `HouseRules`

`config.py` becomes `house_rules.py`. Mechanical: 114 references across 29 files, all caught by
mypy and ruff. Its own commit, so the behavioural change in 0.5.2 reviews cleanly. No data
migration - Phase 1 storage does not exist yet.

### 0.5.2 Contract-declared options

- `Contract` protocol gains `option_defaults()` and `validate_options(options, rules)`
- `HouseRules` gains `contract_options` and the `options_for(name)` merge helper
- Convert the four hardcoded sites listed under Phase 0 "Still open" to declared options, keeping
  today's values as the defaults, so an unconfigured game behaves exactly as it does now
- `_partner_declares_common.py` and `_nello_common.py` read their minimums through `options_for`
  instead of `self._minimum_*` and module constants; `plunge.py` and `splash.py` shrink to a name,
  their defaults, and `_requires_confirmation`

### 0.5.3 Validation

`contracts.validate_house_rules(rules)` covering the three tiers in DESIGN.md §5.1, retiring
today's `validate_enabled`, and **called from `game.py: new_game`** - it currently has no callers
at all, so a game can presently be created naming a contract that does not exist and only fails
mid-auction inside `legal_bids`. The plunge/splash coherence check lives in those two contracts,
not in the core.

### 0.5.4 Tests

`tests/games/texas42/test_house_rules.py`:

- Defaults reproduce current behaviour exactly
- An override changes which bids `legal_bids` offers
- Each validation tier rejects its own case with a clear message: unknown contract, options for a
  disabled contract, unknown option key, a doubles minimum of 8, a mark minimum of 8, and a splash
  bar harder than plunge's on either axis (the two contracts inverted - DESIGN.md §5.1 tier 3)
- The default bars validate clean, including the plunge-dominated-by-splash case that DESIGN.md
  §5.1 explicitly declines to reject
- `nello` + `nello_low` enabled together validates clean (DESIGN.md §12)
- The regression that motivates the phase: two games alive in one process under different plunge
  minimums, where a hand legal in one is rejected in the other - impossible while the minimum
  lives on the registry singleton

Extend `tests/games/texas42/test_full_game.py` with a full game under non-default house rules.

### 0.5.5 Declared leads

The first game-wide mechanic to go behind a house-rule flag, per DESIGN.md §5.2:
`allow_declared_lead` of `never` (default) / `first_trick` / `always`, letting a leader name which
end of their tile is the suit led.

This belongs in Phase 0.5 rather than later because it adds a field to `DominoPlayed`. Before
Phase 1 that is a dataclass edit; after it, a data migration (invariant 6).

The change is small because the led suit is computed in exactly two places, both in
`trick_rules.py`, and every contract routes through them:

- `Trick` gains `declared_suit: Suit | None`; `PlayDomino` and `DominoPlayed` gain the same field.
  It has to be on the event - the suit led is no longer recoverable from the tile, so a log
  without it cannot be replayed
- New `suit_led(trick, trump, rules)` helper replaces the `led_suit()` calls in
  `follow_suit_plays` and `highest_trump_or_led_suit_wins`. No contract module changes
- `tricks.py: play` validates the declaration (leader only, a suit the tile belongs to, neither
  end trump, and the trick index permitted by the setting) and records it on the trick
- `game.py: legal_moves` enumerates `(tile, declaration)` pairs rather than bare tiles, so a
  client can render the two ways of leading `3-2` as the distinct moves they are
- `rank_in_suit` needs no change: it already ranks a tile by its other end relative to whichever
  suit it is asked about

**Write this regression first.** `tricks.py:70-71` currently builds fresh `Trick` objects
(`Trick(plays=new_plays)`) when closing a trick, rather than carrying the existing one forward.
With a defaulted `declared_suit` that still compiles, type-checks and passes every existing test
while silently discarding the declaration on the trick that closes, which is the one place it
decides the winner. Both sites become `replace(hand.current_trick, ...)`.

Tests in `tests/games/texas42/test_declared_leads.py`: `never` reproduces today's behaviour exactly;
`first_trick` permits a declaration on trick 1 and rejects one on trick 2; `always` permits both;
a declared lead changes which tiles the other seats may legally play, and changes the trick winner
where the two readings disagree; declaring a suit the tile does not belong to is rejected; a tile
with a trump end cannot be declared out of trump; doubles offer no declaration under either
`doubles_are_own_suit` setting; a full game under `always` still reaches `GAME_OVER`.

### Exit criteria

- No contract-specific rule value survives as a class attribute or module constant
- `new_game` rejects an incoherent rule set; no invalid rule set can produce a game
- Two concurrent games under different house rules score and replay correctly
- Default `HouseRules()` behaves identically to today, proven by the existing suite passing
  unchanged apart from the rename
- A declared lead survives a trick closing, and is recorded on the event log rather than derived

---

## Phase 1: Persistence

Goal: durable games in DynamoDB, with the event log as the source of truth and a materialized
state item for fast reads. Depends on Phase 0 and Phase 0.5 exit criteria - the codec below
encodes `HouseRules`, so its shape must be settled first.

### 1.1 Item shapes and codec

`tricksy/storage/`, single table per DESIGN.md §4.1 (`META`, `EVENT#<seq>`, `STATE`, `PLAYER#`).

- Encode and decode events, `GameState` and `HouseRules` - including its nested `contract_options`
  map - to plain DynamoDB attribute maps
- Round-trip property test over generated states: `decode(encode(x)) == x`
- Keep the codec separate from the repository so it can be tested with no database

Note: engine dataclasses are the wire format's source of truth. Adding a field is a data
migration, and the codec is where the compatibility shim would live.

### 1.2 Replay

`replay(events) -> GameState`, rebuilding state from `HandDealt` forward using the same
`apply_move` path as live play, so replay and play cannot diverge.

Test: for a game played in memory, replaying its event log reproduces the final state exactly.

### 1.3 Repository writes

- `append(game_id, event, expected_version)`: `TransactWriteItems` writing `EVENT#<seq>` and the
  updated `STATE` together, conditioned on `version`, plus the `PLAYER#` turn-status items
- Stale `version` surfaces as a typed conflict error the API layer can turn into a 409
- Update `last_activity_at` on every write (DESIGN.md §9, so the abandoned-game feature needs no
  migration later)

### 1.4 Idempotency

Store the client request ID with the event and condition the write on its absence; a duplicate
submission returns the prior result rather than applying twice.

Test: submitting the same move twice yields one event and identical responses.

### 1.5 Player-specific view

`projection.py: project(state, player_id)`.

- Strip other seats' hands and any undealt tiles; include own hand, trump, current trick,
  completed tricks, marks, whose turn, and legal moves when it is the caller's turn
- Return plain JSON-able data with nothing CLI-specific (DESIGN.md §11)

Tests: for every seat, the projection contains no tile held by another seat; a leakage test that
walks the projected structure and asserts no foreign tile appears anywhere in it.

### 1.6 Integration tests

Against DynamoDB Local: create, append, read back, concurrent conflicting writes, idempotent
replay of a duplicate request, and a full scripted game persisted move by move.

### Exit criteria

- A game plays to completion through the storage layer, one process at a time
- Concurrent writes to the same game cannot interleave into a corrupt state
- Replaying any game's log reproduces its `STATE` item
- Projection leaks nothing, proven by test rather than inspection

---

## Phase 2: API

Goal: the engine reachable over HTTP. FastAPI handlers over `apply_move` plus the repository,
behind a Mangum adapter for Lambda, implementing the endpoint set in DESIGN.md §6 with
per-endpoint contract tests.

Two things DESIGN.md left open have to be settled here, and both are data-model decisions rather
than handler details, so they come first:

- **Player identity**, §12's open question, resolved there: username plus password, minting
  per-device bearer tokens. See 2.1.
- **The lobby.** §6 has `POST /games` then `POST /games/{id}/join`, but `repository.create_game`
  requires all four seats and deals immediately, so there is no representation of a game waiting
  for players. See 2.2.

Deployment is deliberately **not** in this phase. It stays in Phase 5 until the handlers exist and
the shape of what needs provisioning is settled by working code rather than guessed at.

### 2.1 Accounts and tokens

`tricksy/storage/accounts.py`, adding four item types to the existing single table (DESIGN.md §4.1) -
no GSI, no second table:

| PK | SK | Purpose |
|---|---|---|
| `PLAYER#<id>` | `PROFILE` | username, contact channels, created_at |
| `TOKEN#<sha256(token)>` | `TOKEN` | player_id, device label, created_at, last_used_at, expires_at |
| `PLAYER#<id>` | `TOKEN#<sha256>` | reverse lookup, so a player can list and revoke devices |
| `USERNAME#<lowercased>` | `PLAYER` | uniqueness reservation via conditional put |

- `create_player`, `authenticate`, `issue_token`, `player_for_token`, `revoke_token`,
  `list_tokens`. New `UsernameTaken`, `InvalidCredentials` and `InvalidToken` under the existing
  `StorageError` base.
- **Passwords use stdlib `hashlib.scrypt`, tokens use sha256.** A token is a high-entropy random
  value, so a fast hash is the right one for it and a slow one is right for a password. No new
  dependency either way. Store the salt and parameters alongside the password hash; compare with
  `hmac.compare_digest`.
- **A player has many tokens, one per device**, so signing in on a phone does not disturb a
  desktop and losing one device revokes one token. No expiry; revocation is explicit. The
  `expires_at` attribute is written anyway so adding expiry later is not a migration.
- **`PlayerId` stays opaque**, not the username, so usernames stay renameable and never get
  embedded in the event log.
- **Contacts are a list of channels**, `{"kind": "email", "address": ..., "verified": false}`,
  not a bare `email` attribute - Phase 4 then branches on `kind` and adding SMS or a chat DM is a
  new branch rather than a migration. Same argument DESIGN.md §9 makes for `last_activity_at`.

### 2.2 Lobby

`tricksy/storage/lobby.py`, plus a rework of `repository.create_game`. The lobby lives entirely in the
storage layer: `META` gains `status` and a partial seats map, and the engine is untouched -
`new_game` still takes four seats and deals.

- `create_pending_game` writes `META` with `status="WAITING"` and a one-seat map. It must call
  `contracts.validate_house_rules` itself, since `new_game` will not run until the deal and an
  invalid rule set would otherwise sit in a lobby until the fourth player joined.
- `join_seat` conditionally claims an empty seat while `status="WAITING"`. Re-joining a seat you
  already hold is a no-op success, not a conflict.
- `list_games_for_player` is one `Query` on `PK = PLAYER#<id>`. The `PLAYER#<id> / GAME#<id>`
  items gain `status` so this needs no fan-out; `append` already rewrites all four of them on
  every move, so carrying it is nearly free.
- `create_game` is **reworked, not just renamed**, into `start_game`. `META` already exists by
  then, so its `Put` with `attribute_not_exists(PK)` becomes an `Update` flipping `status` from
  `WAITING` to `ACTIVE`, conditioned on `status = "WAITING"`. That condition is what makes two
  simultaneous fourth joins deal exactly once. The `HAND_DEALT` event, `STATE` and the `PLAYER#`
  turn-status writes are unchanged.
- The game id doubles as the join code (DESIGN.md §2): six characters from an alphabet with no
  `I`, `L`, `O`, `U`, `0` or `1`. Collisions surface as the existing `GameAlreadyExists`.
- Seat labels are denormalized onto `META` at join time, so rendering a view needs no profile
  reads.

### 2.3 Schemas and error mapping

`tricksy/api/schemas.py` and `tricksy/api/errors.py`.

- The bid body is a **discriminated union** on `kind` (`BID`/`PASS`/`CONFIRM_BID`), mapping 1:1
  onto the engine's move alphabet. This is where the plunge confirmation lives; §6 lists no
  endpoint for it, and folding it into the auction endpoint beats inventing a fourth move route.
- `HouseRulesRequest` converts to `HouseRules` and lets `__post_init__` plus
  `contracts.validate_house_rules` do the real checking, rather than restating rules in pydantic.
- **Responses do not re-declare the projection.** The game response is a thin wrapper carrying
  `project()` output opaquely under `view`. A pydantic mirror of the projected shape would put a
  second definition of it next to the single gate invariant 5 requires, and the two would drift.
- Error mapping, each with a machine-readable `code` so the Phase 3 CLI can branch: 401 for a
  missing or invalid token, 403 for a caller not seated in the game, 404 `GameNotFound`, 409 for
  `UsernameTaken`/`SeatTaken`/`VersionConflict`/`OutOfTurn`, 400 for `RulesError` and an invalid
  rule set. Rules rejections are 400 rather than 422 so they stay distinguishable from FastAPI's
  own validation failures, which already own 422.

### 2.4 Routes

`tricksy/api/app.py` and `tricksy/api/deps.py`: `POST /players`, `POST /sessions`,
`DELETE /sessions/current`, `GET /players/me`, `POST /games`, `POST /games/{id}/join`,
`GET /games/{id}`, `GET /players/me/games`, `POST /games/{id}/bid`, `POST /games/{id}/contract`,
`POST /games/{id}/play`.

One shared helper sits behind the three move endpoints and is the heart of the phase: `get_state`,
build the `Move`, `apply_move`, `events_for_move`, `append` with the read version and the
`Idempotency-Key` header as `request_id`, then `project`.

- **`VersionConflict` returns 409 with no automatic retry.** Real contention cannot happen in a
  turn-based game: a second player submitting concurrently is rejected as `OutOfTurn` first, and a
  double submission by the same player is absorbed by 1.4's idempotency marker. A retry loop would
  be machinery guarding nothing.
- The client never supplies a version - the server reads the current one itself - so `version`
  stays off the wire entirely.
- `GET /games/{id}` returns the lobby shape while `WAITING`, since no `STATE` item exists yet.

### 2.5 Contract tests

`tests/api/`, over FastAPI's `TestClient` with the moto-backed `table` fixture injected through
`app.dependency_overrides`. TestClient runs in-process, so there is no HTTP mocking. The `table`
fixture and `_create_texas42_table` move up to a top-level `tests/conftest.py` so both
`tests/storage/` and `tests/api/` can use them.

- The four-case matrix from DESIGN.md §10 per mutating endpoint: valid move, invalid move, out of
  turn, stale version. The stale case is forced by monkeypatching `repository.get_state` to return
  a `StoredGame` one version behind.
- Auth: no header, malformed header, revoked token, and a valid token for a player not seated.
- Idempotency: the same `Idempotency-Key` twice yields one event and identical responses.
- **Leakage at the HTTP boundary**: drive a full game through the API and assert that no response
  any of the four players receives contains a tile held by another seat, reusing the structure
  walker from `tests/games/texas42/test_projection.py`. 1.5 proved this of `project()`; this proves it of
  what actually goes over the wire.

### 2.6 Lambda entry point and integration test

`tricksy/api/lambda_handler.py` is `Mangum(app)` and nothing else. `tests/api/test_api_integration.py`,
marked `integration`, plays a full scripted 4-player game from signup to `GAME_OVER` against the
`dynamodb_local` fixture from 1.6.

### Exit criteria

- Every endpoint in DESIGN.md §6 exists and is covered by the four-case contract matrix
- A full 4-player game runs signup to `GAME_OVER` over HTTP against real DynamoDB Local
- No response any player receives contains another seat's tiles, proven by test rather than
  inspection
- A duplicate submission with the same idempotency key is a no-op returning the prior result
- The engine remains pure: nothing under `tricksy.games.texas42` imports from `tricksy.api` (invariant 1)

---

## Phase 2.7: Tables - rule sets, invites, visibility

Goal: everything about setting a table up, finished before the CLI is written. Saved house-rule
sets, tables that are public or invite-only, invites addressed by username, and a browse of public
tables with seats free.

This lands before Phase 3 for the same reason Phase 0.5 landed before Phase 1: the CLI's command set
should be written once against the finished surface rather than grown into it, and three of these
four features add commands.

It is a fractional phase so that Phase 3 stays the CLI and nothing downstream renumbers. The
fraction is **2.7** rather than the more natural 2.5 because Phase 2 already has a subsection 2.5
(contract tests), cited by name from several test modules - "ROADMAP.md 2.5" has to keep meaning one
thing. Phase 2's last subsection is 2.6, so 2.7 is simply the next free number after it.

Semantics are settled in DESIGN.md §5.1 (saved sets), §6.2 (visibility and invites) and §4.1 (the
new item shapes and the `OpenGames` index) - read those first. Two decisions from there shape the
work below: applying a saved set **copies** it, and an invite is a **permission grant** rather than
a seat reservation.

### 2.7.1 Saved rule sets

`tricksy/storage/rule_sets.py` and one new item type, `PLAYER#<id> / RULESET#<ruleSetId>`, holding a
display name and the encoded `HouseRules`.

- `create_rule_set`, `get_rule_set`, `list_rule_sets`, `update_rule_set`, `delete_rule_set`, plus a
  `RuleSetNotFound` under the existing `StorageError` base. Reuse `codec.encode_house_rules` /
  `decode_house_rules` - the stored config is the same shape as `META.config`, deliberately, so
  there is no second encoder to keep in step.
- **Validate on save**, through `contracts.validate_house_rules`, for the same reason
  `create_pending_game` does rather than deferring to `new_game`: a set that only fails at the table
  is a trap saved weeks earlier.
- `update_rule_set` is a full replace of name and rules, conditioned on `attribute_exists(SK)`, so
  editing something already deleted is an error rather than a resurrection.
- Five endpoints under `/players/me/rule-sets` (DESIGN.md §6). Authorization needs no check of its
  own: the items live in the caller's own partition, so somebody else's id is a miss, not a leak.
- `CreateGameRequest` gains `rule_set_id`, mutually exclusive with the inline `house_rules` body.
  Detecting "supplied both" needs a pydantic `model_validator` reading `model_fields_set`, because
  `house_rules` has a `default_factory` and an absent field is otherwise indistinguishable from a
  defaulted one.

Self-contained and changes no existing behaviour, so it goes first.

### 2.7.2 Visibility and invites

- `Visibility` (`public`/`invite_only`) on `META`, carried on `Lobby`, defaulting to `public` -
  which is exactly today's behaviour, so the existing suite must pass unchanged. `get_lobby` reads
  the attribute directly with no `.get()` fallback, matching the codec's stance that a new field is
  a migration point; nothing is deployed, so there is no data to migrate.
- `tricksy/storage/invites.py`: the `GAME#/INVITE#` and `PLAYER#/INVITE#` pair written and deleted in
  one `transact_write`, plus `invite_player`, `find_invite`, `list_invites_for_player`,
  `revoke_invite`. `invite_player` is idempotent - re-inviting overwrites and returns, since clients
  retry.
- **The check goes inside `join_seat`**, after the existing held-seat/status/seat-taken checks and
  before the conditional claim, raising a new `NotInvited`. Storage stays the single authority on
  who may sit down. Document the read-then-write window in the docstring, as that module already
  does for its other races - the reasoning is in DESIGN.md §6.2 and the short version is that
  promoting the claim to a transaction would cost the error attribution the claim depends on.
- A successful claim revokes the invite, so it leaves the invitee's pending list.
- Three endpoints (DESIGN.md §6). `POST /games/{id}/invites` takes a username, which needs a new
  `accounts.player_for_username` - only `authenticate` reads the `USERNAME#` item today, and
  privately - plus a `PlayerNotFound`. Reject inviting somebody already seated, or into a game past
  `WAITING`.
- `GET /players/me/invites` enriches each row with a `get_lobby` read for seat counts and house
  rules and drops games no longer `WAITING`. A bounded N+1 over a handful of pending invites, in
  exchange for a list that is never stale; keep the enrichment in the handler and the storage
  function a dumb row read.
- **`GET /games/{id}` authorization widens** to seated / invited-or-public-waiting / everybody else
  (DESIGN.md §6.2). The mechanical change is that `_game_response` projects when the caller is
  **seated**, replacing today's test of whether the game has been dealt.

### 2.7.3 Open-games browse

- The `OpenGames` GSI goes into `tests/conftest.py`'s `_create_texas42_table`, which both the moto
  and DynamoDB-Local fixtures already build from, so the schema cannot drift between them.
- `create_pending_game` writes `GSI1PK`/`GSI1SK` only for a public game; `start_game`'s `META`
  update gains `REMOVE GSI1PK, GSI1SK`. That update is the only exit from `WAITING`, so there is no
  second removal site to remember.
- `lobby.list_open_games(table, *, limit)`: one query on the index, `ScanIndexForward=False`.
- `GET /games/open` filters out tables the caller is already seated in - free, since the index
  projects `ALL` and the seats map comes along.

### 2.7.4 Tests

Same split the repo already uses: storage against moto, API through `TestClient` only, nothing
reaching past the API into storage.

- `tests/storage/test_rule_sets.py` - CRUD; an incoherent set is rejected at save; one player cannot
  read another's.
- `tests/storage/test_invites.py` - both items written and both deleted; `join_seat` refuses an
  uninvited player on an invite-only game, consumes the invite on success, and is unaffected on a
  public one.
- `tests/storage/test_lobby.py` - `list_open_games` is newest-first, excludes invite-only games, and
  a game leaves the index when dealt.
- `tests/api/test_rule_sets.py` - the contract matrix; a game created from a saved set; **the
  snapshot guarantee**: edit the set afterwards and assert the game's rules are unchanged. A foreign
  `rule_set_id` is 404; supplying both `rule_set_id` and `house_rules` is 400.
- `tests/api/test_invites.py` - invite by username, it appears in the invitee's list, they join, it
  disappears; an uninvited join is 403; an invitee reading the game gets `view: null`; a stranger
  gets 403.
- `tests/api/test_open_games.py` - a public table appears, an invite-only one does not, a dealt one
  drops off, and the caller's own tables are filtered out.
- `tests/storage/test_lobby_integration.py`, marked `integration` - the index against real DynamoDB
  Local, **polling** rather than asserting immediately. GSI propagation is asynchronous and moto is
  strongly consistent, so this is the one guarantee moto cannot establish.

### Exit criteria

- A rule set survives save, edit and apply, and a table created from one is immune to later edits to
  it - proven by test, since this is a guarantee and not just current behaviour
- An invite-only table cannot be joined by an uninvited player, and a consumed invite disappears
- A public table appears in the browse and leaves it when dealt
- `GET /games/{id}` never projects for a caller who is not seated
- The default suite passes unchanged apart from additions: `public` is the default, so nothing that
  existed before this phase changes meaning

---

## Phase 3: CLI client

Goal: the command set in DESIGN.md §7 - every endpoint reachable, a game playable start to finish
from a terminal, and a dogfooded 4-player game as the milestone. This is the first real client, and
the first evidence that DESIGN.md §11's claim holds: that a client needs the API and the projected
view and nothing else.

Read DESIGN.md §7 first; it settles the shape and this section sequences it. Three decisions from
there govern everything below:

- **The CLI is a client, not a component.** Nothing under `tricksy.cli` imports `tricksy.games.texas42`,
  `tricksy.storage` or boto3, and it derives nothing the server already tells it - no computing whose
  turn it is, no deciding whether a move is legal. Both halves are enforced by test (3.7), because
  an unenforced layering rule is a layering rule that lasts until the first convenient import.
- **stdlib `argparse`**, and one runtime dependency (an HTTP client) in a new `cli` optional extra.
- **A locally hosted API.** `uvicorn` over DynamoDB Local, settling the deployment question this
  file carried as open until now (see the bottom of this file, 3.6, and DESIGN.md §7.3).

### 3.0 The gap the CLI surfaces: `GET /games/{game_id}/invites`

Writing the command set against the finished API turns up one hole. `DELETE
/games/{id}/invites/{playerId}` is addressed by player id, and nothing hands a caller one:
`POST /games/{id}/invites` returns it once in a response no client keeps, and
`GET /players/me/invites` is the invitee's side of the pair. So an invite can be sent but not
practically revoked, and a lobby cannot show who is pending.

- `invites.list_invites_for_game(table, game_id)` - one `Query` on `PK = GAME#<id>` with
  `begins_with(SK, "INVITE#")`. No new item shape: that item already stores `player_id` **and**
  `username` (`invites.py`), because the pair was designed for both directions.
- `GET /games/{game_id}/invites`, seated callers only, reusing `_require_seat` - the same gate
  `POST .../invites` already applies, and for the reason DESIGN.md §6.2 gives.
- `tricksy uninvite <code> <username>` then resolves a name to an id through it. The alternative -
  making the user paste a player id out of earlier terminal output - is what a CLI exists to avoid.

First, for the same reason Phase 2.7 preceded Phase 3 at all: the command set gets written once
against a finished surface rather than grown into one.

### 3.1 Skeleton, profiles and credentials

`src/tricksy/cli/`, plus `[project.scripts] tricksy = "tricksy.cli.main:main"`.

- `main.py`: `main(argv: list[str] | None = None) -> int`, one `argparse` subparser table, one
  dispatch. It **returns** an exit code for every expected failure rather than raising, so a command
  is a function a test can call and the process boundary carries no logic.
- `config.py`: `~/.config/tricksy/config.json` (honouring `XDG_CONFIG_HOME`), written `0600` through a
  temp-file-and-rename so an interrupted write cannot leave a client with no credentials. Holds the
  API base URL and a map of named profiles, each `{player_id, username, token}`.
- **Profiles are load-bearing.** The milestone at the end of this phase is one person playing four
  seats from one machine; without `--profile` / `TRICKSY_PROFILE` that means four home directories.
- Global flags on every command: `--api-url` (`TRICKSY_API_URL`), `--profile`, `--json`.

### 3.2 HTTP client and exit codes

- `api.py`: base URL plus bearer token, `Idempotency-Key` on the three move endpoints, and the
  `{"error": {"code", "message"}}` envelope decoded into a typed `ApiError` carrying the `code`.
  That symbol is the contract - `tricksy/api/errors.py` says in its own docstring that it exists for
  this client - so nothing here branches on a status code or on message prose.
- The client is reached through a narrow `Transport` protocol (`send(method, path, json, headers)`)
  with the real implementation beside it. The indirection earns its place on a concrete blocker, not
  on principle: `fastapi.testclient.TestClient` is built on httpx 0.28 and the CLI's client is
  httpx2, so without it 3.7 cannot drive the CLI against the real app in-process and has to start a
  server to test a `--help` path.
- `errors.py`: the code-to-exit-status table from DESIGN.md §7.2, in one mapping. An unrecognised
  code exits `1`, so the server can add a code without breaking an old client.

### 3.3 Rendering

`render.py`: pure functions from response data to text. No HTTP, no argparse, no engine import - it
takes a dict and returns a string, which is the same testability the engine gets from taking a state
and returning a state.

- The game view: hand, trump, dealer, marks, current trick with seat labels, whose turn, phase.
- Lobby, games list, open-games browse, invites (both directions), rule sets, profile.
- **Legal moves render as the commands that submit them.** This is the CLI being maximally helpful
  with zero rules knowledge: it is formatting `view["legal_moves"]`, which the server computed.
- `--json` never reaches this module; it prints the response body and returns.

### 3.4 Account and table commands

`register`, `login`, `logout`, `whoami`; `rules save|list|show|replace|delete`; `create-game`,
`join`, `open`, `invite`, `invited`, `uninvite`, `decline`, `invites`. The command-to-endpoint table
is in DESIGN.md §7 and is the checklist for this step.

- House-rule flags (`--contracts`, `--marks`, `--doubles-trump`, `--declared-leads`, and
  `--set plunge.minimum_doubles=5`) are shared by `create-game` and `rules save|replace`, so they
  live in one parser-building helper and one flags-to-body function. `--rule-set <id>` is exclusive
  with them; the CLI rejects the combination as a **usage** error, and does not otherwise second-
  guess the rules - the server owns validation and returns 400 for the same thing.
- `--seat` accepts `0-3` or `north|east|south|west`, sending the integer either way.

### 3.5 Play commands

`status`, `games`, `bid`, `declare`, `play`.

- `tricksy bid <code> 32 | pass | 2-marks --contract nello | confirm | decline`: five spellings, one
  endpoint, mapping onto the `kind`-discriminated body. The plunge confirmation rides here for the
  same reason it rides on `/bid` (DESIGN.md §6).
- `tricksy declare <code> trump=fives | trump=doubles | trump=none`, the last being the no-trump case
  nello and sevens need.
- `tricksy play <code> 4-1 [--declare treys]`, the flag being a declared lead (DESIGN.md §5.2).
- Suit and seat names are parsed and printed from the CLI's own label tables, never from the engine
  enums. Aliases stay accepted on input (`5` for `fives`) because a player typing a bid should not
  have to know which spelling the wire uses.

### 3.6 Running it locally

The one piece of non-CLI code in the phase. The table definition currently exists only inside
`tests/conftest.py: _create_texas42_table`, so there is no way to create it outside a test run.

- Promote it to `src/tricksy/storage/schema.py` - `create_table(dynamodb, name)` plus a
  `python -m tricksy.storage.schema` entry point - and have `tests/conftest.py` import it. Both fixtures
  keep building from one definition, which is the property 2.7.3 wanted when it put the `OpenGames`
  index in the shared helper; this just widens "shared" to include a local run.
- README gets the three lines: start DynamoDB Local, create the table, run `uvicorn`.
- The helper lives in `tricksy.storage` and not in a `tricksy dev` subcommand, because the CLI may not
  import boto3 (3.7 checks).

### 3.7 Tests

`tests/cli/`, mirroring the split the repo already uses.

- `test_render.py` - table-driven over the pure renderers. Plus **the third leakage proof**: drive a
  full game, render every view, and assert no tile held by another seat appears in the text. 1.5
  proved it of `project()`, 2.5 proved it of the wire, this proves it of the screen, which is the
  only one a player actually looks at.
- `test_config.py` - `XDG_CONFIG_HOME` pointed at a tmp path: file mode is `0600`, two profiles do
  not see each other's tokens, logout removes only its own.
- `test_commands.py` - `main(argv)` end to end, transport pointed at `TestClient(app)` with the moto
  `table` injected through `app.dependency_overrides`. Every command's happy path, and the error
  paths that earn each exit code in DESIGN.md §7.2.
- `test_layering.py` - no module under `tricksy.cli` imports `tricksy.games.texas42`, `tricksy.storage` or boto3.
  Cheap, and it is the whole difference between DESIGN.md §11 being a claim and being a fact.
- `tests/cli/test_cli_integration.py`, marked `integration` - four profiles play a full 4-player
  game to `GAME_OVER` through CLI commands against DynamoDB Local. This is the scripted end-to-end
  CLI smoke test DESIGN.md §9 has listed since before there was a CLI to script.

### Exit criteria

- Every endpoint in DESIGN.md §6 is reachable from a command, and a full 4-player game plays start
  to `GAME_OVER` through the CLI alone
- Nothing under `tricksy.cli` imports `tricksy.games.texas42`, `tricksy.storage` or boto3, proven by test
- No rendered output ever shows a tile another seat holds, proven by test
- Every documented failure exits with its documented code, and an unknown server code exits `1`
- Four profiles play from one machine without interfering, which is also the dogfood milestone
- The table can be created, and the API run, without invoking pytest

---

## Phase 4: Notifications

Goal: a player who is not looking at a terminal finds out that it is their turn. DESIGN.md §8 is
the spec and this section sequences it, widened to the three things actually worth an email - your
turn, you've been invited, your game is over - and carrying the account work DESIGN.md §6.1
deferred to "whenever a send channel exists", which is now.

Four decisions govern everything below. The first is a contradiction between two sections of
DESIGN.md and has to be settled before any code is written.

- **The trigger is the `PLAYER#` item, not the event log.** DESIGN.md §8 says stream on new
  `EVENT#` items and recompute the current player; §13 says react to the `is_my_turn` flip on the
  `PLAYER#<id>/GAME#<id>` items. §13 wins. `append` already stamps `is_my_turn` and `status` onto
  all four of those items inside the move's own transaction (`repository.py`), and `start_game`
  does the same on the deal, so whose turn it is has already been computed by the one component
  entitled to compute it. §8's version would have to reach for `STATE` - the item holding every
  hand - or replay the log to answer a question that is already answered.

  This generalizes past turn notifications: **all three notifications are transitions on items in
  the recipient's own `PLAYER#` partition** - `is_my_turn` false to true, `status` `ACTIVE` to
  `COMPLETE`, and an `INVITE#<gameId>` insert. One handler, three rules, one partition prefix, and
  no game state at all. The notifier never reads `STATE`, so invariant 5 holds for it by
  construction rather than by audit, and 4.7 makes that a test rather than a claim.

- **It runs locally**, the same answer 3.6 gave for the API. DynamoDB Local implements the Streams
  API, so `python -m tricksy.notifications.pump` polls the stream and hands the handler batches shaped
  exactly like a Lambda `Records` event, and SES is one implementation of a narrow sender protocol
  sitting beside a console one. The AWS-side wiring - event-source mapping, SES identity, IAM - is
  Phase 5's deployment work, and none of this phase's code changes when it lands.

- **Streams are at-least-once**, so a record can arrive twice and an email must not. A
  `notified_version` attribute on the `PLAYER#/GAME#` item, advanced by a conditional write
  (`attribute_not_exists(notified_version) OR notified_version < :v`), gates the send: no
  successful condition, no email. This is why 4.5 also has `append`/`start_game` stamp `version`
  onto those items - one more expression value in an update they already do. The notifier's own
  write produces a further stream record, which matches none of the three rules and is dropped, so
  there is no loop.

- **`verified` is currently never set to `true` by anything**, and no endpoint touches contacts
  after `POST /players`. Email verification is therefore not a nicety in this phase; it is the gate
  deciding who the notifier may write to, and it has to land before the notifier does.

### 4.1 The send channel

New top-level package `src/tricksy/notifications/`. It may import `tricksy.storage.accounts` and boto3; it
may not import `tricksy.games.texas42`, nor `tricksy.storage`'s `repository`, `codec` or `replay` - the import
rule that makes "cannot see a hand" checkable (4.7).

- `sender.py`: an `EmailSender` protocol - `send(to, subject, body) -> None` - with `SesSender`
  over boto3 `sesv2`, `ConsoleSender` for a local run, and a recording fake for tests. Chosen by
  environment variable, the same shape `tricksy.api.deps` already uses for the table handle. A protocol
  rather than a function so DESIGN.md §11's claim holds here too: SMS or a chat DM is another
  implementation, not a rewrite.
- `messages.py`: pure `dict -> (subject, body)` renderers, plain text, one per notification kind.
  Same "no I/O, no client library, table-testable" property `tricksy/cli/render.py` has, and for the
  same reason - the interesting part of a message is its content, and content should be assertable
  without a transport.

### 4.2 Contact channels and email verification

- `ContactChannel` gains `notify: bool = True`, decoded through `.get("notify", True)` so existing
  items need no migration. Without a per-channel mute there is nowhere to turn notifications off,
  and a player at three tables gets a mail flood with no recourse but deleting the address.
- New item `VERIFY#<sha256(token)>` / `TOKEN` holding `{player_id, address, expires_at}`, mirroring
  the `TOKEN#<sha256>` shape `accounts.py` already uses: the hash is stored and the plaintext is
  unrecoverable, so a leaked table dump is not a set of live verification links.
- `accounts.py` gains `add_contact`, `remove_contact`, `set_contact_notify`, `begin_verification`
  and `complete_verification`.
- Endpoints: `POST`/`GET /players/me/contacts`, `PATCH /players/me/contacts/{address}` (the mute),
  `DELETE /players/me/contacts/{address}`, `POST /players/me/contacts/{address}/verification`, and
  `POST /contacts/verify`. The last is unauthenticated: the token *is* the credential, and it
  arrives in an email the player may open on a device that has never run `tricksy login`.

### 4.3 Password reset

- New item `RESET#<sha256(token)>` / `TOKEN`, the same shape as 4.2's, with a short expiry and
  single use.
- `POST /password-resets` taking a username, and `POST /password-resets/confirm` taking the token
  and a new password.
- Three properties, each ruling out an easier wrong version. The request endpoint returns `202`
  whether or not the username exists, for the same reason `authenticate` will not say which half of
  a credential was wrong. It sends only to a **verified** channel, because an unverified address is
  one an attacker may have supplied. And a completed reset revokes every issued token, because "my
  account was taken" and "reset my password" are in practice the same event, and leaving the
  intruder's device signed in defeats the reset.
- Login rate limiting stays in the hardening phase (Phase 7), unchanged. Stated here so the
  omission reads as a decision rather than an oversight, since this is the phase that adds a second
  way to take an account over.

### 4.4 Streams and the local pump

- `schema.py` gains a `StreamSpecification` with `StreamViewType="NEW_AND_OLD_IMAGES"`. Old images
  are what make a *transition* detectable rather than merely a current state, and every rule in 4.5
  is a transition. One change, picked up by the moto and DynamoDB Local fixtures alike, which is
  the property 3.6 moved that module out of `conftest.py` for.
- `records.py`: stream records carry DynamoDB-JSON attribute values, not the plain dicts the
  resource-level `Table` API hands back everywhere else in this codebase - the one place that gap
  is visible. One `boto3.dynamodb.types.TypeDeserializer` pass plus a typed `Transition(old, new)`,
  so 4.5's rules are written against plain data and can be tested without a stream.
- `pump.py`: `python -m tricksy.notifications.pump` polls the local stream and calls the handler with a
  Lambda-shaped `{"Records": [...]}` batch, so the local path and the deployed path exercise the
  same entry point. Same `python -m` precedent as `tricksy.storage.schema`.

### 4.5 The handler

`handler.py`: `lambda_handler(event, context)` over a pure `notifications_for(records)`.

- Filter to `PK` beginning `PLAYER#`, then three rules - `is_my_turn` false to true (your turn),
  `status` `ACTIVE` to `COMPLETE` (game over), and an insert with `SK` beginning `INVITE#` (you've
  been invited).
- Resolve recipients through `accounts.get_player`, dropping any channel that is not
  `kind == "email"` **and** `verified` **and** `notify`. A player with no routable channel is a
  no-op, not an error - contacts are optional by design (DESIGN.md §12).
- Enrich from `META` only: seat usernames, status, join code, all of it public. For the game-over
  message to carry a final score, `append` denormalizes the public `marks` onto `META` as it
  completes the game. That is the whole point - the alternative is reading `STATE`, which is the
  one thing this component must not do.
- The invite message wants to say who invited you, and cannot: `invite_player` writes only
  `{game_id, created_at}` on the player-side item. Add `invited_by` to it - one attribute, one call
  site in `app.py`'s invite handler, which already knows the inviter because `_require_seat` just
  checked them.
- Dedupe through the conditional `notified_version` advance described in the preamble.

### 4.6 CLI commands

`src/tricksy/cli/commands/account.py`, following 3.4's pattern with no new machinery: `tricksy contacts`,
`tricksy contact add|remove|verify|confirm|mute|unmute`, `tricksy forgot-password`, `tricksy reset-password`.
DESIGN.md §7's command table gets the matching rows in the same pass, since that table is the
statement that every endpoint is reachable from a command.

`contact confirm` and `reset-password` work **signed out**, since the token they carry is itself
the credential. That needs nothing new: `build_client`'s `require_auth=False` already exists for
`register` and `login`, which is the point of noting it - this sub-phase should add no machinery
at all.

### 4.7 Tests

`tests/notifications/`, mirroring the package as every other suite here does.

- `test_messages.py` - table-driven over the pure renderers.
- `test_handler.py` - hand-built stream records against the recording sender: each rule fires
  exactly once, a duplicate record sends nothing, an unverified or muted or absent channel sends
  nothing, and a `PLAYER#` record whose watched attribute did not change is ignored.
- `test_layering.py` - the `ast` check from `tests/cli/test_layering.py`, retargeted: nothing under
  `tricksy.notifications` imports `tricksy.games.texas42`, `tricksy.storage.repository`, `tricksy.storage.codec` or
  `tricksy.storage.replay`. Cheap, and it is the whole difference between "the notifier cannot see a
  hand" being an argument and being a fact - the same trade 3.7 made for the CLI.
- `tests/api/test_contacts.py` and `test_password_reset.py` - the four-case contract matrix, with a
  fake sender injected through `app.dependency_overrides` beside the existing `table` override.
- `tests/notifications/test_integration.py`, marked `integration` - **the phase milestone**:
  against DynamoDB Local, play a real game through the API, drive the pump, and assert the right
  seat is mailed at each turn, with a real wall-clock gap between two moves. This is DESIGN.md
  §10's "verify against real play across a delay", earned without provisioning anything.

### Exit criteria

- The seat to act is emailed exactly once per turn, proven across a real delay against DynamoDB
  Local, and not at all when its channel is unverified or muted
- A duplicated stream record sends nothing
- Nothing under `tricksy.notifications` can reach `STATE` or the engine, proven by test
- A contact can be added, verified, muted and removed from the CLI
- A forgotten password can be recovered end to end through a verified channel, and doing so revokes
  every device
- Nothing is provisioned: the AWS-side wiring is still Phase 5's

---

## Phase 5: Deployment

Goal: the table, the API and the notifier running in AWS, provisioned from code, with the CLI
playing a real game against a real endpoint. Every phase so far has ended with "nothing is
provisioned"; this is the one that changes that, and it is deliberately the first phase whose
correctness depends on something outside this repository.

Four decisions govern everything below.

- **CDK, in Python, under `infra/`.** DESIGN.md §10 said "SAM/CDK/Terraform - pick one" and this
  picks CDK: SAM's template model covers a Lambda-and-API stack well but not the alarms, budget
  and SES identities in 5.4-5.5, and Terraform means a second state store to look after for a
  hobby project. Python rather than TypeScript keeps this a one-language repository, which is
  worth more here than CDK's better TypeScript ergonomics: `ruff` and `mypy` reach `infra/` with a
  config line, whereas TypeScript would mean a second toolchain, a second lint setup and a second
  thing to keep current.
- **One stack, one region, one environment, deployed by hand.** No pipeline, no dev/prod split, no
  custom domain. The rejected alternative is the usual one - a deploy pipeline and a staging
  environment up front - and it is rejected for the reason 3.6 rejected pulling a stack forward:
  it buys nothing yet. There is exactly one operator and the fast suite plus DynamoDB Local
  already catch what a staging environment would. A CI deploy is a Phase 7 bullet, added when
  deploying by hand becomes annoying rather than before.
- **This phase lives in the SES sandbox**, which is the one externally imposed constraint worth
  stating up front rather than discovering. In the sandbox SES will deliver only to addresses that
  have themselves been verified, at 200 messages a day. That is enough to dogfood with four known
  players and not enough to sign anyone else up. Leaving the sandbox is a support request that
  wants a verified sending *domain* behind it, so it is recorded as a follow-up rather than done
  here: it is the only thing on the whole list that needs a domain, and the API needs none (an
  `execute-api` URL is a working endpoint, and DESIGN.md §7.3 already promised that reaching a
  real one is a change to `--api-url` and nothing else).
- **The table gets a second definition, and a test rather than a promise.**
  `tricksy.storage.schema.create_table` stays what the moto and DynamoDB Local fixtures build from,
  and it is written as literal `create_table` kwargs on purpose - boto3-stubs types that call as
  overloads keyed on the keyword names - so the stack cannot simply import it and splat it into a
  CDK construct. Rather than contort one side to feed the other, 5.1 declares the table twice and
  adds a parity test comparing the synthesized template against the real thing. That is the trade
  `tests/cli/test_layering.py` already made: a property that would otherwise be an argument in a
  docstring becomes a fact a test can fail on.

### 5.0 Pre-deployment hardening

Five findings from a pre-deployment audit of the codebase, done here rather than after 5.7 because
every one of them is cheaper while no real data exists: three are correctness holes whose fix is a
small code change today and a data-repair exercise once real accounts and games are in the table,
and the other two are decisions that 5.3/5.4 would otherwise bake machinery around. None of them
block local play, which is why no earlier phase caught them as an exit criterion.

- **Make single-use token redemption atomic** (`accounts._redeem_single_use_token`). Today it is a
  `get_item` followed by a separate unconditional `delete_item`, so two concurrent redemptions of
  the same `VERIFY#`/`RESET#` token can both read the item before either deletes it and both
  succeed - contradicting the docstring's "can never be replayed". Replace the pair with a single
  `delete_item(Key=key, ReturnValues="ALL_OLD")` and treat an empty `Attributes` as invalid: the
  delete becomes the atomic claim, and the function gets smaller rather than larger.
- **Close the seat-claim/`PLAYER#` write gap** (`lobby.join_seat`, `lobby.create_pending_game`).
  The seat claim on `META` and the `PLAYER#/GAME#` put are two non-transactional writes. A crash
  between them leaves a held seat with no row - and it never heals, because `start_game` and
  `append` both `UpdateItem` that key, which *creates* a partial item carrying only
  `is_my_turn`/`status`/`version`. `list_games_for_player` then hits `KeyError` on `game_id`, so
  every future "my games" read for that player is a 500. Fix by putting the claim and the put in
  one `transact_write` (unlike the invite check, whose read-then-write trade `join_seat`'s
  docstring defends, the put carries no condition of its own, so nothing about failure attribution
  is lost), and make `list_games_for_player` skip rows missing `game_id` as defense in depth.
- **Settle the notifier's send-failure semantics before 5.4 builds around them**
  (`handler.send_notifications`). `_claim` marks a transition processed *before* the send, so on a
  batch retry the claim returns false and the email is skipped - lost, not resent - and the same
  holds for anything that reaches 5.4's DLQ. Claim-before-send is a defensible at-most-once
  choice, but the code comment saying a send failure is "visible to Lambda's batch retry"
  overpromises, and 5.4's retry/bisect/DLQ design currently assumes redelivery helps a failure
  mode this ordering makes unretryable. Either keep at-most-once and correct the comment plus
  5.4's rationale, or claim after a successful send and accept the occasional duplicate email.
- **Cap the unbounded per-player lists** (`accounts.add_contact`, `RegisterRequest.contacts`,
  `issue_token`). Contacts have no cap at either layer, so an authenticated loop can grow the
  `PROFILE` item toward DynamoDB's 400KB item limit; devices likewise - every sign-in mints a
  token pair, nothing expires them, and `complete_password_reset` loops over all of them. A small
  cap (order of ten contacts, a few dozen devices) is two validations today and an awkward
  migration conversation after real accounts exceed it.
- **Give `HttpTransport` an explicit timeout** (`cli/api.py`). It currently rides on httpx2's
  default. The first request to a cold Lambda - cold start plus a ~16 MiB scrypt on sign-in - can
  plausibly brush a short default, and 5.7's dogfood game should not spend its time debugging a
  transport setting. Choose one deliberately and write it down.

### 5.1 The CDK app and the table

`infra/`: a `app.py` entry point, the stack module, and `cdk.json`. Dependencies go in an `infra`
optional extra (`aws-cdk-lib`, `constructs`), kept out of the Lambda bundle the same way the `cli`
extra already is and for the same reason. `ruff` and `mypy` widen to cover `infra/`, since the
alternative is one directory in the repository nothing checks.

- The table repeats `schema.py`'s key schema, `OpenGames` GSI, `PAY_PER_REQUEST` billing and
  `NEW_AND_OLD_IMAGES` stream, and adds the three things a fixture has no use for and a real table
  must not be without: `RemovalPolicy.RETAIN`, point-in-time recovery, and deletion protection.
  A `cdk destroy` that takes the games with it is not a recoverable mistake.
- `tests/infra/test_table_parity.py` synthesizes the stack, pulls the `AWS::DynamoDB::Table`
  resource out of the template, and compares its key schema, attribute definitions, secondary
  indexes and stream view type against `describe_table` of a moto table built by
  `schema.create_table`. Both sides are CloudFormation-shaped, so this is a direct comparison and
  not a translation layer. It is the whole answer to the duplication the preamble accepted.

### 5.2 TTL for the single-use tokens

Not polish, and not really a deployment concern except that this is the phase where the table
starts accumulating rows nobody deletes. `accounts._mint_single_use_token` writes `expires_at` as
an ISO-8601 **string**, and DynamoDB TTL reads only a Number of epoch seconds, so today every
`VERIFY#` and `RESET#` item that is never redeemed stays in the table forever.

- Add a numeric `ttl` attribute beside the existing `expires_at`. `expires_at` stays the authority
  for the check in `_redeem_single_use_token`, and this is the point rather than a redundancy: TTL
  deletion is best-effort and can run up to 48 hours late, so it is housekeeping and must never be
  the thing standing between an expired token and a redemption.
- Enable `TimeToLiveSpecification` on `ttl` in both `schema.create_table` and the stack, where
  5.1's parity test covers it.
- `TOKEN#` bearer items are untouched. They carry `expires_at: None` deliberately (DESIGN.md §6.1:
  a device credential is revoked, not expired), so there is nothing for a reaper to key on.

### 5.3 The API function and the HTTP API

- A Python 3.13 arm64 `lambda.Function` on `tricksy.api.lambda_handler.handler`, which has been
  waiting since 2.6. The bundle is built in Docker rather than locally: `fastapi` pulls in
  `pydantic-core`, a compiled wheel, so a bundle assembled on a developer's macOS arm64 machine
  imports nothing at all on Lambda. `uv export --frozen --no-dev` plus `uv pip install --target`
  inside the runtime image gets the right wheels; Docker is already a prerequisite for
  `pytest -m integration`, so this adds no new tool. The `cli` extra is excluded, which is what
  that extra exists for.
- Environment: `TRICKSY_TABLE_NAME` set, `TRICKSY_DYNAMODB_ENDPOINT` left unset, since `tricksy.api.deps`
  reads unset as real AWS. IAM through `table.grant_read_write_data`, which covers the GSI.
- In front of it an API Gateway HTTP API with a `$default` proxy route, and **no API keys**.
  DESIGN.md §10 still says "wire up API keys" for Phase 2, which auth resolution (§6.1, §12) made
  obsolete: the credential is a per-device bearer token the app itself checks. This phase deletes
  that line rather than implementing it.
- The stack outputs the endpoint URL. That URL is the entire deployment-facing surface of the
  CLI: `--api-url` or `TRICKSY_API_URL` and nothing else.

### 5.4 The notifier and SES

- A second function on `tricksy.notifications.handler.lambda_handler` from the same bundle asset,
  behind a `DynamoEventSource` on the table's stream. This is the real event-source mapping
  `pump.py` has been standing in for since 4.4, calling the same entry point with the same
  `{"Records": [...]}` shape, which is what that sub-phase built it that way for.
- A retry limit, `bisectBatchOnError`, and an SQS dead-letter queue as the failure destination. A
  stream shard is ordered, so a record that always throws blocks everything behind it until it
  ages out; the DLQ is what turns that into an alarm (5.5) instead of a silence.
- **No `ReportBatchItemFailures`**, stated so the omission reads as a decision. It would let a
  partial batch retry only its failed records, but 4.5's conditional `notified_version` advance
  already makes a redelivered record a no-op, so a whole-batch retry is correct and merely
  wasteful at a volume of a few emails an hour. It is an optimization available later at the cost
  of a return value the handler does not currently produce.
- Environment: `TRICKSY_EMAIL_SENDER=ses` and `TRICKSY_SES_FROM_ADDRESS`. The first matters more than it
  looks: `get_sender()` defaults to `console`, which was the right default for a local run (4.1)
  but in Lambda would print every email to CloudWatch and report success. Nothing fails, nothing
  alarms, and no one is notified, which is why 5.7's check is a real inbox and not a green
  invocation. IAM adds `ses:SendEmail`.
- The SES identities - the from-address and each dogfood recipient, per the sandbox rule above -
  are declared in the stack, but an address identity is only usable once a human clicks the
  confirmation link SES mails it. That step is half-manual by construction, and 5.6 writes it down
  rather than letting a first deploy appear complete while nothing can send.

### 5.5 Operations

Small on purpose: enough to know something broke, and no more.

- Log retention on both functions' log groups. The default is forever, and paying indefinitely to
  store the logs of a game nobody is playing is the easiest cost mistake available here.
- An SNS topic to the operator's address, with alarms on API function errors, notifier function
  errors, and DLQ depth above zero. Those three cover "the API is down", "notifications stopped"
  and "a record is poison", which is the whole failure surface at this size.
- An AWS Budgets monthly alarm. The cheapest possible guard against a runaway, and the only one
  that catches a mistake in a service the alarms above do not watch.
- Deliberately absent: X-Ray, dashboards, a structured-logging framework, custom metrics. They are
  what Phase 7 adds if operating this actually turns out to need them.

### 5.6 Configuration and docs

README gains a Deployment section: the prerequisites (an AWS account, the CDK CLI, one
`cdk bootstrap`, Docker), `cdk deploy`, clicking through the SES verification mails, and pointing
the CLI at the stack's output URL. (An earlier version of this sub-phase also listed three README
staleness fixes; they have since been made, so the Deployment section is all that is left here.)

### 5.7 The milestone: a real game against real infra

Four profiles, four verified addresses, a full game to `GAME_OVER` against the deployed endpoint,
with `--api-url` as the only thing that differs from the Phase 3 dogfood. Real emails arriving
between turns, and at least one gap long enough that no container stays warm across it - the
asynchronous-play claim (DESIGN.md §1) has been verified against a local pump and a `time.sleep`
so far, and this is the first time it is verified against infrastructure that genuinely goes away
between moves.

This is not an automated test. It needs an AWS account and a human inbox, so it is a checklist in
the README, the way Phase 3's dogfood was a thing a person did. The automated suites stay pointed
at DynamoDB Local, which is what keeps `uv run pytest` free and offline.

### Exit criteria

- `cdk deploy` from a clean checkout provisions everything, with no console clicking but the SES
  address confirmations
- A full 4-player game plays to `GAME_OVER` against the deployed endpoint, with `--api-url` the
  only change on the client side
- All three notifications reach real inboxes, across a delay long enough to rule out a warm
  container
- The stack's table and `schema.py`'s cannot drift apart without a test failing
- Verification and reset tokens leave the table on their own
- A function error, a poison record and a surprising bill each reach the operator
- Recorded and not built: leaving the SES sandbox (the one item that needs a domain), a custom API
  domain, a CI deploy, a second environment

---

## Later phases

- **Phase 6, Bot players**: DESIGN.md §13. Bot accounts, a uniform-random-legal policy over the
  projected view's `legal_moves`, and a Streams-driven turn trigger reusing Phase 4's plumbing. Last
  on purpose - a bot is a client of the finished API, so everything else has to work first
- **Phase 7, Hardening**: abandoned games, CLI errors and help, login rate limiting, deeper
  observability, and the CI deploy (GitHub Actions via an OIDC role) that Phase 5 left manual
- **Phase 8, Web client**: DESIGN.md §11. A rich React frontend against the same REST API - no new
  game logic, the same `project()` view every client already reads, and the same move endpoint the
  CLI posts to. Two things this phase settles that the CLI never had to: auth (a browser holding a
  bearer token has different risks than the CLI's `0600` config file, so this is where session
  auth gets a real look rather than staying a footnote) and a CORS policy on the API, untouched
  since Phase 2 because no browser has called it yet. Hosting is a static build, plausibly
  S3+CloudFront alongside the existing CDK stack rather than a new deployment story. Last and
  largest of the three, deliberately: "rich" is open-ended in a way Phase 6's bot policy and Phase
  7's hardening list are not, and the API's shape should be settled by real hardening before a UI
  is built to match it.

Phases 6 and 7 may be done in either order; Phase 8 comes after both, for the reason given above.
The numbering exists to keep Phase 6's existing citations pointing at bots, not to claim hardening
strictly precedes it.

### Resolved: when does anything get deployed?

Phase 3 was originally written as "the CLI against the deployed API", but nothing is provisioned
until Phase 5's deploy scripting, so as written the CLI had nothing to point at. **Resolved:
dogfooding happens against a locally hosted API** - `uvicorn` over DynamoDB Local (3.6) - and
deploy scripting stays in Phase 5 where it was.

Pulling a minimal stack (table, one Lambda, one HTTP API, IAM) forward was the alternative. It was
rejected because it buys nothing the CLI needs and costs the phase its focus: the CLI's entire
coupling to where the server lives is `--api-url`, so pointing it at a provisioned endpoint later
is a configuration change, and building that endpoint first would only mean choosing a deployment
tool under time pressure from an unrelated phase.

That later is now: Phase 5 above is the deployment work, chosen with Phases 0 through 4 finished
and the shape of what needs provisioning settled by working code rather than guessed at, which is
what deferring it was for.
