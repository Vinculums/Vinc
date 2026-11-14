# -*- coding: utf-8 -*-
"""사이드카 HTTP 계약(`api/contract/sidecar.json`)과 `api/routes.py` 라우트 표가 같은가.

계약은 앱(`app/`)이 부르는 경로와 그 밖의 호출자만 쓰는 경로(`x-internal: true`)를 한 파일에
적는다. 이 테스트가 서버 쪽 두 방향을 지킨다:

  - 서버의 라우트가 하나라도 계약에 없으면 실패한다(미기재 라우트 0).
  - 계약이 사이드카 것이라고 적은 연산이 서버에 없으면 실패한다(없는 길을 약속하지 않는다).

앱 쪽 두 방향(앱의 호출이 전부 계약에 있고 내부용이 아닌가, 내부용이 아니라고 적은 연산을
앱이 실제로 부르는가)은 `app/dev/sidecar-contract-check.ts` 가 본다.

라우트 표는 `api.routes` 의 데이터(`GET_ROUTES`·`POST_ROUTES`·접두 표·인증 전 라우트)이고,
목록은 그것을 import 해 읽는다. 계약의 연산마다 실제 디스패치(`routes.dispatch`)를 한 번 태워 같은
핸들러에 닿는지도 본다(표와 런타임이 서로를 검산한다).
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import routes  # noqa: E402

CONTRACT = ROOT / "api" / "contract" / "sidecar.json"
SERVER = ROOT / "api" / "server.py"
METHODS = ("get", "post")
_PARAM = re.compile(r"\{([^{}/]+)\}")


def _contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _operations(doc: dict):
    """(METHOD, 경로, 연산 객체, 경로 항목) 을 차례로."""
    for path, item in doc["paths"].items():
        for m in METHODS:
            if m in item:
                yield m.upper(), path, item[m], item


def _sidecar_ops(doc: dict):
    return [(m, p, op, it) for m, p, op, it in _operations(doc)
            if op["x-served-by"] == "sidecar"]


# ── 서버의 라우트 표 ──────────────────────────────────────────────────────────

def _server_routes():
    """`({(METHOD, 경로): 핸들러}, {(METHOD, 접두): 핸들러})`.

    인증 전 라우트(`/health`·`/connect/callback`)는 핸들러가 None 이거나 앱 메서드 이름이다."""
    exact: dict[tuple[str, str], str | None] = {("GET", p): h for p, h in routes.GET_ROUTES.items()}
    exact.update({("POST", p): h for p, h in routes.POST_ROUTES.items()})
    exact.update(routes.UNAUTHENTICATED_ROUTES)
    prefix: dict[tuple[str, str], str | None] = {
        ("GET", p): h for p, h in routes.GET_PREFIX_ROUTES.items()}
    prefix.update({("POST", p): h for p, h in routes.POST_PREFIX_ROUTES.items()})
    return exact, prefix


def _ingest_engines() -> set[str]:
    """`VincApp.submit_ingest` 가 직접 가르는 엔진 이름(`engine == "pack"`)."""
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    app_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "VincApp")
    fn = next(f for f in app_cls.body if isinstance(f, ast.FunctionDef) and f.name == "submit_ingest")
    out = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "engine" and isinstance(node.comparators[0], ast.Constant)):
            out.add(node.comparators[0].value)
    return out


def _template_prefix(path: str) -> str | None:
    """`/jobs/{job_id}` → `/jobs/`. 매개변수가 마지막 한 칸에만 있을 때만 접두 라우트다."""
    params = _PARAM.findall(path)
    if not params:
        return None
    head, _, last = path.rpartition("/")
    if len(params) != 1 or last != "{" + params[0] + "}":
        return "?"   # 접두 라우트로 표현할 수 없는 모양
    return head + "/"


def _covering_prefix(method: str, path: str, prefixes) -> str | None:
    for m, p in prefixes:
        if m == method and path.startswith(p) and "/" not in path[len(p):]:
            return p
    return None


# ── 계약 파일 자체의 꼴 ───────────────────────────────────────────────────────

def test_계약은_OpenAPI_3_1_이고_연산마다_두_표지가_있다():
    doc = _contract()
    assert doc["openapi"] == "3.1.0"
    seen = set()
    for m, p, op, item in _operations(doc):
        assert isinstance(op.get("x-internal"), bool), f"{m} {p}: x-internal 이 참·거짓이 아니다"
        assert op.get("x-served-by") in ("sidecar", "account"), f"{m} {p}: x-served-by"
        assert op.get("operationId") and op["operationId"] not in seen, f"{m} {p}: operationId"
        seen.add(op["operationId"])
        assert op.get("responses"), f"{m} {p}: responses 가 비었다"
        if op["x-served-by"] == "account":
            # 계정 경로는 앱이 부르기 때문에 여기 있다. 앱이 안 부르면 적을 이유가 없다.
            assert op["x-internal"] is False, f"{m} {p}: 계정 경로가 내부용으로 적혔다"
    for p, item in doc["paths"].items():
        assert p.startswith("/") and (p == "/" or not p.endswith("/")), p
        assert set(item) <= {"get", "post", "parameters"}, f"{p}: 모르는 키 {set(item)}"
        declared = {x["name"] for x in item.get("parameters", [])
                    if x.get("in") == "path" and x.get("required") is True}
        assert declared == set(_PARAM.findall(p)), f"{p}: 경로 매개변수 선언이 템플릿과 다르다"


# ── 서버와 계약의 두 방향 ─────────────────────────────────────────────────────

def test_서버_라우트가_전부_계약에_있다():
    exact, prefix = _server_routes()
    ops = {(m, p) for m, p, _, _ in _sidecar_ops(_contract())}
    templ = {(m, _template_prefix(p)) for m, p in ops if _template_prefix(p)}
    missing = sorted(f"{m} {p}" for m, p in exact if (m, p) not in ops)
    missing += sorted(f"{m} {p}{{...}}" for m, p in prefix if (m, p) not in templ)
    assert not missing, ("계약에 없는 사이드카 라우트(api/contract/sidecar.json 에 적고, 앱이 "
                         f"부르지 않으면 x-internal: true): {missing}")


def test_계약의_사이드카_연산이_전부_서버에_있다():
    exact, prefix = _server_routes()
    ghost = []
    for m, p, op, _ in _sidecar_ops(_contract()):
        tp = _template_prefix(p)
        if tp is not None:
            if (m, tp) not in prefix:
                ghost.append(f"{m} {p}")
        elif (m, p) not in exact and _covering_prefix(m, p, prefix) is None:
            ghost.append(f"{m} {p}")
    assert not ghost, f"계약이 약속하는데 서버에 없는 라우트: {ghost}"


def test_operationId_는_서버_핸들러_이름이다():
    """계약을 읽는 사람이 그 연산을 어느 핸들러가 받는지 바로 찾도록. 접두 아래의 구체 경로
    (`/ingest/fragment`)는 `<핸들러>_<끝 칸>` 이다."""
    exact, prefix = _server_routes()
    engines = _ingest_engines()
    wrong = []
    for m, p, op, _ in _sidecar_ops(_contract()):
        want = None
        tp = _template_prefix(p)
        if tp is not None:
            want = prefix.get((m, tp))
        elif (m, p) in exact:
            want = exact[(m, p)]
            if want is None:      # 핸들러 없이 그 자리에서 답하는 인라인 라우트(`/health`)
                want = p.strip("/").replace("/", "_").replace("-", "_")
        else:
            cover = _covering_prefix(m, p, prefix)
            last = p.rsplit("/", 1)[1]
            want = f"{prefix[(m, cover)]}_{last}"
            if cover == "/ingest/":
                assert last in engines, f"{m} {p}: submit_ingest 가 모르는 엔진 {last}"
        if op["operationId"] != want:
            wrong.append(f"{m} {p}: {op['operationId']} != {want}")
    assert not wrong, wrong


class _RecordingApp:
    """어떤 핸들러가 불렸는지만 돌려주는 가짜 앱. 스토어는 열린 것으로 둔다(POST 의 503 가드를
    지나 실제 표까지 가게)."""

    def __init__(self):
        self.store = SimpleNamespace()

    def __getattr__(self, name):
        def handler(*_args, **_kwargs):
            return 299, {"handler": name}
        return handler


def test_계약의_연산이_실제_디스패치에서_그_핸들러에_닿는다():
    exact, _ = _server_routes()
    app = _RecordingApp()
    seen = 0
    for m, p, op, _ in _sidecar_ops(_contract()):
        if (m, p) in exact and exact[(m, p)] is None:
            continue   # `/health` — 표가 아니라 HTTP 셸이 인증 전에 답한다(`api.server.health`)
        if (m, p) in {("POST", "/connect/callback")}:
            continue   # 인증 전 라우트. 표 쪽 테스트가 덮는다
        concrete = _PARAM.sub("x", p)
        code, body = routes.dispatch(app, m, concrete, {})
        assert code == 299, f"{m} {p}: 디스패치가 핸들러에 닿지 않았다 ({code} {body})"
        want = op["operationId"]
        if _template_prefix(p) is None and (m, p) not in exact:
            want = want.rsplit("_", 1)[0]   # `/ingest/fragment` → submit_ingest
        assert body["handler"] == want, f"{m} {p}: {body['handler']} != {want}"
        seen += 1
    assert seen > 80, f"디스패치를 탄 연산이 너무 적다: {seen}"
