# -*- coding: utf-8 -*-
"""스페이스는 주소로 정하고, 자격증명이 들어갈 수 있는지 증명한다.

정본: [[decision:the-endpoint-carries-the-space-and-the-credential-carries-the-right]].

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

그리고 `/mcp` 는 **바이트 그대로**여야 한다 — `tests/test_mcp_auth.py` 를 한 줄도 안
고치고 통과하는 것이 그 증거다. 거기 손이 가면 이 변경이 틀린 것이다."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

    def test_정적_토큰과_oauth_는_팀_주소를_못_쓴다(self):
        """정적 토큰은 owner 가 없어 멤버십을 물을 대상이 없고, OAuth 는
        resource_metadata 가 `issuer + "/mcp"` 로 하드코딩돼 있어 팀 주소에 등록된
        커넥터가 자기와 안 맞는 resource 를 발견한다."""
        app = self._app(lambda tok, team=None: {"owner": f"team:{team}",
                                                "plan": "team", "role": "admin"})
        self.assertFalse(app.resolve_auth("Bearer S", TEAM)["ok"])
        self.assertFalse(app.resolve_auth("Bearer oauth-ish-token", TEAM)["ok"])

    def test_개인_경로는_그대로다(self):
        app = self._app(lambda tok, team=None: {"owner": "user:u1", "plan": "pro"})
        got = app.resolve_auth("Bearer vinc_sk_ok")
        self.assertTrue(got["ok"])
        self.assertEqual(got["owner"], "user:u1")
        self.assertNotIn("role", got)


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
                              "graph_owner": f"team:{body['team']}", "role": "viewer"})
            return _Resp({"sub": "u1", "plan": "pro"})

        import urllib.request
        real_open, real_seed = urllib.request.urlopen, entitlement._seed
        entitlement._cache.clear()
        try:
            urllib.request.urlopen = fake_urlopen
            entitlement._seed = lambda: b"\x01" * 32
            a = entitlement.verify_key("vinc_sk_x")
            b = entitlement.verify_key("vinc_sk_x", TEAM)
            self.assertEqual(len(calls), 2, "팀 질문이 개인 캐시에 먹히면 안 된다")
            self.assertEqual(a["owner"], "user:u1")
            self.assertEqual(b["owner"], f"team:{TEAM}")
            self.assertEqual(b["role"], "viewer")
        finally:
            urllib.request.urlopen = real_open
            entitlement._seed = real_seed
            entitlement._cache.clear()


if __name__ == "__main__":
    unittest.main()
