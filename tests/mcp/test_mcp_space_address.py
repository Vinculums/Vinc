# -*- coding: utf-8 -*-
"""스페이스는 주소로 정하고, 자격증명이 들어갈 수 있는지 증명한다.

처음에는 주소만이 스페이스를 정했고, 그 결정은 스페이스를 인자로도 받는 지금 결정으로
대체됐다. 주소 경로는 그대로 남아 이 파일이 지킨다. 정본: [[decision:the-space-is-an-argument-and-the-default-is-personal]].

`/mcp` 는 개인 그래프, `/t/<uuid>/mcp` 는 그 팀 그래프. **URL 은 의도, 키는 권한.**
팀 id 를 안다는 것만으로는 아무것도 못 하고, 키 하나가 모든 스페이스를 섬긴다.

여기서 고정하는 것은 성공보다 **거절의 성질**이다. 셋:

  ① 거절은 **브리지를 부르지 않는다.** 상태코드가 아니라 이것이 성질이다 — 어떤
     경로로든 요청이 그래프에 닿으면 그 뒤의 owner 강제는 의미가 없다.
  ② 팀 주소의 실패는 전부 **같은 404** 다. 401 이면 "그 팀은 있는데 너는 아니다"가
     되어, 유효한 키 하나와 추측으로 조직 구조를 훑을 수 있다. 모양 틀린 주소까지
     같은 답이어야 한다.
  ③ **개인으로 떨어지지 않는다.** 팀 쓰기가 조용히 개인 그래프에 쌓이는 것이 여기서
     고를 수 있는 가장 나쁜 고장이다.

그리고 `/mcp` 는 **바이트 그대로**여야 한다 — `tests/mcp/test_mcp_auth.py` 를 한 줄도 안
고치고 통과하는 것이 그 증거다. 거기 손이 가면 이 변경이 틀린 것이다."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import http_server as H  # noqa: E402
from vinc_mcp import tools  # noqa: E402

TEAM = "0198c0de-1111-7222-8333-444455556666"


class SplitSpaceTests(unittest.TestCase):
    """주소 파서. 인증보다 **먼저** 돌고, 여기서 스페이스가 아니게 된 주소는
    뒤에서 404 가 된다 — 400 이 아니라 404 인 것이 요점이다."""

    def test_팀_주소를_가른다(self):
        self.assertEqual(H.split_space(f"/t/{TEAM}/mcp"), (TEAM, "/mcp"))

    def test_개인_주소는_그대로다(self):
        self.assertEqual(H.split_space("/mcp"), (None, "/mcp"))
        self.assertEqual(H.split_space("/health"), (None, "/health"))

    def test_대문자_uuid_는_접힌다(self):
        """같은 팀이 대소문자로 두 스페이스가 되면 안 된다."""
        self.assertEqual(H.split_space(f"/t/{TEAM.upper()}/mcp"), (TEAM, "/mcp"))

    def test_모양이_틀리면_스페이스가_아니다(self):
        for bad in ("/t/not-a-uuid/mcp", "/t//mcp", "/t/123/mcp",
                    f"/t/{TEAM[:-1]}/mcp"):
            self.assertIsNone(H.split_space(bad)[0], bad)


class ToolTierTests(unittest.TestCase):
    """등급은 주석표에서 유도한다 — op 표로는 도구 여섯을 못 덮는다."""

    def test_모든_도구에_등급이_있다(self):
        for t in tools.TOOLS:
            self.assertIn(tools.tool_tier(t["name"]), ("viewer", "editor", "admin"))

    def test_모르는_도구는_admin_이다(self):
        """새 도구가 주석 없이 들어와도 **조용히 열리지 않는다.**"""
        self.assertEqual(tools.tool_tier("vinc_brand_new"), "admin")
        self.assertFalse(tools.role_allows("editor", "vinc_brand_new"))

    def test_모르는_역할은_거부된다(self):
        for role in ("OWNER", "Admin", "", "owner", None):
            self.assertFalse(tools.role_allows(role, "vinc_search"), repr(role))

    def test_등급이_실제로_갈린다(self):
        self.assertTrue(tools.role_allows("viewer", "vinc_search"))
        self.assertFalse(tools.role_allows("viewer", "vinc_ingest"))
        self.assertTrue(tools.role_allows("editor", "vinc_ingest"))
        self.assertFalse(tools.role_allows("editor", "vinc_delete"))
        self.assertTrue(tools.role_allows("admin", "vinc_delete"))

    def test_op_로_매핑되는_도구는_기존_두_표와_어긋나지_않는다(self):
        """세 번째 표를 만든 것이 아니라 **같은 판정을 다른 키로** 한 것임을 못 박는다.
        어긋나면 웹 평면과 MCP 평면이 같은 op 를 다르게 판정하고, 그건 그 op 에서만
        조용히 갈린다(`record:fact-ap1-role-table-mirror` 가 적어 둔 고장)."""
        import inspect
        import re
        route2op = {p: op for op, (_m, p) in H._PROXY_OPS.items()}
        checked = 0
        for spec in tools.TOOLS:
            try:
                src = inspect.getsource(spec["run"])
            except Exception:
                continue
            m = re.search(r'request\(\s*"(?:GET|POST)",\s*"(/[a-z0-9/\-]+)"', src)
            op = route2op.get(m.group(1)) if m else None
            if not op:
                continue
            checked += 1
            want = ("admin" if op in H._ADMIN_OPS
                    else "editor" if op in H._EDITOR_OPS else "viewer")
            self.assertEqual(tools.tool_tier(spec["name"]), want, f"{spec['name']}/{op}")
        self.assertGreaterEqual(checked, 15, "매핑이 통째로 실패하면 이 검사는 공허하다")


class _Bridge:
    """브리지를 **불렀는지**만 본다. 거절의 성질이 상태코드가 아니라 이것이다."""

    def __init__(self):
        self.last = None
        self.owner = "user:local"

    def request(self, method, path, params=None, body=None, timeout=60):
        self.last = (method, path, params, body)
        return {"executed": True}


class ResolveAuthSpaceTests(unittest.TestCase):
    """`resolve_auth(header, space)` 하나로 판정이 끝나야 한다."""

    def _app(self, verify):
        return H.MCPHTTPApp(_Bridge(), static_token="S", verify_key=verify)

    def test_활성_멤버는_팀_owner_와_역할을_받는다(self):
        app = self._app(lambda tok, team=None: (
            {"owner": f"team:{team}", "plan": "team", "role": "editor",
             "member": "user:u1"} if team else {"owner": "user:u1", "plan": "pro"}))
        got = app.resolve_auth("Bearer vinc_sk_ok", TEAM)
        self.assertTrue(got["ok"])
        self.assertEqual(got["owner"], f"team:{TEAM}")
        self.assertEqual(got["role"], "editor")
        self.assertEqual(got["member"], "user:u1")

    def test_비멤버는_개인으로_떨어지지_않는다(self):
        """계정이 None 을 주면 거절이다. 여기서 개인 owner 로 물러서면 팀 쓰기가
        조용히 개인 그래프에 쌓인다 — 이 설계에서 가장 나쁜 고장이다."""
        app = self._app(lambda tok, team=None: (
            None if team else {"owner": "user:u1", "plan": "pro"}))
        got = app.resolve_auth("Bearer vinc_sk_ok", TEAM)
        self.assertFalse(got["ok"])
        self.assertIsNone(got["owner"])

    def test_팀_주소에_개인_owner_가_오면_거절한다(self):
        """계정이 실수로 개인 답을 줘도 허브가 받지 않는다(양쪽에서 닫는다)."""
        app = self._app(lambda tok, team=None: {"owner": "user:u1", "plan": "pro"})
        self.assertFalse(app.resolve_auth("Bearer vinc_sk_ok", TEAM)["ok"])

    def test_정적_토큰은_팀_주소를_못_쓴다(self):
        """정적 토큰은 owner 가 아예 없어 **멤버십을 물을 대상이 없다.** 이건 #120
        이후에도 그대로다 — 팀 접근은 "누구인가"가 있어야 판정되는데 정적 토큰에는
        그 누구가 없다.

        ⚠ 이 검사는 원래 OAuth 거절과 한 몸이었다. #120 이 OAuth 를 열면서 갈랐는데,
        **가르지 않았으면 공허하게 통과했을 것**이다: 이 앱은 `oauth=None` 이라
        OAuth 분기에 닿지도 않으므로 "OAuth 가 막혔다"를 증명하지 못한다. 실제 OAuth
        경로는 아래 `OAuthSpaceTests` 가 게이트를 실물로 물려 잰다."""
        app = self._app(lambda tok, team=None: {"owner": f"team:{team}",
                                                "plan": "team", "role": "admin"})
        self.assertFalse(app.resolve_auth("Bearer S", TEAM)["ok"])

    def test_키도_oauth_도_아닌_토큰은_팀_주소를_못_쓴다(self):
        """OAuth 게이트가 없는 배포에서 키 접두가 아닌 Bearer 는 갈 곳이 없다."""
        app = self._app(lambda tok, team=None: {"owner": f"team:{team}",
                                                "plan": "team", "role": "admin"})
        self.assertFalse(app.resolve_auth("Bearer some-random-token", TEAM)["ok"])

    def test_개인_경로는_그대로다(self):
        app = self._app(lambda tok, team=None: {"owner": "user:u1", "plan": "pro"})
        got = app.resolve_auth("Bearer vinc_sk_ok")
        self.assertTrue(got["ok"])
        self.assertEqual(got["owner"], "user:u1")
        self.assertNotIn("role", got)


class _Gate:
    """`resolve(token)` 만 흉내내는 OAuth 게이트. 토큰 → 스탬핑된 레코드."""

    def __init__(self, table):
        self.table = table
        self.issuer = "https://mcp.example"
        # 앱이 `oauth.store` 를 읽어 자기 store 로 삼는다. None 이면 `_fresh_plan` 이
        # 동결된 등급을 그대로 돌려주므로 이 검사들은 네트워크를 안 탄다.
        self.store = None

    def resolve(self, token):
        return self.table.get(token)

    def resource_metadata(self, resource_path="/mcp"):
        from vinc_mcp.oauth import OAuthGate
        return OAuthGate.resource_metadata(self, resource_path)


class OAuthSpaceTests(unittest.TestCase):
    """OAuth 커넥터가 팀 주소에 붙는다 (#120).

    왜 열었나: claude.ai 커스텀 커넥터는 임의 헤더를 못 넣는다. 키 전용이면 팀
    그래프는 `--header` 를 쓸 수 있는 Claude Code 에서만 닿고, 그건 사실상 "팀은
    개발자만 쓸 수 있다"가 된다. 그리고 그 불편이 "개인 커넥터에서 팀도 되게 해
    달라"는 요구를 만드는데, 들어주면 스페이스가 도구 인자가 되어 팀 쓰기가 조용히
    개인 그래프에 쌓인다.

    그러니 여기서 고정하는 것도 성공보다 **거절의 성질**이다. 키 경로와 **같은**
    셋이어야 한다 — 토큰만으로는 아무것도 아니고, 실패는 개인으로 안 떨어지고,
    역할 없이는 못 들어온다."""

    TOKEN = "oauth-access-token"

    def _app(self, member, *, owner="user:u1"):
        gate = _Gate({self.TOKEN: {"owner": owner, "plan": "pro",
                                   "client_id": "c1"}})
        return H.MCPHTTPApp(_Bridge(), static_token="S", oauth=gate,
                            verify_key=lambda tok, team=None: None,
                            verify_member=member)

    def test_활성_멤버는_팀_owner_와_역할을_받는다(self):
        app = self._app(lambda sub, team: {"owner": f"team:{team}",
                                           "role": "editor",
                                           "member": f"user:{sub}"})
        got = app.resolve_auth(f"Bearer {self.TOKEN}", TEAM)
        self.assertTrue(got["ok"])
        self.assertEqual(got["owner"], f"team:{TEAM}")
        self.assertEqual(got["role"], "editor")
        self.assertEqual(got["source"], "oauth")

    def test_계정에_묻는_것은_토큰의_사람과_그_팀이다(self):
        """토큰이 유효한 것만으로는 아무것도 아니다 — 무엇을 물었는지 못 박는다."""
        asked = []
        app = self._app(lambda sub, team: (asked.append((sub, team))
                                           or {"owner": f"team:{team}",
                                               "role": "viewer"}))
        app.resolve_auth(f"Bearer {self.TOKEN}", TEAM)
        self.assertEqual(asked, [("u1", TEAM)])

    def test_비멤버는_개인으로_떨어지지_않는다(self):
        """이 설계에서 고를 수 있는 가장 나쁜 고장이 여기다. 키 경로와 같은 규칙."""
        app = self._app(lambda sub, team: None)
        got = app.resolve_auth(f"Bearer {self.TOKEN}", TEAM)
        self.assertFalse(got["ok"])
        self.assertIsNone(got["owner"])

    def test_계정이_개인_owner_를_줘도_받지_않는다(self):
        app = self._app(lambda sub, team: {"owner": "user:u1", "role": "admin"})
        self.assertFalse(app.resolve_auth(f"Bearer {self.TOKEN}", TEAM)["ok"])

    def test_역할이_없으면_거절한다(self):
        """역할은 팀 게이트의 **입력**이다. 빈 값으로 통과시키면 role_allows 가
        전부 거부하거나(조용한 고장) 빈 문자열이 표를 비켜 간다."""
        for bad in ({"owner": f"team:{TEAM}"}, {"owner": f"team:{TEAM}", "role": ""}):
            app = self._app(lambda sub, team, r=bad: r)
            self.assertFalse(app.resolve_auth(f"Bearer {self.TOKEN}", TEAM)["ok"], bad)

    def test_관리키_부트스트랩_토큰은_못_쓴다(self):
        """owner=None 으로 발급된 토큰(단일 테넌트 하위호환)은 물을 대상이 없다 —
        정적 토큰과 같은 이유다."""
        app = self._app(lambda sub, team: {"owner": f"team:{team}", "role": "admin"},
                        owner=None)
        self.assertFalse(app.resolve_auth(f"Bearer {self.TOKEN}", TEAM)["ok"])

    def test_모르는_토큰은_거절한다(self):
        app = self._app(lambda sub, team: {"owner": f"team:{team}", "role": "admin"})
        self.assertFalse(app.resolve_auth("Bearer nope", TEAM)["ok"])

    def test_verify_member_가_없으면_팀_oauth_만_닫힌다(self):
        """계정이 라우트를 아직 배포 안 한 동안의 모양. 닫힌 쪽으로 꺼진다."""
        gate = _Gate({self.TOKEN: {"owner": "user:u1", "plan": "pro"}})
        app = H.MCPHTTPApp(_Bridge(), static_token="S", oauth=gate,
                           verify_key=lambda tok, team=None: None)
        self.assertFalse(app.resolve_auth(f"Bearer {self.TOKEN}", TEAM)["ok"])
        # 개인 경로는 그대로 살아 있다.
        self.assertTrue(app.resolve_auth(f"Bearer {self.TOKEN}")["ok"])

    def test_개인_oauth_는_바이트_그대로다(self):
        """#120 이 개인 경로를 안 건드린다는 것이 계약이다."""
        app = self._app(lambda sub, team: {"owner": f"team:{team}", "role": "admin"})
        got = app.resolve_auth(f"Bearer {self.TOKEN}")
        self.assertTrue(got["ok"])
        self.assertEqual(got["owner"], "user:u1")
        self.assertEqual(got["scope"], "full")
        self.assertNotIn("role", got)


class ResourceMetadataTests(unittest.TestCase):
    """RFC 9728 문서는 **자원마다 다르다**. 하드코딩이 팀 주소에서 OAuth 를 막아
    두었던 바로 그 이유이므로, 여기가 #120 의 급소다."""

    def _gate(self):
        from vinc_mcp.oauth import OAuthGate
        return OAuthGate("https://mcp.example", None)

    def test_개인_자원은_종전과_같다(self):
        self.assertEqual(self._gate().resource_metadata(),
                         {"resource": "https://mcp.example/mcp",
                          "authorization_servers": ["https://mcp.example"],
                          "scopes_supported": ["offline_access"]})

    def test_팀_자원은_자기_주소를_말한다(self):
        got = self._gate().resource_metadata(f"/t/{TEAM}/mcp")
        self.assertEqual(got["resource"], f"https://mcp.example/t/{TEAM}/mcp")

    def test_인가서버는_갈리지_않는다(self):
        """자원은 여럿이어도 인가 서버는 하나다. 갈리면 클라이언트가 팀마다 다른
        인가 서버를 발견하는데 그건 사실이 아니다."""
        g = self._gate()
        self.assertEqual(g.resource_metadata()["authorization_servers"],
                         g.resource_metadata(f"/t/{TEAM}/mcp")["authorization_servers"])
        self.assertEqual(g.as_metadata()["issuer"], "https://mcp.example")


class WellKnownRoutingTests(unittest.TestCase):
    """`/.well-known/oauth-protected-resource` 의 **경로 삽입형**(RFC 9728).

    커넥터는 자기가 붙은 자원의 문서를 그 형태로 찾는다. 루트 형태 하나만 답하면
    팀 주소의 커넥터가 개인 자원의 문서를 발견하고, 그것이 팀 주소에서 OAuth 를
    막아 두었던 이유다(#120). 그래서 라우팅까지 실물로 잰다 — 메타데이터 함수만
    고치고 라우트를 안 고치면 아무것도 안 달라진다."""

    def setUp(self):
        import json
        import threading
        import urllib.error
        import urllib.request
        from vinc_mcp.oauth import OAuthGate
        self._json, self._urlreq, self._urlerr = json, urllib.request, urllib.error
        gate = OAuthGate("https://mcp.example", None)
        app = H.MCPHTTPApp(_Bridge(), static_token="S", oauth=gate)
        self.httpd = H.make_server(app, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()

    def _get(self, path):
        try:
            with self._urlreq.urlopen(
                    f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, self._json.loads(r.read().decode())
        except self._urlerr.HTTPError as e:
            return e.code, self._json.loads(e.read().decode() or "{}")

    def test_루트_형태는_개인_자원이다(self):
        """기존 커넥터가 이것으로 발견하고 있다 — 바이트 그대로 유지한다."""
        code, body = self._get("/.well-known/oauth-protected-resource")
        self.assertEqual(code, 200)
        self.assertEqual(body["resource"], "https://mcp.example/mcp")

    def test_경로_삽입형_개인(self):
        code, body = self._get("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(code, 200)
        self.assertEqual(body["resource"], "https://mcp.example/mcp")

    def test_경로_삽입형_팀은_자기_주소를_말한다(self):
        code, body = self._get(
            f"/.well-known/oauth-protected-resource/t/{TEAM}/mcp")
        self.assertEqual(code, 200)
        self.assertEqual(body["resource"], f"https://mcp.example/t/{TEAM}/mcp")

    def test_모르는_자원은_404_다(self):
        """아무 경로에나 답하면 이 엔드포인트가 주소 탐침이 된다."""
        for bad in ("/.well-known/oauth-protected-resource/nope",
                    f"/.well-known/oauth-protected-resource/t/{TEAM}/other",
                    "/.well-known/oauth-protected-resource/t/not-a-uuid/mcp"):
            code, _ = self._get(bad)
            self.assertEqual(code, 404, bad)

    def test_인가서버_문서는_경로를_안_탄다(self):
        code, body = self._get("/.well-known/oauth-authorization-server")
        self.assertEqual(code, 200)
        self.assertEqual(body["issuer"], "https://mcp.example")


class VerifyKeyCacheTests(unittest.TestCase):
    """캐시 키에 team 이 들어가는지 — 안 들어가면 **개인 답이 팀 질문을 만족**시킨다.

    같은 키로 두 스페이스를 묻는 것이 정상 흐름이라 이건 이론이 아니다."""

    def test_개인과_팀이_같은_캐시_칸을_쓰지_않는다(self):
        from vinc_mcp import entitlement
        calls: list = []

        class _Resp:
            def __init__(self, body):
                self._b = body
            def read(self):
                import json
                return json.dumps(self._b).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            import json
            body = json.loads(req.data.decode())
            calls.append(body.get("team"))
            if body.get("team"):
                return _Resp({"sub": "u1", "plan": "team",
                              "graph_owner": f"team:{body['team']}", "role": "viewer", "key_space": body["team"]})
            return _Resp({"sub": "u1", "plan": "pro"})

        real_open, real_seed = entitlement.hubhttp.urlopen, entitlement._seed
        entitlement._cache.clear()
        try:
            entitlement.hubhttp.urlopen = fake_urlopen
            entitlement._seed = lambda: b"\x01" * 32
            a = entitlement.verify_key("vinc_sk_x")
            b = entitlement.verify_key("vinc_sk_team_x", TEAM)
            self.assertEqual(len(calls), 2, "팀 질문이 개인 캐시에 먹히면 안 된다")
            self.assertEqual(a["owner"], "user:u1")
            self.assertEqual(b["owner"], f"team:{TEAM}")
            self.assertEqual(b["role"], "viewer")
        finally:
            entitlement.hubhttp.urlopen = real_open
            entitlement._seed = real_seed
            entitlement._cache.clear()


if __name__ == "__main__":
    unittest.main()
