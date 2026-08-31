# -*- coding: utf-8 -*-
"""팀 스페이스 통과로 — 데스크탑의 그래프 호출이 계정으로 나가는 자리.

이 모듈은 **해석하지 않는 것**이 요점이다. 경로를 그대로 얹고 무엇이 허용되는지는
계정이 판정한다 — 여기서 op 를 다시 해석하면 네 번째 목록이 생기고, 목록 넷이 어긋나면
그 기능만 조용히 죽는다(이 리포의 실제 사고 이력).

그래서 여기서 고정하는 것은 "무엇을 통과시키나"가 아니라 **경계**다:

  ① 임의 호스트로 못 나간다. 접두는 이 모듈이 붙이고 `..`·스킴·호스트는 거절한다.
     안 그러면 루프백에 닿는 무엇이든 이 사이드카를 포워더로 쓴다.
  ② 자격은 바디로만 온다. 없으면 나가지 않는다.
  ③ 계정의 상태코드와 몸통을 **그대로** 돌려준다. 다시 포장하면 호출부가 "누가
     거절했는가"를 잃는다.
  ④ 앞문 401(빈 본문)과 계정의 거절을 **가른다**. 2026-08-31 에 그 구분이 없어서
     하루를 썼다.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import spaceproxy  # noqa: E402

TEAM = "0198c0de-1111-7222-8333-444455556666"


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class PathBoundaryTests(unittest.TestCase):
    """① — 이 사이드카가 임의 호스트 포워더가 되지 않는가."""

    def test_상대경로만_통과한다(self):
        for good in ("/view", "/ingest/fragment", "/doc/put"):
            self.assertEqual(spaceproxy._safe_path(good), good)

    def test_절대주소와_상위이동은_거절한다(self):
        for bad in ("https://evil.example/x", "//evil.example/x", "/../../etc/passwd",
                    "view", "", "/a\\b"):
            self.assertIsNone(spaceproxy._safe_path(bad), bad)

    def test_쿼리와_프래그먼트를_거절한다(self):
        """경로에 `?`·`#` 이 섞이면 조립이 **조용히** 틀린다.

        `#` 은 더 나쁘다: urllib 이 프래그먼트부터 잘라 내보내므로 뒤에 붙인
        `team` 이 통째로 사라지고, 계정은 팀을 못 봤으니 개인 그래프로 답한다.
        팀에 쓰려던 것이 조용히 개인 것이 되는 것이야말로 이 표면이 막으려는
        고장이다. `?` 쪽은 `team` 이 두 번 실려 어느 쪽이 이기는지를 계정의
        파서가 정하게 된다. 쿼리는 `params` 로 오므로 경로에 실을 자리가 없다."""
        for bad in ("/view?team=other", "/view#frag", "/view?x=1", "/doc/put#"):
            self.assertIsNone(spaceproxy._safe_path(bad), bad)

    def test_퍼센트로_감춘_상위이동도_거절한다(self):
        """리터럴 `..` 만 보면 `%2e%2e` 가 지나간다. 앞단 인그레스가 정규화하면
        `/api/graph` 접두 밖으로 나가고, 접두가 하나뿐인 것이 이 모듈의 경계다."""
        for bad in ("/%2e%2e/%2e%2e/api/teams", "/a/%2E%2E/b", "/a%5Cb"):
            self.assertIsNone(spaceproxy._safe_path(bad), bad)

    def test_팀은_정확히_한_번_실린다(self):
        """통과한 경로로 조립했을 때 `team` 이 하나이고 살아남는가.

        위 두 검사가 입구를 막지만, 막는 이유가 여기 있다 — 이것이 깨지면
        위의 거절은 목적을 잃는다."""
        seen = {}
        old = spaceproxy.urllib.request.urlopen

        def spy(req, *a, **k):
            seen["selector"] = req.selector
            return _Resp(b"{}")

        spaceproxy.urllib.request.urlopen = spy
        try:
            spaceproxy.call(token="k", team="TEAM-1", method="GET", path="/view")
        finally:
            spaceproxy.urllib.request.urlopen = old
        self.assertIn("team=TEAM-1", seen["selector"])
        self.assertEqual(seen["selector"].count("team="), 1, seen["selector"])

    def test_나쁜_경로는_아예_안_나간다(self):
        called = []
        old = spaceproxy.urllib.request.urlopen
        spaceproxy.urllib.request.urlopen = lambda *a, **k: called.append(a) or _Resp(b"{}")
        try:
            st, out = spaceproxy.call(token="vinc_sk_x", team=TEAM,
                                      method="GET", path="https://evil.example/x")
        finally:
            spaceproxy.urllib.request.urlopen = old
        self.assertEqual(st, 400)
        self.assertEqual(called, [], "거절은 요청을 보내지 않는다")


class CredentialTests(unittest.TestCase):
    """② — 자격 없이는 나가지 않는다."""

    def test_키나_팀이_없으면_400_이고_안_나간다(self):
        called = []
        old = spaceproxy.urllib.request.urlopen
        spaceproxy.urllib.request.urlopen = lambda *a, **k: called.append(a) or _Resp(b"{}")
        try:
            self.assertEqual(spaceproxy.call(token="", team=TEAM, method="GET",
                                             path="/view")[0], 400)
            self.assertEqual(spaceproxy.call(token="k", team="", method="GET",
                                             path="/view")[0], 400)
        finally:
            spaceproxy.urllib.request.urlopen = old
        self.assertEqual(called, [])


class WireTests(unittest.TestCase):
    """무엇을 들고 나가는가 — 주소·헤더·바디."""

    def _capture(self, **kw):
        seen = {}

        def _open(req, timeout=None):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["headers"] = {k.lower(): v for k, v in req.header_items()}
            seen["data"] = req.data
            return _Resp(json.dumps({"executed": True}).encode())

        old = spaceproxy.urllib.request.urlopen
        spaceproxy.urllib.request.urlopen = _open
        try:
            st, out = spaceproxy.call(**kw)
        finally:
            spaceproxy.urllib.request.urlopen = old
        return st, out, seen

    def test_계정의_그래프_평면으로_간다(self):
        _st, _out, seen = self._capture(token="vinc_sk_x", team=TEAM, method="GET",
                                        path="/view", params={"depth": 2})
        self.assertTrue(seen["url"].startswith("https://vincs.io/api/graph/view?"),
                        seen["url"])
        self.assertIn("team=" + TEAM, seen["url"])
        self.assertIn("depth=2", seen["url"])
        self.assertEqual(seen["headers"]["authorization"], "Bearer vinc_sk_x")

    def test_빈_파라미터는_안_싣는다(self):
        """사이드카가 `None`·빈 문자열을 그대로 실으면 계정이 op 인자로 오해한다."""
        _st, _out, seen = self._capture(token="k", team=TEAM, method="GET",
                                        path="/view", params={"a": "", "b": None, "c": 1})
        self.assertNotIn("a=", seen["url"])
        self.assertNotIn("b=", seen["url"])
        self.assertIn("c=1", seen["url"])

    def test_쓰기는_바디를_json_으로_싣는다(self):
        _st, _out, seen = self._capture(token="k", team=TEAM, method="POST",
                                        path="/ingest/fragment", body={"nodes": []})
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(json.loads(seen["data"].decode()), {"nodes": []})
        self.assertEqual(seen["headers"]["content-type"], "application/json")

    def test_base_를_바꿀_수_있다(self):
        """스테이징·자체호스팅에서 이 주소가 고정이면 통과로가 통째로 못 쓴다."""
        _st, _out, seen = self._capture(token="k", team=TEAM, method="GET",
                                        path="/view", base="http://127.0.0.1:8080")
        self.assertTrue(seen["url"].startswith("http://127.0.0.1:8080/api/graph/view"),
                        seen["url"])


class RefusalPassthroughTests(unittest.TestCase):
    """③④ — 계정이 준 것을 그대로, 앞문은 갈라서."""

    def _http_error(self, code, body):
        def _open(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, code, "err", {},
                                         io.BytesIO(body))
        old = spaceproxy.urllib.request.urlopen
        spaceproxy.urllib.request.urlopen = _open
        try:
            return spaceproxy.call(token="k", team=TEAM, method="GET", path="/view")
        finally:
            spaceproxy.urllib.request.urlopen = old

    def test_계정의_거절은_그대로_온다(self):
        st, out = self._http_error(401, b'{"error":"not_authenticated"}')
        self.assertEqual(st, 401)
        self.assertEqual(out, {"error": "not_authenticated"})

    def test_앞문_401_은_계정의_거절과_구분된다(self):
        """빈 본문 401 은 EasyAuth 다. 뭉개면 "키가 나쁘다"로 읽히고, 실제로 그
        오독에 하루를 썼다(2026-08-31)."""
        st, out = self._http_error(401, b"")
        self.assertEqual(st, 401)
        self.assertEqual(out["error"], "account_unreachable")

    def test_네트워크_실패는_502_다(self):
        def _open(req, timeout=None):
            raise OSError("dns")
        old = spaceproxy.urllib.request.urlopen
        spaceproxy.urllib.request.urlopen = _open
        try:
            st, out = spaceproxy.call(token="k", team=TEAM, method="GET", path="/view")
        finally:
            spaceproxy.urllib.request.urlopen = old
        self.assertEqual(st, 502)
        self.assertEqual(out["error"], "account_unreachable")


class RouteTests(unittest.TestCase):
    """사이드카 라우트로 실제로 닿는가, 그리고 **스토어 없이도** 도는가."""

    def test_스토어가_없어도_도는_목록에_있다(self):
        """팀 그래프는 로컬에 없다. 로컬이 깨진 기기에서 팀까지 같이 죽으면 안 된다."""
        from api.server import _Handler
        self.assertIn("/space/proxy", _Handler._POST_WITHOUT_STORE)

    def test_핸들러가_통과로를_부른다(self):
        from api import server as sv
        app = sv.VincApp.__new__(sv.VincApp)
        seen = {}

        def _call(**kw):
            seen.update(kw)
            return 200, {"executed": True}

        old = sv.spaceproxy.call
        sv.spaceproxy.call = _call
        try:
            st, out = app.write_space_proxy(
                {"token": "k", "team": TEAM, "method": "POST", "path": "/ingest/fragment",
                 "body": {"nodes": []}})
        finally:
            sv.spaceproxy.call = old
        self.assertEqual((st, out), (200, {"executed": True}))
        self.assertEqual(seen["team"], TEAM)
        self.assertEqual(seen["path"], "/ingest/fragment")
        self.assertEqual(seen["body"], {"nodes": []})


if __name__ == "__main__":
    unittest.main()
