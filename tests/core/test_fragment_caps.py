# -*- coding: utf-8 -*-
"""api/fragment.py 필드 크기 상한(L1, 보안 감사 2026-08-18) — 거대 필드 노드는
어떤 store.run 보다 먼저 스킵되고 warnings+partial 로 통지된다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment  # noqa: E402


class _NoStore:
    """상한 검사는 존재확인(store.run)보다 먼저 스킵하므로 run 이 불리면 안 된다."""

    def run(self, *a, **k):
        raise AssertionError("store.run must not be called for an oversized-skip node")


def _merge(node: dict) -> dict:
    return fragment.merge_fragment({"owner": "user:x", "nodes": [node]}, store=_NoStore())


def test_oversized_title_skipped():
    r = _merge({"id": "concept:big", "type": "concept", "title": "x" * 20_000})
    assert r["merged"]["nodes"] == {"created": 0, "matched": 0}
    assert r.get("partial") is True
    assert any("title too large" in w for w in r["warnings"])


def test_too_many_tags_skipped():
    r = _merge({"id": "concept:tags", "type": "concept", "title": "ok",
                "tags": [f"t{i}" for i in range(300)]})
    assert r["merged"]["nodes"]["created"] == 0
    assert any("tags too large" in w for w in r["warnings"])


def test_oversized_props_skipped():
    r = _merge({"id": "concept:props", "type": "concept", "title": "ok",
                "props": {"blob": "y" * (600 * 1024)}})
    assert r["merged"]["nodes"]["created"] == 0
    assert any("props too large" in w for w in r["warnings"])
