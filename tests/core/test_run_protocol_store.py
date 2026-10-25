"""실행 프로토콜을 실엔진에 올린다: 상태가 실행 기록 Episode 로 저장되고 되읽히며, 끝난 실행이
`vinc_runs` 목록과 인용 조회에 그대로 나온다. 역할 읽기만 고정 계약으로 대신한다.

근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from api import fragment as frag, runs
from store.backend import open_store
from store.config import load_config
from vinc_mcp import tools
from vinc_mcp.bridge import Bridge

ROOT = Path(__file__).resolve().parents[2]
OWNER = "user:run-protocol-owner"
ROLE = "concept:agent-dev-core"
PROFILE = next(p for p in json.loads(
    (ROOT / "integrations" / "fixtures" / "output_gate" / "proposed-role-checks.json")
    .read_text(encoding="utf-8"))["profiles"] if p["id"] == ROLE)
PROPS = {**PROFILE, "mission": "m", "tier": "execute", "rung": "strong", "never": ["merge"]}


@pytest.fixture()
def store(tmp_path):
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(MEMORY_STORE_BACKEND="embedded", MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path / "db")})
    st = open_store(cfg)
    st.provision(dim=cfg.embedding_dim)
    try:
        frag.merge_fragment({"owner": OWNER, "nodes": [
            {"id": ROLE, "type": "concept", "title": "Core dev", "domain": "agent",
             "props": PROPS}], "edges": []}, store=st)
        yield st
    finally:
        st.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _StoreBridge(Bridge):
    def __init__(self, store):
        super().__init__(base="http://127.0.0.1:1", token="t", owner=OWNER)
        self.store = store

    def request(self, method, path, params=None, body=None, timeout=60):
        if path == "/roles":
            return {"executed": True, "found": True, "relations": [], "peers": [],
                    "node": {"id": params["role"], "title": "Core dev",
                             "domain": "vinc/agent/roles", "props": PROPS}}
        if path == "/runs":
            return runs.list_runs(self.store, owner=params["owner"], run=params.get("run"),
                                  cited=params.get("cited"), role=params.get("role"),
                                  limit=params.get("limit") or 50)
        if path == "/ingest/fragment":
            return frag.merge_fragment(dict(body), store=self.store)
        return {"executed": False, "error": "unexpected path " + path}


def test_a_run_survives_the_store_and_lands_in_the_runs_list(store):
    bridge = _StoreBridge(store)
    opened = tools.call("vinc_run_start", {"role": ROLE, "task": "Review it.", "run_id": "live-1"},
                        bridge)
    assert opened["executed"] and opened["state"] == "working"
    waiting = tools.call("vinc_run_submit", {
        "run": "live-1", "output": "Ready to merge.",
        "person_only_requests": [{"action": "merge", "reason": "green"}]}, bridge)
    assert waiting["state"] == "waiting" and waiting["pending_actions"] == ["merge"]
    shown = runs.list_runs(store, owner=OWNER, run="live-1")["runs"][0]["props"]
    assert shown["state"] == "waiting" and shown["pending"]["actions"] == ["merge"]
    tools.call("vinc_run_submit", {"run": "live-1", "answer": "approve"}, bridge)
    done = tools.call("vinc_run_submit", {
        "run": "live-1", "output": "The change reads correctly; two findings follow.",
        "cited_ids": ["record:claim-x"]}, bridge)
    assert done["state"] == "done" and done["outcome"] == "success"
    listed = runs.list_runs(store, owner=OWNER, cited="record:claim-x")
    assert [r["run_id"] for r in listed["runs"]] == ["live-1"]
    assert listed["runs"][0]["outcome"] == "success" and listed["runs"][0]["role"] == ROLE
    # 같은 run_id 로 다시 열어도 새 실행이 생기지 않는다.
    again = tools.call("vinc_run_start", {"role": ROLE, "task": "Review it.", "run_id": "live-1"},
                       bridge)
    assert again["resumed"] is True and again["state"] == "done"
    assert runs.list_runs(store, owner=OWNER)["total"] == 1
