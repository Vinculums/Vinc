#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""홈페이지가 이 변경의 MCP 도구 목록과 REST 레퍼런스를 따라왔는가 (알림 게이트).

    python -X utf8 scripts/gate_homepage_drift.py              # 대조
    BASE=<sha> python -X utf8 scripts/gate_homepage_drift.py   # 이 변경이 만든 차이를 가른다
    python -X utf8 scripts/gate_homepage_drift.py --selftest   # 게이트가 무는지

exit 0=일치 · 1=홈페이지가 뒤처졌다 · 2=페이지를 읽거나 해석하지 못했다(결론 없음).

왜 있나: 홈페이지 CI 는 Vinc main 의 `vinc_mcp/tool_reference.json`·`openapi_v1.json` 으로
문서 페이지를 대조한다(`scripts/mcp/build_tool_reference.py --check`,
`scripts/api/build_page.py --check`). 그 CI 는 홈페이지 PR 에서만 돌기 때문에, Vinc 에서
도구나 경로가 바뀌어도 그 Vinc PR 은 초록이고 뒤처진 것은 다음 홈페이지 PR 이 빨개질 때
드러난다. 이 게이트는 같은 대조를 Vinc PR 의 머리에 대고 미리 돌려 그 PR 에 보인다.

무엇과 대조하나: 공개된 문서 페이지(`<site>/docs/mcp/tools/`, `<site>/docs/api/`).
홈페이지 리포는 비공개이고 이 리포에는 그것을 읽을 토큰이 없다. 공개 페이지는 홈페이지
main 이 배포한 결과라 같은 것을 본다(배포가 늦으면 그만큼 늦게 본다).

무엇을 대조하나: 홈페이지의 두 `--check` 가 Vinc 쪽 변화로 빨개지는 항목.
  · 도구: 이름과 순서, 표시(read-only·writes·destructive), 요약, 본문, 인자(이름·필수·형식·뜻)
  · REST: 연산(메서드·경로), 연산별 인자(이름·위치·필수·형식), 성공 상태
홈페이지가 직접 쓰는 문구(연산 제목·설명, REST 인자의 뜻)는 Vinc 가 바꾸지 않으므로 보지 않는다.
페이지는 홈페이지 렌더러의 마크업(`section.tref`, `section.api-op`)으로 읽는다. 마크업이
바뀌어 하나도 못 읽으면 "해석하지 못했다"(exit 2)로 말하고, 전부 빠졌다고 말하지 않는다.

막지 않는다. 홈페이지는 Vinc main 을 보고 대조하므로 이 PR 이 머지돼야 홈페이지가
따라올 수 있다. 이 PR 이 머지된 뒤 홈페이지 PR 이 따라와야 한다는 알림이다.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

try:
    from scripts._root import ROOT
    from scripts._gitutil import git
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT
    from _gitutil import git

TOOLS_JSON = "vinc_mcp/tool_reference.json"
OPENAPI_JSON = "vinc_mcp/openapi_v1.json"
SITE = "https://vincs.io"
TOOLS_PAGE = "/docs/mcp/tools/"
API_PAGE = "/docs/api/"
#: 도구 페이지에서 Vinc 계약이 들어가는 자리. 홈페이지 build_tool_reference.py 와 같은 표지다.
START, END = "<!-- tool-reference:start -->", "<!-- tool-reference:end -->"
#: 홈페이지 build_page.py 가 연산으로 세는 메서드.
METHODS = ("get", "post", "put", "patch", "delete")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_TICKS = re.compile(r"`([^`]+)`")
_TREF = re.compile(r'<section class="tref" id="([a-z0-9_]+)">(.*?)</section>', re.S)
_APIOP = re.compile(r'<section class="api-op"[^>]*>(.*?)</section>', re.S)
_PARA = re.compile(r'<p(?: class="([^"]*)")?[^>]*>(.*?)</p>', re.S)
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td>(.*?)</td>", re.S)


class Unreadable(Exception):
    """페이지를 받거나 해석하지 못했다. 결론이 아니다."""


def _text(fragment: str) -> str:
    """마크업 조각의 보이는 글자. 태그를 벗기고 엔티티를 풀고 공백을 하나로 접는다."""
    return _WS.sub(" ", html.unescape(_TAG.sub("", fragment))).strip()


def _plain(source: str) -> str:
    """JSON 쪽 문자열을 페이지 글자와 같은 모양으로. 홈페이지는 백틱 짝을 <code> 로 바꾼다."""
    return _WS.sub(" ", _TICKS.sub(r"\1", source or "")).strip()


# ── 기대값: Vinc 의 두 JSON ───────────────────────────────────────────────────

def tools_expected(reference: dict) -> list[dict]:
    out = []
    for tool in reference.get("tools") or []:
        ann = tool.get("annotations") or {}
        mode = ("read-only" if ann.get("readOnlyHint") else
                "destructive" if ann.get("destructiveHint") else "writes")
        params = [(p["name"], bool(p.get("required")), str(p.get("type") or ""),
                   _plain(p.get("reference") or p.get("description") or ""))
                  for p in tool.get("params") or []]
        out.append({"name": tool["name"], "mode": mode, "summary": _plain(tool.get("summary", "")),
                    "reference": _plain(tool.get("reference", "")), "params": params})
    return out


def ops_expected(document: dict) -> dict[tuple[str, str], dict]:
    out = {}
    for path, routes in (document.get("paths") or {}).items():
        for method in METHODS:
            if method not in routes:
                continue
            op = routes[method]
            params = [(p["name"], p["in"], bool(p.get("required")),
                       str((p.get("schema") or {}).get("type", "")))
                      for p in op.get("parameters") or []]
            body = (((op.get("requestBody") or {}).get("content") or {})
                    .get("application/json") or {}).get("schema") or {}
            required = set(body.get("required") or [])
            params += [(name, "body", name in required, str(spec.get("type", "")))
                       for name, spec in (body.get("properties") or {}).items()]
            statuses = ", ".join(c for c in (op.get("responses") or {}) if c != "default")
            out[(method.upper(), path)] = {"params": params, "statuses": statuses}
    return out


# ── 실제값: 공개 페이지 ───────────────────────────────────────────────────────

def tools_on_page(page: str) -> list[dict]:
    if page.count(START) != 1 or page.count(END) != 1:
        raise Unreadable("도구 페이지에 tool-reference 표지가 한 쌍 있지 않다")
    block = page.split(START, 1)[1].split(END, 1)[0]
    out = []
    for name, body in _TREF.findall(block):
        mode = re.search(r'<span class="tref-mode">(.*?)</span>', body, re.S)
        paras = _PARA.findall(body)
        summary = next((_text(t) for cls, t in paras if cls == "tref-sum"), "")
        reference = next((_text(t) for cls, t in paras if not cls), "")
        params = []
        for row in _ROW.findall(body):
            cells = [_text(c) for c in _CELL.findall(row)]
            if len(cells) != 3:
                continue
            name_cell, kind, meaning = cells
            required = name_cell.endswith(" (required)")
            params.append((name_cell.removesuffix(" (required)"), required, kind, meaning))
        out.append({"name": name, "mode": _text(mode.group(1)) if mode else "",
                    "summary": summary, "reference": reference, "params": params})
    if not out:
        raise Unreadable("도구 페이지에서 도구 절(section.tref)을 하나도 못 읽었다")
    return out


def ops_on_page(page: str) -> dict[tuple[str, str], dict]:
    out = {}
    for body in _APIOP.findall(page):
        line = re.search(r'<span class="api-meth">(\w+)</span>\s*<code>(.*?)</code>', body, re.S)
        if not line:
            continue
        params = []
        for row in _ROW.findall(body):
            cells = [_text(c) for c in _CELL.findall(row)]
            if len(cells) != 4:
                continue
            name_cell, where, kind, _meaning = cells
            required = name_cell.endswith(" *")
            params.append((name_cell.removesuffix(" *"), where, required, kind))
        ok = re.search(r'<p class="api-ok"[^>]*>(.*?)</p>', body, re.S)
        statuses = _text(ok.group(1)).removeprefix("Success:").strip() if ok else ""
        out[(line.group(1).upper(), html.unescape(line.group(2)))] = {
            "params": params, "statuses": statuses}
    if not out:
        raise Unreadable("API 페이지에서 연산(section.api-op)을 하나도 못 읽었다")
    return out


# ── 대조: (열쇠, 문장) 목록. 열쇠로 기준 커밋의 차이와 견준다 ─────────────────

def compare_tools(want: list[dict], have: list[dict]) -> list[tuple[tuple, str]]:
    found = []
    want_by = {t["name"]: t for t in want}
    have_by = {t["name"]: t for t in have}
    for name in want_by.keys() - have_by.keys():
        found.append((("tool-missing", name), f"도구 {name} 이 홈페이지 도구 페이지에 없다"))
    for name in have_by.keys() - want_by.keys():
        found.append((("tool-extra", name), f"홈페이지 도구 페이지의 {name} 은 서버에 없다"))
    common = [t["name"] for t in want if t["name"] in have_by]
    if common != [t["name"] for t in have if t["name"] in want_by]:
        found.append((("tool-order",), "도구 순서가 홈페이지 도구 페이지와 다르다"))
    for name in common:
        w, h = want_by[name], have_by[name]
        for field, label in (("mode", "표시"), ("summary", "요약"), ("reference", "본문")):
            if w[field] != h[field]:
                found.append((("tool-field", name, field), f"도구 {name} 의 {label}가 홈페이지와 다르다"))
        wp = {p[0]: p for p in w["params"]}
        hp = {p[0]: p for p in h["params"]}
        missing, extra = sorted(wp.keys() - hp.keys()), sorted(hp.keys() - wp.keys())
        changed = sorted(n for n in wp.keys() & hp.keys() if wp[n] != hp[n])
        if missing or extra or changed or ([p[0] for p in w["params"]] != [p[0] for p in h["params"]]
                                           and not (missing or extra)):
            parts = [f"없음 {missing}" if missing else "", f"남음 {extra}" if extra else "",
                     f"다름 {changed}" if changed else ""]
            detail = " · ".join(p for p in parts if p) or "순서가 다름"
            found.append((("tool-params", name), f"도구 {name} 의 인자가 홈페이지와 다르다: {detail}"))
    return found


def compare_ops(want: dict, have: dict) -> list[tuple[tuple, str]]:
    found = []
    for method, path in sorted(want.keys() - have.keys()):
        found.append((("op-missing", method, path), f"REST {method} {path} 가 홈페이지 API 페이지에 없다"))
    for method, path in sorted(have.keys() - want.keys()):
        found.append((("op-extra", method, path), f"홈페이지 API 페이지의 {method} {path} 는 서버에 없다"))
    for key in sorted(want.keys() & have.keys()):
        w, h = want[key], have[key]
        if sorted(w["params"]) != sorted(h["params"]):
            wn, hn = {p[0] for p in w["params"]}, {p[0] for p in h["params"]}
            parts = [f"없음 {sorted(wn - hn)}" if wn - hn else "", f"남음 {sorted(hn - wn)}" if hn - wn else ""]
            detail = " · ".join(p for p in parts if p) or "위치·필수·형식이 다름"
            found.append((("op-params", *key), f"REST {key[0]} {key[1]} 의 인자가 홈페이지와 다르다: {detail}"))
        if w["statuses"] != h["statuses"]:
            found.append((("op-status", *key), f"REST {key[0]} {key[1]} 의 성공 상태가 다르다: "
                                                f"서버 {w['statuses']} · 홈페이지 {h['statuses']}"))
    return found


def findings(tools_ref: dict, openapi: dict, tools_page: str, api_page: str) -> list[tuple[tuple, str]]:
    return (compare_tools(tools_expected(tools_ref), tools_on_page(tools_page))
            + compare_ops(ops_expected(openapi), ops_on_page(api_page)))


# ── 입출력 ────────────────────────────────────────────────────────────────────

def fetch(url: str, attempts: int = 2, timeout: float = 20) -> str:
    last = None
    for _ in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vinc-ci-homepage-drift"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
    raise Unreadable(f"{url} 를 받지 못했다: {last}")


def _base_json(base: str, rel: str) -> dict | None:
    """기준 커밋의 JSON. 커밋이나 파일이 없으면 None(이 변경과 기준을 가르지 못한다)."""
    try:
        return json.loads(git(["show", f"{base}:{rel}"], cwd=ROOT))
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


def _annotate(level: str, file: str, message: str) -> None:
    clean = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} file={file},title=homepage drift::{clean}")


def _summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def run(site: str) -> int:
    head_tools = json.loads((ROOT / TOOLS_JSON).read_text(encoding="utf-8"))
    head_api = json.loads((ROOT / OPENAPI_JSON).read_text(encoding="utf-8"))
    try:
        tools_page = fetch(site.rstrip("/") + TOOLS_PAGE)
        api_page = fetch(site.rstrip("/") + API_PAGE)
        head = findings(head_tools, head_api, tools_page, api_page)
    except Unreadable as exc:
        print(f"[gate_homepage_drift] 결론 없음 — {exc}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::warning title=homepage drift::홈페이지를 대조하지 못했다(결론 없음): {exc}")
        return 2

    base = os.environ.get("BASE", "").strip()
    base_keys: set | None = None
    if base:
        bt, ba = _base_json(base, TOOLS_JSON), _base_json(base, OPENAPI_JSON)
        if bt is not None and ba is not None:
            base_keys = {k for k, _ in findings(bt, ba, tools_page, api_page)}
        else:
            print(f"[gate_homepage_drift] 기준 {base[:12]} 의 JSON 을 못 읽어 이 변경의 몫을 가르지 않는다")

    if not head:
        print(f"[gate_homepage_drift] PASS — 홈페이지({site})가 도구 목록·REST 레퍼런스와 맞다")
        return 0

    ours = [(k, m) for k, m in head if base_keys is None or k not in base_keys]
    before = [(k, m) for k, m in head if base_keys is not None and k in base_keys]
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    lines = [f"### 홈페이지가 따라와야 할 것 ({len(head)}건, {site})", ""]
    if ours:
        title = "이 변경이 만든 차이" if base_keys is not None else "차이"
        print(f"── {title} {len(ours)}건")
        lines.append(f"**{title}** — 이 PR 이 머지된 뒤 홈페이지 PR 이 따라와야 한다.")
        for key, msg in ours:
            print(f"  {msg}")
            lines.append(f"- {msg}")
            if in_actions:
                _annotate("warning", TOOLS_JSON if key[0].startswith("tool") else OPENAPI_JSON, msg)
    if before:
        print(f"── 기준 커밋에서도 이미 있던 차이 {len(before)}건")
        lines += ["", "**기준 커밋에서도 이미 있던 차이** — 홈페이지가 main 을 아직 못 따라왔다."]
        for key, msg in before:
            print(f"  {msg}")
            lines.append(f"- {msg}")
            if in_actions:
                _annotate("notice", TOOLS_JSON if key[0].startswith("tool") else OPENAPI_JSON, msg)
    lines += ["", "홈페이지에서 `scripts/mcp/build_tool_reference.py`·`scripts/api/build_page.py` 로 "
                  "다시 렌더하고, 도구 수가 바뀌었으면 `scripts/gate_tool_count.mjs` 의 EXPECTED 와 "
                  "카피를 같이 고친다."]
    _summary(lines)
    print(f"[gate_homepage_drift] DRIFT — {len(head)}건(이 변경 {len(ours)} · 기준부터 {len(before)}). "
          "막지 않는 알림이다")
    return 1


# ── 셀프테스트: 게이트가 무는가 ───────────────────────────────────────────────

def _tool_section(t: dict) -> str:
    """셀프테스트용 도구 절. 홈페이지 build_tool_reference.py 가 내는 모양을 따른다."""
    def inline(s: str) -> str:
        return _TICKS.sub(r"<code>\1</code>", html.escape(s, quote=False))
    ann = t.get("annotations") or {}
    mode = "read-only" if ann.get("readOnlyHint") else "destructive" if ann.get("destructiveHint") else "writes"
    rows = "".join(f'<tr><td><code>{html.escape(p["name"])}</code>'
                   f'{" (required)" if p.get("required") else ""}</td>'
                   f'<td>{html.escape(str(p.get("type") or ""))}</td>'
                   f'<td>{inline(p.get("reference") or p.get("description") or "")}</td></tr>'
                   for p in t.get("params") or [])
    table = (f'<div class="rtable"><table><thead><tr><th>Parameter</th><th>Type</th><th>Meaning</th>'
             f'</tr></thead><tbody>{rows}</tbody></table></div>') if rows else ""
    return (f'<section class="tref" id="{t["name"]}">\n<h3><code>{t["name"]}</code> '
            f'<span class="tref-mode">{mode}</span></h3>\n<p class="tref-sum">{inline(t["summary"])}</p>\n'
            f'<p>{inline(t["reference"])}</p>\n{table}\n</section>')


def _tools_page(tools: list[dict]) -> str:
    return "<article>" + START + "\n" + "\n".join(_tool_section(t) for t in tools) + "\n" + END + "</article>"


def _api_page(document: dict) -> str:
    parts = []
    for (method, path), op in ops_expected(document).items():
        rows = "".join(f"<tr><td><code>{n}</code>{' *' if rq else ''}</td><td>{w}</td><td>{ty}</td><td>뜻</td></tr>"
                       for n, w, rq, ty in op["params"])
        parts.append(f'<section class="api-op" id="op"><h3>t</h3><p class="api-line"><span class="api-meth">'
                     f'{method}</span> <code>{html.escape(path)}</code></p><div class="rtable"><table><tbody>'
                     f'{rows}</tbody></table></div><p class="api-ok" data-i="s">Success: <code>'
                     f'{op["statuses"]}</code></p></section>')
    return "<article>" + "".join(parts) + "</article>"


def selftest() -> int:
    tools = {"tools": [
        {"name": "vinc_search", "summary": "Search `the` graph.", "reference": "Finds <things> & more.",
         "annotations": {"readOnlyHint": True},
         "params": [{"name": "query", "type": "string", "required": True, "reference": "What to find."}]},
        {"name": "vinc_ingest", "summary": "Write nodes.", "reference": "Merges by id.",
         "annotations": {}, "params": [{"name": "nodes", "type": "array", "required": True,
                                        "reference": "Nodes."}]},
    ]}
    api = {"paths": {
        "/v1/search": {"post": {"requestBody": {"content": {"application/json": {"schema": {
            "required": ["query"], "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}}}},
            "responses": {"200": {}, "default": {}}}},
        "/v1/nodes/{id}": {"get": {"parameters": [{"name": "id", "in": "path", "required": True,
                                                   "schema": {"type": "string"}}],
                                   "responses": {"200": {}}}},
    }}

    def page_for(t, a):
        return _tools_page(t["tools"]), _api_page(a)

    def variant(fn):
        t, a = json.loads(json.dumps(tools)), json.loads(json.dumps(api))
        fn(t, a)
        return t, a

    synced_tools, synced_api = page_for(tools, api)
    cases = [
        # (이름, 서버 쪽 변형, 기대하는 열쇠 종류 · 빈 집합이면 일치)
        ("일치", lambda t, a: None, set()),
        ("도구가 늘었다", lambda t, a: t["tools"].append(
            {"name": "vinc_new", "summary": "s", "reference": "r", "params": []}), {"tool-missing"}),
        ("도구가 빠졌다", lambda t, a: t["tools"].pop(), {"tool-extra"}),
        ("도구 순서가 바뀌었다", lambda t, a: t["tools"].reverse(), {"tool-order"}),
        ("도구 요약이 바뀌었다", lambda t, a: t["tools"][0].update(summary="Search."), {"tool-field"}),
        ("도구 인자가 늘었다", lambda t, a: t["tools"][1]["params"].append(
            {"name": "space", "type": "string", "reference": "Team."}), {"tool-params"}),
        ("도구 표시가 바뀌었다", lambda t, a: t["tools"][1].update(annotations={"destructiveHint": True}),
         {"tool-field"}),
        ("REST 경로가 늘었다", lambda t, a: a["paths"].update(
            {"/v1/roles": {"get": {"responses": {"200": {}}}}}), {"op-missing"}),
        ("REST 경로가 빠졌다", lambda t, a: a["paths"].pop("/v1/nodes/{id}"), {"op-extra"}),
        ("REST 인자가 늘었다", lambda t, a: a["paths"]["/v1/search"]["post"]["requestBody"]["content"]
         ["application/json"]["schema"]["properties"].update(space={"type": "string"}), {"op-params"}),
        ("REST 성공 상태가 바뀌었다", lambda t, a: a["paths"]["/v1/nodes/{id}"]["get"]["responses"].update(
            {"304": {}}), {"op-status"}),
    ]
    bad = 0
    for label, change, kinds in cases:
        t, a = variant(change)
        got = {k[0] for k, _ in findings(t, a, synced_tools, synced_api)}
        if got != kinds:
            print(f"  [selftest] {label}: 기대 {sorted(kinds)} · 실제 {sorted(got)}")
            bad += 1
    # 마크업을 하나도 못 읽으면 "전부 빠졌다"가 아니라 결론 없음이어야 한다.
    for label, tp, ap in (("도구 표지 없음", "<article></article>", synced_api),
                          ("도구 절 없음", START + END, synced_api),
                          ("API 연산 없음", synced_tools, "<article></article>")):
        try:
            findings(tools, api, tp, ap)
            print(f"  [selftest] {label}: 결론 없음이어야 하는데 결론을 냈다")
            bad += 1
        except Unreadable:
            pass
    print(f"[gate_homepage_drift] selftest {'FAIL ' + str(bad) + '건' if bad else 'PASS'} "
          f"(경우 {len(cases) + 3}건)")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="홈페이지가 도구 목록·REST 레퍼런스를 따라왔는가(알림)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--site", default=os.environ.get("VINC_SITE_URL", SITE))
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    return run(args.site)


if __name__ == "__main__":
    sys.exit(main())
