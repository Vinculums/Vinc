# -*- coding: utf-8 -*-
"""PR-0 (v0.8.4 axis 1) regression for the two measurement scripts.

scripts/measure_pk_collisions.py is exercised against a fake store double
(the same technique tests/test_owner_isolation.py uses for _ChunkSpy) rather
than a real EmbeddedBackend. That is a deliberate choice, not a shortcut: the
embedded backend enforces primary-key uniqueness per label at the engine
level (verified empirically while writing the script -- a second CREATE with
a duplicate primary key raises a RuntimeError), so "one known collision"
cannot exist as two real rows in one store today. The fake store lets this
test assert the script's query shape and its aggregation/formatting without
fighting a constraint the script is specifically measuring the absence of.

scripts/inventory_id_bindings.py is exercised against real temp files, since
it is a plain regex sweep with no store involved.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import measure_pk_collisions as mpc   # noqa: E402
import inventory_id_bindings as iib   # noqa: E402


class _FakeStore:
    """A store double: routes on recognizable substrings of the Cypher the
    script itself generates, the way _ChunkSpy/_S route in
    tests/test_owner_isolation.py. Holds exactly the three fixtures the task
    asks for: one known collision (Concept), one legacy owner (Record), one
    Bridge with an empty domain."""

    def run(self, cypher: str, params: dict | None = None) -> list[dict]:
        params = params or {}

        if "collect(DISTINCT" in cypher:
            if "Concept" in cypher:
                return [{"slug": "concept:shared-slug", "owners": ["user:a", "user:b"]}]
            return []

        if "owner_id IN $legacy" in cypher:
            if "Record" in cypher:
                return [{"owner": "tenant:local", "c": 1}]
            return []

        if "RETURN n.owner_id AS owner, count(*)" in cypher:
            if "Record" in cypher:
                return [{"owner": "tenant:local", "c": 1}]
            if "Concept" in cypher:
                return [{"owner": "user:a", "c": 1}, {"owner": "user:b", "c": 1}]
            return []

        if "CONTAINS $sep" in cypher:
            return [{"c": 0}]

        if "n.title = n." in cypher:
            return [{"c": 0}]

        if "source_domain" in cypher:
            return [{"c": 1}]   # the one Bridge with an empty domain

        if cypher.startswith("MATCH (n:Bridge) WHERE n.owner_id IS NULL"):
            return [{"c": 0}]

        if "STARTS WITH $prefix" in cypher:
            return [{"id": "concept:doc.d275b0a42bcb8b2ac"}, {"id": "concept:doc.vinc.spec.dataplane"}]

        raise AssertionError(f"unrouted query in fake store: {cypher}")


def test_owner_distribution_and_legacy_rows():
    s = _FakeStore()
    assert mpc.owner_distribution(s, "Record") == {"tenant:local": 1}
    assert mpc.legacy_owner_rows(s, "Record") == {"tenant:local": 1}
    assert mpc.legacy_owner_rows(s, "Concept") == {}


def test_slug_collision_is_visible_when_the_engine_allows_it():
    """Not achievable on a real store today (PK uniqueness) -- see module
    docstring -- but the script's aggregation must surface it once it is."""
    s = _FakeStore()
    coll = mpc.slug_collisions(s, "Concept")
    assert coll == [{"slug": "concept:shared-slug", "owners": ["user:a", "user:b"]}]
    assert mpc.slug_collisions(s, "Record") == []


def test_bridge_empty_domain_and_owner():
    s = _FakeStore()
    assert mpc.bridge_empty_domain(s) == 1
    assert mpc.bridge_empty_owner(s) == 0


def test_doc_mirror_id_format_split():
    s = _FakeStore()
    out = mpc.doc_mirror_id_formats(s)
    assert out == {"hashed": 1, "unhashed": 1, "total": 2}


def test_measure_assembles_all_labels_and_formats_as_text():
    s = _FakeStore()
    result = mpc.measure(s)
    assert set(result["labels"]) == set(mpc.LABELS)
    assert result["labels"]["Bridge"]["empty_domain"] == 1
    text = mpc.format_text(result)
    assert "Bridge rows with empty domain" in text
    assert "concept:doc.* mirror ids" in text


# ── inventory_id_bindings.py: a real scan over a small temp tree ───────────

def test_inventory_scan_finds_each_pattern(tmp_path, monkeypatch):
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    (api_dir / "sample.py").write_text(
        "def f(store, id):\n"
        "    store.run('MATCH (n:Concept) WHERE n.concept_id = $id RETURN n', {'id': id})\n"
        "    store.run('MATCH (n) WHERE n.concept_id IN $ids RETURN n')\n"
        "    store.run(\"MATCH (n) WHERE n.concept_id STARTS WITH 'concept:'\")\n"
        "    prefix = seed.split(':', 1)[0]\n"
        "    kind = 'concept:margiela'\n"
        "    where = 'a.id = b.id'\n",
        encoding="utf-8")
    (tmp_path / "store").mkdir()
    (tmp_path / "engines").mkdir()

    monkeypatch.setattr(iib, "REPO_ROOT", tmp_path)
    grouped = iib.scan()

    assert grouped["dollar_id"] == ["api/sample.py:2"]
    assert grouped["in_dollar_ids"] == ["api/sample.py:3"]
    assert grouped["starts_with"] == ["api/sample.py:4"]
    assert grouped["split_colon"] == ["api/sample.py:5"]
    assert grouped["kind_prefix_literal"] == ["api/sample.py:4", "api/sample.py:6"]
    assert grouped["alias_id_eq_alias_id"] == ["api/sample.py:7"]


def test_inventory_format_text_reports_a_total():
    grouped = {"dollar_id": ["a.py:1"], "in_dollar_ids": [], "starts_with": [],
               "split_colon": [], "kind_prefix_literal": [], "alias_id_eq_alias_id": []}
    text = iib.format_text(grouped)
    assert "total: 1" in text
