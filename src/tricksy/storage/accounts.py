"""Player accounts and auth tokens (ROADMAP.md 2.1, 4.2, 4.3, DESIGN.md §6.1).

Six item types in the same single table as the games (DESIGN.md §4.1), so no GSI and no second
table are needed:

``PLAYER#<id> / PROFILE``
    Username, contact channels, and the password hash.
``USERNAME#<lowercased> / PLAYER``
    A reservation, claimed by conditional put, which is what makes usernames unique. DynamoDB has
    no unique-secondary-key constraint, so uniqueness has to be an item somebody races for.
``TOKEN#<sha256(token)> / TOKEN``
    Resolves a bearer token to a player in one ``GetItem`` on the request path.
``PLAYER#<id> / TOKEN#<sha256>``
    The same fact stored the other way round, so a player can list and revoke their own devices
    without a scan. Both are written in one transaction and deleted in one transaction.
``VERIFY#<sha256(token)> / TOKEN``
    A pending contact-channel verification: player id, address, expires_at. Unlike a bearer
    token, nothing needs to list a player's pending verifications, so there is no reverse index -
    one item, minted by :func:`begin_verification` and consumed by :func:`complete_verification`.
``RESET#<sha256(token)> / TOKEN``
    A pending password reset: player id, expires_at (DESIGN.md §4.1). Unlike ``VERIFY#`` it
    carries no ``address`` - it is tied to the player, not a channel - and minting it (
    :func:`begin_password_reset`) is not itself proof of anything; redeeming it
    (:func:`complete_password_reset`) is what grants a new password and revokes every device.

**Two hashes, on purpose.** A password is low-entropy and human-chosen, so it gets ``scrypt``,
which is deliberately slow and memory-hard. A token is 32 bytes from ``secrets``, so brute force
is not a threat and a fast ``sha256`` is the right choice - it also keeps the auth path cheap,
since it runs on every request. Both come from the standard library; nothing here needs a
dependency or an external service, so the whole auth path is testable offline.

**Plaintext never lands in DynamoDB.** ``issue_token`` returns the token exactly once, to be
handed to the caller and never recovered; the table only ever holds its hash. Same for passwords.

Numeric attributes are avoided throughout (scrypt's parameters are encoded inside the password
string, timestamps are ISO-8601 text), so unlike the game items these need no ``Decimal``
normalization on read.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from botocore.exceptions import ClientError
from mypy_boto3_dynamodb.service_resource import Table

from tricksy.games.texas42.state import PlayerId

from ._dynamo import as_text, is_transaction_cancelled, transact_write
from .errors import (
    ContactAlreadyExists,
    ContactNotFound,
    InvalidCredentials,
    InvalidResetToken,
    InvalidToken,
    InvalidVerificationToken,
    PlayerNotFound,
    StorageError,
    UsernameTaken,
)

#: scrypt cost parameters. ``n=2**14`` with ``r=8`` needs ~16 MiB per hash, which is a sane bar on
#: a 128 MB Lambda and takes well under a second. They are recorded in each stored hash rather
#: than assumed, so raising them later re-hashes on next sign-in instead of locking everyone out.
_SCRYPT_N: Final = 2**14
_SCRYPT_R: Final = 8
_SCRYPT_P: Final = 1
_SCRYPT_DKLEN: Final = 32
_SALT_BYTES: Final = 16
_TOKEN_BYTES: Final = 32
_PLAYER_ID_BYTES: Final = 12
#: How long a mailed verification token stays redeemable (ROADMAP.md 4.2).
_VERIFICATION_TTL: Final = timedelta(hours=24)
#: How long a mailed password-reset token stays redeemable (ROADMAP.md 4.3). Shorter than
#: verification's: redeeming this one grants a new password outright, a higher-stakes credential
#: than proving address ownership.
_RESET_TTL: Final = timedelta(hours=1)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _player_pk(player_id: PlayerId) -> str:
    return f"PLAYER#{player_id}"


def _username_pk(username: str) -> str:
    return f"USERNAME#{normalize_username(username)}"


def _token_pk(token_hash: str) -> str:
    return f"TOKEN#{token_hash}"


def _verify_pk(token_hash: str) -> str:
    return f"VERIFY#{token_hash}"


def _reset_pk(token_hash: str) -> str:
    return f"RESET#{token_hash}"


def _token_sk(token_hash: str) -> str:
    return f"TOKEN#{token_hash}"


def normalize_username(username: str) -> str:
    """The key form of a username. Case-insensitive, so ``Charlie`` and ``charlie`` are the same
    account and cannot both be registered."""
    return username.strip().lower()


def hash_token(token: str) -> str:
    """The lookup key for a bearer token. Public because the API layer needs it to revoke the
    token a request arrived with, without that token ever being stored anywhere."""
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_password(password: str, *, salt: bytes | None = None) -> str:
    """A self-describing hash string, PHC-style: ``scrypt$n=..,r=..,p=..$salt$hash``, both halves
    base64. Self-describing so the parameters can be raised later without a migration - a stored
    hash always carries the ones it was made with."""
    salt = secrets.token_bytes(_SALT_BYTES) if salt is None else salt
    derived = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    params = f"n={_SCRYPT_N},r={_SCRYPT_R},p={_SCRYPT_P}"
    return (
        f"scrypt${params}${urlsafe_b64encode(salt).decode()}${urlsafe_b64encode(derived).decode()}"
    )


def _verify_password(password: str, encoded: str) -> bool:
    """Recomputes ``encoded``'s hash over ``password`` using the parameters recorded in it.
    Returns ``False`` rather than raising on a malformed string, so a corrupt profile fails
    closed as a rejected sign-in."""
    try:
        scheme, params, salt_b64, hash_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        parsed = dict(pair.split("=", 1) for pair in params.split(","))
        derived = hashlib.scrypt(
            password.encode(),
            salt=urlsafe_b64decode(salt_b64),
            n=int(parsed["n"]),
            r=int(parsed["r"]),
            p=int(parsed["p"]),
            dklen=len(urlsafe_b64decode(hash_b64)),
        )
    except (ValueError, KeyError):
        return False
    return hmac.compare_digest(derived, urlsafe_b64decode(hash_b64))


#: Verified against when no such username exists, so that sign-in takes roughly the same time
#: whether the username is real or not. Without it, response latency is a username oracle - the
#: same reason ``InvalidCredentials`` does not say which half was wrong.
_DUMMY_HASH: Final = _hash_password("", salt=b"\x00" * _SALT_BYTES)


@dataclass(frozen=True, slots=True)
class ContactChannel:
    """Somewhere a player can be reached. A list of these rather than a bare ``email`` field, so
    Phase 4 branches on ``kind`` and adding SMS or a chat DM is a new branch rather than a data
    migration (DESIGN.md §12). A player may have none at all."""

    kind: str
    address: str
    verified: bool = False
    #: Per-channel mute, so notifications can be turned off without deleting the address
    #: (ROADMAP.md 4.2). Decoded through ``.get("notify", True)`` so items written before this
    #: field existed need no migration.
    notify: bool = True


@dataclass(frozen=True, slots=True)
class Player:
    """A player's public-facing account. Never carries the password hash: this is what gets
    returned from a read, and the hash has no business leaving this module."""

    player_id: PlayerId
    username: str
    contacts: tuple[ContactChannel, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class Device:
    """One issued token, identified by its hash - the plaintext is unrecoverable. ``token_hash``
    is what :func:`revoke_token` takes, so a player can sign out a device from another one."""

    token_hash: str
    label: str
    created_at: str
    last_used_at: str


def _encode_contacts(contacts: Iterable[ContactChannel]) -> list[dict[str, Any]]:
    return [
        {"kind": c.kind, "address": c.address, "verified": c.verified, "notify": c.notify}
        for c in contacts
    ]


def _decode_contacts(data: Any) -> tuple[ContactChannel, ...]:
    return tuple(
        ContactChannel(
            kind=c["kind"],
            address=c["address"],
            verified=bool(c["verified"]),
            notify=bool(c.get("notify", True)),
        )
        for c in data or ()
    )


def _find_contact(contacts: tuple[ContactChannel, ...], address: str) -> ContactChannel:
    for contact in contacts:
        if contact.address == address:
            return contact
    raise ContactNotFound(address)


def _player_from_item(item: Mapping[str, Any]) -> Player:
    return Player(
        player_id=item["player_id"],
        username=item["username"],
        contacts=_decode_contacts(item.get("contacts")),
        created_at=item["created_at"],
    )


def create_player(
    table: Table,
    username: str,
    password: str,
    contacts: Iterable[ContactChannel] = (),
    *,
    now: Callable[[], datetime] = _utcnow,
) -> Player:
    """Registers a player, reserving ``username`` in the same transaction that writes the profile.

    Raises :class:`~tricksy.storage.errors.UsernameTaken` if the reservation is already held. The
    player id is server-generated and opaque, never the username, so a username stays renameable
    and never gets embedded in the immutable event log (invariant 6).
    """
    normalized = normalize_username(username)
    if not normalized:
        raise ValueError("username cannot be blank")
    player_id = secrets.token_urlsafe(_PLAYER_ID_BYTES)
    timestamp = now().isoformat()

    try:
        transact_write(
            table,
            [
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": {
                            "PK": _username_pk(username),
                            "SK": "PLAYER",
                            "player_id": player_id,
                        },
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": {
                            "PK": _player_pk(player_id),
                            "SK": "PROFILE",
                            "player_id": player_id,
                            "username": username.strip(),
                            "password_hash": _hash_password(password),
                            "contacts": _encode_contacts(contacts),
                            "created_at": timestamp,
                        },
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
            ],
        )
    except ClientError as exc:
        if is_transaction_cancelled(exc):
            raise UsernameTaken(username) from exc
        raise

    return Player(
        player_id=player_id,
        username=username.strip(),
        contacts=tuple(contacts),
        created_at=timestamp,
    )


def get_player(table: Table, player_id: PlayerId) -> Player:
    """The profile behind ``player_id``. Raises ``KeyError`` if there is none, matching
    :func:`tricksy.games.texas42.state.seat_of`'s convention for an unknown player."""
    item = table.get_item(Key={"PK": _player_pk(player_id), "SK": "PROFILE"}).get("Item")
    if item is None:
        raise KeyError(f"no player with id {player_id!r}")
    return _player_from_item(item)


def player_for_username(table: Table, username: str) -> PlayerId:
    """Resolves a username to a player id, for inviting somebody by name (ROADMAP.md 2.7.2).

    Unlike :func:`authenticate`, this is meant to be a public existence check - an invite is
    necessarily addressed by a name the inviter can find, so there is no oracle to protect against
    here the way there is for sign-in. Raises :class:`~tricksy.storage.errors.PlayerNotFound` for an
    unknown username.
    """
    reservation = table.get_item(Key={"PK": _username_pk(username), "SK": "PLAYER"}).get("Item")
    if reservation is None:
        raise PlayerNotFound(username)
    return as_text(reservation["player_id"])


def authenticate(table: Table, username: str, password: str) -> PlayerId:
    """Verifies a username and password, returning the player id.

    Raises :class:`~tricksy.storage.errors.InvalidCredentials` for both an unknown username and a
    wrong password, and takes about the same time either way - see ``_DUMMY_HASH``.
    """
    reservation = table.get_item(Key={"PK": _username_pk(username), "SK": "PLAYER"}).get("Item")
    if reservation is None:
        _verify_password(password, _DUMMY_HASH)
        raise InvalidCredentials

    player_id: PlayerId = as_text(reservation["player_id"])
    profile = table.get_item(Key={"PK": _player_pk(player_id), "SK": "PROFILE"}).get("Item")
    if profile is None or not _verify_password(password, as_text(profile["password_hash"])):
        raise InvalidCredentials
    return player_id


def issue_token(
    table: Table,
    player_id: PlayerId,
    label: str = "",
    *,
    now: Callable[[], datetime] = _utcnow,
) -> str:
    """Mints a bearer token for one device and returns it. This is the only time the plaintext
    exists outside the caller: only its hash is stored, in both lookup directions.

    ``expires_at`` is written as ``None``. Tokens do not expire and are revoked explicitly
    (DESIGN.md §6.1), but the attribute is present from the start so introducing expiry later is
    a behaviour change rather than a data migration.
    """
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    digest = hash_token(token)
    timestamp = now().isoformat()

    transact_write(
        table,
        [
            {
                "Put": {
                    "TableName": table.name,
                    "Item": {
                        "PK": _token_pk(digest),
                        "SK": "TOKEN",
                        "player_id": player_id,
                        "label": label,
                        "created_at": timestamp,
                        "last_used_at": timestamp,
                        "expires_at": None,
                    },
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            },
            {
                "Put": {
                    "TableName": table.name,
                    "Item": {
                        "PK": _player_pk(player_id),
                        "SK": _token_sk(digest),
                        "token_hash": digest,
                        "label": label,
                        "created_at": timestamp,
                        "last_used_at": timestamp,
                    },
                    "ConditionExpression": "attribute_not_exists(SK)",
                }
            },
        ],
    )
    return token


def player_for_token(
    table: Table, token: str, *, now: Callable[[], datetime] = _utcnow
) -> PlayerId:
    """Resolves a bearer token to a player in one ``GetItem``, and stamps ``last_used_at`` so a
    player can tell their devices apart when reviewing them.

    That stamp makes every authenticated request a write as well as a read. At this scale that is
    the honest trade for keeping the code obvious; if it ever matters, the fix is to skip the
    update when the stored value is recent.

    Raises :class:`~tricksy.storage.errors.InvalidToken` if the token was never issued or has since
    been revoked.
    """
    digest = hash_token(token)
    item = table.get_item(Key={"PK": _token_pk(digest), "SK": "TOKEN"}).get("Item")
    if item is None:
        raise InvalidToken
    player_id: PlayerId = as_text(item["player_id"])

    timestamp = now().isoformat()
    table.update_item(
        Key={"PK": _token_pk(digest), "SK": "TOKEN"},
        UpdateExpression="SET last_used_at = :ts",
        ExpressionAttributeValues={":ts": timestamp},
    )
    table.update_item(
        Key={"PK": _player_pk(player_id), "SK": _token_sk(digest)},
        UpdateExpression="SET last_used_at = :ts",
        ExpressionAttributeValues={":ts": timestamp},
    )
    return player_id


def revoke_token(table: Table, player_id: PlayerId, token_hash: str) -> None:
    """Signs one device out, deleting both directions of the lookup in a single transaction.

    Takes ``player_id`` as well as the hash so a token can only ever be revoked by the player who
    holds it: the delete of the reverse item is conditioned on that pairing existing, so passing
    someone else's token hash deletes nothing. Revoking an already-revoked token is a no-op.
    """
    try:
        transact_write(
            table,
            [
                {
                    "Delete": {
                        "TableName": table.name,
                        "Key": {"PK": _player_pk(player_id), "SK": _token_sk(token_hash)},
                        "ConditionExpression": "attribute_exists(SK)",
                    }
                },
                {
                    "Delete": {
                        "TableName": table.name,
                        "Key": {"PK": _token_pk(token_hash), "SK": "TOKEN"},
                    }
                },
            ],
        )
    except ClientError as exc:
        if is_transaction_cancelled(exc):
            return
        raise


def list_tokens(table: Table, player_id: PlayerId) -> tuple[Device, ...]:
    """Every device currently signed in as ``player_id``, newest first."""
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": _player_pk(player_id), ":prefix": "TOKEN#"},
    )
    devices = [
        Device(
            token_hash=as_text(item["token_hash"]),
            label=as_text(item["label"]),
            created_at=as_text(item["created_at"]),
            last_used_at=as_text(item["last_used_at"]),
        )
        for item in response.get("Items", ())
    ]
    return tuple(sorted(devices, key=lambda d: d.created_at, reverse=True))


def _get_profile_item(table: Table, player_id: PlayerId) -> dict[str, Any]:
    item = table.get_item(Key={"PK": _player_pk(player_id), "SK": "PROFILE"}).get("Item")
    if item is None:
        raise KeyError(f"no player with id {player_id!r}")
    return dict(item)


def _write_contacts(
    table: Table, player_id: PlayerId, contacts: tuple[ContactChannel, ...]
) -> None:
    table.update_item(
        Key={"PK": _player_pk(player_id), "SK": "PROFILE"},
        UpdateExpression="SET contacts = :c",
        ExpressionAttributeValues={":c": _encode_contacts(contacts)},
    )


def add_contact(table: Table, player_id: PlayerId, kind: str, address: str) -> Player:
    """Appends a new, unverified, un-muted channel.

    Raises :class:`~tricksy.storage.errors.ContactAlreadyExists` if this address is already
    registered - every other contact operation names a channel by its address, so duplicates
    would make "the" channel ambiguous.
    """
    item = _get_profile_item(table, player_id)
    contacts = _decode_contacts(item.get("contacts"))
    if any(c.address == address for c in contacts):
        raise ContactAlreadyExists(address)
    new_contacts = (*contacts, ContactChannel(kind=kind, address=address))
    _write_contacts(table, player_id, new_contacts)
    return _player_from_item({**item, "contacts": _encode_contacts(new_contacts)})


def remove_contact(table: Table, player_id: PlayerId, address: str) -> Player:
    """Removes one channel by address. Raises :class:`~tricksy.storage.errors.ContactNotFound` if no
    channel has this address."""
    item = _get_profile_item(table, player_id)
    contacts = _decode_contacts(item.get("contacts"))
    _find_contact(contacts, address)
    new_contacts = tuple(c for c in contacts if c.address != address)
    _write_contacts(table, player_id, new_contacts)
    return _player_from_item({**item, "contacts": _encode_contacts(new_contacts)})


def set_contact_notify(table: Table, player_id: PlayerId, address: str, notify: bool) -> Player:
    """Mutes or unmutes one channel without deleting it. Raises
    :class:`~tricksy.storage.errors.ContactNotFound` if no channel has this address."""
    item = _get_profile_item(table, player_id)
    contacts = _decode_contacts(item.get("contacts"))
    _find_contact(contacts, address)
    new_contacts = tuple(replace(c, notify=notify) if c.address == address else c for c in contacts)
    _write_contacts(table, player_id, new_contacts)
    return _player_from_item({**item, "contacts": _encode_contacts(new_contacts)})


def _mint_single_use_token(
    table: Table,
    pk_for: Callable[[str], str],
    ttl: timedelta,
    fields: Mapping[str, Any],
    *,
    now: Callable[[], datetime],
) -> str:
    """Mints a single-use, expiring token under the partition key ``pk_for`` builds from its
    hash, storing ``fields`` alongside ``expires_at``. Returns the plaintext - the only time it
    exists outside the caller. Shared by :func:`begin_verification` and
    :func:`begin_password_reset`, which differ only in ``pk_for``, ``ttl`` and which fields ride
    along on the item.

    **The token never starts with ``-``.** ``tricksy.notifications.messages`` prints these inside
    a command the player is told to copy and run verbatim, and a leading ``-`` reads as an option
    flag to ``argparse`` - so ``tricksy contact confirm -abc...`` dies as a usage error before it
    ever reaches the server. ``secrets.token_urlsafe`` emits base64url, so without this roughly
    one verification and reset mail in 64 would carry an unrunnable command. Re-minting costs
    about 1.6 bits out of ``_TOKEN_BYTES * 8``, which is no reduction worth caring about at this
    size, and it keeps the fix at the one place both tokens are made."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    while token.startswith("-"):
        token = secrets.token_urlsafe(_TOKEN_BYTES)
    digest = hash_token(token)
    table.put_item(
        Item={
            "PK": pk_for(digest),
            "SK": "TOKEN",
            "expires_at": (now() + ttl).isoformat(),
            **fields,
        }
    )
    return token


def _redeem_single_use_token(
    table: Table,
    pk_for: Callable[[str], str],
    token: str,
    invalid: type[StorageError],
    *,
    now: Callable[[], datetime],
) -> dict[str, Any]:
    """Deletes and returns the item a matching :func:`_mint_single_use_token` call stored,
    whether or not it turns out to have expired - so it can never be replayed (DESIGN.md §6.1:
    "mails a single-use token"). Raises ``invalid`` if the token was never issued, has already
    been redeemed, or has expired. Shared by :func:`complete_verification` and
    :func:`complete_password_reset`, which differ only in ``pk_for``, the exception raised, and
    what they do with the fields on the returned item."""
    key = {"PK": pk_for(hash_token(token)), "SK": "TOKEN"}
    item = table.get_item(Key=key).get("Item")
    if item is None:
        raise invalid
    table.delete_item(Key=key)
    if as_text(item["expires_at"]) < now().isoformat():
        raise invalid
    return dict(item)


def begin_verification(
    table: Table, player_id: PlayerId, address: str, *, now: Callable[[], datetime] = _utcnow
) -> str:
    """Mints a single-use verification token for one contact channel and returns it - the only
    time its plaintext exists outside the caller, mirroring :func:`issue_token`.

    Raises :class:`~tricksy.storage.errors.ContactNotFound` if the address is not one of this
    player's channels.
    """
    item = _get_profile_item(table, player_id)
    contacts = _decode_contacts(item.get("contacts"))
    _find_contact(contacts, address)
    return _mint_single_use_token(
        table,
        _verify_pk,
        _VERIFICATION_TTL,
        {"player_id": player_id, "address": address},
        now=now,
    )


def complete_verification(
    table: Table, token: str, *, now: Callable[[], datetime] = _utcnow
) -> None:
    """Redeems a verification token, marking its channel verified.

    Raises :class:`~tricksy.storage.errors.InvalidVerificationToken` if the token was never issued,
    has already been redeemed, or has expired. A no-op, not an error, if the channel was removed
    between minting and redeeming - there is nothing left to verify.
    """
    item = _redeem_single_use_token(table, _verify_pk, token, InvalidVerificationToken, now=now)
    player_id: PlayerId = as_text(item["player_id"])
    address = as_text(item["address"])
    profile_item = _get_profile_item(table, player_id)
    contacts = _decode_contacts(profile_item.get("contacts"))
    new_contacts = tuple(replace(c, verified=True) if c.address == address else c for c in contacts)
    _write_contacts(table, player_id, new_contacts)


def begin_password_reset(
    table: Table, player_id: PlayerId, *, now: Callable[[], datetime] = _utcnow
) -> str:
    """Mints a single-use password-reset token for ``player_id`` and returns it - the only time
    its plaintext exists outside the caller, mirroring :func:`begin_verification`.

    Unlike :func:`begin_verification`, this needs no existence check against the player's
    contacts: it is not addressed to a channel, and the caller (the API handler) has already
    decided who to mail before calling this.
    """
    return _mint_single_use_token(table, _reset_pk, _RESET_TTL, {"player_id": player_id}, now=now)


def complete_password_reset(
    table: Table, token: str, new_password: str, *, now: Callable[[], datetime] = _utcnow
) -> None:
    """Redeems a password-reset token: sets a new password and revokes every device.

    Raises :class:`~tricksy.storage.errors.InvalidResetToken` if the token was never issued, has
    already been redeemed, or has expired.

    The password change and each device's revocation are separate writes, not one transaction -
    DynamoDB's transaction size limit does not accommodate an unbounded number of devices, and a
    player has few enough of them that this is the same trade-off ``list_tokens``' callers
    already accept elsewhere. If a revocation fails partway through, the new password has already
    taken effect and the remaining devices stay signed in; DESIGN.md §6.1's "revoke every issued
    token" is therefore a same-request best effort; a device left behind that way is fixed by any
    later, successful revoke.
    """
    item = _redeem_single_use_token(table, _reset_pk, token, InvalidResetToken, now=now)
    player_id: PlayerId = as_text(item["player_id"])
    table.update_item(
        Key={"PK": _player_pk(player_id), "SK": "PROFILE"},
        UpdateExpression="SET password_hash = :h",
        ExpressionAttributeValues={":h": _hash_password(new_password)},
    )
    for device in list_tokens(table, player_id):
        revoke_token(table, player_id, device.token_hash)
