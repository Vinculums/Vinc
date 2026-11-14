"""`api.query_base._store_scope`: 스토어를 받았으면 그대로, 못 받았으면 열고 닫는다."""
from __future__ import annotations

import pytest

from api import graph_edit, nav, query_base


class _Store:
    def __init__(self) -> None:
        self.closed = 0

    def run(self, *_args, **_kwargs):
        return []

    def close(self) -> None:
        self.closed += 1


def test_given_store_is_neither_opened_nor_closed():
    given = _Store()

    def opener(_cfg):
        raise AssertionError("a store was given, so nothing opens")

    with query_base._store_scope(given, "cfg", opener) as got:
        assert got is given
    assert given.closed == 0


def test_given_store_survives_an_error_in_the_block():
    given = _Store()
    with pytest.raises(ValueError):
        with query_base._store_scope(given, None, lambda _cfg: _Store()):
            raise ValueError("body failed")
    assert given.closed == 0


def test_opens_with_the_given_cfg_and_closes_once():
    seen, opened = [], _Store()

    def opener(cfg):
        seen.append(cfg)
        return opened

    with query_base._store_scope(None, "the-cfg", opener) as got:
        assert got is opened
        assert opened.closed == 0
    assert seen == ["the-cfg"]
    assert opened.closed == 1


def test_missing_cfg_is_resolved_from_the_environment_config(monkeypatch):
    marker = object()
    monkeypatch.setattr(query_base, "load_config", lambda: marker)
    seen = []
    with query_base._store_scope(None, None, lambda cfg: seen.append(cfg) or _Store()):
        pass
    assert seen == [marker]


def test_given_cfg_does_not_call_load_config(monkeypatch):
    monkeypatch.setattr(query_base, "load_config",
                        lambda: pytest.fail("cfg was given, so nothing loads"))
    with query_base._store_scope(None, "cfg", lambda _cfg: _Store()):
        pass


def test_opened_store_is_closed_when_the_block_raises():
    opened = _Store()
    with pytest.raises(ValueError):
        with query_base._store_scope(None, "cfg", lambda _cfg: opened):
            raise ValueError("body failed")
    assert opened.closed == 1


def test_a_store_that_cannot_open_yields_none_and_closes_nothing():
    with query_base._store_scope(None, "cfg", lambda _cfg: None) as got:
        assert got is None


def test_a_failing_opener_propagates():
    def opener(_cfg):
        raise RuntimeError("open failed")

    with pytest.raises(RuntimeError, match="open failed"):
        with query_base._store_scope(None, "cfg", opener):
            pytest.fail("the block must not run")


def test_return_inside_the_block_still_closes():
    opened = _Store()

    def read():
        with query_base._store_scope(None, "cfg", lambda _cfg: opened):
            return "early"

    assert read() == "early"
    assert opened.closed == 1


def test_delete_edge_that_refuses_an_unsupported_type_closes_the_store_it_opened(monkeypatch):
    opened = _Store()
    monkeypatch.setattr(graph_edit, "open_store", lambda _cfg: opened)
    got = graph_edit.delete_edge("concept:a", "nonsense", "concept:b", owner="user:scope", cfg="cfg")
    assert got["executed"] and not got["found"] and "unsupported edge type" in got["note"]
    assert opened.closed == 1


def test_nav_tree_that_refuses_an_unknown_kind_closes_the_store_it_opened(monkeypatch):
    opened = _Store()
    monkeypatch.setattr(nav, "open_store", lambda _cfg: opened)
    got = nav.nav_tree(owner="user:scope", kind="bogus", cfg="cfg")
    assert not got["executed"] and got["note"] == "unknown kind: bogus"
    assert opened.closed == 1
