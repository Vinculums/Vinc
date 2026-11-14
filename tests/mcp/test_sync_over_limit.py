# -*- coding: utf-8 -*-
"""허브의 `/sync/*` 는 상한을 넘는 본문을 413 으로 거절하고, 기기는 그 묶음을 잃지 않는다.

허브는 상한을 넘는 본문을 읽지 않고 빈 dict 로 돌려준다. 동기화 라우트가 그것을 빈 묶음으로 사이드카에
넘기면 `executed: true, applied: 0` 의 200 이 나가고, 보낸 기기는 올라간 줄 알고 커서를 옮기고 base 를
갱신한다. 그러면 올라가지 않은 묶음(삭제 표식·문서)은 다시 보내지 않는다. 이 파일은 그 길을 실물 스택으로 고정한다.

스택: 임베디드 스토어 위의 사이드카(HTTP) ← 브리지 ← 공개 허브(HTTP), 그리고 기기용 스토어 하나.
기기는 실제 클라이언트(`vinc_mcp/syncclient`, `api/sync.push_local`)로 허브에 보낸다.

- 상한 밖·상한 정확히·상한 안의 푸시가 허브에서 각각 어떻게 답하고 저장소에 무엇이 남는가.
- 네 동기화 라우트 모두, 인증 뒤에서 413 이다. 사이드카에는 아무것도 닿지 않는다.
- 413 은 연결을 닫는다. HTTP/1.1 이어도 읽지 않은 본문이 다음 요청이 되지 않는다.
- 기기: 413 이면 커서도 base 도 움직이지 않고, 상한이 풀린 뒤 같은 묶음이 그대로 올라간다.
"""
from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import graph_edit  # noqa: E402
from api import fragment as frag  # noqa: E402
from api import sync  # noqa: E402
from store import pkns  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402
from vinc_mcp import edverify, http_server, syncclient  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402

pytestmark = pytest.mark.skipif(import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")

OWNER = "user:u-1"
KEY = "vinc_sk_full_fixture"
SIDECAR_TOKEN = "fixture-sidecar"
STAMP = "2026-11-14T00:00:00.000Z"
DOMAIN = "overlimit"
#: 거절 문장. 클라이언트가 오류 문장에 그대로 싣는다.
TOO_LARGE = "request body is too large"


class World:
    """허브(사이드카 + 브리지 + 공개 HTTP)와 기기 스토어. 키 하나가 OWNER 의 전권 키다."""

    def __init__(self, root: Path):
        from api.auth import SessionAuth
        from api.server import VincApp, make_server as sidecar_server
        from store.backend import open_store
        from store.config import load_config

        self.hub_cfg = load_config({"data_dir": str(root / "hub")})
        self.hub = open_store(self.hub_cfg)
        self.hub.provision(dim=self.hub_cfg.embedding_dim)
        self.sidecar_app = VincApp(self.hub_cfg, store=self.hub, auth=SessionAuth(SIDECAR_TOKEN))
        self.sidecar = sidecar_server(self.sidecar_app, "127.0.0.1", 0)
        self.bridge = Bridge(base=f"http://127.0.0.1:{self.sidecar.server_address[1]}",
                             token=SIDECAR_TOKEN, owner=OWNER)
        #: 허브가 사이드카에 건 요청 (method, path). 거절한 요청이 사이드카에 닿았는지 본다.
        self.reached: list[tuple[str, str]] = []
        real_request = self.bridge.request

        def spy(method, path, params=None, body=None, timeout=60):
            self.reached.append((method, path))
            return real_request(method, path, params=params, body=body, timeout=timeout)

        self.bridge.request = spy  # type: ignore[method-assign]
        self.app = MCPHTTPApp(
            self.bridge,
            verify_key=lambda key, space=None: (
                {"owner": OWNER, "scope": "full", "plan": "pro"} if key == KEY else None),
            account_public=edverify.secret_to_public(bytes(range(32))))
        self.public = make_server(self.app, "127.0.0.1", 0)
        self.port = self.public.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True)
                        for s in (self.sidecar, self.public)]
        for thread in self.threads:
            thread.start()

        self.dev_cfg = load_config({"data_dir": str(root / "device")})
        self.dev = open_store(self.dev_cfg)
        self.dev.provision(dim=self.dev_cfg.embedding_dim)
        sync.set_selected_domains(self.dev, [DOMAIN])
        self.remote = sync.RemoteHTTP(KEY, self.base)

    def close(self):
        for server in (self.public, self.sidecar):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)
        self.sidecar_app.close()
        self.dev.close()

    def has(self, store, nid: str) -> bool:
        rows = store.run("MATCH (c:Concept) WHERE c.concept_id = $id AND c.owner_id = $o RETURN c.title AS t",
                         {"id": pkns.key(OWNER, nid), "o": OWNER})
        return bool(rows)

    def exchange(self, path: str, raw: bytes, *, key: str | None = KEY,
                 claimed: int | None = None) -> tuple[int, dict, str | None]:
        """POST 하나. 상태, JSON 본문, `Connection` 헤더를 돌려준다. `claimed` 는 선언하는 길이."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.putrequest("POST", path)
            conn.putheader("Content-Type", "application/json")
            if key:
                conn.putheader("Authorization", "Bearer " + key)
            conn.putheader("Content-Length", str(len(raw) if claimed is None else claimed))
            conn.endheaders(raw)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read() or b"{}"), resp.getheader("Connection")
        finally:
            conn.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    monkeypatch.setenv("VINC_DURABLE_ACK", "off")
    monkeypatch.setenv("VINC_TOPIC_CONVERSION", "0")
    monkeypatch.setenv("VINC_STORE_SNAPSHOT", "")
    w = World(tmp_path)
    try:
        yield w
    finally:
        w.close()


def _node(nid: str, pad: int = 0) -> dict:
    return {"id": nid, "type": "concept", "title": nid, "domain": DOMAIN,
            "props": {"note": "x" * pad} if pad else {}, "_rev": 1, "_updated_at": STAMP}


def _push_body(nid: str, size: int) -> bytes:
    """정확히 `size` 바이트인 /sync/push 본문. 노드 하나를 싣고 모자란 만큼은 props 로 채운다."""
    fixed = len(json.dumps({"bundle": {"nodes": [_node(nid, 1)], "edges": []}}).encode()) - 1
    raw = json.dumps({"bundle": {"nodes": [_node(nid, size - fixed)], "edges": []}}).encode()
    assert len(raw) == size, (len(raw), size)
    return raw


def _wire(chunk: dict) -> int:
    """클라이언트가 이 청크를 보낼 때의 본문 바이트 수(`syncclient.push` 와 같은 직렬화)."""
    return len(json.dumps({"bundle": {**chunk, **syncclient.HANDLES}},
                          ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


# ── 상한 밖 · 정확히 · 안 ─────────────────────────────────────────────────────
def test_push_over_the_limit_is_413_and_nothing_reaches_the_store(world, monkeypatch):
    monkeypatch.setattr(http_server, "MAX_BODY", 4096)
    # 선언한 길이만큼 본문을 실제로 보낸다. 허브는 읽지 않아야 한다.
    status, out, connection = world.exchange("/sync/push", _push_body("concept:over", 4097))
    assert status == 413 and out == {"error": TOO_LARGE}, out
    assert connection == "close"
    assert not world.has(world.hub, "concept:over")
    assert world.reached == [], "거절한 요청이 사이드카에 닿았다"


def test_push_that_only_claims_an_over_the_limit_length_is_413_without_a_body(world, monkeypatch):
    monkeypatch.setattr(http_server, "MAX_BODY", 4096)
    status, out, _ = world.exchange("/sync/push", b"{}", claimed=4097)
    assert status == 413 and out == {"error": TOO_LARGE}, out
    assert world.reached == []


def test_push_exactly_at_the_limit_is_stored(world, monkeypatch):
    monkeypatch.setattr(http_server, "MAX_BODY", 4096)
    status, out, _ = world.exchange("/sync/push", _push_body("concept:at", 4096))
    assert status == 200 and out["executed"] is True and out["applied"] == 1, out
    assert world.has(world.hub, "concept:at")


def test_push_under_the_limit_is_stored(world, monkeypatch):
    monkeypatch.setattr(http_server, "MAX_BODY", 4096)
    status, out, _ = world.exchange("/sync/push", _push_body("concept:under", 1024))
    assert status == 200 and out["executed"] is True and out["applied"] == 1, out
    assert world.has(world.hub, "concept:under")


@pytest.mark.parametrize("path", ["/sync/push", "/sync/pull", "/sync/docs-push", "/sync/docs-pull"])
def test_every_sync_route_refuses_an_over_the_limit_body(world, monkeypatch, path):
    """당기기도 같다: 빈 본문으로 읽으면 `since` 가 0 이 되어 그래프 전부가 내려간다."""
    monkeypatch.setattr(http_server, "MAX_BODY", 256)
    raw = json.dumps({"since": 0, "bundle": {}, "payload": {}, "pad": "x" * 300}).encode()
    status, out, connection = world.exchange(path, raw)
    assert status == 413 and out == {"error": TOO_LARGE}, out
    assert connection == "close"
    assert world.reached == []


def test_the_limit_check_comes_after_authentication(world, monkeypatch):
    monkeypatch.setattr(http_server, "MAX_BODY", 256)
    raw = json.dumps({"pad": "x" * 300}).encode()
    status, _, _ = world.exchange("/sync/push", raw, key=None)
    assert status == 401
    status, _, _ = world.exchange("/sync/push", raw, key="vinc_sk_wrong")
    assert status == 401
    assert world.reached == []


# ── 연결을 닫는다 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/sync/push", "/mcp"])
def test_the_connection_is_closed_so_the_unread_body_is_never_a_second_request(world, monkeypatch, path):
    """HTTP/1.1 로 올려도 읽지 않은 본문이 다음 요청이 되지 않는다(요청 밀수).

    본문 앞머리에 완결된 요청 하나를 심는다. 연결이 열려 있으면 허브가 그것을 다음 요청으로 읽고
    답한다. 닫았다면 응답은 하나뿐이다. `/mcp` 는 413 을 정하지 않는 라우트라, 본문을 읽는 자리
    (`_body`)가 연결을 닫는지를 따로 본다."""
    monkeypatch.setattr(http_server._Handler, "protocol_version", "HTTP/1.1")
    monkeypatch.setattr(http_server, "MAX_BODY", 256)
    smuggled = b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n"
    body = smuggled + b" " * 300
    head = (f"POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {KEY}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode()
    sock = socket.create_connection(("127.0.0.1", world.port), timeout=5)
    sock.sendall(head + body)
    received = b""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break                       # 서버가 연결을 닫았다
            received += chunk
    except ConnectionResetError:
        pass                                # 읽지 않은 바이트가 있는 채로 닫으면 RST 로 끝날 수 있다
    except socket.timeout:
        pytest.fail("연결이 닫히지 않았다: 읽지 않은 본문을 다음 요청으로 기다린다")
    finally:
        sock.close()
    assert received.count(b"HTTP/1") == 1, received
    if path == "/sync/push":
        assert b" 413 " in received.split(b"\r\n", 1)[0], received
        assert b"Connection: close" in received


# ── 기기: 커서와 묶음 ──────────────────────────────────────────────────────────
def test_the_client_names_the_refusal_and_does_not_retry(world, monkeypatch):
    monkeypatch.setattr(http_server, "MAX_BODY", 256)
    chunk = {"nodes": [_node("concept:big", 1000)], "edges": []}
    with pytest.raises(syncclient.SyncError) as err:
        syncclient.push([chunk], token=KEY, base=world.base)
    assert "(413)" in str(err.value) and TOO_LARGE in str(err.value), str(err.value)
    assert not world.has(world.hub, "concept:big")


def test_a_refused_delete_only_bundle_moves_neither_the_cursor_nor_the_batch(world, monkeypatch):
    """삭제 표식만 있는 묶음은 쪼개지지 않는다. 상한을 넘으면 413 이고, 기기는 그대로 남는다."""
    ids = [f"concept:gone{i}" for i in range(3)]
    for nid in ids:
        res = frag.merge_fragment({"owner": OWNER, "nodes": [
            {"id": nid, "type": "concept", "title": nid, "domain": DOMAIN}], "edges": []}, store=world.dev)
        assert res["executed"], res
    first = sync.push_local(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert first["sent"] == 3 and all(world.has(world.hub, nid) for nid in ids), first
    for nid in ids:
        assert graph_edit.delete_node(nid, owner=OWNER, cfg=world.dev_cfg, store=world.dev)["found"]

    cursor = sync.get_cursor(world.dev, "push")
    bundle = sync.export_delta(owner=OWNER, since=cursor, cfg=world.dev_cfg, store=world.dev)
    chunks = sync.chunk_bundle(bundle)
    assert len(chunks) == 1 and chunks[0]["nodes"] == [] and len(chunks[0]["tombstones"]) == 3
    size = _wire(chunks[0])

    # 한 바이트 모자라면 허브가 거절한다.
    monkeypatch.setattr(http_server, "MAX_BODY", size - 1)
    with pytest.raises(syncclient.SyncError) as err:
        sync.push_local(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert "(413)" in str(err.value), str(err.value)
    assert sync.get_cursor(world.dev, "push") == cursor
    again = sync.export_delta(owner=OWNER, since=cursor, cfg=world.dev_cfg, store=world.dev)
    assert len(again["tombstones"]) == 3, "거절된 삭제 표식이 다음 동기화에서 사라졌다"
    assert all(world.has(world.hub, nid) for nid in ids), "허브는 아직 노드를 갖고 있다"

    # 정확히 상한이면 같은 묶음이 그대로 올라가고, 그때서야 커서가 움직인다.
    monkeypatch.setattr(http_server, "MAX_BODY", size)
    done = sync.push_local(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert done["deletes"] == 3 and done["cursor"] == bundle["next_cursor"], done
    assert sync.get_cursor(world.dev, "push") == bundle["next_cursor"]
    assert not any(world.has(world.hub, nid) for nid in ids)


def test_a_refused_document_chunk_keeps_the_docs_cursor(world, monkeypatch):
    from api import docstore as ds
    ds.put_doc(world.dev, owner=OWNER, doc_id="doc:overlimit", title="Over limit", text="한 줄. " * 40)
    cursor = sync.get_cursor(world.dev, "docs-push")
    monkeypatch.setattr(http_server, "MAX_BODY", 256)
    with pytest.raises(syncclient.SyncError) as err:
        sync.push_docs(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert "(413)" in str(err.value), str(err.value)
    assert sync.get_cursor(world.dev, "docs-push") == cursor

    monkeypatch.setattr(http_server, "MAX_BODY", 4 * 1024 * 1024)
    done = sync.push_docs(world.dev, world.dev_cfg, owner=OWNER, remote=world.remote)
    assert done["sent"] == 1 and done["remote"]["applied"] + done["remote"]["updated"] >= 1, done
    assert sync.get_cursor(world.dev, "docs-push") > cursor
