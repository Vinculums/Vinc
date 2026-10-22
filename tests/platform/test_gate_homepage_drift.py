# -*- coding: utf-8 -*-
"""gate_homepage_drift 가 홈페이지의 뒤처짐을 잡고, 못 읽으면 결론을 내지 않는가.

네트워크는 쓰지 않는다. 공개 페이지 대신 셀프테스트의 렌더러로 만든 페이지를 준다.
실제 페이지 마크업과 맞는지는 CI 스텝이 공개 페이지에 대고 확인한다.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts import gate_homepage_drift as g

_ROOT = Path(__file__).resolve().parents[2]


def _repo_json() -> tuple[dict, dict]:
    tools = json.loads((_ROOT / g.TOOLS_JSON).read_text(encoding="utf-8"))
    api = json.loads((_ROOT / g.OPENAPI_JSON).read_text(encoding="utf-8"))
    return tools, api


def _pages(tools: dict, api: dict):
    pages = {g.TOOLS_PAGE: g._tools_page(tools["tools"]), g.API_PAGE: g._api_page(api)}
    return lambda url, **_: next(v for k, v in pages.items() if url.endswith(k))


def _run(fetch, base_json=None, env=None) -> tuple[int, str]:
    out = io.StringIO()
    environ = {"GITHUB_ACTIONS": "", "GITHUB_STEP_SUMMARY": "", "BASE": ""}
    environ.update(env or {})
    with patch.object(g, "fetch", fetch), patch.dict(os.environ, environ), \
            contextlib.redirect_stdout(out):
        if base_json is not None:
            with patch.object(g, "_base_json", base_json):
                rc = g.run("https://example.test")
        else:
            rc = g.run("https://example.test")
    return rc, out.getvalue()


class HomepageDriftTests(unittest.TestCase):
    def test_selftest_bites(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(g.selftest(), 0)

    def test_pages_rendered_from_this_tree_match(self):
        tools, api = _repo_json()
        rc, out = _run(_pages(tools, api))
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS", out)

    def test_unreadable_site_is_no_verdict(self):
        def down(url, **_):
            raise g.Unreadable(f"{url} 를 받지 못했다")
        rc, out = _run(down)
        self.assertEqual(rc, 2)
        self.assertNotIn("DRIFT", out)

    def test_a_tool_and_a_route_this_change_adds_are_named(self):
        """이 변경이 더한 것과 기준부터 뒤처진 것을 가른다. 경고 주석은 이 변경 몫에만 단다."""
        tools, api = _repo_json()
        base_tools = json.loads(json.dumps(tools))
        base_api = json.loads(json.dumps(api))
        # 홈페이지는 기준에서 도구 하나를 아직 못 따라왔다(기준부터 있던 차이).
        lagging = base_tools["tools"][-1]["name"]
        page_tools = {"tools": base_tools["tools"][:-1]}
        fetch = _pages(page_tools, base_api)
        # 이 변경이 도구 하나와 경로 하나를 더한다.
        tools["tools"].append({"name": "vinc_brand_new", "summary": "s", "reference": "r", "params": []})
        api["paths"]["/v1/brand-new"] = {"get": {"responses": {"200": {}}}}

        def base_json(_base, rel):
            return base_tools if rel == g.TOOLS_JSON else base_api

        summary = Path(tempfile.mkdtemp()) / "summary.md"
        with patch.object(g, "ROOT", self._tree(tools, api)):
            rc, out = _run(fetch, base_json, {"BASE": "abc", "GITHUB_ACTIONS": "true",
                                              "GITHUB_STEP_SUMMARY": str(summary)})
        self.assertEqual(rc, 1, out)
        self.assertIn("::warning file=vinc_mcp/tool_reference.json,title=homepage drift::"
                      "도구 vinc_brand_new 이", out)
        self.assertIn("::warning file=vinc_mcp/openapi_v1.json,title=homepage drift::"
                      "REST GET /v1/brand-new", out)
        self.assertIn(f"::notice file=vinc_mcp/tool_reference.json,title=homepage drift::"
                      f"도구 {lagging} 이", out)
        text = summary.read_text(encoding="utf-8")
        self.assertIn("이 변경이 만든 차이", text)
        self.assertIn("기준 커밋에서도 이미 있던 차이", text)

    def _tree(self, tools: dict, api: dict) -> Path:
        root = Path(tempfile.mkdtemp(prefix="drift_"))
        (root / "vinc_mcp").mkdir()
        (root / g.TOOLS_JSON).write_text(json.dumps(tools), encoding="utf-8")
        (root / g.OPENAPI_JSON).write_text(json.dumps(api), encoding="utf-8")
        return root


if __name__ == "__main__":
    unittest.main()
