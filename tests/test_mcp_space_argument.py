# -*- coding: utf-8 -*-
"""스페이스는 **인자로도** 정해진다(발주자 지시 2026-08-31, A안).

종전 불변식은 "주소 = 스페이스"였다
([[decision:the-endpoint-carries-the-space-and-the-credential-carries-the-right]]).
그 결정이 실제로 막던 것은 **무음 오배송**이지 주소 자체가 아니다 — 지금도 개인 키
하나가 주소만 알면 자기가 멤버인 모든 팀에 닿으므로(`verify_key(raw, team)`) 주소는
라우팅이지 권한이 아니었다. 그래서 인자를 열되 **판정은 한 줄도 안 옮긴다**: 같은
`resolve_auth` 가 다시 돌고, 거절의 성질 셋이 그대로 성립해야 한다.

  ① 거절은 **브리지를 부르지 않는다.**
  ② 닿을 수 없는 스페이스는 전부 **같은 답**이다(모르는 팀·비멤버·모양 오류).
  ③ **개인으로 떨어지지 않는다.**

잃는 것은 실수 방지 하나고 그것을 셋으로 되산다: `initialize` 의 instructions ·
쓰기 응답의 정체 스탬프 · `vinc_whoami`. 앞의 둘을 여기서 잰다.

⚠ 이 파일에서 가장 중요한 검사는 `test_배치에서_메시지마다_스페이스가_갈린다` 다.
역할·스코프 게이트가 **요청당 하나**였기 때문에, 그 자리를 안 고치면 배치 첫
메시지의 판정이 나머지를 통과시킨다. 나머지 검사는 그 자리를 안 고쳐도 통과한다."""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp import http_server as H  # noqa: E402

TEAM = "0198c0de-1111-7222-8333-444455556666"
OTHER = "0198c0de-2222-7333-8444-555566667777"


class _RecordingBridge:
    """호출을 **전부** 모은다. 배치에서 메시지마다 owner 가 갈리는지 보려면 마지막
    하나로는 부족하다. 그리고 '브리지를 안 불렀다'가 거절의 성질이라 빈 목록 자체가
    검사 대상이다."""

    def __init__(self):
        self.owner = "user:local"
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        return {"executed": True}

    def owners(self):
        """각 호출이 어느 그래프로 갔는가. owner 는 params 나 body 에 실린다."""
        out = []
        for _m, _p, params, body in self.calls:
            src = body if isinstance(body, dict) and "owner" in body else (params or {})
            out.append(src.get("owner"))
        return out


class _SpaceServer(unittest.TestCase):
    """실물 HTTP 로 잰다 — 게이트가 사는 곳이 핸들러라 함수 단위로는 못 잡는다."""

    def setUp(self):
        self.bridge = _RecordingBridge()
        self.role = "editor"
        self.scope = "full"
        self.asked = []                  # 계정에 물어본 team 값들

        def verify(tok, team=None):
            self.asked.append(team)
            if team is None:
                return {"owner": "user:u1", "plan": "pro", "scope": self.scope}
            if team != TEAM:                     # OTHER 는 비멤버다
                return None
            return {"owner": f"team:{team}", "plan": "team", "scope": self.scope,
                    "role": self.role, "member": "user:u1"}

        app = H.MCPHTTPApp(self.bridge, static_token="S", verify_key=verify)
        self.httpd = H.make_server(app, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()

    def post(self, msg, path="/mcp"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(msg).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer vinc_sk_ok",
                     "MCP-Protocol-Version": "2025-06-18"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    @staticmethod
    def call(i, name, **args):
        return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                "params": {"name": name, "arguments": args}}


class RoutingTests(_SpaceServer):
    def test_인자로_고른_팀에_스탬핑된다(self):
        code, _ = self.post(self.call(1, "vinc_search", query="x", space=TEAM))
        self.assertEqual(code, 200)
        self.assertEqual(self.bridge.owners(), [f"team:{TEAM}"])

    def test_안_실으면_개인이다(self):
        """A안의 기본. 이 값이 바뀌면 이미 등록된 개인 커넥터가 전부 첫날에 깨진다."""
        code, _ = self.post(self.call(1, "vinc_search", query="x"))
        self.assertEqual(code, 200)
        self.assertEqual(self.bridge.owners(), ["user:u1"])

    def test_personal_은_개인을_가리키는_이름이다(self):
        self.post(self.call(1, "vinc_search", query="x", space="personal"))
        self.assertEqual(self.bridge.owners(), ["user:u1"])

    def test_대문자_uuid_도_같은_팀이다(self):
        """같은 팀이 대소문자로 두 스페이스가 되면 안 된다(주소 파서와 같은 규칙)."""
        self.post(self.call(1, "vinc_search", query="x", space=TEAM.upper()))
        self.assertEqual(self.bridge.owners(), [f"team:{TEAM}"])



class SpaceIsStrippedTests(unittest.TestCase):
    """`space` 가 도구 인자로 흘러가지 않는가.

    ⚠ 이 검사를 **사이드카 쪽에 두면 공허하다**(실제로 그렇게 썼다가 변이로 걸렀다):
    사이드카 핸들러는 params 를 명시로 조립하므로 모르는 키가 흘러갈 길이 애초에 없다.
    무는 자리는 **원격 전달**이다 — `remote.call` 은 `owner`·`target` 만 빼고 전부
    넘기므로, 안 벗기면 데스크탑의 `target:"remote"` 호출이 스키마에 광고하지도 않은
    스페이스 선택을 허브에서 하게 된다."""

    def test_원격으로_전달되지_않는다(self):
        from vinc_mcp import remote, tools
        seen = {}
        old = remote.call
        remote.call = lambda name, args, **kw: seen.update(args) or {"ok": True}
        try:
            tools.call("vinc_search",
                       {"query": "x", "space": TEAM, "target": "remote"},
                       _RecordingBridge())
        finally:
            remote.call = old
        self.assertEqual(seen.get("query"), "x")
        self.assertNotIn("space", seen)


class RefusalShapeTests(_SpaceServer):
    """결정이 고정한 거절의 성질 셋이 인자 경로에서도 그대로인가."""

    def test_닿을_수_없는_스페이스는_전부_같은_답이다(self):
        """비멤버·모르는 팀·모양 오류가 갈리면 id 를 추측해 조직을 훑을 수 있다."""
        seen = set()
        for bad in (OTHER, "not-a-uuid", "0198c0de", TEAM[:-1]):
            _code, body = self.post(self.call(1, "vinc_search", query="x", space=bad))
            res = body.get("result") or {}
            self.assertTrue(res.get("isError"), bad)
            seen.add(res["content"][0]["text"])
        self.assertEqual(len(seen), 1, f"거절 문구가 갈렸다: {seen}")

    def test_모양이_틀린_것은_계정에_묻지도_않는다(self):
        """오타 하나가 계정 왕복이 되면 안 된다 — 주소 파서가 같은 일을 한다.
        `asked` 는 요청 단위 인증이 이미 한 번 물은 것(None)만 담고 있어야 한다."""
        self.post(self.call(1, "vinc_search", query="x", space="nope"))
        self.assertEqual([t for t in self.asked if t is not None], [])
        self.assertEqual(self.bridge.calls, [])

    def test_거절은_브리지를_부르지_않는다(self):
        """상태코드가 아니라 이것이 거절의 성질이다."""
        self.post(self.call(1, "vinc_search", query="x", space=OTHER))
        self.assertEqual(self.bridge.calls, [])

    def test_거절은_개인으로_떨어지지_않는다(self):
        """가장 나쁜 고장: 팀 쓰기가 조용히 개인 그래프에 쌓이는 것."""
        self.post(self.call(1, "vinc_ingest", space=OTHER,
                            nodes=[{"id": "concept:x", "type": "concept"}]))
        self.assertEqual(self.bridge.owners(), [])


class GateTests(_SpaceServer):
    def test_인자로_고른_팀에도_역할_게이트가_걸린다(self):
        """주소로 온 것만 검사하면 인자가 그 게이트의 우회로가 된다."""
        self.role = "viewer"
        _code, body = self.post(self.call(
            1, "vinc_ingest", space=TEAM,
            nodes=[{"id": "concept:x", "type": "concept"}]))
        self.assertTrue((body.get("result") or {}).get("isError"))
        self.assertIn("may not call", body["result"]["content"][0]["text"])
        self.assertEqual(self.bridge.calls, [])

    def test_열람자도_읽기는_된다(self):
        """게이트가 등급을 실제로 가르는지 — 전부 막으면 위 검사가 공허해진다."""
        self.role = "viewer"
        self.post(self.call(1, "vinc_search", query="x", space=TEAM))
        self.assertEqual(self.bridge.owners(), [f"team:{TEAM}"])

    def test_읽기전용_키는_인자_경로에서도_쓰기를_못_한다(self):
        """W18 스코프 게이트도 메시지별이어야 한다([[decision:scoped-key-tier]])."""
        self.scope = "read"
        _code, body = self.post(self.call(
            1, "vinc_ingest", space=TEAM,
            nodes=[{"id": "concept:x", "type": "concept"}]))
        self.assertIn("read-only", body["result"]["content"][0]["text"])
        self.assertEqual(self.bridge.calls, [])


class BatchTests(_SpaceServer):
    def test_배치에서_메시지마다_스페이스가_갈린다(self):
        """게이트가 요청당 하나면 여기서 깨진다 — 첫 메시지의 판정이 둘째를
        통과시키거나, 둘째의 스페이스가 첫째에 샌다."""
        _code, body = self.post([
            self.call(1, "vinc_search", query="a"),
            self.call(2, "vinc_search", query="b", space=TEAM),
        ])
        self.assertEqual(len(body), 2)
        self.assertEqual(self.bridge.owners(), ["user:u1", f"team:{TEAM}"])

    def test_배치의_역할_게이트도_메시지마다_본다(self):
        """개인 읽기 하나를 앞에 두면 요청 단위 게이트는 팀 쓰기를 통과시킨다."""
        self.role = "viewer"
        _code, body = self.post([
            self.call(1, "vinc_search", query="a"),
            self.call(2, "vinc_ingest", space=TEAM,
                      nodes=[{"id": "concept:x", "type": "concept"}]),
        ])
        self.assertTrue((body.get("result") or {}).get("isError"), body)
        self.assertEqual(self.bridge.calls, [])

    def test_한_메시지가_거절되면_전체가_안_돈다(self):
        """부분 실행이 여기서 최악이다 — 무엇이 실제로 들어갔는지 응답만 봐서
        모른다. 브리지에 닿기 전에 끊는다."""
        self.post([
            self.call(1, "vinc_search", query="a"),
            self.call(2, "vinc_search", query="b", space=OTHER),
        ])
        self.assertEqual(self.bridge.calls, [])


class AddressStillWinsTests(_SpaceServer):
    """주소로 붙은 세션은 그 팀에 **고정**이다. 인자가 이기면 팀 주소로 등록한
    커넥터가 개인 그래프로 새어 나갈 수 있다."""

    def test_팀_주소에서는_personal_인자가_무시된다(self):
        self.post(self.call(1, "vinc_search", query="x", space="personal"),
                  path=f"/t/{TEAM}/mcp")
        self.assertEqual(self.bridge.owners(), [f"team:{TEAM}"])

    def test_팀_주소는_다른_팀_인자로도_못_옮긴다(self):
        self.post(self.call(1, "vinc_search", query="x", space=OTHER),
                  path=f"/t/{TEAM}/mcp")
        self.assertEqual(self.bridge.owners(), [f"team:{TEAM}"])


class WhereAmITests(unittest.TestCase):
    """`initialize` 가 **묻기 전에** 스페이스를 말한다.

    "안 실으면 개인"이라는 기본은 조용해서 위험하다. 도구로만 알 수 있게 두면 부르기로
    마음먹은 세션만 알고, 읽기만 하는 세션은 영영 모른다. `instructions` 는 첫 도구
    호출 전에 무조건 도착한다."""

    def test_로컬은_아무_말도_안_한다(self):
        from vinc_mcp.rpc import _where_am_i
        self.assertIsNone(_where_am_i(None))

    def test_팀_주소는_고정을_말한다(self):
        from vinc_mcp.rpc import _where_am_i
        txt = _where_am_i(f"team:{TEAM}")
        self.assertIn(TEAM, txt)
        self.assertIn("ignored", txt)

    def test_개인은_기본이_개인임을_말한다(self):
        """이 문장이 없으면 "안 실으면 개인"이 무음 규칙이 된다."""
        from vinc_mcp import entitlement, rpc
        old = entitlement.list_spaces
        entitlement.list_spaces = lambda sub: [
            {"id": TEAM, "name": "Acme", "role": "editor"}]
        try:
            txt = rpc._where_am_i("user:u1")
        finally:
            entitlement.list_spaces = old
        self.assertIn("personal", txt)
        self.assertIn("`space`", txt)
        self.assertIn(TEAM, txt)
        self.assertIn("Acme", txt)

    def test_목록을_못_얻어도_문단은_나간다(self):
        """계정 조회 실패와 "기본이 개인이다"는 다른 층이고 후자가 더 중요하다.
        그리고 빈 목록과 못 물은 것을 뭉개면 "팀이 없다"로 읽힌다."""
        from vinc_mcp import entitlement, rpc
        old = entitlement.list_spaces
        entitlement.list_spaces = lambda sub: None
        try:
            txt = rpc._where_am_i("user:u1")
        finally:
            entitlement.list_spaces = old
        self.assertIn("personal", txt)
        self.assertIn("could not be fetched", txt)


class ListSpacesShapeTests(unittest.TestCase):
    """계정 응답을 **그대로 통과시키지 않는다** — 이 값은 도구 응답으로 나간다."""

    def _with_account(self, payload):
        import io
        from vinc_mcp import entitlement as E
        E._spaces_cache.clear()

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        old_open = E.urllib.request.urlopen
        old_seed, old_sign = E._seed, E._sign_assertion
        E._seed = lambda: b"\x00" * 32
        E._sign_assertion = lambda seed: "assertion"
        E.urllib.request.urlopen = lambda req, timeout=None: _Resp(
            json.dumps(payload).encode())
        try:
            return E.list_spaces("u1")
        finally:
            E.urllib.request.urlopen = old_open
            E._seed, E._sign_assertion = old_seed, old_sign
            E._spaces_cache.clear()

    def test_모양이_맞는_행만_담는다(self):
        rows = self._with_account({"spaces": [
            {"id": TEAM, "name": "Acme", "role": "editor"},
            {"id": OTHER, "role": "viewer"},             # 이름 없음 → 빈 문자열
            {"id": "", "role": "admin"},                 # id 없음 → 탈락
            {"id": TEAM},                                # role 없음 → 탈락
            "not-a-dict",                                # → 탈락
        ]})
        self.assertEqual(rows, [{"id": TEAM, "name": "Acme", "role": "editor"},
                                {"id": OTHER, "name": "", "role": "viewer"}])

    def test_빈_목록과_못_물었다를_가른다(self):
        """뭉개면 호출자가 "팀이 없다"로 읽고 사람에게 그렇게 말한다."""
        self.assertEqual(self._with_account({"spaces": []}), [])
        self.assertIsNone(self._with_account({"nope": 1}))


if __name__ == "__main__":
    unittest.main()
