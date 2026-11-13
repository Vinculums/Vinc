"""MCP Events 와 서명 웹훅(vinc_mcp/events.py): 서명, 주소 검사, 구독, 전달, 재시도, 회전.

끝에서 끝까지 하나는 실제 임베디드 스토어 위에서 돈다: 역할의 필수 문서를 고치면 구독한
웹훅이 서명된 전달 하나를 받고, 같은 변경을 REST 와 같은 도구(vinc_events)가 같은 id 로 낸다.
근거: [[vinc.plan.mcp-events-and-webhooks]]
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vinc_mcp import events, rpc, tools

SECRET_RAW = b"k" * 32
SECRET = "whsec_" + base64.b64encode(SECRET_RAW).decode()
URL = "https://hooks.example.com/mcp/1"
ROLE = "concept:agent-events-watcher"
PUBLIC_IP = "93.184.216.34"


# ── 받는 쪽 ────────────────────────────────────────────────────────────────

def verify(secret_raw: bytes, headers, body: bytes) -> bool:
    msg_id, ts = headers["webhook-id"], headers["webhook-timestamp"]
    want = base64.b64encode(hmac.new(secret_raw, f"{msg_id}.{ts}.".encode() + body,
                                     hashlib.sha256).digest()).decode()
    return any(sig == "v1," + want for sig in headers["webhook-signature"].split(" "))


class Receiver:
    """127.0.0.1 의 받는 쪽. 확인 요청에는 challenge 를 되돌리고, 전달은 기록한다."""

    def __init__(self, secret_raw=SECRET_RAW):
        self.secret_raw = secret_raw
        self.got: list[tuple[dict, bytes]] = []
        self.status = 200
        self.echo = True
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                headers = {k.lower(): v for k, v in self.headers.items()}
                outer.got.append((headers, body))
                payload = json.loads(body)
                if payload.get("type") == "verification":
                    reply = {"challenge": payload["challenge"] if outer.echo else "no"}
                    raw = json.dumps(reply).encode()
                    self.send_response(200)
                else:
                    raw = b"{}"
                    self.send_response(outer.status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def deliveries(self):
        return [(h, b) for h, b in self.got if json.loads(b).get("type") is None]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def sender_for(rx: Receiver, resolver=None):
    """공인 주소로 풀린 것처럼 하고 실제로는 로컬 받는 쪽으로 붙는다(평문)."""
    def resolve(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port))]

    def connect(ip, port, host, timeout):
        assert ip == PUBLIC_IP and host == "hooks.example.com"
        return socket.create_connection(("127.0.0.1", rx.port), timeout=timeout)
    return events.Sender(resolver=resolver or resolve, connector=connect, timeout=5)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeBridge:
    """사이드카 대신. `/events` 는 정해 둔 페이지를, `/roles` 는 역할 하나를 답한다."""
    owner = "user:test"

    def __init__(self):
        self.head = "10.5"
        self.page = {"executed": True, "events": [], "cursor": "10.5", "has_more": False}
        self.writes: list[dict] = []
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, **_kw):
        self.calls.append((method, path, dict(params or {})))
        if path == "/roles":
            return {"executed": True, "found": True, "node": {"id": ROLE, "domain": "agent"}}
        if path == "/events":
            if "since" not in (params or {}):
                return {"executed": True, "events": [], "cursor": self.head, "head": self.head}
            return self.page
        if path == "/ingest/fragment":
            self.writes.append(body)
            return {"executed": True, "merged": {}}
        return {"executed": False}


def event(eid="evt_1", pos=("d", 6), role=ROLE, name="role.reading_changed"):
    return {"eventId": eid, "name": name, "timestamp": "2026-10-25T00:00:00.000Z",
            "data": {"role": role, "doc_id": "plan.a", "change": "updated"}, "_pos": list(pos)}


def subscribe(rt, **extra):
    params = {"name": "role.reading_changed", "arguments": {"role": "agent-events-watcher"},
              "delivery": {"mode": "webhook", "url": URL, "secret": SECRET}, **extra}
    return rt.subscribe(params, principal="user:a", owner="user:a", can_write=True)


@pytest.fixture()
def rig():
    rx = Receiver()
    bridge, clock = FakeBridge(), Clock()
    rt = events.Runtime(bridge, sender=sender_for(rx), clock=clock, tick_s=3600)
    yield rt, bridge, rx, clock
    rt.stop()
    rx.close()


# ── 순수 함수 ──────────────────────────────────────────────────────────────

def test_kinds_mirror_the_core_feed():
    from api import events as core
    assert events.KINDS == core.KINDS and events.RESERVED == core.RESERVED


def test_standard_webhooks_signature_vector():
    # standard-webhooks 명세의 예시 값.
    secret = events.decode_secret("whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw")
    sig = events.sign(secret, "msg_p5jXN8AQM9LWM0D4loKWxJek", 1614265330,
                      b'{"test": 2432232314}')
    assert sig == "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="


def test_secret_url_and_ttl_validation():
    for bad in ("", "whsec_", "secret", "whsec_" + base64.b64encode(b"x" * 10).decode(),
                "whsec_" + base64.b64encode(b"x" * 65).decode(), "whsec_@@@@"):
        with pytest.raises(events.EventsError) as e:
            events.decode_secret(bad)
        assert e.value.code == events.INVALID_PARAMS
    assert events.check_url("https://h.example/a?b=1") == ("h.example", 443, "/a?b=1")
    assert events.check_url("https://h.example:443") == ("h.example", 443, "/")
    for bad in ("http://h.example/", "https://h.example:8443/", "https://u:p@h.example/",
                "https://h.example/#x", "ftp://h.example/", "https:///x", "https://h.example/a b"):
        with pytest.raises(events.EventsError):
            events.check_url(bad)
    assert events.grant_ttl_ms({}) == events.TTL_MAX_MS
    assert events.grant_ttl_ms({"ttlMs": None}) == events.TTL_MAX_MS
    assert events.grant_ttl_ms({"ttlMs": 1}) == events.TTL_MIN_MS
    assert events.grant_ttl_ms({"ttlMs": 10 ** 9}) == events.TTL_MAX_MS


def test_only_public_addresses_pass():
    for ip in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254",
               "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1",
               "fec0::1", "::ffff:10.0.0.1", "2002:a00:1::", "not-an-ip"):
        assert events.public_address(ip) is False, ip
    for ip in (PUBLIC_IP, "2606:4700:4700::1111"):
        assert events.public_address(ip) is True, ip
    mixed = lambda *a, **k: [(0, 0, 0, "", (PUBLIC_IP, 443)), (0, 0, 0, "", ("10.0.0.1", 443))]
    with pytest.raises(events.CallbackFailure) as e:
        events.resolve_public("h.example", 443, mixed)
    assert e.value.category == "connection_refused"


def test_the_address_is_checked_again_at_send_time():
    """구독 때 공인으로 풀린 이름이 보낼 때 사설로 풀리면 붙지 않는다(DNS 재바인딩)."""
    answers = iter([PUBLIC_IP, "127.0.0.1"])
    connected = []
    sender = events.Sender(resolver=lambda h, p, type=None: [(0, 0, 0, "", (next(answers), p))],
                           connector=lambda *a: connected.append(a) or (_ for _ in ()).throw(OSError()))
    with pytest.raises(events.CallbackFailure):
        sender.post(URL, {}, b"{}")
    assert len(connected) == 1
    with pytest.raises(events.CallbackFailure) as e:
        sender.post(URL, {}, b"{}")
    assert e.value.category == "connection_refused" and len(connected) == 1


# ── 구독과 전달 ────────────────────────────────────────────────────────────

def test_subscribe_verifies_then_delivers_one_signed_event_per_change(rig):
    rt, bridge, rx, clock = rig
    out = subscribe(rt)
    assert out["id"].startswith("sub_") and out["cursor"] == "10.5" and out["truncated"] is False
    assert out["refreshBefore"].endswith("Z")
    (vh, vb), = rx.got
    assert json.loads(vb)["type"] == "verification" and verify(SECRET_RAW, vh, vb)
    assert vh["x-mcp-subscription-id"] == out["id"]
    # 구독 기록: 역할에 붙고, 주소와 비밀은 없다.
    (write,) = bridge.writes
    blob = json.dumps(write)
    assert "hooks.example.com" not in blob and SECRET not in blob and "whsec_" not in blob
    assert write["nodes"][0]["id"].startswith("record:event-subscription-")
    assert write["edges"][0]["to"] == ROLE
    bridge.head = "10.7"
    bridge.page = {"executed": True, "cursor": "10.7", "has_more": False,
                   "events": [event("evt_a", ("d", 6)), event("evt_b", ("d", 7), role="concept:agent-x"),
                              event("evt_c", ("d", 4))]}
    stats = rt.tick()
    assert stats["delivered"] == 1
    (h, b), = rx.deliveries()
    body = json.loads(b)
    assert verify(SECRET_RAW, h, b) and h["webhook-id"] == body["eventId"] == "evt_a"
    assert h["x-mcp-subscription-id"] == out["id"] and body["cursor"] == "10.5"
    assert "_pos" not in body and body["data"]["doc_id"] == "plan.a"
    # 같은 id 가 뒤 위치에서 다시 나와도 두 번 보내지 않는다.
    bridge.head = "10.9"
    bridge.page = {"executed": True, "cursor": "10.9", "has_more": False,
                   "events": [event("evt_a", ("d", 9))]}
    rt.tick()
    assert len(rx.deliveries()) == 1
    assert rt.subs[out["id"]].watermark == (10, 9)


def test_failures_retry_with_backoff_then_give_up_and_410_is_final(rig):
    rt, bridge, rx, clock = rig
    sid = subscribe(rt)["id"]
    rx.status = 500
    bridge.head, bridge.page = "10.6", {"executed": True, "cursor": "10.6", "has_more": False,
                                        "events": [event("evt_r", ("d", 6))]}
    rt.tick()
    sub = rt.subs[sid]
    assert len(rx.deliveries()) == 1 and sub.pending and sub.last_error == "http_5xx"
    assert sub.watermark == (10, 5)                     # 대기 중인 것을 넘지 않는다
    rt.tick()
    assert len(rx.deliveries()) == 1                    # 다음 시각이 아직 아니다
    for delay in events.RETRY_DELAYS:
        clock.t += delay
        rt.tick()
    assert len(rx.deliveries()) == 1 + len(events.RETRY_DELAYS)
    assert not sub.pending and "evt_r" in sub.done and sub.watermark == (10, 6)
    first = rx.deliveries()[0][0]["webhook-timestamp"]
    assert rx.deliveries()[-1][0]["webhook-timestamp"] != first   # 시도마다 새로 서명한다
    rx.status = 410
    bridge.head, bridge.page = "10.7", {"executed": True, "cursor": "10.7", "has_more": False,
                                        "events": [event("evt_g", ("d", 7))]}
    rt.tick()
    assert "evt_g" in sub.done and not sub.pending
    assert sub.status()["lastError"] == "http_4xx"


def test_refresh_rotates_the_secret_with_a_grace_window(rig):
    rt, bridge, rx, clock = rig
    first = subscribe(rt)
    new_raw = b"n" * 32
    new = "whsec_" + base64.b64encode(new_raw).decode()
    again = subscribe(rt, delivery={"mode": "webhook", "url": URL, "secret": new}, ttlMs=600_000)
    assert again["id"] == first["id"] and "deliveryStatus" in again
    assert len([g for g in rx.got if json.loads(g[1]).get("type")]) == 1   # 확인은 한 번
    bridge.head, bridge.page = "10.6", {"executed": True, "cursor": "10.6", "has_more": False,
                                        "events": [event("evt_x", ("d", 6))]}
    rt.tick()
    (h, b), = rx.deliveries()
    assert len(h["webhook-signature"].split(" ")) == 2
    assert verify(SECRET_RAW, h, b) and verify(new_raw, h, b)
    clock.t += events.ROTATION_GRACE_S + 1
    bridge.head, bridge.page = "10.7", {"executed": True, "cursor": "10.7", "has_more": False,
                                        "events": [event("evt_y", ("d", 7))]}
    rt.tick()
    h2, b2 = rx.deliveries()[-1]
    assert len(h2["webhook-signature"].split(" ")) == 1 and verify(new_raw, h2, b2)
    assert not verify(SECRET_RAW, h2, b2)


def test_unsubscribe_and_expiry_retire_the_record(rig):
    rt, bridge, rx, clock = rig
    out = subscribe(rt)
    assert rt.unsubscribe({"name": "role.reading_changed",
                           "arguments": {"role": "agent-events-watcher"},
                           "delivery": {"url": URL}}, principal="user:a") == {}
    assert out["id"] not in rt.subs
    assert bridge.writes[-1]["nodes"] == [{"id": bridge.writes[0]["nodes"][0]["id"],
                                           "type": "record", "valid_until": "2027-01-15"}]
    # 모르는 키의 해지도 빈 답이다(멱등).
    assert rt.unsubscribe({"name": "role.reading_changed",
                           "arguments": {"role": "agent-events-watcher"},
                           "delivery": {"url": URL}}, principal="user:a") == {}
    subscribe(rt, ttlMs=300_000)
    clock.t += 301
    assert rt.tick()["expired"] == 1 and not rt.subs


def test_refusals_store_nothing(rig):
    rt, bridge, rx, clock = rig
    rx.echo = False
    with pytest.raises(events.EventsError) as e:
        subscribe(rt)
    assert e.value.code == events.CALLBACK_ERROR and e.value.data == {"reason": "challenge_failed"}
    assert not rt.subs and not bridge.writes
    rx.echo = True
    for params, code in (
            ({"name": "nope"}, events.NOT_FOUND),
            ({"arguments": {"role": "x"}}, events.INVALID_PARAMS),
            ({"arguments": {"role": "agent-a", "extra": 1}}, events.INVALID_PARAMS),
            ({"delivery": {"mode": "push", "url": URL, "secret": SECRET}}, events.UNSUPPORTED),
            ({"delivery": {"mode": "webhook", "url": "http://h.example/", "secret": SECRET}},
             events.INVALID_PARAMS),
            ({"cursor": "garbage"}, events.INVALID_PARAMS)):
        with pytest.raises(events.EventsError) as e:
            subscribe(rt, **params)
        assert e.value.code == code, params
    rt.max_per_principal = 1
    subscribe(rt)
    with pytest.raises(events.EventsError) as e:
        subscribe(rt, arguments={"role": "agent-other"})
    assert e.value.code == events.RESOURCE_EXHAUSTED
    subscribe(rt)                                     # 같은 키의 갱신은 한도에 걸리지 않는다


def test_a_callback_that_resolves_to_a_private_address_is_refused(rig):
    rt, bridge, rx, clock = rig
    rt.sender = events.Sender(resolver=lambda h, p, type=None: [(0, 0, 0, "", ("10.1.2.3", p))])
    with pytest.raises(events.EventsError) as e:
        subscribe(rt)
    assert e.value.code == events.CALLBACK_ERROR and e.value.data == {"reason": "connection_refused"}
    assert not rx.got


# ── 디스패처 ──────────────────────────────────────────────────────────────

META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}


def _msg(method, params=None, meta=META):
    p = dict(params or {})
    if meta:
        p["_meta"] = meta
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": p}


def test_dispatcher_serves_events_only_in_the_modern_era():
    d = rpc.Dispatcher(FakeBridge(), hub=True)
    assert d.handle(_msg("events/list", meta=None))["error"]["code"] == -32601
    listed = d.handle(_msg("events/list"))["result"]
    assert [e["name"] for e in listed["events"]] == list(events.KINDS)
    assert all(e["delivery"] == ["poll"] for e in listed["events"])
    assert (listed["ttlMs"], listed["cacheScope"]) == (3_600_000, "public")
    bad = d.handle(_msg("events/subscribe", {"name": "role.lesson_added"}), forced_owner="user:a",
                   events_auth={"principal": "user:a", "can_write": True})
    assert bad["error"]["code"] == events.UNSUPPORTED
    assert d.handle(_msg("events/unsubscribe", {}))["result"]["resultType"] == "complete"
    d.events = events.Runtime(FakeBridge(), tick_s=3600)
    assert d.handle(_msg("events/list"))["result"]["events"][0]["delivery"] == ["poll", "webhook"]
    local = d.handle(_msg("events/subscribe", {"name": "role.lesson_added"}))
    assert local["error"]["code"] == events.FORBIDDEN       # 인증된 주체가 없다
    community = d.handle(_msg("events/poll", {"name": "role.lesson_added",
                                              "arguments": {"role": "agent-a"}}),
                         forced_owner="community:0198c0de-1111-7222-8333-444455556666")
    assert community["error"]["code"] == events.FORBIDDEN


def test_poll_reads_the_feed_for_one_name_and_role():
    bridge = FakeBridge()
    bridge.page = {"executed": True, "cursor": "11.6", "has_more": True, "truncated": False,
                   "events": [event("evt_p", ("d", 6))]}
    d = rpc.Dispatcher(bridge)
    spent = []
    out = d.handle(_msg("events/poll", {"name": "role.reading_changed", "cursor": "10.5",
                                        "arguments": {"role": "agent-events-watcher"}}),
                   forced_owner="user:a", quota=lambda o, p: spent.append(o))["result"]
    assert out["events"] == [{k: v for k, v in event("evt_p").items() if k != "_pos"}]
    assert (out["cursor"], out["hasMore"], out["nextPollMs"]) == ("11.6", True, events.POLL_MS)
    assert spent == ["user:a"]
    _m, path, params = bridge.calls[-1]
    assert path == "/events" and params["kinds"] == "role.reading_changed"
    assert params["role"] == ROLE and params["since"] == "10.5" and params["owner"] == "user:a"


def test_events_tool_strips_positions_and_validates_since():
    bridge = FakeBridge()
    bridge.page = {"executed": True, "events": [event()], "cursor": "1.2", "has_more": False}
    out = tools.call("vinc_events", {"since": "1.1"}, bridge)
    assert out["events"] and "_pos" not in out["events"][0]
    for bad in ({"since": "1"}, {"since": "x.y"}, {"kinds": ["run.nope"]}, {"kinds": "x"}):
        with pytest.raises(tools.InvalidArgument):
            tools.validate_args("vinc_events", bad)


def test_events_auth_names_the_member_on_a_team_and_needs_write_for_records():
    from vinc_mcp.http_server import _events_auth
    assert _events_auth({"owner": "user:a", "scope": "full"}) == {"principal": "user:a",
                                                                   "can_write": True}
    assert _events_auth({"owner": "user:a", "scope": "read"})["can_write"] is False
    team = {"owner": "team:t", "member": "user:m", "role": "viewer", "scope": "full"}
    assert _events_auth(team) == {"principal": "user:m", "can_write": False}
    assert _events_auth({**team, "role": "editor"})["can_write"] is True
    assert _events_auth({"owner": None, "source": "static"})["principal"] is None


# ── 끝에서 끝까지: 실제 스토어 ──────────────────────────────────────────────

class StoreBridge:
    """사이드카 라우트 셋을 같은 스토어 위에서 직접 부른다."""

    def __init__(self, store, owner):
        self.store, self.owner = store, owner

    def request(self, method, path, params=None, body=None, **_kw):
        from api import events as core, fragment as frag, roles
        p = dict(params or {})
        if path == "/events":
            return core.scan(self.store, owner=p["owner"], since=core.parse_cursor(p.get("since")),
                             limit=core.clamp_limit(p.get("limit")), role=p.get("role"),
                             kinds=core.parse_kinds(p.get("kinds")))
        if path == "/roles":
            return roles.read(p.get("role"), owner=p["owner"], store=self.store)
        if path == "/ingest/fragment":
            return frag.merge_fragment(body, store=self.store)
        raise AssertionError(path)


@pytest.fixture()
def store(tmp_path):
    from store.backend import open_store
    from store.config import load_config
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_EMBEDDING_MODEL")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(MEMORY_STORE_BACKEND="embedded", MEMORY_EMBEDDING_MODEL="local-en")
    cfg = load_config({"data_dir": str(tmp_path / "db")})
    st = open_store(cfg)
    st.provision(dim=cfg.embedding_dim)
    try:
        yield st
    finally:
        st.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_a_subscribed_role_gets_one_signed_delivery_and_the_feed_has_the_same_event(store):
    from api import docstore, fragment as frag
    from store import pkns
    owner = "user:e2e"
    frag.merge_fragment({"owner": owner, "nodes": [{
        "id": ROLE, "type": "concept", "title": "Events watcher", "domain": "agent",
        "props": {"mission": "Watch.", "tier": "verify", "rung": "fast", "never": ["guess"],
                  "person_only": [], "reads": ["plan.a"]}}]}, store=store)
    docstore.put_doc(store, owner=owner, text="# A\n\none\n", doc_id="plan.a", title="Plan A")
    bridge, rx, clock = StoreBridge(store, owner), Receiver(), Clock()
    rt = events.Runtime(bridge, sender=sender_for(rx), clock=clock, tick_s=3600)
    try:
        sub = rt.subscribe({"name": "role.reading_changed",
                            "arguments": {"role": "agent-events-watcher"},
                            "delivery": {"mode": "webhook", "url": URL, "secret": SECRET}},
                           principal=owner, owner=owner, can_write=True)
        start = sub["cursor"]
        # 구독 기록이 생겼고 역할에 붙었다. 그 쓰기는 이벤트를 만들지 않는다.
        rec = store.run("MATCH (r:Record)-[:ATTACHES_TO]->(c:Concept) WHERE c.concept_id = $c "
                        "RETURN r.record_id AS id, r.props AS props", {"c": pkns.key(owner, ROLE)})
        assert len(rec) == 1 and "hooks.example.com" not in str(rec[0]["props"])
        rt.tick()
        assert rx.deliveries() == []
        docstore.put_doc(store, owner=owner, text="# A\n\ntwo\n", doc_id="plan.a")
        rt.tick()
        rt.tick()
        (h, b), = rx.deliveries()
        assert verify(SECRET_RAW, h, b)
        delivered = json.loads(b)
        assert delivered["name"] == "role.reading_changed" and delivered["data"]["doc_id"] == "plan.a"
        feed = tools.call("vinc_events", {"since": start, "owner": owner}, bridge)
        assert [e["eventId"] for e in feed["events"]] == [delivered["eventId"]]
        assert feed["events"][0] == {k: delivered[k] for k in ("eventId", "name", "timestamp", "data")}
    finally:
        rt.stop()
        rx.close()


# ── HTTP: 인증된 주체와 스페이스 ─────────────────────────────────────────────

TEAM = "0198c0de-aaaa-7222-8333-444455556666"


def _http(app):
    import urllib.request
    from vinc_mcp import modern
    from vinc_mcp.http_server import make_server
    httpd = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/mcp"

    def post(method, params, token="vinc_sk_personal"):
        msg = _msg(method, params)
        req = urllib.request.Request(base, data=json.dumps(msg).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + token)
        for k, v in modern.request_headers(msg).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    return httpd, post


def test_http_subscribe_uses_the_key_owner_and_a_team_member_as_principal(monkeypatch):
    from vinc_mcp.http_server import MCPHTTPApp
    monkeypatch.setenv("VINC_EVENTS_WEBHOOK", "on")

    def verify_key(token, space=None):
        if space:
            return {"owner": "team:" + space, "plan": None, "scope": "full", "role": "viewer",
                    "member": "user:m"}
        return {"owner": "user:p", "plan": None, "scope": "full"}
    bridge, rx = FakeBridge(), Receiver()
    app = MCPHTTPApp(bridge, static_token="static-x", verify_key=verify_key)
    rt = app.dispatcher().events
    assert isinstance(rt, events.Runtime)
    rt.sender = sender_for(rx)
    httpd, post = _http(app)
    try:
        code, listed = post("events/list", {})
        assert code == 200 and listed["result"]["events"][0]["delivery"] == ["poll", "webhook"]
        params = {"name": "role.lesson_added", "arguments": {"role": "agent-events-watcher"},
                  "delivery": {"mode": "webhook", "url": URL, "secret": SECRET}}
        code, mine = post("events/subscribe", params)
        assert code == 200 and mine["result"]["id"].startswith("sub_"), mine
        assert rt.subs[mine["result"]["id"]].principal == "user:p"
        assert bridge.writes and bridge.writes[0]["owner"] == "user:p"
        team = {**params, "arguments": {**params["arguments"], "space": TEAM}}
        code, theirs = post("events/subscribe", team)
        sub = rt.subs[theirs["result"]["id"]]
        assert (sub.principal, sub.owner) == ("user:m", "team:" + TEAM)
        assert len(bridge.writes) == 1                # 열람자는 구독 기록을 쓰지 않는다
        code, static = post("events/subscribe", params, token="static-x")
        assert static["error"]["code"] == events.FORBIDDEN
    finally:
        rt.stop()
        httpd.shutdown()
        httpd.server_close()
        rx.close()
