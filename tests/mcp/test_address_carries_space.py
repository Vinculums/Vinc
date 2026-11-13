# -*- coding: utf-8 -*-
"""노드 주소는 자기 스페이스를 말한다.

정본: [[decision:a-node-address-names-its-space]].

전에는 앱이 복사하는 주소(`…/app/#view/trace/<id>`)에 스페이스가 없었다. 사람이
채팅에 주소를 붙이면 어시스턴트는 그 노드가 개인 그래프에 있는지 어느 팀에 있는지
몰라 스페이스를 하나씩 두드려 봐야 했다. 이제 주소가 해시 앞의 쿼리로 스페이스를
싣는다(`?space=personal` · 팀 id · `local`).

여기서 고정하는 것:
  ① 파서가 쿼리에서 스페이스를 읽고, 해시 해석은 그대로다(구 주소도 그대로 읽힌다).
  ② 허브가 `space` 인자 없이 온 호출을 **주소의 스페이스로** 보낸다 — 판정은 인자와
     같은 줄이라 비멤버 팀은 인자와 **같은 답**으로 막히고 개인으로 떨어지지 않는다.
  ③ 명시 `space` 가 주소를 이긴다.
  ④ `local` 주소는 허브가 이유를 말하고 거절한다(브리지를 부르지 않는다).
  ⑤ 데스크탑 커넥터는 계정 그래프 주소를 로컬 스토어에서 찾지 않는다.
  ⑥ 인용 URL 이 앱 주소와 같은 모양이다(`/app/` + 스페이스)."""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import http_server as H  # noqa: E402
from vinc_mcp import tools, viewurl  # noqa: E402

TEAM = "0198c0de-1111-7222-8333-444455556666"
OTHER = "0198c0de-2222-7333-8444-555566667777"


class ParseTests(unittest.TestCase):
    def test_쿼리에서_스페이스를_읽는다(self):
        self.assertEqual(viewurl.space_of(
            f"https://vincs.io/app/?space={TEAM}#view/trace/concept:x"), TEAM)
        self.assertEqual(viewurl.space_of(
            "https://vincs.io/app/?space=personal#view/trace/concept:x"), "personal")
        self.assertEqual(viewurl.space_of(
            "http://tauri.localhost/?space=local#view/trace/concept:x"), "local")

    def test_대문자도_접는다(self):
        self.assertEqual(viewurl.space_of(
            f"https://vincs.io/app/?space={TEAM.upper()}#view"), TEAM)

    def test_스페이스가_없으면_빈_값이다(self):
        for url in ("#view/trace/concept:x", "https://vincs.io/app/#view/trace/concept:x",
                    "", None, "https://vincs.io/app/?other=1#view"):
            self.assertEqual(viewurl.space_of(url), "", repr(url))

    def test_해시_안의_물음표는_스페이스가_아니다(self):
        """쿼리는 **해시 앞**만 본다. 해시 안의 문자열이 스페이스를 고를 수 있으면 id 에
        섞인 글자가 라우팅을 바꾼다."""
        self.assertEqual(viewurl.space_of(f"#view/trace/concept:x?space={TEAM}"), "")

    def test_해시_해석은_스페이스와_무관하다(self):
        a = viewurl.parse(f"https://vincs.io/app/?space={TEAM}#view/trace/concept:x/d2")
        b = viewurl.parse("#view/trace/concept:x/d2")
        self.assertEqual(a, b)


class _RecordingBridge:
    def __init__(self):
        self.owner = "user:local"
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        return {"kind": "graph", "executed": True}

    def owners(self):
        out = []
        for _m, _p, params, body in self.calls:
            src = body if isinstance(body, dict) and "owner" in body else (params or {})
            out.append(src.get("owner"))
        return out


class HubRoutingTests(unittest.TestCase):
    """실물 HTTP 로 잰다 — 스페이스를 정하는 곳이 핸들러(`_auth_for`)다."""

    def setUp(self):
        self.bridge = _RecordingBridge()

        def verify(tok, team=None):
            if team is None:
                return {"owner": "user:u1", "plan": "pro", "scope": "full"}
            if team != TEAM:                     # OTHER 는 비멤버다
                return None
            return {"owner": f"team:{team}", "plan": "team", "scope": "full",
                    "role": "editor", "member": "user:u1"}

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
    def graph(url, **extra):
        return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "vinc_graph", "arguments": {"url": url, **extra}}}

    def test_주소의_팀으로_간다(self):
        code, _ = self.post(self.graph(
            f"https://vincs.io/app/?space={TEAM}#view/trace/concept:x/d1"))
        self.assertEqual(code, 200)
        self.assertEqual(self.bridge.owners(), [f"team:{TEAM}"])

    def test_personal_주소는_개인으로_간다(self):
        self.post(self.graph("https://vincs.io/app/?space=personal#view/trace/concept:x"))
        self.assertEqual(self.bridge.owners(), ["user:u1"])

    def test_스페이스_없는_구_주소는_종전대로_개인이다(self):
        self.post(self.graph("https://vincs.io/app/#view/trace/concept:x"))
        self.assertEqual(self.bridge.owners(), ["user:u1"])

    def test_비멤버_팀_주소는_인자와_같은_답으로_막힌다(self):
        """주소에서 읽었다고 멤버십을 건너뛰지 않는다. 그리고 개인으로 떨어지지 않는다
        — 떨어지면 같은 id 의 **남의 것이 아닌 내 노드**를 그 팀 것인 양 보여 준다."""
        _, by_url = self.post(self.graph(
            f"https://vincs.io/app/?space={OTHER}#view/trace/concept:x"))
        _, by_arg = self.post(self.graph("#view/trace/concept:x", space=OTHER))
        self.assertTrue(by_url["result"]["isError"])
        self.assertEqual(by_url["result"]["content"], by_arg["result"]["content"])
        self.assertEqual(self.bridge.calls, [])

    def test_명시_인자가_주소를_이긴다(self):
        self.post(self.graph(f"https://vincs.io/app/?space={TEAM}#view/trace/concept:x",
                             space="personal"))
        self.assertEqual(self.bridge.owners(), ["user:u1"])

    def test_local_주소는_이유를_말하고_브리지를_안_부른다(self):
        _, got = self.post(self.graph(
            "http://tauri.localhost/?space=local#view/trace/concept:x"))
        self.assertTrue(got["result"]["isError"])
        self.assertIn("one device", got["result"]["content"][0]["text"])
        self.assertEqual(self.bridge.calls, [])

    def test_팀_주소로_붙은_세션은_주소가_이긴다(self):
        """`/t/<uuid>/mcp` 는 스페이스를 못박는다. 붙여넣은 주소가 그걸 개인으로 되돌릴
        수 있으면 팀 주소로 등록한 커넥터가 우회로가 된다."""
        self.post(self.graph("https://vincs.io/app/?space=personal#view/trace/concept:x"),
                  path=f"/t/{TEAM}/mcp")
        self.assertEqual(self.bridge.owners(), [f"team:{TEAM}"])


class LocalProfileTests(unittest.TestCase):
    def setUp(self):
        self.bridge = _RecordingBridge()

    def test_계정_그래프_주소를_로컬에서_찾지_않는다(self):
        with self.assertRaises(ValueError) as cm:
            tools.call("vinc_graph",
                       {"url": f"https://vincs.io/app/?space={TEAM}#view/trace/concept:x"},
                       self.bridge)
        self.assertIn("local mode", str(cm.exception))
        self.assertEqual(self.bridge.calls, [])

    def test_local_주소와_구_주소는_로컬에서_읽는다(self):
        for url in ("http://tauri.localhost/?space=local#view/trace/concept:x",
                    "#view/trace/concept:x"):
            tools.call("vinc_graph", {"url": url}, self.bridge)
        self.assertEqual(len(self.bridge.calls), 2)


class DocAddressTests(unittest.TestCase):
    def test_문서_주소는_다음_호출을_스페이스까지_적어_준다(self):
        """vinc_doc_get 에는 url 이 없다. 안 적어 주면 다음 호출이 개인으로 떨어진다."""
        run = next(t for t in tools.TOOLS if t["name"] == "vinc_graph")["run"]
        out = run({"url": f"https://vincs.io/app/?space={TEAM}#view/doc/vinc.spec.x",
                   "owner": "user:a"}, _RecordingBridge())
        self.assertFalse(out["executed"])
        self.assertIn(f"vinc_doc_get doc_id=vinc.spec.x space={TEAM}", out["note"])

    def test_개인_문서_주소에는_space_를_안_붙인다(self):
        run = next(t for t in tools.TOOLS if t["name"] == "vinc_graph")["run"]
        out = run({"url": "https://vincs.io/app/?space=personal#view/doc/vinc.spec.x",
                   "owner": "user:a"}, _RecordingBridge())
        self.assertTrue(out["note"].endswith("vinc_doc_get doc_id=vinc.spec.x"))


class CitationTests(unittest.TestCase):
    def test_커뮤니티_인용은_공개_커뮤니티_스페이스를_유지한다(self):
        owner = f"community:{TEAM}"
        for kind, ident in (("node", "concept:energy"), ("doc", f"page.{TEAM}.energy")):
            url = tools._cite_url(kind, ident, owner)
            self.assertEqual(viewurl.space_of(url), owner)
            parsed = viewurl.parse(url)
            self.assertEqual(parsed["seed"] if kind == "node" else parsed["doc"], ident)
            self.assertNotIn("space=personal", url)

    def test_팀에서_나간_인용은_팀_주소다(self):
        self.assertEqual(
            tools._cite_url("node", "concept:x", f"team:{TEAM}"),
            f"https://vincs.io/app/?space={TEAM}#view/trace/concept:x")

    def test_개인_인용은_personal_이다(self):
        self.assertEqual(tools._cite_url("doc", "vinc.spec.x", "user:u1"),
                         "https://vincs.io/app/?space=personal#view/doc/vinc.spec.x")

    def test_인용이_앱이_복사하는_주소와_같게_읽힌다(self):
        url = tools._cite_url("node", "concept:x", f"team:{TEAM}")
        self.assertEqual(viewurl.space_of(url), TEAM)
        self.assertEqual(viewurl.parse(url)["seed"], "concept:x")


APP = Path(__file__).resolve().parents[2] / "app" / "src"


class AppSourceTests(unittest.TestCase):
    """앱에는 JS 테스트 러너가 없다(`app/package.json` 에 `test` 없음). 그래서 이 리포의
    관례대로 소스를 대조한다(`test_view_url.py` · `test_lnb_tint.py` 와 같은 자리)."""

    def test_복사하는_주소에_스페이스가_실린다(self):
        src = (APP / "view-url.ts").read_text(encoding="utf-8")
        body = src[src.index("export function viewAddress"):]
        self.assertIn('"?space=" + enc(space)', body)
        # 쿼리는 해시 **앞**이다 — 해시 뒤에 붙이면 파서 둘이 주제로 읽는다.
        self.assertLess(body.index('"?space="'), body.index("formatViewUrl(sel, opts)"))
        # absoluteViewUrl 은 스페이스를 **필수**로 받는다(빠뜨린 호출이 컴파일되면 안 된다).
        self.assertRegex(src, r"export function absoluteViewUrl\([^)]*\bspace: string\b")

    def test_view_url_ts_는_import_0_이다(self):
        """`dev/view-url-check.ts` 가 브라우저 없이 이 파일을 node 로 직접 돌린다.
        무엇이든 import 하면 CI 의 Dev checks 가 ERR_MODULE_NOT_FOUND 로 죽는다
        (2026-09-26 PR 425 에서 실제로 죽었다)."""
        src = (APP / "view-url.ts").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(?m)^\s*import\s")

    def test_복사하는_자리가_전부_스페이스를_넘긴다(self):
        # 링크 복사는 지도 화면(view.ts)과 그 카드(view-cards.ts)에 있다.
        src = "".join((APP / "surfaces" / name).read_text(encoding="utf-8")
                      for name in ("view.ts", "view-cards.ts"))
        calls = src.count("absoluteViewUrl(")
        self.assertGreater(calls, 0)
        self.assertEqual(src.count("spaceName())"), calls,
                         "absoluteViewUrl 호출마다 spaceName() 을 넘겨야 한다")

    def test_스페이스_이름은_셋이다(self):
        src = (APP / "api.ts").read_text(encoding="utf-8")
        body = src[src.index("export function spaceName"):]
        body = body[:body.index("\n}\n")]
        for word in ('"local"', '"personal"', "cfg.team"):
            self.assertIn(word, body)

    def test_첫_화면_전에_주소의_스페이스를_연다(self):
        """데이터를 처음 읽기 전이어야 한다 — 뒤에 바꾸면 첫 화면이 옛 스페이스로 남는다."""
        src = (APP / "main.ts").read_text(encoding="utf-8")
        self.assertLess(src.index("await adoptSpaceFromAddress()"), src.index("  route();"))
        self.assertLess(src.index("await adoptSpaceFromAddress()"),
                        src.index("mountLiveRefresh();"))

    def test_우리가_쓴_스페이스는_밖에서_온_링크로_읽지_않는다(self):
        """스페이스 전환 뒤 새로고침에서 주소창의 옛 값이 방금 고른 것을 되돌리면 안 된다."""
        src = (APP / "api.ts").read_text(encoding="utf-8")
        body = src[src.index("export async function adoptSpaceFromAddress"):]
        self.assertIn("raw !== mark", body)
        self.assertIn("sessionStorage", src[src.index("export function writeSpaceToAddress"):])


if __name__ == "__main__":
    unittest.main()
