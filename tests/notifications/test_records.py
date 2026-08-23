"""ROADMAP.md 4.4: decoding raw DynamoDB Streams records into plain data."""

from __future__ import annotations

from tricksy.notifications.records import Transition, transition_from_record


def test_insert_has_no_old_image() -> None:
    record = {
        "eventName": "INSERT",
        "dynamodb": {
            "Keys": {"PK": {"S": "PLAYER#abc"}, "SK": {"S": "GAME#7F3AKM"}},
            "NewImage": {
                "PK": {"S": "PLAYER#abc"},
                "SK": {"S": "GAME#7F3AKM"},
                "is_my_turn": {"BOOL": False},
                "status": {"S": "ACTIVE"},
            },
        },
    }

    transition = transition_from_record(record)

    assert transition == Transition(
        event_name="INSERT",
        keys={"PK": "PLAYER#abc", "SK": "GAME#7F3AKM"},
        old=None,
        new={
            "PK": "PLAYER#abc",
            "SK": "GAME#7F3AKM",
            "is_my_turn": False,
            "status": "ACTIVE",
        },
    )


def test_modify_decodes_both_images_including_bool_and_numeric_attributes() -> None:
    record = {
        "eventName": "MODIFY",
        "dynamodb": {
            "Keys": {"PK": {"S": "PLAYER#abc"}, "SK": {"S": "GAME#7F3AKM"}},
            "OldImage": {
                "PK": {"S": "PLAYER#abc"},
                "SK": {"S": "GAME#7F3AKM"},
                "is_my_turn": {"BOOL": False},
                "marks": {"N": "3"},
            },
            "NewImage": {
                "PK": {"S": "PLAYER#abc"},
                "SK": {"S": "GAME#7F3AKM"},
                "is_my_turn": {"BOOL": True},
                "marks": {"N": "3"},
            },
        },
    }

    transition = transition_from_record(record)

    assert transition.event_name == "MODIFY"
    assert transition.keys == {"PK": "PLAYER#abc", "SK": "GAME#7F3AKM"}
    assert transition.old is not None
    assert transition.old["is_my_turn"] is False
    assert transition.old["marks"] == 3
    assert transition.new is not None
    assert transition.new["is_my_turn"] is True


def test_empty_map_attribute_decodes_without_its_type_wrapper() -> None:
    """Real DynamoDB Streams (confirmed against DynamoDB Local, not reproduced by moto) drops the
    "M" wrapper for an empty map attribute - HouseRules.contract_options={} is the common case -
    so a bare {} shows up nested inside NewImage/OldImage instead of {"M": {}}. Regression for a
    crash found end-to-end: TypeDeserializer treats a bare {} as malformed input."""
    record = {
        "eventName": "MODIFY",
        "dynamodb": {
            "Keys": {"PK": {"S": "GAME#g1"}, "SK": {"S": "META"}},
            "NewImage": {
                "PK": {"S": "GAME#g1"},
                "SK": {"S": "META"},
                "config": {
                    "M": {
                        "marks_to_win": {"N": "7"},
                        "contract_options": {},
                    }
                },
            },
        },
    }

    transition = transition_from_record(record)

    assert transition.new is not None
    assert transition.new["config"] == {"marks_to_win": 7, "contract_options": {}}


def test_remove_has_no_new_image() -> None:
    record = {
        "eventName": "REMOVE",
        "dynamodb": {
            "Keys": {"PK": {"S": "GAME#7F3AKM"}, "SK": {"S": "INVITE#abc"}},
            "OldImage": {
                "PK": {"S": "GAME#7F3AKM"},
                "SK": {"S": "INVITE#abc"},
                "username": {"S": "alice"},
            },
        },
    }

    transition = transition_from_record(record)

    assert transition.new is None
    assert transition.old == {
        "PK": "GAME#7F3AKM",
        "SK": "INVITE#abc",
        "username": "alice",
    }
