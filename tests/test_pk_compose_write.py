# -*- coding: utf-8 -*-
"""PR-B (v0.8.4 axis 1, design A) — write-side compose and the single read-side strip.

PR-A shipped store/pkns.py and the U+001F ingest guard (tests/test_pk_namespace.py).
This file covers what PR-B added on top: the four write chokepoints compose the
physical key (api/fragment.py _merge_node/_merge_edge, api/docstore.py extract_doc,
engines/recall_ingest.py merge_episode), and store/embedded_backend.py
_exec_to_dicts decomposes on the way out, recursively.

Two invariants get their own tests because getting them wrong is silent:

* with the flag off every binding is byte identical to what shipped before, and
* an edge tombstone key is built from the LOGICAL endpoints and composed exactly
  once; joining two already-physical endpoints would produce a second, different
  key for the same edge, and then a delete marker never cancels.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from api import docstore, fragment  # noqa: E402
from store import pkns  # noqa: E402
from store.embedded_backend import _exec_to_dicts, _import_driver  # noqa: E402

OWNER = "user:019fae48-real-account"
PHYS = f"{OWNER}\x1f"


class _RecordingStore:
    """Every (cypher, params) pair, with no rows back — so `matched` is always
    False and the write path takes its create branch."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def run(self, cypher, params=None, **kw):
        self.calls.append((cypher, dict(params or {})))
        return []

    def bound(self, key: str, *, cypher: str = "") -> list:
        return [p[key] for c, p in self.calls if key in p and cypher in c]


def _ingest(store, payload):
    return fragment.merge_fragment(payload, store=store)


# ---------------------------------------------------------------------------
# pkns.ekey — logical src|REL|dst, composed once
# ---------------------------------------------------------------------------

def test_ekey_is_identity_when_disabled(monkeypatch):
    monkeypatch.delenv("VINC_PK_NAMESPACED", raising=False)
    assert pkns.ekey(OWNER, "concept:a", "RELATED_TO", "concept:b") \
        == "concept:a|RELATED_TO|concept:b"


def test_ekey_composes_the_whole_logical_key_once(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    assert pkns.ekey(OWNER, "concept:a", "RELATED_TO", "concept:b") \
        == f"{PHYS}concept:a|RELATED_TO|concept:b"


def test_ekey_never_recomposes_a_physical_key(monkeypatch):
    """The required PR-B assertion. Callers hold logical slugs in some places and
    already-composed keys in others; both must land on the same tombstone key."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    logical = pkns.ekey(OWNER, "concept:a", "RELATED_TO", "concept:b")
    physical_ends = pkns.ekey(
        OWNER,
        pkns.compose(OWNER, "concept:a", normalize=False),
        "RELATED_TO",
        pkns.compose(OWNER, "concept:b", normalize=False))
    assert physical_ends == logical
    assert physical_ends.count(pkns.SEP) == 1, "owner appears once, not per endpoint"


# ---------------------------------------------------------------------------
# pkns.strip_deep — the read-side decomposition, recursive
# ---------------------------------------------------------------------------

def test_strip_deep_returns_the_same_object_when_there_is_nothing_to_do():
    row = {"id": "concept:a", "tags": ["x"]}
    assert pkns.strip_deep(row) is row


def test_strip_deep_plain_key():
    assert pkns.strip_deep(f"{PHYS}concept:a") == "concept:a"


def test_strip_deep_recurses_into_lists():
    """`collect(n.concept_id)` comes back as a list of physical keys."""
    assert pkns.strip_deep([f"{PHYS}concept:a", "concept:b"]) == ["concept:a", "concept:b"]


def test_strip_deep_recurses_into_nested_dicts():
    got = pkns.strip_deep({"n": {"id": f"{PHYS}concept:a", "deep": [{"x": f"{PHYS}record:r"}]}})
    assert got == {"n": {"id": "concept:a", "deep": [{"x": "record:r"}]}}


def test_json_dumps_escapes_the_separator_so_props_never_carries_it():
    """Measured, and the reason the JSON branch almost never fires: json.dumps
    escapes control characters regardless of ensure_ascii, so a props column
    written through it cannot hold a literal separator."""
    raw = json.dumps({"ref": f"{PHYS}concept:a"}, ensure_ascii=False)
    assert pkns.SEP not in raw
    assert pkns.strip_deep(raw) is raw       # nothing to do, same object back


def test_json_loads_refuses_a_literal_separator_so_the_other_direction_is_shut():
    """The second half of why strip_deep has no JSON branch: even if something
    hand-built such a string, it is not loadable JSON in strict mode. A physical
    key inside a JSON *string* cannot exist in this store at all."""
    with pytest.raises(ValueError):
        json.loads('{"ref": "' + PHYS + 'concept:a"}')


def test_strip_deep_leaves_prose_that_starts_with_a_brace_alone():
    """The plain-string rule declines it because the prefix has whitespace."""
    broken = "{not json " + PHYS + "tail"
    assert pkns.strip_deep(broken) == broken


def test_strip_deep_does_not_truncate_document_body_text():
    """put_doc guards ids/title/origin but deliberately not the body (D45,
    Chunk=SSoT is not censored), so a stray control byte can reach the store
    inside chunk text. Cutting on it would destroy the text on every read."""
    body = "a paragraph\nand another\x1fwith a stray byte"
    assert pkns.strip_deep(body) == body


def test_strip_deep_needs_exactly_one_separator():
    assert pkns.strip_deep(f"{PHYS}a\x1fb") == f"{PHYS}a\x1fb"


def test_looks_physical_declines_a_prefix_with_whitespace():
    """The narrowing that protects prose: an owner token never has whitespace."""
    assert pkns.looks_physical(f"{OWNER}\x1fconcept:a") is True
    assert pkns.looks_physical("some prose\x1fmore prose") is False
    assert pkns.looks_physical("x" * 200 + "\x1ftail") is False
    assert pkns.looks_physical("\x1ftail") is False


# ---------------------------------------------------------------------------
# store/embedded_backend._exec_to_dicts — the one strip hook
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, cols, rows):
        self._cols, self._rows = cols, list(rows)

    def get_column_names(self):
        return self._cols

    def has_next(self):
        return bool(self._rows)

    def get_next(self):
        return self._rows.pop(0)


class _FakeConn:
    def __init__(self, cols, rows):
        self._cols, self._rows = cols, rows

    def execute(self, cypher, params=None):
        return _FakeResult(self._cols, self._rows)


def test_exec_to_dicts_is_untouched_when_the_flag_is_off(monkeypatch):
    monkeypatch.delenv("VINC_PK_NAMESPACED", raising=False)
    conn = _FakeConn(["id"], [[f"{PHYS}concept:a"]])
    assert _exec_to_dicts(conn, "RETURN 1", None) == [{"id": f"{PHYS}concept:a"}]


def test_exec_to_dicts_strips_every_shape_when_the_flag_is_on(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    conn = _FakeConn(
        ["id", "ids", "node", "props", "text"],
        [[f"{PHYS}concept:a",
          [f"{PHYS}concept:b", f"{PHYS}concept:c"],
          {"concept_id": f"{PHYS}concept:d"},
          json.dumps({"ref": f"{PHYS}concept:e"}, ensure_ascii=False),
          "body text\x1fwith a stray byte in a line of prose"]])
    row = _exec_to_dicts(conn, "RETURN 1", None)[0]
    assert row["id"] == "concept:a"
    assert row["ids"] == ["concept:b", "concept:c"]
    assert row["node"] == {"concept_id": "concept:d"}
    # props: the separator arrived escaped, so there is nothing here to strip and
    # the column comes back exactly as stored.
    assert json.loads(row["props"]) == {"ref": f"{PHYS}concept:e"}
    assert row["text"] == "body text\x1fwith a stray byte in a line of prose"


# ---------------------------------------------------------------------------
# api/fragment.py — _merge_node / _merge_edge compose
# ---------------------------------------------------------------------------

def test_merge_node_binds_a_composed_key(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    st = _RecordingStore()
    r = _ingest(st, {"owner": OWNER,
                     "nodes": [{"id": "concept:a", "type": "concept", "title": "A"}]})
    assert r["merged"]["nodes"]["created"] == 1
    assert st.bound("id") and all(v == f"{PHYS}concept:a" for v in st.bound("id"))


def test_merge_node_binds_the_logical_id_when_the_flag_is_off(monkeypatch):
    monkeypatch.delenv("VINC_PK_NAMESPACED", raising=False)
    st = _RecordingStore()
    _ingest(st, {"owner": OWNER,
                 "nodes": [{"id": "concept:a", "type": "concept", "title": "A"}]})
    assert st.bound("id") and all(v == "concept:a" for v in st.bound("id"))


def test_flag_on_and_off_differ_only_in_the_key(monkeypatch):
    """Byte identical except for the composed bindings — nothing else moves."""
    def run(flag):
        if flag:
            monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
        else:
            monkeypatch.delenv("VINC_PK_NAMESPACED", raising=False)
        st = _RecordingStore()
        _ingest(st, {"owner": OWNER, "nodes": [
            {"id": "concept:a", "type": "concept", "title": "A", "domain": "vinc/x"},
            {"id": "concept:b", "type": "concept", "title": "B"}],
            "edges": [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}]})
        return st.calls

    off, on = run(False), run(True)
    assert [c for c, _ in off] == [c for c, _ in on], "cypher text must not change"
    # The HLC counter advances between the two runs, so timestamps and sequence
    # numbers are expected to differ; the key bindings are what this pins.
    keyed = ("id", "s", "d", "k")
    for (_, po), (_, pn) in zip(off, on):
        assert set(po) == set(pn), "no parameter appears or disappears"
        for k in keyed:
            if k in po:
                assert pkns.strip(pn[k]) == po[k], f"{k}: {pn[k]!r} is not {po[k]!r} composed"
    assert any(pkns.SEP in p[k] for _, p in on for k in keyed if k in p), \
        "the flag-on run must actually have composed something"


def test_merge_node_keeps_the_logical_id_out_of_user_facing_values(monkeypatch):
    """The title fallback and the warnings are read by people. A physical key
    there would leak an account identifier into the graph."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    st = _RecordingStore()
    _ingest(st, {"owner": OWNER, "nodes": [{"id": "concept:a", "type": "concept"}]})
    assert "concept:a" in st.bound("title")
    r = _ingest(st, {"owner": OWNER, "nodes": [{"id": "bridge:b", "type": "bridge",
                                               "domain": "vinc/x"}]})
    assert any("bridge:b" in w and pkns.SEP not in w for w in r["warnings"])


def test_merge_node_does_not_compose_a_domain_node(monkeypatch):
    """Domain's only column is `name`, so it holds no ownership and is shared
    globally (engines/domain_hierarchy ownership model). It is not one of the
    six labels the migration rewrites; composing here would silently fork it."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    st = _RecordingStore()
    _ingest(st, {"owner": OWNER, "nodes": [{"id": "vinc/x", "type": "domain"}]})
    assert st.bound("id") and set(st.bound("id")) == {"vinc/x"}


def test_merge_edge_binds_composed_endpoints(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    st = _RecordingStore()
    r = _ingest(st, {"owner": OWNER, "nodes": [
        {"id": "concept:a", "type": "concept", "title": "A"},
        {"id": "concept:b", "type": "concept", "title": "B"}],
        "edges": [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}]})
    assert r["merged"]["edges"]["created"] == 1
    assert st.bound("s") and all(v == f"{PHYS}concept:a" for v in st.bound("s"))
    assert st.bound("d") and all(v == f"{PHYS}concept:b" for v in st.bound("d"))


def test_edge_tombstone_key_is_composed_once(monkeypatch):
    """The endpoints reaching _stamp_edge_endpoints are already physical. The
    tombstone key must still carry the owner exactly once."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    st = _RecordingStore()
    _ingest(st, {"owner": OWNER, "nodes": [
        {"id": "concept:a", "type": "concept", "title": "A"},
        {"id": "concept:b", "type": "concept", "title": "B"}],
        "edges": [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}]})
    keys = st.bound("k", cypher="_EdgeTombstone")
    assert keys == [f"{PHYS}concept:a|RELATED_TO|concept:b"]
    assert keys[0].count(pkns.SEP) == 1


def test_edge_tombstone_key_unchanged_when_the_flag_is_off(monkeypatch):
    monkeypatch.delenv("VINC_PK_NAMESPACED", raising=False)
    st = _RecordingStore()
    _ingest(st, {"owner": OWNER, "nodes": [
        {"id": "concept:a", "type": "concept", "title": "A"},
        {"id": "concept:b", "type": "concept", "title": "B"}],
        "edges": [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}]})
    assert st.bound("k", cypher="_EdgeTombstone") == ["concept:a|RELATED_TO|concept:b"]


def test_orphan_decision_check_reads_the_composed_key(monkeypatch):
    """A store read on the write path. Left logical it returns zero rows under
    the flag, so a decision linked in the same call is still called an orphan."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    st = _RecordingStore()
    _ingest(st, {"owner": OWNER,
                 "nodes": [{"id": "decision:d1", "type": "decision", "title": "D"}]})
    about = [p for c, p in st.calls if "ABOUT" in c]
    assert about and all(p["id"] == f"{PHYS}decision:d1" for p in about)


# ---------------------------------------------------------------------------
# api/docstore.py extract_doc — the third chokepoint
# ---------------------------------------------------------------------------

def _patch_extract(monkeypatch):
    monkeypatch.setattr(docstore, "_meta",
                        lambda *a, **k: {"doc_id": "d1", "owner_id": OWNER,
                                         "title": "T", "origin": "", "content_time": ""})
    monkeypatch.setattr(docstore, "_relink_mentions", lambda *a, **k: 0)
    monkeypatch.setattr(docstore, "backfill_evidence", lambda *a, **k: {})


@pytest.mark.parametrize("flag,prefix", [("on", PHYS), (None, "")])
def test_extract_doc_composes_the_concept_key(monkeypatch, flag, prefix):
    if flag:
        monkeypatch.setenv("VINC_PK_NAMESPACED", flag)
    else:
        monkeypatch.delenv("VINC_PK_NAMESPACED", raising=False)
    _patch_extract(monkeypatch)
    st = _RecordingStore()
    r = docstore.extract_doc(st, "d1", owner=OWNER, apply=True,
                             candidates=[{"title": "Alpha", "one_liner": "A thing."}])
    assert r["executed"] is True and r["candidates"], r
    slug = r["candidates"][0]["slug"]
    ids = st.bound("id", cypher="Concept")
    assert ids and all(v == f"{prefix}concept:{slug}" for v in ids)


# ---------------------------------------------------------------------------
# engines/recall_ingest.merge_episode — the fourth chokepoint
# ---------------------------------------------------------------------------

def _episode(**over):
    ep = {"episode_id": "episode:e1", "title": "T", "date": "2026-08-22",
          "owner_id": OWNER, "tenant_id": "tenant:local", "domain": "vinc/x",
          "workspace": "", "mode": "", "summary": "s",
          "completed_items": [], "patterns": [], "source_file": ""}
    ep.update(over)
    return ep


class _RecordingSession(_RecordingStore):
    def run(self, cypher, params=None, **kw):
        self.calls.append((cypher, dict(params or {}) or dict(kw)))
        return []


@pytest.mark.parametrize("flag,expected",
                         [("on", f"{PHYS}episode:e1"), (None, "episode:e1")])
def test_merge_episode_composes_the_episode_key(monkeypatch, flag, expected):
    from engines.recall_ingest import merge_episode
    if flag:
        monkeypatch.setenv("VINC_PK_NAMESPACED", flag)
    else:
        monkeypatch.delenv("VINC_PK_NAMESPACED", raising=False)
    s = _RecordingSession()
    merge_episode(s, _episode())
    assert expected in s.bound("episode_id")
    assert all(v == expected for v in s.bound("id") if "Episode" in "".join(
        c for c, p in s.calls if p.get("id") == v))


# ---------------------------------------------------------------------------
# Round trip against a real store — write composed, read back logical
# ---------------------------------------------------------------------------

@unittest.skipIf(_import_driver()[0] is None, "embedded driver not installed")
class NamespacedRoundTripTests(unittest.TestCase):
    """The whole point of the strip hook: nothing above store/ ever sees the
    separator, even though the primary key carries it."""

    def _store(self, tmp):
        from store.backend import open_store
        from store.config import StoreConfig
        st = open_store(StoreConfig(data_dir=str(tmp)))
        assert st is not None
        st.provision(dim=8)
        return st

    def test_write_composes_and_read_returns_the_slug(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            # ⚠ 끝에 **pop 하지 않는다**. 여기서 지우면 켠 모드 CI 잡에서 이 파일
            # 뒤에 도는 모든 테스트가 조용히 꺼진 채 돌고, 그 잡의 초록은 "전량이
            # 켠 모드로 통과했다"가 아니라 "앞부분만 돌았다"가 된다(실측으로 물렸다:
            # test_view_viewpoints 7건이 그렇게 숨어 있었다). 원래 값을 되돌린다.
            _prev = os.environ.get("VINC_PK_NAMESPACED")
            os.environ["VINC_PK_NAMESPACED"] = "on"
            try:
                store = self._store(Path(td) / "db")
                out = fragment.merge_fragment(
                    {"owner": OWNER, "nodes": [
                        {"id": "concept:a", "type": "concept", "title": "A"},
                        {"id": "concept:b", "type": "concept", "title": "B"}],
                     "edges": [{"from": "concept:a", "to": "concept:b",
                                "type": "relates_to"}]},
                    store=store)
                self.assertEqual(out["merged"]["nodes"]["created"], 2, out)
                self.assertEqual(out["merged"]["edges"]["created"], 1, out)
                # The projection returns the logical slug, and collect() too.
                rows = store.run(
                    "MATCH (a:Concept)-[:RELATED_TO]->(b:Concept) "
                    "RETURN a.concept_id AS a, collect(b.concept_id) AS bs")
                self.assertEqual(rows[0]["a"], "concept:a")
                self.assertEqual(list(rows[0]["bs"]), ["concept:b"])
                store.close()
            finally:
                if _prev is None:
                    os.environ.pop("VINC_PK_NAMESPACED", None)
                else:
                    os.environ["VINC_PK_NAMESPACED"] = _prev

    def test_two_owners_hold_the_same_slug(self):
        """H1 itself: one physical node per slug across every account is what
        design A removes. Read isolation is a separate layer and untouched here."""
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            # ⚠ 끝에 **pop 하지 않는다**. 여기서 지우면 켠 모드 CI 잡에서 이 파일
            # 뒤에 도는 모든 테스트가 조용히 꺼진 채 돌고, 그 잡의 초록은 "전량이
            # 켠 모드로 통과했다"가 아니라 "앞부분만 돌았다"가 된다(실측으로 물렸다:
            # test_view_viewpoints 7건이 그렇게 숨어 있었다). 원래 값을 되돌린다.
            _prev = os.environ.get("VINC_PK_NAMESPACED")
            os.environ["VINC_PK_NAMESPACED"] = "on"
            try:
                store = self._store(Path(td) / "db")
                for owner in ("user:one", "user:two"):
                    out = fragment.merge_fragment(
                        {"owner": owner,
                         "nodes": [{"id": "concept:shared", "type": "concept",
                                    "title": f"held by {owner}"}]},
                        store=store)
                    self.assertEqual(out["merged"]["nodes"]["created"], 1, out)
                    self.assertNotIn("id_conflicts", out, out)
                rows = store.run("MATCH (n:Concept) WHERE n.concept_id = $id "
                                 "RETURN count(n) AS c",
                                 {"id": "concept:shared"})
                self.assertEqual(rows[0]["c"], 0, "logical id must not match a row")
                rows = store.run("MATCH (n:Concept) "
                                 "RETURN n.concept_id AS id, n.owner_id AS o")
                self.assertEqual(sorted(r["id"] for r in rows),
                                 ["concept:shared", "concept:shared"])
                self.assertEqual(sorted(r["o"] for r in rows),
                                 ["user:one", "user:two"])
                store.close()
            finally:
                if _prev is None:
                    os.environ.pop("VINC_PK_NAMESPACED", None)
                else:
                    os.environ["VINC_PK_NAMESPACED"] = _prev
