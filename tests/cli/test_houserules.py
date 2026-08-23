from __future__ import annotations

import argparse

import pytest

from tricksy.cli import houserules


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    houserules.add_house_rule_flags(parser)
    return parser.parse_args(argv)


def test_no_flags_produces_empty_body() -> None:
    args = _parse([])

    assert houserules.house_rules_body(args) == {}
    assert houserules.any_house_rule_flag_given(args) is False


def test_contracts_flag_splits_on_comma() -> None:
    args = _parse(["--contracts", "nello,plunge,sevens"])

    assert houserules.house_rules_body(args) == {"enabled_contracts": ["nello", "plunge", "sevens"]}


def test_marks_flag() -> None:
    args = _parse(["--marks", "5"])

    assert houserules.house_rules_body(args) == {"marks_to_win": 5}


def test_doubles_trump_flag() -> None:
    args = _parse(["--doubles-trump"])

    assert houserules.house_rules_body(args) == {"doubles_are_own_suit": True}


def test_declared_leads_flag() -> None:
    args = _parse(["--declared-leads", "first_trick"])

    assert houserules.house_rules_body(args) == {"allow_declared_lead": "first_trick"}


def test_declared_leads_rejects_unknown_value() -> None:
    parser = argparse.ArgumentParser()
    houserules.add_house_rule_flags(parser)

    with pytest.raises(SystemExit):
        parser.parse_args(["--declared-leads", "sometimes"])


def test_set_flag_builds_nested_contract_options() -> None:
    args = _parse(["--set", "plunge.minimum_doubles=5", "--set", "plunge.minimum_marks=4"])

    assert houserules.house_rules_body(args) == {
        "contract_options": {"plunge": {"minimum_doubles": 5, "minimum_marks": 4}}
    }


def test_set_flag_coerces_int() -> None:
    args = _parse(["--set", "plunge.minimum_doubles=5"])

    value = houserules.house_rules_body(args)["contract_options"]["plunge"]["minimum_doubles"]
    assert value == 5
    assert isinstance(value, int)


def test_set_flag_coerces_bool() -> None:
    args = _parse(["--set", "nello.some_flag=true"])

    value = houserules.house_rules_body(args)["contract_options"]["nello"]["some_flag"]
    assert value is True


def test_set_flag_falls_back_to_str() -> None:
    args = _parse(["--set", "nello.mode=aggressive"])

    value = houserules.house_rules_body(args)["contract_options"]["nello"]["mode"]
    assert value == "aggressive"


@pytest.mark.parametrize("bad", ["noequals", "nodot=5", ".key=5", "contract.=5"])
def test_malformed_set_flag_exits_two(bad: str) -> None:
    parser = argparse.ArgumentParser()
    houserules.add_house_rule_flags(parser)

    with pytest.raises(SystemExit):
        parser.parse_args(["--set", bad])


def test_any_house_rule_flag_given_true_when_any_flag_set() -> None:
    assert houserules.any_house_rule_flag_given(_parse(["--marks", "5"])) is True
    assert houserules.any_house_rule_flag_given(_parse(["--doubles-trump"])) is True
    assert houserules.any_house_rule_flag_given(_parse(["--contracts", "nello"])) is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [("north", 0), ("east", 1), ("south", 2), ("west", 3), ("NORTH", 0), ("0", 0), ("3", 3)],
)
def test_parse_seat_accepts_name_or_number(value: str, expected: int) -> None:
    assert houserules.parse_seat(value) == expected


@pytest.mark.parametrize("value", ["4", "-1", "northwest", "nope"])
def test_parse_seat_rejects_invalid_values(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        houserules.parse_seat(value)
