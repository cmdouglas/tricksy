# CLAUDE.md

Guidance for working in this repository.

## What this is

Texas 42 online: a server-authoritative, asynchronous implementation of the partnership domino
game, including the nello, plunge and sevens special contracts. Games are played over minutes or
hours, not in real time. The MVP ships a CLI client, but the architecture exists so that a web,
mobile or chatbot client is later a thin client against the same API, not a rewrite.

Design priorities, in order: correctness of rules, no leakage of hidden information (hands, undealt
tiles) to the wrong player, and support for long-running asynchronous play. Real-time performance
is not a concern.

- [DESIGN.md](DESIGN.md): architecture and data model. The authority on intent.
- [ROADMAP.md](ROADMAP.md): phase-by-phase execution breakdown and exit criteria.

## Current status

Phase 0 (pure rules engine) is complete: dealing, bidding (including the plunge confirmation
sub-flow and the dealer-must-bid all-pass rule), all six contracts (standard, nello, nello_low,
sevens, plunge, splash), the trick engine, and a full in-memory game per contract, all through the
public `new_game`/`apply_move`/`legal_moves` entry points. See ROADMAP.md for the exit-criteria
checklist. Nothing under `api/` or `cli/` exists yet.

Phase 0.5 (house rules) is complete. The rule-config type is `HouseRules` in `house_rules.py`;
each contract declares its own options through `option_defaults()`/`validate_options()` (the
plunge/splash doubles-and-marks minimums and the nello/nello_low/sevens mark floor all live in
`contract_options`, not as class attributes); `new_game` rejects an invalid house-rule set via
`contracts.validate_house_rules` before a game is ever created; and declared leads
(`allow_declared_lead`: `never`/`first_trick`/`always`, DESIGN.md §5.2) let a leader name which end
of a two-ended tile is the suit led, recorded on `Trick.declared_suit` and read through
`trick_rules.suit_led` rather than derived.

Phase 1 (persistence) is complete. boto3 landed in 1.3 - see below.

1.1 (item shapes and codec) is complete: `t42.storage.codec` encodes and decodes `GameState`,
`HouseRules` and every `Event` to the plain dict/list/str/int/bool/None shapes boto3's
resource-level `Table` API accepts directly, proven by a round-trip property test in
`tests/storage/` that drives real games through `new_game`/`apply_move` and asserts
`decode(encode(x)) == x` on every intermediate state.

1.2 (replay) is complete. `apply_move` still doesn't emit events itself (unchanged, and still
deliberately out of scope - see 1.1's note above, now resolved by construction rather than
wired-in), so `t42.storage.events` supplies the write direction: `event_for_move`/
`hand_dealt_event`/`events_for_move` translate an accepted move (and any deal it triggers) into
the `Event`(s) it produces. `t42.storage.replay.replay(game_id, players, config, events)` is the
read direction, rebuilding a `GameState` by feeding events back through real `new_game`/
`apply_move` calls - not a parallel reimplementation - using `_ReplayRandom`, a `Random` subclass
whose `shuffle` deterministically replays each `HandDealt` event's recorded deal instead of
randomizing, so dealing runs through its real code path even though the outcome is fixed by
history. Proven against real games (all six contracts, plunge confirmation, declared leads, and a
multi-hand random smoke test) in `tests/storage/test_replay.py`.

1.3 (repository writes) is complete: `t42.storage.repository` is the first real boto3 dependency.
`create_game` deals the first hand and writes `META`, the opening `HAND_DEALT` event, `STATE`
(`version=1`) and one `PLAYER#` item per seat in a single `TransactWriteItems` call; `get_state`
reads the materialized `STATE` item back into a `StoredGame(state, version)`; `append(table,
game_id, events, new_state, expected_version)` writes further events and the resulting state in
one transaction, conditioned on `expected_version` still matching what's stored, and also updates
`META.last_activity_at` and every `PLAYER#` item's turn status. A stale `expected_version` raises
`VersionConflict` (`t42.storage.errors`, alongside `GameNotFound`/`GameAlreadyExists`) - the
optimistic-concurrency guarantee 1.6 exercises under real concurrency. `version` lives only in
`StoredGame`, never on `GameState` itself (invariant 1). Tested against `moto`'s in-memory
DynamoDB (`tests/storage/conftest.py`'s `table` fixture) rather than DynamoDB Local, which stays
reserved for 1.6's integration tests per ROADMAP.md; one test drives a full game through
`create_game`/`append` and confirms `t42.storage.replay.replay` over the resulting event log
reproduces the same `STATE` item, tying 1.2 and 1.3 together.

1.4 (idempotency) is complete: `append` takes an optional keyword-only `request_id`; when given, the
same transaction that writes the events and updates `STATE`/`META`/`PLAYER#` also `Put`s a
`REQUEST#<requestId>` marker recording the resulting version, conditioned on its own absence. A
duplicate call with the same `request_id` - including one carrying a now-stale `expected_version`,
as a real client retry would - finds that marker already written and returns its recorded version
as a no-op instead of raising `VersionConflict` or applying `events` twice; `request_id=None`
(the default) leaves `append`'s behavior unchanged from 1.3. `create_game` needs no equivalent,
since `game_id` is already its idempotency key (`GameAlreadyExists` on a repeat). Engine `Move`/
`Event` types, `apply_move` and `t42.storage.replay` stay untouched - idempotency is a repository
concern only (invariant 1).

1.5 (player-specific view) is complete: `t42.engine.projection.project(state, player_id)` is the
one gate hidden information passes through (invariant 5). Almost everything on `GameState` turns
out to already be public at a real table - the auction, tricks, marks, declarer and contract - so
`project()` mostly copies those fields to plain dict/list/str/int/None data, substitutes the
caller's own tiles for the full `HandState.hands` mapping, and calls `game.legal_moves` directly
for the `legal_moves` field rather than re-deriving whose turn it is. Its small encoders are
deliberately not shared with `t42.storage.codec`: `t42.engine` may not import from `t42.storage`
(invariant 1), and the two serve different purposes - a durable wire format versus a client
read-model. Proven in `tests/engine/test_projection.py` by a leakage test that drives real games
(standard and nello, the latter for its sitting-out partner) through every phase and asserts no
tile held by another seat ever appears anywhere in the projected structure.

1.6 (integration tests) is complete, closing out Phase 1. `tests/storage/conftest.py`'s
`dynamodb_local` fixture starts a real `amazon/dynamodb-local` container via `testcontainers` for
the whole test session and `real_table` creates/drops a fresh `Texas42` table in it per test - the
same per-test isolation `table`'s moto fixture gives, just against a long-lived container. Both
fixtures create their table through the shared `_create_texas42_table` helper, so the schema can't
drift between the two. These tests are marked `@pytest.mark.integration` and excluded from the
default `uv run pytest` (`addopts` carries `-m "not integration"`) since they need Docker and are
slower to start; `uv run pytest -m integration` runs them explicitly, and CI runs both as separate
steps. `tests/storage/test_repository_integration.py` proves, against real infra rather than
moto's approximation of it, the two guarantees moto alone couldn't fully establish: genuine
concurrent `append` calls from a `ThreadPoolExecutor` racing the same `expected_version` never let
more than one land or produce a mixed state, and a full scripted game persisted move by move
round-trips through `replay()` exactly as the moto-backed version does. With this, Phase 1's exit
criteria (ROADMAP.md) are all met.

Phase 2 (API) is complete: a FastAPI app behind Mangum implementing DESIGN.md §6, with every
endpoint covered by the four-case contract matrix and a full game playable signup-to-`GAME_OVER`
over HTTP. It settled the two things DESIGN.md left open, both recorded there:

- **Identity** (§6.1, §12): username + password minting **per-device bearer tokens**, hashed with
  stdlib `scrypt` (passwords) and `sha256` (tokens, being high-entropy). A player holds many
  tokens, so signing in on a phone leaves a desktop alone and losing one device revokes one
  credential. `PlayerId` is opaque, never the username, so usernames stay renameable and never
  enter the immutable event log. Contacts are a list of `{kind, address, verified}` channels, not
  an `email` field, so Phase 4 adds a branch rather than a migration. `t42/storage/accounts.py`.
- **The lobby** (§4.1): a game is created `WAITING` with only its creator seated, and the join
  that fills the fourth seat deals and flips it to `ACTIVE` - conditioned on the status still
  being `WAITING`, which is what makes two simultaneous fourth joins deal exactly once. It lives
  entirely in `t42/storage/lobby.py`; the engine still has no notion of a partially seated game.
  This reworked `repository.create_game` into `start_game`, whose `META` write is now a
  conditional `Update` rather than a `Put`.

Two things worth knowing before touching the API layer:

- **`GameResponse.view` is deliberately opaque.** It carries `project()`'s output verbatim rather
  than through a pydantic model mirroring it. A model would be a second definition of the
  projected shape sitting next to the one gate invariant 5 requires, free to drift - and a
  drifted mirror is how a field leaks. The cost is a vaguer OpenAPI schema for that one field.
- **An idempotency key is checked twice**, before the move and again after a rejection. 1.4's
  marker inside `append` is necessary but not sufficient once a handler sits on top: the handler
  re-derives the move from fresh state, so a retry arriving after the turn moved on is rejected
  as out-of-turn long before `append` ever sees the marker. `repository.find_request` is the
  up-front look; the second look catches parallel retries that both miss it. See `api/app.py`'s
  `_submit`, which is the single write path behind all three move endpoints.

Deployment is deliberately not part of Phase 2 - there is a Mangum entry point and nothing
provisioned. That changes in Phase 5 (DESIGN.md §14, ROADMAP.md 5.1-5.7), which is written out but
not yet executed: still nothing provisioned.

Phase 2.7 (tables) is complete, landing ahead of the CLI so its command set is written once
against the finished surface rather than grown into it. See DESIGN.md §5.1, §6.2 and §4.1 for the
semantics and ROADMAP.md for the breakdown; three things are worth knowing before touching this
surface:

- **A saved rule set is a copy, not a reference** (§5.1). `t42/storage/rule_sets.py` stores named
  `HouseRules` values under a player's own partition (`RULESET#`); `POST /games` may name one via
  `rule_set_id`, mutually exclusive with an inline `house_rules` body via `model_fields_set` (an
  absent field and an explicitly-sent default are otherwise indistinguishable, since
  `HouseRulesRequest` has a `default_factory`). Editing or deleting a set afterwards never touches
  a game already created from it - `META.config` already holds the resolved rules independently.
- **Invites are a permission grant, not a seat reservation** (§6.2). `t42/storage/invites.py` is
  deliberately dumb - CRUD over a `GAME#/INVITE#` + `PLAYER#/INVITE#` item pair with no dependency
  on `lobby.py`, which avoids a circular import (`lobby.join_seat` needs `invites.find_invite` to
  gate an `invite_only` table; validating an invite request needs the `Lobby` that `invites.py`
  can't import back). `t42/api/app.py`'s invite handler does that validation itself, the same
  "dumb storage, smart handler" split the "my pending invites" enrichment already uses. `GET
  /games/{id}` widened from strictly-seated to seated / invited-or-public-`WAITING` / forbidden,
  which is also what moved `GameResponse.view`'s gate from "game has been dealt" to "caller is
  seated" - the one thing DESIGN.md §6.2 says changes about invariant 5's gate.
- **The `OpenGames` GSI is sparse** (§4.1): a `META` item carries `GSI1PK`/`GSI1SK` only while
  public and `WAITING`, written by `create_pending_game` and removed by `start_game`'s conditional
  update - the only transition out of `WAITING`, so there is exactly one removal site. A GSI is
  eventually consistent, unlike moto, so `tests/storage/test_lobby_integration.py` polls against
  real DynamoDB Local rather than asserting immediately; that file is the one place this project
  currently exercises that gap. `GET /games/open` is registered before `GET /games/{game_id}` in
  `api/app.py` on purpose - Starlette matches routes in registration order, and the path parameter
  would otherwise swallow the literal `open` segment.

Phase 3 (CLI) is complete; bot players are designed in DESIGN.md §13 and sequenced last, as Phase
6:

- **3.0** closed the gap the CLI's command set surfaces before any CLI code exists: an invite's
  player id is handed back once, in a response no client keeps, so `t42 uninvite` had nothing to
  address a revocation to. `invites.list_invites_for_game` plus `GET /games/{game_id}/invites`
  (seated callers only) fix that, giving `t42 invited`/`t42 uninvite` a finished surface.
- **3.1 (skeleton, profiles, credentials)** is complete: `src/t42/cli/main.py`'s `main(argv) -> int`
  returns an exit code for every expected failure rather than raising, so a command is a plain
  function a test can call; `_COMMANDS` starts empty, the table 3.4/3.5 append real commands to.
  `config.py` holds `~/.config/t42/config.json` (honouring `XDG_CONFIG_HOME`), written `0600`
  through a temp-file-and-rename, keyed by named **profiles** rather than one credential - a
  four-handed game needs four accounts, and the phase's own dogfood milestone is one person driving
  all four from one machine.
- **3.2 (HTTP client and exit codes)** is complete: `api.py`'s `ApiClient` decodes the
  `{"error": {"code","message"}}` envelope into a typed `ApiError` carrying `code`, which
  `errors.py`'s `exit_code_for` maps to the DESIGN.md §7.2 table - an unrecognised code exits 1
  rather than crashing. `ApiClient` is reached through a narrow `Transport` protocol rather than a
  concrete HTTP library, purely because `fastapi.testclient.TestClient` is built on httpx 0.28 while
  the CLI's own runtime dependency (the new `cli` optional extra) is httpx2; without that seam 3.7
  couldn't drive the CLI against the real app in-process.
- **3.3 (rendering)** is complete: `render.py` is pure `dict -> str` - no HTTP, no `argparse`, and
  (like every module under `t42.cli`) no import from `t42.engine`, `t42.storage` or boto3. It
  therefore keeps its own seat- and suit-name tables rather than importing `Seat`/`Suit`, the
  duplication DESIGN.md §7 explicitly trades for `t42.cli` being provably just a client. Its most
  load-bearing piece is `render_legal_moves`: it renders each entry of `view["legal_moves"]` as the
  literal `t42 ...` command that would submit it, which is formatting the server's own answer, not
  deriving anything - the same "client never decides" rule 3.1-3.2 already followed for turn order
  and legality.
- **3.4 (account and table commands)** is complete: the thirteen commands from DESIGN.md §7 that
  aren't play commands - `register`, `login`, `logout`, `whoami`; `rules save|list|show|replace|
  delete`; `create-game`, `join`, `open`, `invite`, `invited`, `uninvite`, `decline`, `invites` -
  wired onto `main.py`'s dispatch table. Two helpers back most of them: `context.py`'s
  `build_client`/`emit` turn `--profile`/`T42_PROFILE` into an authenticated `ApiClient` and give
  every handler the `--json`-vs-`render.py` split; `houserules.py`'s `add_house_rule_flags`/
  `house_rules_body` are shared by `create-game` and `rules save`/`replace` and only set a body key
  when its flag was actually given, so the server's own `HouseRulesRequest` defaults apply rather
  than the CLI restating one. `command.py`'s `Command` dataclass (`name`/`help`/`configure`/
  `handler`) is what `t42.cli.commands.COMMANDS` is built from, split out from `main.py` so the
  `commands` package can build values of it without a circular import back into `main.py`.
- **3.5 (play commands)** is complete: `status`, `games`, `bid`, `declare`, `play` - the commands
  that actually run a game. `bid`'s five spellings (a points bid, `pass`, an `N-marks` bid with an
  optional `--contract`, `confirm`, `decline`) all dispatch onto the one `kind`-discriminated
  move body (DESIGN.md §6; it rode on `/bid` until the move endpoints were merged - see "Multi-game
  hedges" below); `confirm`/`decline` are both `CONFIRM_BID` with `accept=True`/
  `False`, since there is no `DECLINE` kind on the wire. `declare`'s `trump=<suit>`/`trump=none`
  token and `play`'s `--declare` flag both parse through a new `parse_suit`, the input-side
  counterpart to `render.py`'s own suit-name table - duplicated rather than shared, the same
  tradeoff `houserules.py`'s seat-name table already made (DESIGN.md §7).
- **3.6 (running it locally)** is complete, and is the one piece of non-CLI code in the phase: the
  `Texas42` table definition, previously reachable only through a pytest fixture, is now
  `src/t42/storage/schema.py`'s `create_table(dynamodb, name)`, plus a
  `python -m t42.storage.schema` entry point for creating it outside a test run. The `table` (moto)
  and `real_table` (DynamoDB Local) fixtures in `tests/conftest.py` both build from it now, the
  same "one shared definition" property 2.7.3 wanted when it put the `OpenGames` GSI in a shared
  helper, just widened to include a local run. The README's local-run instructions use the entry
  point too, in place of an inline snippet that had drifted out of sync with the real schema and
  was missing the `OpenGames` GSI. It lives under `t42.storage`, not a `t42 dev` subcommand,
  because `t42.cli` may not import boto3 - the layering rule 3.7 checks by test.
- **3.7 (tests)** is complete, closing out Phase 3. `tests/cli/test_render.py` gained **the third
  leakage proof**: real games driven through the engine, every seat's `project()` view rendered,
  and no other seat's tile notation ever found in the text (1.5 proved this of `project()`, 2.5 of
  the wire, this of the screen - the only one a player actually looks at).
  `tests/cli/test_layering.py` is a static, `ast`-based check that no module under `t42.cli`
  imports `t42.engine`, `t42.storage` or boto3 - the claim three module docstrings already made,
  now enforced. `tests/cli/test_commands.py` drives `main(argv)` end to end against the real app
  in-process through `fastapi.testclient.TestClient` - the seam 3.2 built `Transport`/
  `transport_factory` for, since `TestClient` subclasses `httpx` 0.28's `Client` while the CLI's
  own runtime dependency is the separate `httpx2` package - with the moto `table` injected through
  `app.dependency_overrides`: one scripted walkthrough exercises every command's happy path, and
  one further test per DESIGN.md §7.2 exit-code bucket earns that exit code through a real command
  rather than a synthetic one. `tests/cli/test_cli_integration.py` (`@pytest.mark.integration`) is
  the dogfood milestone itself: four profiles register, seat and play a full game to `GAME_OVER`
  purely through CLI commands against real DynamoDB Local. `tests/cli/conftest.py` is new too,
  holding the `cli_app_client` fixture (`TestClient(app)` + the moto override) and the
  `_config_home` autouse fixture, promoted out of four files that had each hand-rolled an
  identical copy. Driving a real game to completion this way surfaced a genuine bug along the way:
  `render.py`'s `_render_trick` assumed `current_trick` was never `None`, but every response to
  the move that ends a game has exactly that shape - fixed, with a regression test.

Phase 4 (notifications) is complete; see ROADMAP.md for the full 4.1-4.7 breakdown.

- **4.1 (the send channel)** is complete: new top-level package `src/t42/notifications/`, importing
  only `boto3` (nothing from `t42.storage` or `t42.engine` yet - that permission is reserved for
  4.5's handler). `sender.py`'s `EmailSender` protocol has three implementations: `ConsoleSender`
  (the default, printing to stdout), `SesSender` (boto3 `sesv2`, with its client kept untyped the
  same way `t42.api.deps`'s DynamoDB resource is, avoiding a dependency on `sesv2` stubs), and
  `tests/notifications/_helpers.py`'s `FakeSender`, the recording fake, following the
  `FakeTransport` precedent of living in test helpers rather than in the module itself.
  `get_sender()` picks between them via `T42_EMAIL_SENDER`, mirroring `t42.api.deps`'s env-var
  shape but inverting its "no silent default" rule on purpose: an unset table name risks writing to
  production, but an unset sender just prints to stdout instead of emailing, so `console` is a safe
  default rather than a `RuntimeError`. `messages.py` holds three pure `dict -> (subject, body)`
  renderers, one per notification kind from DESIGN.md §8 (your turn, game over, invite) - no I/O,
  the same property `t42/cli/render.py` has for the same reason. Nothing called these yet at the
  time; the Streams wiring (4.4) and the handler that decides who to notify and assembles the
  dict (4.5) are still ahead.
- **4.2 (contact channels and email verification)** is complete: `ContactChannel` gains
  `notify: bool = True`, decoded through `.get("notify", True)` so pre-4.2 items need no
  migration. `accounts.py` gains `add_contact`/`remove_contact`/`set_contact_notify` (a
  read-modify-write over the `contacts` list on `PROFILE`, deliberately not conditioned on the
  list being unchanged - contention between a player's own devices is rare and low-stakes, unlike
  game state) and `begin_verification`/`complete_verification`, which mint and redeem a
  single-use `VERIFY#<sha256(token)>/TOKEN` item mirroring the bearer-token shape but with no
  reverse index, since nothing needs to list a player's pending verifications. A verification
  token is deleted the moment it's looked at, whether or not it turns out to be expired, so it can
  never be replayed. This is `t42.notifications`'s first real caller: `POST
  /players/me/contacts/{address}/verification` sends synchronously through a new `EmailSenderDep`
  (mirroring `TableDep`'s override shape) using a fourth renderer, `render_verify_contact` - the
  odd one out in `messages.py`, since it isn't driven by a `PLAYER#` item transition the way the
  three from 4.1 are. `POST /contacts/verify` takes no bearer token at all, the same shape
  `register`/`sign_in` already use for working signed-out, since the token in the body is itself
  the credential (DESIGN.md §6.1).
- **4.3 (password reset)** is complete: a sixth item type, `RESET#<sha256(token)>/TOKEN`, carrying
  only `player_id` and `expires_at` - no `address`, unlike `VERIFY#`, since a reset is tied to the
  player rather than a channel. Minting and redeeming a single-use expiring token was, by this
  point, the same shape twice over (`begin_verification`/`complete_verification` from 4.2), so
  `accounts.py` factors it into `_mint_single_use_token`/`_redeem_single_use_token` - parameterized
  on the partition-key function, the TTL, the extra fields carried on the item, and (for redeem)
  which exception to raise - and `begin_password_reset`/`complete_password_reset` are now the
  second, thinner callers alongside the rewritten verification pair. Completing a reset also
  revokes every device via the existing `list_tokens`/`revoke_token`, looped rather than
  transactional (DynamoDB's transaction size limit doesn't accommodate an unbounded device count,
  and a player has few enough that this is the same bounded-fan-out trade-off `GET
  /players/me/invites` already made in 2.7.2) - DESIGN.md §6.1's "revoke every issued token" is
  therefore a same-request best effort, not an atomic guarantee, and the docstring says so rather
  than overclaiming. The TTL itself is shorter than verification's (one hour against 24) since
  redeeming one grants a new password outright rather than merely proving address ownership.
  `POST /password-resets` answers `202` unconditionally
  - unknown username, no verified contact, and a mailed reset all look identical on the wire, the
  same refusal to distinguish cases `authenticate` already makes (though not a fully timing-safe
  one: unlike `authenticate`'s constant-time dummy hash, the real-username path does strictly more
  work than the unknown-username one, a residual signal closing which would cost an async send
  queue this endpoint's risk profile doesn't yet justify) - and mails only the first **verified**
  email contact, deliberately not every one, so a mid-flight send failure across a multi-contact
  player's addresses can never surface as a raised exception after a token already reached some of
  them. `notify` (the per-channel mute) is deliberately not consulted, since this is a
  security-critical message the player just triggered by name, not a gameplay notification. `POST
  /password-resets/confirm` takes no bearer token, the same shape `POST /contacts/verify` already
  uses, since the token in the body is itself the credential. `request_password_reset` is the one
  handler that calls `player_for_username` and *catches* its `PlayerNotFound` rather than letting
  it propagate to a 404, the opposite of every other caller (`POST /games/{id}/invites`) - the
  whole point being that an unknown username produces the same `202` as a known one. `messages.py`
  gains a fifth renderer,
  `render_password_reset`, alongside a gap closed in passing: `render_verify_contact` had no test of
  its own since 4.2 landed it - both now have one in `tests/notifications/test_messages.py`.
- **4.4 (Streams and the local pump)** is complete: `schema.create_table` enables the table's
  stream (`NEW_AND_OLD_IMAGES`), picked up by the moto and DynamoDB Local fixtures alike since both
  build from that one function. Two new modules are pure plumbing, deciding nothing: `records.py`'s
  `transition_from_record` turns one raw stream record (still DynamoDB-JSON - the one place that
  shape is visible outside the resource-level `Table` API) into a `Transition(event_name, keys, old,
  new)` of plain data via `boto3.dynamodb.types.TypeDeserializer`; `pump.py`'s `poll()` (`python -m
  t42.notifications.pump`) discovers the stream's shards, tracks a `TRIM_HORIZON` iterator per open
  shard, and calls a handler with a `{"Records": [...]}` batch shaped exactly like a real Lambda
  event-source mapping would deliver - the local and deployed paths exercise the same entry point.
  A shard that closes (no `NextShardIterator`) goes into a `drained` set rather than just being
  dropped from the tracked iterators: `describe_stream` keeps listing a closed shard for a while,
  so without that memory the next cycle's discovery step would treat it as new and re-read it from
  `TRIM_HORIZON` forever, redelivering the same records on every cycle - caught by code review
  before merge, with `tests/notifications/test_pump.py`'s fake multi-cycle streams client (a real
  moto session can't deterministically close a shard on demand) proving the regression and the fix
  both: the test fails against the pre-fix code and passes against the shipped version.
  `handler.py`'s `lambda_handler` is a stub (`NotImplementedError`, no tests, the project's existing
  convention for a phase's not-yet-built consumer) that exists purely so `pump.py` has something to
  call; `poll()` itself takes `handler` as an injectable parameter, so its own tests exercise the
  real polling/decoding path against a spy rather than the stub. `tests/notifications/
  test_layering.py`, pulled forward from ROADMAP.md 4.7 since it's cheap and guards exactly the
  modules this phase adds, is an `ast`-based check that nothing under `t42.notifications` imports
  `t42.engine` or any of `t42.storage`'s `repository`/`codec`/`replay` (only `accounts` is allowed).
  **A real regression surfaced and was fixed in the same phase**: moto's `TransactWriteItems`
  deep-copies the whole table - stream history included - as a rollback backup before every call, so
  once the table's stream carries the accumulating history of a long game's many
  `repository.append()` transactions, that per-call deep-copy cost grows with the call count,
  making the whole fast suite's cost roughly quadratic in the heaviest test's move count. The one
  test genuinely exposed to it, `tests/storage/test_repository.py`'s full-game/replay test, played
  a complete `marks_to_win=7` game (hundreds of moves) specifically to prove a mid-game re-deal
  round-trips correctly; dropping it to `marks_to_win=2` keeps that same proof (still asserts a
  version delta of 2, i.e. a real re-deal, for its fixed seed) while cutting the suite's added cost
  from unusable (multiple minutes) to about ten seconds. Real DynamoDB (and DynamoDB Local) has no
  such behavior - this is a moto-only cost, confirmed against 5.2.2, the latest release - so nothing
  about the fix reflects a real production concern, only a test-tool one.
- **4.5 (the handler)** is complete: `handler.py`'s stub is replaced by a pure classifier,
  `notifications_for(records)`, and an I/O half, `send_notifications(table, sender, records)`,
  injectable the same way `pump.poll()`'s `handler`/`sleep` are. Three storage-layer gaps closed
  first, since ROADMAP.md 4.5 named them as this sub-phase's own prerequisite work rather than
  separate phases: `repository.py`'s `start_game`/`append` now stamp `version` onto every
  `PLAYER#` item - reusing the same version number already being written to `STATE` in that
  transaction, not a separate counter - so the handler has something to dedupe a redelivered
  stream record against; `append` denormalizes final `marks` onto `META` on the game-ending write,
  in the same `{"north_south": int, "east_west": int}` shape `projection.py` already uses, so the
  handler can render a game-over email without ever reading `STATE`; and `invites.py`'s
  `invite_player` gains a keyword-only `inviter_username`, stored as `invited_by` on the invitee's
  own item, with `app.py`'s invite handler resolving it off the `lobby` it already has in scope.
  The classifier claims a transition (`notified_version` on `GAME#` items, a plain `notified` flag
  on `INVITE#` items, since those have no version to compare against) *before* resolving a
  recipient, deliberately: `notified_version` means "this transition was processed," not "an email
  went out," which is both a simpler invariant and cheaper on the redelivery `pump.py`'s own
  restart-reopens-at-`TRIM_HORIZON` behavior can produce. A transition missing `version` (a
  pre-migration item) is skipped rather than claimed with `version=None`, which would make the
  dedup condition permanently unsatisfiable. **A real bug surfaced by end-to-end testing against
  real DynamoDB Local, not caught by any existing test**: real DynamoDB Streams drops the `"M"`
  type wrapper for an empty map attribute - `HouseRules.contract_options` is the common case,
  since it's `{}` on any game with no contract options set - so a bare `{}` shows up nested inside
  `OldImage`/`NewImage` instead of `{"M": {}}`, which `boto3.dynamodb.types.TypeDeserializer`
  treats as malformed input and raises on, crashing the whole batch. moto's stream implementation
  never reproduces this (confirmed against moto 5.2.2), so it was invisible to every test written
  through 4.4. `records.py`'s `_decode` now walks the attribute-value tree ahead of deserializing
  and restores the dropped wrapper (`_restore_empty_map_wrapper`); empty lists are unaffected,
  arriving as `{"L": []}` with the wrapper intact, confirmed by inspecting real captured stream
  records rather than assumed. `tests/notifications/test_handler.py` is new, covering each rule
  firing exactly once, a duplicate record sending nothing (both kinds of dedup marker), an
  unverified/muted/absent/non-email contact sending nothing while still claiming the transition,
  the handler's own dedup write producing a `MODIFY` that matches neither rule (the no-loop
  guarantee), `join_seat`'s initial `INSERT` not misfiring as a turn flip, and `lambda_handler`'s
  delegation to `send_notifications`. Verified against a real signup-to-first-move game driven
  through a locally hosted API, a real DynamoDB Local table and stream, and `pump.py` with
  `T42_EMAIL_SENDER=console` - the console print of "It's your turn" for the correct seat, with
  `notified_version` on the item matching the `STATE` version that produced it, is what surfaced
  the empty-map bug above and confirmed the fix.
- **4.6 (CLI commands)** is complete: `t42 contacts`, `t42 contact add|remove|verify|confirm|
  mute|unmute`, `t42 forgot-password` and `t42 reset-password` join `src/t42/cli/commands/
  account.py`, following 3.4's `register`/`login`/`rules` shape with no new machinery -
  `contact confirm` and `reset-password` reuse `build_client(..., require_auth=False)`, the same
  seam `register`/`login` already use, since the token each one carries is itself the credential
  (DESIGN.md §6.1). `render.py` gains `render_contact`/`render_contact_list`, and factors the
  shared `_render_contact_line` helper out of `render_profile`'s own contact loop so it also shows
  mute status - `render_profile` had no way to reflect a mute before this, which would have left
  `t42 whoami` silently stale the moment `t42 contact mute` existed. `tests/cli/_helpers.py`'s
  `FakeResponse.json()` no longer raises on a `None` body: real FastAPI serializes a `None`-typed
  `202`/`204` response body as JSON `null`, which `.json()` decodes to `None` without error, and
  nothing here previously exercised a non-204 success response with an empty body to notice the
  mismatch. `tests/cli/conftest.py`'s `cli_app_client` now also overrides `t42.notifications.
  get_sender` with a `FakeSender`, exposed as a `fake_sender` fixture the same way `tests/api/
  conftest.py`'s already does - what lets `test_commands.py`'s full walkthrough capture a mailed
  verification/reset token and complete both round trips for real, the same way `tests/api/
  test_contacts.py`/`test_password_reset.py` already do at the API layer.
- **4.7 (tests)** is complete, closing out Phase 4. Every other test the phase named had already
  been pulled forward as its own sub-phase landed - `test_messages.py`, `test_handler.py` and
  `test_layering.py` (4.1/4.4/4.5), `tests/api/test_contacts.py`/`test_password_reset.py` (4.2/4.3)
  and the CLI round trip in `tests/cli/test_commands.py`/`test_commands_account.py` (4.6) - so the
  one thing left was the phase milestone itself: `tests/notifications/test_integration.py`
  (`@pytest.mark.integration`), which registers four real players against real DynamoDB Local,
  verifies three of their email contacts over real HTTP and deliberately leaves the fourth's
  unverified, plays a full game move by move through the real API, and after every move drains the
  real stream through `t42.notifications.pump.poll` into `send_notifications` - not a spy standing
  in for either. `poll` opens a fresh `TRIM_HORIZON` iterator on every call rather than persisting
  one, so every drain re-reads the stream's entire history so far; the test leans on that rather
  than working around it, since it means every single drain - not just a dedicated one at the end -
  is a real proof that `_claim`'s conditional write makes redelivery a no-op. One real
  `time.sleep` sits between two moves rather than a mocked clock, earning DESIGN.md §10's "verify
  against real play across a delay" without provisioning anything. A first assumption that the
  initial deal produces no notification turned out to be wrong and the test caught it: joining a
  seat inserts a fresh `PLAYER#`/`GAME#` item, but dealing the first hand on the fourth join then
  *updates* that already-existing item to flip `is_my_turn` for whoever bids first - a genuine
  false-to-true transition, so the very first actor legitimately gets a "your turn" email, and the
  test now asserts that rather than assuming silence. With this, Phase 4's exit criteria
  (ROADMAP.md) are all met.

**Multi-game hedges** (DESIGN.md §11.1) are not a phase and did not come from ROADMAP.md. They are a
small set of changes made in answer to "what would a second game with its own rules engine affect?",
taken while nothing was deployed and therefore while anything written to disk or exposed on the wire
was still free to change. **Supporting a second game is still not a goal**, and nothing dispatches on
which game is being played - there is no `Game` protocol, no game registry, no per-kind codec. The
rule the work followed, and the thing to remember before extending any of it:

> Hedge what goes on disk or on the wire. Do not hedge code shape.

A discriminator missing from a persisted item is a backfill over an immutable event log (invariant 6);
a missing abstraction in `codec.py` is a refactor, no dearer later and cheaper once a second game's
real shape is known rather than guessed. What that bought, concretely:

- **`t42.engine.GAME_KIND`** (`"texas42"`) is written as `kind` on `GAME#/META`, `GAME#/STATE`,
  `PLAYER#/GAME#` and `PLAYER#/RULESET#`, and read back with a default so a pre-`kind` item still
  decodes. `EVENT#` items carry none on purpose: they are partition-scoped under a `GAME#` whose
  `META` already says the kind, and the log is never replayed without the `config`/`players` that
  come from that same item. `update_rule_set` deliberately does *not* write `kind` - a set's game is
  fixed at creation, and an edit that could change it would be the bug the field exists to prevent.
- **`GSI1PK` is `OPEN#<kind>`**, not a bare `OPEN`; `list_open_games` takes a `kind` keyword.
- **`t42.storage.lobby` no longer derives its table size from the engine.** `seat_count` lives on
  `META`, `Lobby.seats` is keyed by plain `int`, and `is_full` counts against the stored number
  rather than `len(Seat)`. `_deal` is the one place `Seat` re-enters, because it is the call into the
  engine. This was the only hard four-handed assumption outside `t42.engine`.
- **`META.scores` replaced `META.marks`** as an open `{label: int}` map keyed on `team.name.lower()`
  - the identical `{north_south, east_west}` shape for 42, but readable without knowing that. With
  `handler._read_scores` and `messages.render_game_over` iterating it, **`t42.notifications` now
  encodes nothing about how any one game scores**, on top of already importing nothing from
  `t42.engine`.
- **`POST /games/{game_id}/moves` replaced `/bid`, `/contract` and `/play`**, with `MoveRequest`
  discriminating all five move bodies on `kind`. This is a simplification independent of any second
  game: all three handlers were the same one-liner and `_submit` never inspects the move, so the URLs
  carried nothing the body did not. The payoff is that `MoveRequest`'s `kind` tags are exactly the
  ones `project()` stamps on each `legal_moves` entry, so **a projected legal move is a valid request
  body verbatim** - `tests/api/_helpers.py`'s `submit` used to hold a kind-to-path table and now
  posts the move unchanged, which is the cleanest evidence the merge was right.

Two things documented rather than changed. `t42.storage.replay` reconstructs a game by feeding
recorded deals back through `Random.shuffle`, so it silently depends on `game._deal_hand` calling
`rng.shuffle` **exactly once per hand** and slicing in `Seat` order; a dealer that violated that
would not raise, it would replay a different game. Both docstrings now say so. And the natural
extraction boundary, if a second game is ever actually wanted, is small: `new_game`/`apply_move`/
`legal_moves`/`project`, a codec quintuple, and `events_for_move` - everything `t42.storage` does
with a `GameState` beyond encode/decode is three accessors (`.phase`, `.players`+`.to_act`,
`.marks`).

## Layout

```
src/t42/engine/     pure rules library (Phase 0 - complete)
    __init__.py     re-export hub, plus GAME_KIND ("texas42"): what this engine is, as a value
                     storage and the API write down (multi-game hedges)
    dominoes.py     the 28 tiles, a-b notation
    suits.py        trump membership, led suit, follow, ranking, doubles-own-suit variant
    scoring.py      count-domino values, hand point totals
    house_rules.py  per-game HouseRules (rule variants), incl. contract_options
    trick_rules.py  shared follow-suit legality and highest-trump-or-led-suit winner
    contracts/      Contract protocol, name-keyed registry, all six contract strategies
    state.py        frozen dataclasses: GameState, HandState, Trick, PendingBid
    events.py       immutable log events (the persistence contract)
    moves.py        what a client may propose
    bidding.py      auction state machine, incl. plunge confirmation and dealer-must-bid
    tricks.py       trick legality and resolution, active-seat-aware for nello's 3-handed hands
    game.py         new_game / apply_move / legal_moves entry points
    errors.py       RulesError, IllegalMove, OutOfTurn, UnknownContract
    projection.py   project(state, player_id): the hidden-information gate (1.5 - complete)
src/t42/storage/    DynamoDB event log + materialized state   (Phases 1, 2 and 2.7, complete)
    _dynamo.py      shared boto3 plumbing: transact_write, Decimal and str narrowing
    codec.py        GameState/HouseRules/Event <-> plain attribute maps (1.1)
    events.py       move/deal -> Event (write direction)                (1.2)
    replay.py       Event log -> GameState via real new_game/apply_move (1.2)
    repository.py   start_game/get_state/append/find_request, GameStatus (1.3, 1.4, 2.2);
                     STATE carries `kind`, META carries `scores` (multi-game hedges)
    lobby.py        create_pending_game/join_seat/list_games_for_player/
                     list_open_games, Visibility                  (2.2, 2.7.2, 2.7.3);
                     int-keyed seats and a stored seat_count      (multi-game hedges)
    accounts.py     players, passwords, per-device bearer tokens, contact channels,
                     email verification + password reset      (2.1, 2.7.2, 4.2, 4.3)
    rule_sets.py    named HouseRules saved under a player's own partition, tagged with
                     the game they are for (2.7.1, multi-game hedges)
    invites.py      GAME#/INVITE# + PLAYER#/INVITE# permission-grant CRUD (2.7.2)
    errors.py       GameNotFound, VersionConflict, SeatTaken, InvalidToken, ...
    schema.py       create_table(dynamodb, name); `python -m t42.storage.schema` (3.6);
                     stream enabled, NEW_AND_OLD_IMAGES (4.4)
src/t42/api/        FastAPI app behind Mangum                 (Phases 2, 2.7 and 4.2, ongoing)
    app.py          the twenty-eight endpoints; `_submit` is the one write path behind the one
                     move endpoint, POST /games/{id}/moves (2.4, multi-game hedges)
    deps.py         table/sender handles and the bearer-token dependency, all overridable (2.4, 4.2)
    schemas.py      pydantic request/response bodies; MoveRequest discriminates every move
                     on `kind` (2.3, multi-game hedges)
    errors.py       domain exception -> status code + machine-readable code          (2.3)
    lambda_handler.py  `Mangum(app)`, nothing else                                   (2.6)
src/t42/cli/        thin command-line client                  (Phase 3, complete)
    main.py         argparse + dispatch; `main(argv) -> int` returns rather than raises (3.1)
    config.py       ~/.config/t42/config.json, named profiles, 0600 (3.1)
    api.py          ApiClient/Transport/HttpTransport, the `{"error": ...}` envelope (3.2)
    errors.py       DESIGN.md §7.2's code -> exit-status table (3.2)
    render.py       pure dict -> str renderers, incl. legal moves as runnable commands (3.3)
    command.py      the `Command` shape `commands.COMMANDS` is built from (3.4)
    context.py      build_client/emit, shared by every command handler (3.4)
    houserules.py   --contracts/--marks/--set flags shared by create-game and rules (3.4)
    commands/       account.py, tables.py, rules.py (3.4); play.py (3.5)
src/t42/notifications/  the send channel (Phase 4, complete). Imports nothing from t42.engine and,
                    since the multi-game hedges, encodes nothing about how any game scores either
    sender.py       EmailSender protocol; ConsoleSender/SesSender, chosen by env var (4.1)
    messages.py     pure dict -> (subject, body) renderers, one per notification kind
                     (4.1: turn/game-over/invite; 4.2 adds render_verify_contact;
                     4.3 adds render_password_reset). render_game_over iterates an open
                     {label: int} scores map rather than naming partnerships
    records.py      transition_from_record: raw DynamoDB Streams record -> Transition (4.4);
                     tolerates real DynamoDB Streams dropping the "M" wrapper on an empty map (4.5)
    handler.py      lambda_handler over notifications_for (pure classifier) and
                     send_notifications (injectable I/O): who gets emailed, when (4.5)
    pump.py         poll()/main(); `python -m t42.notifications.pump` polls the stream
                     locally and calls the handler with a Lambda-shaped batch (4.4)
tests/conftest.py   the `table` (moto) and `real_table` (DynamoDB Local via testcontainers)
                    fixtures, shared by tests/storage/ and tests/api/
tests/engine/       mirrors the engine modules; test_full_game.py is the Phase 0 milestone demo;
                    _helpers.py's `drive_to_game_over`/`prefer_contract` (plus its `on_state`/
                    `on_transition` hooks) are reused by tests/storage/ to generate real games for
                    the codec, replay and repository round-trip tests
tests/storage/      mirrors src/t42/storage/; _helpers.py's `started_game` reaches a dealt game
                    the way a real one is reached, through the lobby rather than around it.
                    test_game_kind.py covers the multi-game hedges: `kind` written on all four
                    item types and read back tolerantly, `is_full`/`open_seats` following the
                    stored seat_count rather than len(Seat), and the per-kind OpenGames partition
tests/api/          contract tests over FastAPI's in-process TestClient, with `table` and (4.2)
                    `get_sender` injected via `app.dependency_overrides`; _helpers.py's
                    `Client`/`play_until` drive a whole game over HTTP, and every test goes
                    through the public API only. test_contacts.py (4.2) covers contact
                    channels and verification, including redeeming a token with no bearer
                    token at all - proving `POST /contacts/verify` really works signed-out.
                    test_password_reset.py (4.3) covers the same signed-out shape for
                    `POST /password-resets`/`/password-resets/confirm`, plus the `202`
                    regardless-of-username-existence contract
tests/cli/          mirrors src/t42/cli/; conftest.py's `cli_app_client` (`TestClient(app)` + the
                    moto `table` override) and `_config_home` are shared by every file here.
                    _helpers.py's `FakeTransport` fakes `t42.cli.api.Transport` with no network,
                    letting the per-command test_commands_*.py files exercise handlers directly;
                    `run_json`/`whose_turn_via_cli`/`play_full_game_via_cli` instead point
                    `context.transport_factory` at a real `TestClient`, letting test_commands.py
                    and the Docker-backed test_cli_integration.py drive `main(argv)` against the
                    real app. test_render.py's leakage proof, test_layering.py and test_main.py
                    round out the suite (3.7)
tests/notifications/  mirrors src/t42/notifications/; _helpers.py's `FakeSender` records sends
                    with no network, following the `FakeTransport` precedent (4.1).
                    test_records.py (4.4) decodes hand-built DynamoDB-JSON records, including the
                    4.5 regression for a dropped empty-map type wrapper; test_pump.py (4.4) drives
                    poll() against the moto `table` fixture, which genuinely implements the Streams
                    API; test_layering.py (4.4, pulled forward from 4.7) is the
                    `t42.notifications`-specific ast import guard; test_handler.py (4.5) covers
                    notifications_for's classification rules and send_notifications' dedup/
                    recipient-filtering/rendering against the moto `table` fixture and `FakeSender`
```

## Commands

Requires [uv](https://docs.astral.sh/uv/); Python 3.13 is fetched automatically.

```bash
uv sync --extra dev            # venv + dev tooling
uv run pytest                  # tests (fast; excludes the Docker-backed integration suite)
uv run pytest -m integration   # integration tests against a real DynamoDB Local (needs Docker)
uv run mypy                    # strict type check over src and tests
uv run ruff check .            # lint
uv run ruff format .           # format
```

Run `pytest`, `mypy`, `ruff check` and `ruff format` before considering a change done; CI runs the
same set, plus the integration suite as its own step.

## Workflow

Small fixes and documentation-only changes (typos, a `CLAUDE.md`/`DESIGN.md`/`ROADMAP.md` update, a
one-line bug fix) can be committed and pushed directly to `main`. Anything larger, and especially
anything that changes production behavior (a new module, a new endpoint, a schema or event-shape
change, anything under `src/`) goes to a branch with a pull request instead.

## Invariants

These are the rules that keep the design working. Breaking one is a design change, not a detail.

1. **The engine is pure.** Nothing under `t42.engine` may do I/O, import boto3, or import from
   `t42.storage`, `t42.api` or `t42.cli`. It takes a state plus a proposed move and returns a new
   state or raises. Randomness is injected (`rng: Random`), never ambient.
2. **State is immutable.** State types are frozen dataclasses and functions return new state.
   Never mutate in place, so replay, caching and comparison stay sound.
3. **Rule variants are per-game data, never globals.** Anything that differs between rule sets
   lives on `HouseRules` and is threaded through as an argument. Two games under different variants
   must score and replay correctly in the same process. This includes each contract's own terms:
   a bid minimum belongs in `contract_options` (DESIGN.md §5.1), never as a class attribute on the
   registered contract, because that singleton is shared by every game in the process.
4. **Contracts are registered, not switched on.** Behaviour that differs between standard, nello,
   plunge, sevens and splash goes behind the `Contract` protocol in `contracts/`. Do not add
   `if contract == "nello"` branches to the bidding or trick code.
5. **Hidden information has exactly one gate.** `projection.project()` is the only thing that
   decides what a player may see. No handler or client may hand out anything else, and nothing
   may re-declare the projected shape - a response model mirroring it would be a second
   definition free to drift from the gate, so `GameResponse.view` passes it through opaquely.
   `tests/api/test_moves.py` sweeps every response of a whole game to prove nothing leaks at the
   wire, not just at `project()`.
6. **Events are the persistence contract.** The dataclasses in `events.py` are what gets written to
   DynamoDB. Changing a field is a data migration, so treat their shape as an interface.

## Conventions

- Line length 100, ruff lint rules per `pyproject.toml`, mypy `strict` over both `src` and `tests`.
- Prefer frozen slotted dataclasses and plain functions over classes with behaviour, except for
  the contract strategies, where the protocol is the point.
- Tests are table-driven or property-based where the input space is enumerable; the engine gets the
  heaviest test investment, since this is where domino implementations go subtly wrong.
- Stubs raise `NotImplementedError("Phase N: <what>")` and have no tests written against them, so a
  green suite always means what it says. Only `projection.py` is still a stub.
- Contract rule variants (plunge/splash doubles-and-marks minimums, nello doubles handling,
  sevens tie-breaking, all-pass) are resolved and recorded in DESIGN.md §12 - read there before
  assuming a different regional rule.
