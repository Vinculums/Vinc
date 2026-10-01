# -*- coding: utf-8 -*-
"""PR-A (v0.8.4 axis 1, design A) — store/pkns.py and the U+001F ingest guard.

store/pkns.py is the flag-gated compose/strip module (vinc.plan.node-pk-namespacing
§7); this file also covers the wiring in api/fragment.py and api/docstore.py that
rejects the reserved separator in any id/string field before it can be mistaken
for a physical key later (PR-B's decomposition discriminator is exactly this byte).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from store import pkns  # noqa: E402
from api import docstore, fragment  # noqa: E402


# ---------------------------------------------------------------------------
# store/pkns.py — enabled(), compose(), strip(), has_sep()
# ---------------------------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VINC_PK_NAMESPACED", raising=False)
    assert pkns.enabled() is False


@pytest.mark.parametrize("v", ["1", "true", "True", "on", "ON", "yes"])
def test_enabled_truthy_values(monkeypatch, v):
    monkeypatch.setenv("VINC_PK_NAMESPACED", v)
    assert pkns.enabled() is True


def test_compose_and_strip_are_identity_when_disabled(monkeypatch):
    monkeypatch.delenv("VINC_PK_NAMESPACED", raising=False)
    # 꺼진 스토어는 owner 가 뭐든(빈 문자열 포함) 검증 없이 그대로 통과한다 —
    # 로컬 데스크탑이 이 파일의 존재를 몰라야 한다는 계약(vinc.plan.node-pk-namespacing §7).
    assert pkns.compose("", "concept:x") == "concept:x"
    assert pkns.compose("anything-unmapped", "concept:x") == "concept:x"
    assert pkns.strip("concept:x") == "concept:x"


def test_compose_tenant_local_maps_to_itself(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    assert pkns.compose("tenant:local", "concept:margiela") == "tenant:local\x1fconcept:margiela"


@pytest.mark.parametrize("owner", ["user:local", "tenant:shared"])
def test_compose_raises_for_unmeasured_legacy_owners(monkeypatch, owner):
    """W0 measured only tenant:local in the dev store; user:local and tenant:shared
    never appeared, so PR-A must not guess a mapping for them (a wrong guess would
    silently compose a wrong physical key). The error names PR-E as the step that
    fills this row from a real hub measurement."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    with pytest.raises(ValueError, match="PR-E"):
        pkns.compose(owner, "concept:x")


def test_compose_raises_on_empty_owner(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    with pytest.raises(ValueError):
        pkns.compose("", "concept:x")


def test_compose_raises_on_unknown_owner(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    with pytest.raises(ValueError):
        pkns.compose("tenant:mystery", "concept:x")


def test_compose_is_idempotent(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    once = pkns.compose("tenant:local", "concept:x")
    twice = pkns.compose("tenant:local", once)
    assert once == twice == "tenant:local\x1fconcept:x"


def test_compose_normalize_false_bypasses_the_map(monkeypatch):
    """normalize=False is the escape hatch for owners that are already real,
    canonical account ids (not a legacy default needing remapping) — they never
    need to appear in the small normalization table."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    assert pkns.compose("user:019fae48-real-account", "concept:x", normalize=False) \
        == "user:019fae48-real-account\x1fconcept:x"
    with pytest.raises(ValueError):
        pkns.compose("", "concept:x", normalize=False)


def test_strip_decomposes_a_physical_key(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    assert pkns.strip("tenant:local\x1fconcept:x") == "concept:x"
    assert pkns.strip("concept:x") == "concept:x"   # no SEP — already logical


def test_has_sep_recurses_into_lists_and_dicts():
    assert pkns.has_sep("a\x1fb") is True
    assert pkns.has_sep(["ok", "a\x1fb"]) is True
    assert pkns.has_sep({"k": "a\x1fb"}) is True
    assert pkns.has_sep({"a\x1fb": "ok"}) is True
    assert pkns.has_sep({"k": ["ok", {"n": "a\x1fb"}]}) is True
    assert pkns.has_sep({"k": "ok", "list": ["fine"]}) is False
    assert pkns.has_sep(None) is False
    assert pkns.has_sep(42) is False


# ---------------------------------------------------------------------------
# api/fragment.py — reject U+001F before any store.run
# ---------------------------------------------------------------------------

class _NoStore:
    """The guard must fire before any store access (same shape as the L1
    oversized-field guard in tests/core/test_fragment_caps.py)."""

    def run(self, *a, **k):
        raise AssertionError("store.run must not be called for a U+001F-skip")


def test_merge_node_rejects_sep_in_id():
    r = fragment.merge_fragment(
        {"owner": "user:x", "nodes": [{"id": "concept:a\x1fb", "type": "concept", "title": "ok"}]},
        store=_NoStore())
    assert r["merged"]["nodes"] == {"created": 0, "matched": 0}
    assert any("U+001F" in w for w in r["warnings"])
    assert r.get("partial") is True


def test_merge_node_rejects_sep_in_title():
    r = fragment.merge_fragment(
        {"owner": "user:x", "nodes": [{"id": "concept:a", "type": "concept", "title": "bad\x1ftitle"}]},
        store=_NoStore())
    assert r["merged"]["nodes"]["created"] == 0
    assert any("U+001F" in w for w in r["warnings"])


def test_merge_node_rejects_sep_in_tags_and_props():
    r = fragment.merge_fragment(
        {"owner": "user:x", "nodes": [{
            "id": "concept:a", "type": "concept", "title": "ok",
            "tags": ["fine", "bad\x1ftag"], "props": {"note": "bad\x1fnote"}}]},
        store=_NoStore())
    assert r["merged"]["nodes"]["created"] == 0
    assert any("U+001F" in w for w in r["warnings"])


def test_merge_edge_rejects_sep_in_endpoints():
    r = fragment.merge_fragment(
        {"owner": "user:x", "edges": [
            {"from": "concept:a\x1fb", "to": "concept:b", "type": "relates_to"}]},
        store=_NoStore())
    assert r["merged"]["edges"]["created"] == 0
    assert any("U+001F" in w for w in r["warnings"])


def test_clean_node_still_merges_when_no_sep_present():
    """The guard must not false-positive on ordinary payloads."""
    calls = []

    class _RecordingStore:
        def run(self, cypher, params=None):
            calls.append(cypher)
            return []

    r = fragment.merge_fragment(
        {"owner": "user:x", "nodes": [{"id": "concept:clean", "type": "concept", "title": "ok"}]},
        store=_RecordingStore())
    assert r["merged"]["nodes"]["created"] == 1
    assert calls   # store was actually consulted for the clean node


# ---------------------------------------------------------------------------
# api/docstore.py — put_doc rejects U+001F in owner/doc_id/title/origin
# ---------------------------------------------------------------------------

def test_put_doc_rejects_sep_before_touching_the_store():
    r = docstore.put_doc(store=_NoStore(), owner="user:a\x1fb", text="hello")
    assert r["executed"] is False
    assert "U+001F" in r["error"]


def test_put_doc_rejects_sep_in_title_and_origin():
    r = docstore.put_doc(store=_NoStore(), owner="user:a", text="hello",
                         title="bad\x1ftitle")
    assert r["executed"] is False
    r2 = docstore.put_doc(store=_NoStore(), owner="user:a", text="hello",
                          origin="bad\x1forigin")
    assert r2["executed"] is False


def test_put_doc_does_not_reject_sep_in_body_text():
    """The reserved byte is a key-material discriminator, not a content
    censor: free-form document text is untouched (D45 — Chunk=SSoT)."""
    class _RecordingStore:
        def run(self, cypher, params=None):
            return []

    r = docstore.put_doc(store=_RecordingStore(), owner="user:a",
                         text="body text with a stray \x1f byte is fine")
    assert r["executed"] is True
