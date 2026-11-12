# -*- coding: utf-8 -*-
"""실행 기록의 프로토콜 상태를 쓰기 입구마다 꾸며 보고, 허브가 지금 무엇을 하는지 적는다.

실행 기록은 Episode `episode:run-*` 이다. 사람만 답하는 대기(`state: waiting`)를 에이전트가
`state`·`approved`·`answer` 를 직접 써서 풀 수 있으면 승인을 꾸밀 수 있다. 입구는 넷이다:
MCP `vinc_ingest`, REST v1 `POST /v1/fragments`, 계정의 웹 프록시 `ingest-fragment`,
기기 동기화 `POST /sync/push`. 모두 전권 키(scope full)로 부른다.

앞의 셋은 그런 쓰기를 거절한다. 동기화는 묶음을 거절하지 않고, 실행 기록의 프로토콜 상태만
받는 스토어가 저장한 값으로 남긴 채 나머지를 보통의 동기화 규칙으로 적용한다. 규칙은
`store/run_state.py` 한 벌이고 동기화 적용(`api/sync.py` `apply_delta`)이 부르므로, 허브는 push 에서,
기기는 pull 에서 자기 값을 남긴다. 각 테스트는 쓰기 뒤의 저장된 행, 승인함, 호스트가 읽는 실행 상태를
함께 단정한다.

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

from store import run_state  # noqa: E402
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


# ── 허브의 값을 남기는 입구: 기기 동기화 ─────────────────────────────────────

def _stamp(seconds: int = 1) -> str:
    """기기가 찍는 보통의 HLC: 지금보다 조금 뒤."""
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    return later.strftime("%Y-%m-%dT%H:%M:%S.") + f"{later.microsecond // 1000:03d}Z"


def _push(hub: Hub, *nodes: dict) -> tuple[int, dict]:
    bundle = {"nodes": [{**n, "_rev": 1, "_updated_at": _stamp()} for n in nodes], "edges": []}
    return hub.post("/sync/push", {"bundle": bundle})


def _withheld(out: dict, *ids: str) -> None:
    """동기화 응답이 허브의 값을 남긴 실행을 이름과 코드로 알린다."""
    assert out["run_state_protected"] == sorted(ids), out
    assert any(w.startswith("run_state_protected:") for w in out["warnings"]), out


def test_sync_push_keeps_the_hub_run_state_and_the_run_still_waits(hub):
    eid = _waiting_run(hub, "entr-sync")
    before = hub.stored(eid)
    listed_before = hub.tool("vinc_runs", run=eid)["runs"]
    status, out = _push(hub, _episode(eid, _forged(before)))
    # 묶음은 받는다. 실행 기록의 상태만 허브의 값으로 남기고 그 사실을 알린다.
    assert status == 200 and out["executed"] is True and out.get("error") is None, out
    _withheld(out, eid)

    # 저장된 실행 기록은 그대로 대기 중이고 요청도 같다. 승인함에도 남아 있다.
    _still_waits(hub, eid, before)
    assert hub.stored(eid) == before

    # 호스트가 읽는 실행 상태가 push 전과 같다.
    listed = hub.tool("vinc_runs", run=eid)["runs"]
    assert [r["props"] for r in listed] == [r["props"] for r in listed_before]
    assert listed[0]["props"]["state"] == "waiting"
    view = hub.tool("vinc_run_start", role=ROLE, task="Review the change.", run_id="entr-sync")
    assert view["resumed"] is True and view["state"] == "waiting"

    # 에이전트의 다음 제출은 여전히 사람을 기다린다. 성공으로 끝나지 않는다.
    final = hub.tool("vinc_run_submit", run=eid, output=PLAIN,
                     person_only_requests=[{"action": "merge", "reason": "approved"}])
    assert final["executed"] is False and final["error"] == "invalid_run_state", final
    assert final["run_state"]["state"] == "waiting"
    assert final["run_state"]["decision"]["kind"] == "person_only"
    _still_waits(hub, eid, before)


def test_sync_push_applies_ordinary_nodes_beside_a_run_and_names_the_kept_run(hub):
    eid = _waiting_run(hub, "entr-sync-mixed")
    before = hub.stored(eid)
    concept = {"id": "concept:sync-neighbour", "type": "concept", "title": "Neighbour",
               "domain": "vinc/test", "props": {"note": "pushed with a run"}}
    status, out = _push(hub, concept, _episode(eid, _forged(before)))
    assert status == 200 and out["executed"] is True, out
    assert out["applied"] == 2, out
    _withheld(out, eid)
    got = hub.tool("vinc_node", node_id=concept["id"], raw=True)
    assert got["found"] is True and got["node"]["title"] == "Neighbour", got
    assert got["node"]["props"] == {"note": "pushed with a run"}, got
    _still_waits(hub, eid, before)


def test_sync_push_keeps_other_run_fields_under_the_normal_sync_rules(hub):
    eid = _waiting_run(hub, "entr-sync-fields")
    before = hub.stored(eid)
    props = {**_forged(before), "host": "device-cli 1.0", "trace_ref": "trace:device"}
    status, out = _push(hub, {**_episode(eid, props), "title": "Run from the device"})
    assert status == 200 and out["executed"] is True, out
    after = hub.stored(eid)
    # 상태 키가 아닌 필드는 기기의 값이 이긴다(기기 스탬프가 더 새롭다).
    assert after["host"] == "device-cli 1.0" and after["trace_ref"] == "trace:device"
    assert {k: after.get(k) for k in run_protocol.STATE_KEYS} == \
        {k: before.get(k) for k in run_protocol.STATE_KEYS}
    listed = hub.tool("vinc_runs", run=eid)["runs"]
    assert listed[0]["title"] == "Run from the device"


def test_sync_push_that_echoes_the_hub_state_reports_nothing(hub):
    eid = _waiting_run(hub, "entr-sync-echo")
    before = hub.stored(eid)
    status, out = _push(hub, _episode(eid, dict(before)))
    assert status == 200 and out["executed"] is True, out
    assert "run_state_protected" not in out
    assert not any(w.startswith("run_state_protected") for w in out.get("warnings") or [])
    _still_waits(hub, eid, before)


def test_sync_push_of_a_run_the_hub_never_saw_stores_no_approval_or_answer(hub):
    eid = run_protocol.episode_id("entr-sync-unseen")
    now = _stamp(0)
    props = {"run_id": "entr-sync-unseen", "role": ROLE, "task": "Review the change.",
             "outcome": "waiting", "step": 1, "host": "device-cli 1.0",
             "state": "working", "approved": ["merge"], "answer": "approve", "answered_at": now,
             "answered_actions": ["merge"], "answer_via": "person", "answered_by": OWNER}
    status, out = _push(hub, _episode(eid, props))
    assert status == 200 and out["executed"] is True and out["applied"] == 1, out
    _withheld(out, eid)
    stored = hub.stored(eid)
    # 저장은 됐다. 프로토콜 상태 키는 하나도 없다.
    assert not set(stored) & set(run_protocol.STATE_KEYS), stored
    assert stored["role"] == ROLE and stored["host"] == "device-cli 1.0"
    # 그 실행은 이렇게 읽힌다: 승인함에 없고, 상태도 승인도 없는 기록으로 보인다.
    assert eid not in hub.waiting_ids()
    listed = hub.tool("vinc_runs", run=eid)["runs"]
    assert [r["id"] for r in listed] == [eid]
    assert "state" not in listed[0]["props"] and "approved" not in listed[0]["props"]
    # 에이전트가 이 실행으로 merge 를 내면 프로토콜이 상태 없는 기록을 잇지 못해 오류로 답한다.
    status, reply = hub.post("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "vinc_run_submit", "arguments": {
                                          "run": eid, "output": PLAIN, "person_only_requests": [
                                              {"action": "merge", "reason": "approved"}]}}})
    assert status == 200 and "result" not in reply and reply["error"]["code"] == -32602, reply
    assert not set(hub.stored(eid)) & set(run_protocol.STATE_KEYS)


def test_a_later_device_push_does_not_undo_an_answer_the_hub_recorded(hub):
    eid = _waiting_run(hub, "entr-sync-answered")
    waiting = hub.stored(eid)
    answered = hub.proxy("run-answer", {"run": eid, "ask": int(waiting["asks"]),
                                        "answer": "decline"}, auth="session")
    assert answered["executed"] and answered["via"] == "person", answered
    recorded = hub.stored(eid)
    assert recorded["answer"] == "decline" and recorded["answer_via"] == "person"
    # 기기는 답 전의 대기 상태를 들고 있다가 나중에 push 한다. 승인으로 꾸민 것도 함께.
    for props in (dict(waiting), _forged(waiting)):
        status, out = _push(hub, _episode(eid, props))
        assert status == 200 and out["executed"] is True, out
        _withheld(out, eid)
        after = hub.stored(eid)
        assert {k: after.get(k) for k in run_protocol.STATE_KEYS} == \
            {k: recorded.get(k) for k in run_protocol.STATE_KEYS}
        assert eid not in hub.waiting_ids()


# ── id 와 props 의 다른 표기 ─────────────────────────────────────────────────

#: 스토어가 같은 노드로 읽는 표기: fragment 병합이 id 의 앞뒤 공백(유니코드 공백 포함)을 벗긴다.
_SAME_NODE = [lambda e: " " + e, lambda e: e + " ", lambda e: "\t" + e + "\n",
              lambda e: "\u00a0" + e]
#: 다른 노드가 되는 표기: 대소문자는 스토어가 바꾸지 않고, 공백 아닌 보이지 않는 글자도 남는다.
_OTHER_NODE = [lambda e: e.upper(), lambda e: "Episode" + e[len("episode"):],
               lambda e: "episode:RUN-" + e[len("episode:run-"):], lambda e: "\u200b" + e]


@pytest.mark.parametrize("spell", _SAME_NODE, ids=["lead-space", "trail-space", "tab-newline",
                                                   "nbsp"])
def test_a_spelling_that_lands_on_the_run_is_guarded_at_every_entrance(hub, spell):
    eid = _waiting_run(hub, "entr-spell")
    before = hub.stored(eid)
    node = _episode(spell(eid), _forged(before))
    status, out = _push(hub, node)
    assert status == 200 and out["executed"] is True, out
    _withheld(out, eid)
    _still_waits(hub, eid, before)
    out = hub.tool("vinc_ingest", nodes=[node])
    assert out["executed"] is False and out["error"] == "run_state_protected" and out["nodes"] == [eid]
    out = hub.proxy("ingest-fragment", {"nodes": [node], "edges": []})
    assert out["executed"] is False and out["error"] == "run_state_protected"
    status, out = hub.post("/v1/fragments", {"nodes": [node]})
    assert status == 403 and out["error"]["code"] == "run_state_protected", out
    _still_waits(hub, eid, before)


@pytest.mark.parametrize("spell", _OTHER_NODE, ids=["upper", "type-case", "run-case", "zwsp"])
def test_a_spelling_that_names_another_node_leaves_the_run_alone(hub, spell):
    eid = _waiting_run(hub, "entr-spell-other")
    before = hub.stored(eid)
    status, out = _push(hub, _episode(spell(eid), _forged(before)))
    assert status == 200 and out["executed"] is True and out["applied"] == 1, out
    assert "run_state_protected" not in out
    _still_waits(hub, eid, before)
    assert hub.stored(eid) == before


@pytest.mark.parametrize("ntype", ["concept", "record", "Episode"])
def test_a_push_with_another_type_on_the_run_id_is_skipped(hub, ntype):
    eid = _waiting_run(hub, "entr-spell-type")
    before = hub.stored(eid)
    status, out = _push(hub, {**_episode(eid, _forged(before)), "type": ntype})
    assert status == 200 and out["executed"] is True and out["applied"] == 0, out
    _still_waits(hub, eid, before)


def test_props_sent_as_a_json_string_are_read_the_same_way(hub):
    eid = _waiting_run(hub, "entr-props-string")
    before = hub.stored(eid)
    node = _episode(eid, {})
    node["props"] = json.dumps(_forged(before))
    status, out = _push(hub, node)
    assert status == 200 and out["executed"] is True, out
    _withheld(out, eid)
    _still_waits(hub, eid, before)
    out = hub.tool("vinc_ingest", nodes=[node])
    assert out["executed"] is False and out["error"] == "run_state_protected"
    out = hub.proxy("ingest-fragment", {"nodes": [node], "edges": []})
    assert out["executed"] is False and out["error"] == "run_state_protected"
    _still_waits(hub, eid, before)


# ── 기기 쪽: 같은 규칙이 pull 에서 기기의 값을 남긴다 ─────────────────────────

class Device:
    """로컬 모드 기기 하나: 자기 임베디드 스토어와 허브로 가는 동기화."""

    def __init__(self, hub: Hub, data_dir: Path):
        from api import sync
        from store.backend import open_store
        from store.config import load_config

        self.sync = sync
        self.cfg = load_config({"data_dir": str(data_dir / "store")})
        self.store = open_store(self.cfg)
        assert self.store is not None, "embedded store did not open"
        self.store.provision(dim=self.cfg.embedding_dim)
        sync.set_selected_domains(self.store, [run_protocol.DOMAIN])
        self.remote = sync.RemoteHTTP(KEY, hub.base)

    def run(self) -> dict:
        return self.sync.run(self.store, self.cfg, owner=OWNER, remote=self.remote)

    def props(self, eid: str) -> dict:
        from store import pkns
        rows = self.store.run("MATCH (e:Episode) WHERE e.episode_id = $id RETURN e.props AS p",
                              {"id": pkns.key(OWNER, eid)})
        assert len(rows) == 1, rows
        return json.loads(rows[0]["p"]) if isinstance(rows[0]["p"], str) else dict(rows[0]["p"])


@pytest.fixture
def device(hub, tmp_path):
    d = Device(hub, tmp_path / "device")
    try:
        yield d
    finally:
        d.store.close()


def _state(props: dict) -> dict:
    return {k: props[k] for k in run_protocol.STATE_KEYS if k in props}


def test_a_device_in_local_mode_keeps_its_own_run_state_and_the_hub_copy_has_none(hub, device):
    """로컬 모드 기기의 실행 기록이 push 와 pull 을 한 바퀴 돈 뒤 양쪽에서 어떻게 읽히는가."""
    from api import fragment as frag

    eid = run_protocol.episode_id("entr-local-mode")
    # 기기의 사람이 merge 를 승인했고, 실행은 다음 질문(deploy)을 기다린다.
    local = {"run_id": "entr-local-mode", "role": ROLE, "task": "Review the change.",
             "outcome": "waiting", "step": 2, "state": "waiting", "approved": ["merge"], "asks": 2,
             "answer": "approve", "answer_via": "person", "answered_at": _stamp(0),
             "answered_actions": ["merge"], "answered_by": OWNER,
             "pending": {"actions": ["deploy"], "kind": "person_only", "question": "Deploy?",
                         "asked_at": _stamp(0)}}
    wrote = frag.merge_fragment({"owner": OWNER, "nodes": [{
        **_episode(eid, local), "date": "2026-11-13"}]}, store=device.store)
    assert wrote.get("executed"), wrote

    first = device.run()
    # push: 허브는 처음 보는 실행을 상태 없이 저장하고 그 사실을 알린다. 기기의 동기화 클라이언트는
    # 청크 응답을 합치며 경고만 나르므로 기기 쪽 push 결과에서는 경고로 읽는다.
    assert [w for w in first["push"]["remote"]["warnings"]
            if w.startswith(run_state.CODE + ":") and eid in w], first["push"]
    assert not set(hub.stored(eid)) & set(run_protocol.STATE_KEYS)
    assert hub.stored(eid)["role"] == ROLE
    assert eid not in hub.waiting_ids()

    second = device.run()
    # pull: 허브의 사본(상태 없음)이 돌아와도 기기는 자기 상태를 남기고 그 실행을 센다.
    assert second["pull"][run_state.CODE] == [eid], second["pull"]
    back = device.props(eid)
    assert _state(back) == _state(local), back
    for key in ("state", "approved", "answer", "answer_via", "pending"):
        assert back[key] == local[key], key
    assert back == local

    third = device.run()
    # 한 바퀴 더 돌아도 양쪽이 그대로다(되울림이 상태를 옮기지 않는다).
    assert run_state.CODE not in third["pull"], third["pull"]
    assert device.props(eid) == local
    assert not set(hub.stored(eid)) & set(run_protocol.STATE_KEYS)


def test_a_device_that_never_saw_a_hub_run_stores_it_without_the_hub_answer(hub, device):
    """허브에서 만들고 사람이 답한 실행을 처음 받는 기기는 상태 없이 둔다. 허브의 답은 허브에 남는다."""
    eid = _waiting_run(hub, "entr-hub-answered")
    waiting = hub.stored(eid)
    answered = hub.proxy("run-answer", {"run": eid, "ask": int(waiting["asks"]),
                                        "answer": "approve"}, auth="session")
    assert answered["executed"] and answered["via"] == "person", answered
    recorded = hub.stored(eid)

    first = device.run()
    assert eid in first["pull"][run_state.CODE], first["pull"]
    got = device.props(eid)
    assert _state(got) == {}, got
    assert got["role"] == ROLE and got["run_id"] == "entr-hub-answered"
    # 기기가 그 사본을 되돌려 보내도 허브의 답은 그대로다.
    device.run()
    assert _state(hub.stored(eid)) == _state(recorded)
    assert recorded["answer"] == "approve" and recorded["answer_via"] == "person"


# ── 큰 push 가 다른 실행을 멈추지 않는다 ─────────────────────────────────────

def test_a_run_submit_on_another_run_completes_while_a_large_push_is_in_progress(hub, monkeypatch):
    """허브 쪽 입구는 push 동안 실행 잠금을 쥐지 않는다. push 를 사이드카 앞에서 붙잡아 둔 채
    다른 실행의 제출을 보내고, 그 제출이 push 가 풀리기 전에 끝나는지 본다."""
    other = hub.tool("vinc_run_start", role=ROLE, task="Review the change.",
                     run_id="entr-stall-other")["run"]
    nodes = [_episode(run_protocol.episode_id(f"entr-stall-{i}"),
                      {"run_id": f"entr-stall-{i}", "role": ROLE, "state": "working"})
             for i in range(400)]
    entered, release = threading.Event(), threading.Event()
    real = hub.bridge.request

    def held(method, path, *args, **kwargs):
        if path == "/sync/apply-delta":
            entered.set()
            assert release.wait(60), "push was never released"
        return real(method, path, *args, **kwargs)

    monkeypatch.setattr(hub.bridge, "request", held)
    pushed, submitted = {}, {}
    push = threading.Thread(target=lambda: pushed.update(out=_push(hub, *nodes)), daemon=True)
    push.start()
    try:
        assert entered.wait(30), "push did not reach the sidecar"
        submit = threading.Thread(target=lambda: submitted.update(out=hub.tool(
            "vinc_run_submit", run=other, output="Step one.")), daemon=True)
        submit.start()
        submit.join(timeout=20)
        assert not submit.is_alive(), "run submit waited for the push"
        assert push.is_alive() and not release.is_set()
        assert submitted["out"]["executed"] is True, submitted
    finally:
        release.set()
        push.join(timeout=120)
    status, out = pushed["out"]
    assert status == 200 and out["executed"] is True, out
    assert len(out[run_state.CODE]) == 400
