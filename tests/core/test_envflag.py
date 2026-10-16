"""store.envflag: 플래그 값 한 벌의 표 시험."""
import pytest

from store.envflag import truthy, truthy_strict

CASES = [
    # (값, truthy, truthy_strict)
    ("1", True, True), ("true", True, True), ("yes", True, True), ("on", True, True),
    ("TRUE", True, True), ("Yes", True, True), ("ON", True, True), ("  on \n", True, True),
    ("0", False, False), ("false", False, False), ("no", False, False), ("off", False, False),
    ("OFF", False, False), (" False ", False, False),
    ("", False, None), ("   ", False, None), (None, False, None),
    ("2", False, None), ("enabled", False, None), ("t", False, None), ("y", False, None),
    (True, True, True), (False, False, False), (1, True, True), (0, False, False),
]


@pytest.mark.parametrize("value,expected,_", CASES)
def test_truthy(value, expected, _):
    assert truthy(value) is expected


@pytest.mark.parametrize("value,_,expected", CASES)
def test_truthy_strict(value, _, expected):
    assert truthy_strict(value) is expected


@pytest.mark.parametrize("value", [None, "", "  "])
def test_default_applies_only_to_empty(value):
    assert truthy(value, default=True) is True


@pytest.mark.parametrize("value", ["off", "0", "nope"])
def test_default_does_not_override_a_given_value(value):
    assert truthy(value, default=True) is False
