# -*- coding: utf-8 -*-
"""vinc_sync S2 — 오케스트레이션 글루(커서·순서·루프).

실 엔진 2-스토어 왕복은 test_embedded_backend.py 와 서버측이 검증한다
([[record:vinc-tests-need-a-clean-interpreter-on-windows]]). 여기서는 export/apply 를 스텁으로 갈아 **글루
로직**만 본다: pull 먼저인가, 커서가 성공 후에만 전진하는가, 페이지 루프가 끝나는가.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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


# ── §4 일회성 전량 왕복(이미 갈라진 출생 화해) ────────────────────────────────
def test_birth_resync_rewinds_both_cursors_exactly_once():
    s = _MetaStore()
    sync.set_cursor(s, "push", 42)
    sync.set_cursor(s, "pull", 17)

    assert sync.ensure_birth_resync(s) is True
    # **둘 다** 0 이라야 한다 — 한쪽만 되감으면 수렴이 한 방향으로만 간다
    # (pull 만 0 이면 이 기기가 받기만 하고, 자기가 가진 더 이른 출생은 안 올라간다).
    assert sync.get_cursor(s, "push") == 0 and sync.get_cursor(s, "pull") == 0

    # 표식이 찍히기 전에는 아직 '한 번'이 안 끝난 것 — 다시 물어도 True(재실행 안전)
    assert sync.ensure_birth_resync(s) is True
    s.kv[f"{sync._ONESHOT_KEY}"] = "done"
    sync.set_cursor(s, "push", 9)
    assert sync.ensure_birth_resync(s) is False
    assert sync.get_cursor(s, "push") == 9, "표식 이후에는 커서를 건드리면 안 된다"


def test_run_marks_the_oneshot_only_after_the_session_completes(monkeypatch):
    """중간에 죽으면 표식을 안 찍는다 — 커서가 0 인 채 남아 다음 세션이 다시 한다."""
    s = _MetaStore()
    monkeypatch.setattr(sync, "pull_apply", lambda *a, **k: {"phase": "pull"})

    def _boom(*a, **k):
        raise RuntimeError("네트워크 끊김")

    monkeypatch.setattr(sync, "push_local", _boom)
    try:
        sync.run(s, None, owner="user:local", remote=object())
    except RuntimeError:
        pass
    assert s.kv.get(sync._ONESHOT_KEY, "") != "done"
    assert sync.get_cursor(s, "pull") == 0        # 되감긴 채로 남는다

    monkeypatch.setattr(sync, "push_local", lambda *a, **k: {"phase": "push"})
    out = sync.run(s, None, owner="user:local", remote=object())
    assert out["birth_resync"] is True
    assert s.kv.get(sync._ONESHOT_KEY) == "done"
    # 두 번째 세션은 평범한 delta 다
    out2 = sync.run(s, None, owner="user:local", remote=object())
    assert out2["birth_resync"] is False


class _KeyedRemote:
    """계정 지문만 있으면 되는 최소 원격 스텁."""

    def __init__(self, token: str):
        self.token = token


def test_cursors_survive_the_same_account():
    """같은 키로 다시 붙는 것은 전환이 아니다 — 매 세션 전량 재동기화가 되면 안 된다."""
    s = _MetaStore()
    sync.set_cursor(s, "pull", 42)
    assert sync.reset_cursors_on_account_change(s, _KeyedRemote("vinc_sk_a")) is False
    assert sync.reset_cursors_on_account_change(s, _KeyedRemote("vinc_sk_a")) is False
    assert sync.get_cursor(s, "pull") == 42


def test_cursors_reset_when_the_account_changes():
    """A 의 커서를 B 에 들이대면 B 의 그 번호 아래가 통째로 안 내려온다(무음 누락)."""
    s = _MetaStore()
    sync.reset_cursors_on_account_change(s, _KeyedRemote("vinc_sk_a"))
    for k in sync._CURSOR_KEYS:
        sync.set_cursor(s, k, 99)
    assert sync.reset_cursors_on_account_change(s, _KeyedRemote("vinc_sk_b")) is True
    for k in sync._CURSOR_KEYS:
        assert sync.get_cursor(s, k) == 0, k


def test_selected_domains_are_not_touched_by_an_account_switch():
    """무엇을 내보낼지는 계정이 아니라 이 기기에서의 사용자 선택이다."""
    s = _MetaStore()
    sync.set_selected_domains(s, ["design"])
    sync.reset_cursors_on_account_change(s, _KeyedRemote("vinc_sk_a"))
    sync.reset_cursors_on_account_change(s, _KeyedRemote("vinc_sk_b"))
    assert sync.get_selected_domains(s) == ["design"]


def test_first_connection_does_not_count_as_a_switch():
    """처음 붙는 기기는 초기화할 커서가 없다 — 전량 재동기화를 유발하면 안 된다."""
    s = _MetaStore()
    sync.set_cursor(s, "pull", 5)
    assert sync.reset_cursors_on_account_change(s, _KeyedRemote("vinc_sk_a")) is False
    assert sync.get_cursor(s, "pull") == 5


def test_tokenless_remote_holds_judgement():
    """키를 모르는 원격(테스트 스텁)에서 커서를 날리면 스텁이 데이터를 지운다."""
    s = _MetaStore()
    sync.set_cursor(s, "pull", 8)
    assert sync.reset_cursors_on_account_change(s, object()) is False
    assert sync.get_cursor(s, "pull") == 8
