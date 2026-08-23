# Tricksy

A server-authoritative, asynchronous server for trick-taking games, played over minutes or hours
rather than in real time. One game ships today:
[Texas 42](https://en.wikipedia.org/wiki/42_(dominoes)) - partnership domino trick-taking with
nello, plunge, sevens and splash. See [DESIGN.md](DESIGN.md) for the architecture and
[ROADMAP.md](ROADMAP.md) for the phase-by-phase breakdown.

## Status

Phases 0 through 4 are complete. There is a pure rules engine covering all six contracts, durable
storage in DynamoDB with the event log as the source of truth, an HTTP API over both, a CLI client
that plays a whole game, and email notifications driven off the table's stream.

- **Phase 0, rules engine.** Dealing, the full auction (including plunge confirmation and the
  dealer-must-bid rule), all six contracts, trick resolution and scoring, with a whole game
  runnable in memory.
- **Phase 0.5, house rules.** Every rule variant is per-game data on one validated `HouseRules`
  value, including each contract's own bid entry bar and the declared-lead privilege.
- **Phase 1, persistence.** Event log plus a materialized state item, optimistic concurrency,
  idempotent writes, replay that reruns real `apply_move` calls rather than reimplementing them,
  and the player-specific projection.
- **Phase 2, API.** FastAPI behind a Mangum adapter: accounts with per-device bearer tokens, a
  lobby, and the move endpoints. A full 4-player game runs signup to game-over over HTTP.
- **Phase 2.7, tables.** Saved house-rule sets, invites by username, public or invite-only tables,
  and a browse of open ones. Ahead of the CLI so its command set was written once against the
  finished surface.
- **Phase 3, CLI client.** The full command set, with a four-profile game played start to finish
  from one machine.
- **Phase 4, notifications.** DynamoDB Streams to a handler to SES, for the three things worth an
  email: your turn, you've been invited, your game is over. Carries contact channels, email
  verification and password reset.

Next: **Phase 5**, deployment - the table, the API and the notifier provisioned from code with AWS
CDK, and a real game played against a real endpoint (DESIGN.md section 14). Nothing is deployed
yet; everything above runs locally against DynamoDB Local.

## Layout

```
src/tricksy/games/texas42/     pure rules library: no I/O, no AWS, no dependency on the layers below
src/tricksy/storage/    DynamoDB event log, materialized state, lobby and accounts
src/tricksy/api/        FastAPI app and its Lambda entry point
src/tricksy/cli/        thin command-line client  (Phase 3)
src/tricksy/notifications/  stream handler, message renderers and the email sender  (Phase 4)
tests/
```

The engine is the only place game rules live, and every client type consumes the same projected
view, so hidden-information rules exist in exactly one place. See the invariants in
[CLAUDE.md](CLAUDE.md) before making structural changes.

## Development

Requires [uv](https://docs.astral.sh/uv/). Python 3.13 matches the AWS Lambda runtime and is
fetched automatically.

```bash
uv sync --extra dev            # create the venv and install dev tooling
uv run pytest                  # tests (fast; excludes the Docker-backed integration suite)
uv run pytest -m integration   # integration tests against real DynamoDB Local (needs Docker)
uv run mypy                    # type check (strict, over src and tests)
uv run ruff check .            # lint
uv run ruff format .           # format
```

CI runs all of the above, with the integration suite as its own step.

## Running the API locally

```bash
docker run -d --name tricksy-ddb -p 8123:8000 amazon/dynamodb-local:latest

export AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local AWS_DEFAULT_REGION=us-east-1
export TRICKSY_TABLE_NAME=Tricksy TRICKSY_DYNAMODB_ENDPOINT=http://localhost:8123

uv run python -m tricksy.storage.schema

uv run uvicorn tricksy.api.app:app --reload --port 8765
```

Interactive API docs are then at `http://localhost:8765/docs`. Register a player, create a game,
and share the six-character game code with three others to fill the seats:

```bash
curl -X POST localhost:8765/players -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"correct-horse-battery"}'

curl -X POST localhost:8765/games -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" -d '{"seat":0}'
```

To see notifications as well, run the stream pump in a second shell with the same environment
exported. It polls DynamoDB Local's stream and calls the same handler AWS will, printing each
email to stdout rather than sending it:

```bash
TRICKSY_EMAIL_SENDER=console uv run python -m tricksy.notifications.pump
```
