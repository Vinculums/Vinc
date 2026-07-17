# -*- coding: utf-8 -*-
"""vinc_sync S2 — 오케스트레이션 글루(커서·순서·루프).

실 엔진 2-스토어 왕복은 서버측에서 검증한다(Windows 로컬 임베디드 불가,
[[windows-embedded-test-fails]]). 여기서는 export/apply 를 스텁으로 갈아 **글루
로직**만 본다: pull 먼저인가, 커서가 성공 후에만 전진하는가, 페이지 루프가 끝나는가.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import sync  # noqa: E402


class _MetaStore:
    """_SyncMeta 만 흉내내는 dict 스토어(커서·선택도메인 get/set)."""

    def __init__(self):
        self.kv: dict[str, str] = {}

    def run(self, cypher, params=None):
        p = params or {}
        if cypher.startswith("MERGE (m:_SyncMeta"):
            self.kv[p["k"]] = p["v"]
            return []
        if "MATCH (m:_SyncMeta)" in cypher:
            return [{"v": self.kv.get(p.get("k"), "")}]
        raise AssertionError(f"예상 밖 쿼리: {cypher[:40]}")


def test_cursor_roundtrip():
    s = _MetaStore()
    assert sync.get_cursor(s, "push") == 0
    sync.set_cursor(s, "push", 7)
    assert sync.get_cursor(s, "push") == 7
    assert sync.get_cursor(s, "pull") == 0        # 커서는 키별로 독립


def test_run_pulls_before_pushes(monkeypatch):
    """플랜 §6-1: pull 먼저(원격 최신 받아 병합 후 push)."""
    calls = []
    monkeypatch.setattr(sync, "pull_apply",
                        lambda *a, **k: calls.append("pull") or {"phase": "pull"})
    monkeypatch.setattr(sync, "push_local",
                        lambda *a, **k: calls.append("push") or {"phase": "push"})
    out = sync.run(_MetaStore(), None, owner="user:local", remote=object())
    assert calls == ["pull", "push"]
    assert out["executed"] and out["pull"]["phase"] == "pull"


def test_push_advances_cursor_only_with_content(monkeypatch):
    s = _MetaStore()

    # 내보낼 게 없으면 커서 불변·remote 미호출
    monkeypatch.setattr(sync, "export_delta",
                        lambda **k: {"executed": True, "nodes": [], "edges": [],
                                     "next_cursor": k["since"]})
    called = {"push": 0}
    remote = type("R", (), {"push": lambda self, c: called.__setitem__("push", called["push"] + 1)})()
    r = sync.push_local(s, None, owner="user:local", remote=remote)
    assert r["sent"] == 0 and called["push"] == 0 and sync.get_cursor(s, "push") == 0

    # 내보낼 게 있으면 push 후 커서 전진
    monkeypatch.setattr(sync, "export_delta",
                        lambda **k: {"executed": True, "next_cursor": 12,
                                     "nodes": [{"id": "concept:a", "_seq": 12}], "edges": []})
    monkeypatch.setattr(sync, "chunk_bundle", lambda b: [b])
    remote2 = type("R", (), {"push": lambda self, c: {"applied": 1}})()
    r2 = sync.push_local(s, None, owner="user:local", remote=remote2)
    assert r2["sent"] == 1 and sync.get_cursor(s, "push") == 12


def test_pull_loop_paginates_and_terminates(monkeypatch):
    s = _MetaStore()
    # 허브가 2페이지(커서 0→5→9) 후 빈 페이지
    pages = iter([
        {"nodes": [{"id": "c1"}], "next_cursor": 5},
        {"nodes": [{"id": "c2"}], "next_cursor": 9},
        {"nodes": [], "next_cursor": 9},
    ])
    remote = type("R", (), {"pull": lambda self, since: next(pages)})()
    monkeypatch.setattr(sync, "apply_delta",
                        lambda **k: {"applied": len(k["bundle"].get("nodes") or []),
                                     "skipped_stale": 0, "edges": 0, "warnings": []})
    r = sync.pull_apply(s, None, owner="user:local", remote=remote)
    assert r["applied"] == 2                       # 두 페이지의 노드
    assert r["cursor"] == 9 and sync.get_cursor(s, "pull") == 9
    assert r["pages"] == 3                          # 마지막 빈 페이지까지


def test_pull_stops_when_cursor_does_not_advance(monkeypatch):
    """next_cursor 가 안 늘면(노드 있어도) 즉시 중단 — 무한루프 방어."""
    s = _MetaStore()
    remote = type("R", (), {"pull": lambda self, since: {"nodes": [{"id": "x"}], "next_cursor": 0}})()
    monkeypatch.setattr(sync, "apply_delta",
                        lambda **k: {"applied": 1, "skipped_stale": 0, "edges": 0, "warnings": []})
    r = sync.pull_apply(s, None, owner="user:local", remote=remote)
    assert r["pages"] == 1                          # 진행 없음 → 1회로 종료
