"""store.envflag: 플래그 값 한 벌의 표 시험."""
import pytest

from store.envflag import default_off, default_on, one_of, positive_int, strict, truthy, truthy_strict

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


NAME = "VINC_ENVFLAG_TEST_FLAG"


@pytest.fixture
def setenv(monkeypatch):
    monkeypatch.delenv(NAME, raising=False)

    def put(value):
        if value is None:
            monkeypatch.delenv(NAME, raising=False)
        else:
            monkeypatch.setenv(NAME, value)
    return put


@pytest.mark.parametrize("value,expected,_", [c for c in CASES if isinstance(c[0], (str, type(None)))])
def test_default_on_is_false_only_for_the_false_words(setenv, value, expected, _):
    setenv(value)
    want_false = isinstance(value, str) and value.strip().lower() in ("0", "false", "no", "off")
    assert default_on(NAME) is (not want_false)


@pytest.mark.parametrize("value,expected,_", [c for c in CASES if isinstance(c[0], (str, type(None)))])
def test_default_off_is_true_only_for_the_true_words(setenv, value, expected, _):
    setenv(value)
    assert default_off(NAME) is expected


@pytest.mark.parametrize("default", [True, False])
def test_strict_returns_the_default_only_when_unset(setenv, default):
    setenv(None)
    assert strict(NAME, default) is default


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("yes", True), ("on", True), (" ON\n", True),
    ("0", False), ("false", False), ("no", False), ("off", False), (" Off ", False)])
@pytest.mark.parametrize("default", [True, False])
def test_strict_reads_the_given_word_whatever_the_default(setenv, value, expected, default):
    setenv(value)
    assert strict(NAME, default) is expected


@pytest.mark.parametrize("value", ["", "   ", "of", "offf", "2", "enabled", "t", "fp32", "on!"])
def test_strict_refuses_an_unknown_value_and_names_the_variable_and_the_words(setenv, value):
    setenv(value)
    with pytest.raises(ValueError) as err:
        strict(NAME, True)
    text = str(err.value)
    assert NAME in text and repr(value) in text
    for word in ("1", "true", "yes", "on", "0", "false", "no", "off"):
        assert word in text


@pytest.mark.parametrize("value,want", [
    (None, "production"), ("", "production"), ("v3", "v3"), (" V3\n", "v3"), ("production", "production"),
    ("v2", "production"), ("v3 production", "production")])
def test_one_of_falls_back_to_the_default(setenv, value, want):
    setenv(value)
    assert one_of(NAME, ("production", "v3"), "production") == want


@pytest.mark.parametrize("value,want", [
    (None, 5), ("", 5), ("  ", 5), ("3", 3), (" 4 ", 4), ("+3", 3), ("0", 1), ("-2", 1),
    ("3.5", 5), ("abc", 5), ("on", 5), ("1_0", 10)])
def test_positive_int_floors_at_one_and_defaults_on_junk(setenv, value, want):
    setenv(value)
    assert positive_int(NAME, 5) == want
