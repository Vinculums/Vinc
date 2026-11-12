# -*- coding: utf-8 -*-
"""실행 기록의 프로토콜 상태를 쓰기 입구마다 꾸며 보고, 허브가 지금 무엇을 하는지 적는다.

실행 기록은 Episode `episode:run-*` 이다. 사람만 답하는 대기(`state: waiting`)를 에이전트가
`state`·`approved`·`answer` 를 직접 써서 풀 수 있으면 승인을 꾸밀 수 있다. 입구는 넷이다:
MCP `vinc_ingest`, REST v1 `POST /v1/fragments`, 계정의 웹 프록시 `ingest-fragment`,
기기 동기화 `POST /sync/push`. 모두 전권 키(scope full)로 부른다.

이 파일은 판정이 아니라 **지금의 동작 기록**이다. 거절하는 입구는 거절 코드를, 받는 입구는
받은 쓰기와 그 결과(승인함·호스트가 읽는 실행 상태)를 그대로 단정한다. 동작이 바뀌면 이
파일이 깨지고, 그때 테스트 이름과 단정을 함께 고친다.

스택은 실물이다: 임베디드 스토어 위의 사이드카(HTTP) ← 브리지 ← 공개 허브(HTTP).
웹 프록시는 이웃 테스트(test_run_approvals.py)처럼 `graph_owner_request` 를 직접 부른다.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from vinc_mcp import edverify, run_protocol, tools  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402

PROFILES = {p["id"]: p for p in json.loads(
    (ROOT / "integrations" / "fixtures" / "output_gate" / "proposed-role-checks.json")
    .read_text(encoding="utf-8"))["profiles"]}
ROLE = "concept:agent-dev-core"
OWNER = "user:u-1"
KEY = "vinc_sk_full_fixture"
SIDECAR_TOKEN = "fixture-sidecar"
ACCOUNT_SEED = bytes(range(32))
_BH_EMPTY = base64.urlsafe_b64encode(hashlib.sha256(b"").digest()).rstrip(b"=").decode()
PLAIN = "The change reads correctly; two findings follow."


class Hub:
    """임베디드 스토어 + 사이드카 HTTP + 공개 허브 HTTP. 키 하나가 OWNER 의 전권 키다."""

    def __init__(self, data_dir: Path):
        from api.auth import SessionAuth
        from api.server import VincApp, make_server as sidecar_server
        from store.backend import open_store
        from store.config import load_config

        cfg = load_config({"data_dir": str(data_dir)})
        self.store = open_store(cfg)
        assert self.store is not None, "embedded store did not open"
        self.store.provision(dim=cfg.embedding_dim)
        self.sidecar_app = VincApp(cfg, store=self.store, auth=SessionAuth(SIDECAR_TOKEN))
        self.sidecar = sidecar_server(self.sidecar_app, "127.0.0.1", 0)
        self.bridge = Bridge(base=f"http://127.0.0.1:{self.sidecar.server_address[1]}",
                             token=SIDECAR_TOKEN, owner=OWNER)

        def verify(key, space=None):
            return {"owner": OWNER, "scope": "full", "plan": "pro"} if key == KEY else None

        self.app = MCPHTTPApp(self.bridge, verify_key=verify,
                              account_public=edverify.secret_to_public(ACCOUNT_SEED))
        self.public = make_server(self.app, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.public.server_address[1]}"
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True)
                        for s in (self.sidecar, self.public)]
        for thread in self.threads:
            thread.start()

    def close(self):
        for server in (self.public, self.sidecar):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)
        self.sidecar_app.close()

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + KEY},
                                     method="POST")
        try:
            response = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read() or b"{}")

    def tool(self, name: str, **arguments) -> dict:
        """`/mcp` 의 tools/call 한 번. 도구 결과 JSON 을 돌려준다."""
        status, reply = self.post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": name, "arguments": arguments}})
        assert status == 200, reply
        result = reply["result"]
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        return json.loads(result["content"][0]["text"])

    def proxy(self, op: str, args: dict, **claims) -> dict:
        now = int(time.time())
        token = edverify.sign_assertion(ACCOUNT_SEED, {
            "iss": "account", "aud": "mcp", "iat": now, "exp": now + 60, "bh": _BH_EMPTY,
            "jti": uuid.uuid4().hex, "typ": "proxy", "sub": OWNER.split(":", 1)[1], "op": op,
            **claims})
        return self.app.graph_owner_request(token, args)

    def stored(self, eid: str) -> dict:
        """실행 프로토콜이 읽는 그대로(저장된 행)의 props."""
        got = self.bridge.request("GET", "/runs", params={"owner": OWNER, "run": eid,
                                                          "limit": 1, "stored": "1"})
        assert got.get("executed"), got
        return dict(got["runs"][0]["props"])

    def waiting_ids(self) -> list[str]:
        """승인함 화면이 읽는 목록(`runs-waiting` 프록시 op)."""
        out = self.proxy("runs-waiting", {})
        assert out.get("executed"), out
        return [item["id"] for item in out["runs"]]


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    monkeypatch.setenv("VINC_DURABLE_ACK", "off")
    monkeypatch.setenv("VINC_TOPIC_CONVERSION", "0")
    monkeypatch.setenv("VINC_STORE_SNAPSHOT", "")
    h = Hub(tmp_path / "store")
    try:
        props = dict(PROFILES[ROLE])
        props.update({"mission": "m", "tier": "execute", "rung": "strong", "never": [],
                      "reads": [], "act_policy": {"notify": "ask_first"}})
        seeded = tools.call("vinc_ingest", {"nodes": [{
            "id": ROLE, "type": "concept", "title": "Agent role", "domain": "vinc/agent/roles",
            "props": props}]}, h.bridge, forced_owner=OWNER)
        assert seeded.get("executed"), seeded
        yield h
    finally:
        h.close()


def _waiting_run(hub: Hub, run_id: str) -> str:
    """사람만 답하는 merge 대기에 든 실행 하나를 만든다. episode id 를 돌려준다."""
    run = hub.tool("vinc_run_start", role=ROLE, task="Review the change.", run_id=run_id)
    assert run.get("executed"), run
    out = hub.tool("vinc_run_submit", run=run["run"], output="Ready to merge once approved.",
                   person_only_requests=[{"action": "merge", "reason": "run merge for PR 12"}])
    assert out["state"] == "waiting" and out["decision"]["kind"] == "person_only", out
    assert run["run"] in hub.waiting_ids()
    return run["run"]


def _forged(props: dict) -> dict:
    """사람이 merge 를 승인한 뒤의 모양으로 바꾼 props. 대기는 지운다."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    forged = {k: v for k, v in props.items() if k != "pending"}
    forged.update(state="working", approved=["merge"], answer="approve", answered_at=now,
                  answered_actions=["merge"], answer_via="person", answered_by=OWNER)
    return forged


def _episode(eid: str, props: dict) -> dict:
    return {"id": eid, "type": "episode", "title": "Run", "domain": run_protocol.DOMAIN,
            "tags": ["agent-run"], "props": props}


def _still_waits(hub: Hub, eid: str, before: dict) -> None:
    """거절된 쓰기 뒤: 저장된 상태가 그대로이고 승인함에 남아 있다."""
    after = hub.stored(eid)
    assert after["state"] == "waiting" and after.get("approved") == []
    assert after["pending"] == before["pending"] and "answer" not in after
    assert eid in hub.waiting_ids()


# ── 거절하는 입구 ────────────────────────────────────────────────────────────

def test_mcp_ingest_refuses_run_state(hub):
    eid = _waiting_run(hub, "entr-mcp")
    before = hub.stored(eid)
    out = hub.tool("vinc_ingest", nodes=[_episode(eid, _forged(before))])
    assert out["executed"] is False and out["error"] == "run_state_protected"
    assert out["nodes"] == [eid]
    _still_waits(hub, eid, before)


def test_rest_v1_fragments_refuses_run_state(hub):
    eid = _waiting_run(hub, "entr-rest")
    before = hub.stored(eid)
    status, out = hub.post("/v1/fragments", {"nodes": [_episode(eid, _forged(before))]})
    assert status == 403 and out["error"]["code"] == "run_state_protected", out
    _still_waits(hub, eid, before)


@pytest.mark.parametrize("op", ["ingest-fragment", "ingest-pack"])
def test_web_proxy_ingest_refuses_run_state(hub, op):
    eid = _waiting_run(hub, "entr-proxy-" + op)
    before = hub.stored(eid)
    fragment = {"nodes": [_episode(eid, _forged(before))], "edges": []}
    args = fragment if op == "ingest-fragment" else {"pack": {"fragment": fragment}}
    out = hub.proxy(op, args)
    assert out["executed"] is False and out["error"] == "run_state_protected"
    assert out["nodes"] == [eid]
    _still_waits(hub, eid, before)


# ── 받는 입구 ────────────────────────────────────────────────────────────────

def test_sync_push_accepts_run_state_today_and_the_run_reads_as_approved(hub):
    eid = _waiting_run(hub, "entr-sync")
    before = hub.stored(eid)
    # 기기가 찍는 보통의 스탬프: 지금보다 1초 뒤의 HLC 와 rev 하나.
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=1)
    node = {**_episode(eid, _forged(before)), "_rev": 1,
            "_updated_at": later.strftime("%Y-%m-%dT%H:%M:%S.") + f"{later.microsecond // 1000:03d}Z"}
    status, out = hub.post("/sync/push", {"bundle": {"nodes": [node], "edges": []}})
    # 받은 쓰기: 거절 코드 없이 노드 하나를 병합했다.
    assert status == 200 and out["executed"] is True, out
    assert out.get("error") is None and out["merged"] == 1, out

    # 저장된 실행 기록이 사람이 승인한 모양이 됐다.
    after = hub.stored(eid)
    assert after["state"] == "working" and after["approved"] == ["merge"]
    assert after["answer_via"] == "person" and after["answered_by"] == OWNER
    assert "pending" not in after

    # 승인함 화면에서 사라졌다.
    assert eid not in hub.waiting_ids()

    # 호스트가 읽는 실행 상태(`vinc_runs`, `vinc_run_start` 재개 뷰)가 승인을 보인다.
    listed = hub.tool("vinc_runs", run=eid)
    assert [r["props"]["state"] for r in listed["runs"]] == ["working"]
    view = hub.tool("vinc_run_start", role=ROLE, task="Review the change.", run_id="entr-sync")
    assert view["resumed"] is True and view["state"] == "working"
    assert view["last_answer"]["answer"] == "approve" and view["last_answer"]["via"] == "person"

    # 결과: 에이전트의 다음 제출은 merge 를 다시 묻지 않고 실행을 성공으로 끝낸다.
    final = hub.tool("vinc_run_submit", run=eid, output=PLAIN,
                     person_only_requests=[{"action": "merge", "reason": "approved"}])
    assert final["state"] == "done" and final["outcome"] == "success", final
