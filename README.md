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

## Deployment

The stack (`infra/`, AWS CDK in Python) provisions the table, both Lambda functions, the HTTP
API, the notifier's DynamoDB Streams trigger and dead-letter queue, SES identities, and the
operational alarms and budget - see DESIGN.md section 14. There is one stack, one region,
deployed by hand: no pipeline, no staging environment.

### Prerequisites

- An AWS account, with credentials available to the CDK CLI (`aws configure`, or any of the
  usual credential sources).
- The CDK CLI itself: `npm install -g aws-cdk` (a one-time global install; the project's own
  Python dependencies, including `aws-cdk-lib`, come from the `infra` extra -
  `uv sync --extra infra`).
- Docker running locally. The Lambda deployment package is built inside a container so it
  targets Lambda's Linux/arm64 runtime rather than the host's.
- `cdk bootstrap`, once per AWS account and region:
  ```bash
  cd infra && uv run --extra infra cdk bootstrap
  ```

### Before the first deploy

`cdk synth`/`cdk deploy` read three environment variables to build SES identities and the
alarm/budget subscription. None of these are committed to source - this repo is public:

```bash
export TRICKSY_SES_FROM_ADDRESS=noreply@yourdomain.example       # the sending identity
export TRICKSY_SES_DOGFOOD_RECIPIENTS=you@example.com,a-friend@example.com  # optional
export TRICKSY_OPERATOR_EMAIL=you@example.com                    # alarms and budget go here
```

`TRICKSY_SES_FROM_ADDRESS` and `TRICKSY_OPERATOR_EMAIL` are required - `cdk synth` fails loudly
if either is unset. `TRICKSY_SES_DOGFOOD_RECIPIENTS` is optional: it only pre-registers addresses
as SES identities so the SES sandbox will accept sending to them, and can be added in a later
deploy once you've recruited players.

### Deploy

```bash
cd infra
uv run --extra infra cdk deploy
```

This provisions everything, but SES and SNS both mail a confirmation link to every address you
gave them - the from-address, each dogfood recipient, and the operator's own address for the
alerts topic - and each is unusable until a human clicks its link. That's the one step a
deploy can't do for you: check every inbox above after the first deploy.

`cdk deploy`'s output includes `ApiUrl`, the stack's HTTP API endpoint - point the CLI at it and
nothing else about using it changes:

```bash
tricksy --api-url https://<id>.execute-api.<region>.amazonaws.com register ...
# or: export TRICKSY_API_URL=https://<id>.execute-api.<region>.amazonaws.com
```

Prefer the env var: a profile in `~/.config/tricksy/config.json` does not carry the API URL, so
`--api-url` or `TRICKSY_API_URL` is the only way to point the CLI anywhere but its default.

### Milestone checklist: a real game against real infra

ROADMAP.md 5.7's milestone is a human checklist, not an automated test - it needs an AWS account
and real inboxes, so the automated suites stay pointed at DynamoDB Local. Work through it after a
first deploy.

**1. Pre-flight.** From a clean checkout of `main`:

```bash
uv sync --extra dev --extra infra --extra cli
uv run pytest && uv run pytest -m integration
aws sts get-caller-identity          # the account and region you mean to deploy to
```

**2. Choose addresses.** The SES sandbox delivers only to verified identities, so every player's
inbox must be listed in `TRICKSY_SES_DOGFOOD_RECIPIENTS` - adding it as a contact in the app is not
enough. Playing all four seats yourself, plus-addressing (`you+p1@gmail.com`, ...) gives four
identities, each confirmed separately, landing in one inbox. The from-address must be one you can
receive mail at, since SES mails it a confirmation link too. Sent through SES without a domain of
your own, mail may land in spam - check there before deciding a notification never arrived.

**3. Deploy** as above, and confirm `cdk deploy` needed no console clicking at all.

**4. Confirm every identity.** Click each SES confirmation (the from-address and every dogfood
recipient) and the SNS subscription mailed to `TRICKSY_OPERATOR_EMAIL`, then check:

```bash
aws sesv2 list-email-identities \
  --query 'EmailIdentities[].[IdentityName,VerificationStatus]' --output table   # all SUCCESS
aws sns list-subscriptions \
  --query "Subscriptions[?contains(TopicArn,'TricksyStack')].[Endpoint,SubscriptionArn]"
```

A subscription still awaiting its click shows `PendingConfirmation` in place of an ARN.

**5. Four players, four verified contacts.** With `TRICKSY_API_URL` exported, for each of
`p1`-`p4`:

```bash
tricksy --profile p1 register alice
tricksy --profile p1 contact add you+p1@gmail.com
tricksy --profile p1 contact verify you+p1@gmail.com    # mailed through real SES
tricksy --profile p1 contact confirm <token-from-email>
tricksy --profile p1 contacts                           # shows the contact as verified
```

**6. Seat the table through invites**, so the invite email is exercised along the way:

```bash
tricksy --profile p1 create-game --visibility invite_only    # note the game code
tricksy --profile p1 invite <code> bob                        # and carol, and dave
tricksy --profile p2 join <code> --seat 1                     # p3 seat 2, p4 seat 3
```

- [ ] Each invitee receives an invite email
- [ ] The fourth join deals, and the first bidder receives a "your turn" email

**7. Play to `GAME_OVER`.** `tricksy --profile pN status` lists the legal moves as runnable
commands.

- [ ] Each move produces a "your turn" email for whoever acts next
- [ ] At least once, leave a gap long enough that no container stays warm - an hour or more, since
  AWS does not document when an idle Lambda container is reclaimed. Prove it rather than assume
  it: the first invocation after the gap has an `Init Duration` field on its `REPORT` line in the
  function's CloudWatch log group, which only a cold start writes
- [ ] Every player receives the game-over email with the final scores

**8. The remaining exit criteria.** Function and queue names are generated by CloudFormation;
find them with:

```bash
aws lambda list-functions \
  --query "Functions[?starts_with(FunctionName,'TricksyStack-')].FunctionName"
aws sqs list-queues --queue-name-prefix TricksyStack
```

- [ ] **Tokens expire on their own.** Step 5 left `VERIFY#` items behind; run
  `tricksy forgot-password alice` to leave a `RESET#` one too. DynamoDB deletes expired TTL items
  lazily, usually within a day or two, so scan for both key prefixes a few days later and confirm
  they are gone.
- [ ] **A function error reaches the operator.** Invoke the API function with an event Mangum
  cannot interpret; it raises, which counts toward the errors alarm:
  ```bash
  aws lambda invoke --function-name <api-function> --payload '{}' \
    --cli-binary-format raw-in-base64-out /dev/null
  ```
- [ ] **A poison record reaches the operator.** The notifier lets a failed send propagate rather
  than claiming the transition, so a recipient SES refuses produces a record that fails on every
  retry. Drop one player's address from `TRICKSY_SES_DOGFOOD_RECIPIENTS` and redeploy, which
  deletes that SES identity; their contact stays verified in the app. Make a move that hands that
  player the turn: the send is rejected, the stream retries and bisects the batch, and the record
  lands in the notifier's DLQ, tripping the depth alarm. Afterwards, restore the address, redeploy,
  click its new confirmation mail, and purge the DLQ (`aws sqs purge-queue --queue-url <dlq-url>`)
  so the alarm returns to OK.
- [ ] **A surprising bill reaches the operator.** Not realistically triggerable; confirm the
  budget exists with its limit and subscriber via
  `aws budgets describe-budgets --account-id <account-id>`.

### Least-privilege IAM (optional)

`cdk bootstrap` creates a separate `CloudFormationExecutionRole` that does the actual
provisioning - by default it's granted `AdministratorAccess`. Two narrower policies live under
`infra/iam/`:

- [`execution-policy.json`](infra/iam/execution-policy.json) - what `CloudFormationExecutionRole`
  actually needs to create this stack's resources, scoped by action and, where CloudFormation's
  auto-generated physical names make it possible (everything prefixed `TricksyStack-`), by
  resource too.
- [`deploy-identity-policy.json`](infra/iam/deploy-identity-policy.json) - what your own IAM
  user/role needs to drive a deploy: assuming CDK's bootstrap roles, the CloudFormation
  stack-lifecycle calls, and `iam:PassRole` on the execution role. The same for any CDK app, not
  specific to this stack.

Replace every `ACCOUNT_ID` placeholder, then bootstrap with the narrower execution policy in
place:

```bash
aws iam create-policy --policy-name TricksyExecutionPolicy \
  --policy-document file://infra/iam/execution-policy.json

cd infra && uv run --extra infra cdk bootstrap \
  --cloudformation-execution-policies arn:aws:iam::ACCOUNT_ID:policy/TricksyExecutionPolicy
```

`cdk bootstrap` itself still needs broader privilege than either policy grants - it's what
creates the roles that enforce them, plus the asset S3 bucket, ECR repo and SSM parameter. Run it
once under an admin-level credential; day-to-day `cdk deploy` afterward only needs the deploy
identity policy plus the execution role carrying the narrower one. For a one-person account, the
pragmatic alternative is skipping this section entirely and just using an admin-privileged user
throughout - reasonable here, as long as it's a deliberate choice rather than an oversight.
