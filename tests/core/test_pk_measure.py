# -*- coding: utf-8 -*-
"""PR-0 (v0.8.4 axis 1) regression for the pk collision measurement script.

scripts/measure_pk_collisions.py is exercised against a fake store double
(the same technique tests/mcp/test_owner_isolation.py uses for _ChunkSpy) rather
than a real EmbeddedBackend. That is a deliberate choice, not a shortcut: the
embedded backend enforces primary-key uniqueness per label at the engine
level (verified empirically while writing the script -- a second CREATE with
a duplicate primary key raises a RuntimeError), so "one known collision"
cannot exist as two real rows in one store today. The fake store lets this
test assert the script's query shape and its aggregation/formatting without
fighting a constraint the script is specifically measuring the absence of.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import measure_pk_collisions as mpc   # noqa: E402


class _FakeStore:
    """A store double: routes on recognizable substrings of the Cypher the
    script itself generates, the way _ChunkSpy/_S route in
    tests/mcp/test_owner_isolation.py. Holds exactly the three fixtures the task
    asks for: one known collision (Concept), one legacy owner (Record), one
    Bridge with an empty domain, and three _SyncBase rows of which one is an
    orphan."""

    def run(self, cypher: str, params: dict | None = None) -> list[dict]:
        params = params or {}

        # _SyncBase is routed FIRST on purpose: the per-label owner join emits
        # the same "RETURN n.owner_id AS owner, count(*)" tail the plain owner
        # distribution does, so a later branch would swallow it and the test
        # would assert against the wrong query's fixture.
        if "_SyncBase" in cypher:
            if "b.label AS label" in cypher:
                return [{"label": "Concept", "c": 2}, {"label": "Record", "c": 1}]
            if "base_updated_at" in cypher:
                return [{"ua": "2026-09-02T00:00:00Z"}] if "DESC" in cypher \
                    else [{"ua": "2026-07-18T00:00:00Z"}]
            if "n.owner_id AS owner" in cypher:
                # Two bases resolve to a Concept owned by user:a; the Record
                # base resolves to nothing, which is the orphan.
                return [{"owner": "user:a", "c": 2}] if "(n:Concept)" in cypher else []
            return [{"c": 3}]

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


def test_sync_base_resolves_owners_and_counts_orphans():
    """The table migrate_pk_namespacing refuses on. The join answers whose NODE
    each base is for; it deliberately does not claim to answer who wrote it."""
    s = _FakeStore()
    sb = mpc.sync_base(s)
    assert sb["total"] == 3
    assert sb["owner_distribution"] == {"user:a": 2}
    assert sb["resolved"] == 2
    assert sb["orphans"] == 1          # total minus what resolved to a node
    assert sb["by_label"] == {"Concept": 2, "Record": 1}
    assert sb["oldest_base"] == "2026-07-18T00:00:00Z"
    assert sb["newest_base"] == "2026-09-02T00:00:00Z"


def test_measure_assembles_all_labels_and_formats_as_text():
    s = _FakeStore()
    result = mpc.measure(s)
    assert set(result["labels"]) == set(mpc.LABELS)
    assert result["labels"]["Bridge"]["empty_domain"] == 1
    text = mpc.format_text(result)
    assert "Bridge rows with empty domain" in text
    assert "concept:doc.* mirror ids" in text
    assert result["sync_base"]["orphans"] == 1
    assert "_SyncBase" in text
    # The caveat travels with the number: the distribution is about whose node
    # the base belongs to, and reading it as authorship is the M3 mistake.
    assert "not who wrote it" in text
