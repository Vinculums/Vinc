# -*- coding: utf-8 -*-
"""E4 — MCP 도구 사용 평가셋의 **정답 검증기**.

이 스크립트는 모델을 채점하지 않는다(그건 호스트가 한다). 하는 일은 하나다:
`rag_eval/fixtures/tooluse_evalset.json` 에 적힌 정답이 **지금도 픽스처에서 그대로
나오는지** 실호출로 확인한다.

왜 이게 필요한가. E1 이 고친 함정이 정확히 이것이다 — 평가셋이 조용히 낡으면
"측정하지 않는 측정"이 된다. 픽스처를 바꾸거나 엔진 동작이 달라지면 정답이 틀리게
되는데, 모델 채점만 돌리면 그때는 **모델이 틀린 것으로 보인다**. 여기서 먼저
깨져야 한다.

    python -m rag_eval.tooluse          # 정답 검증
    python -m rag_eval.tooluse --print  # 호스트에 줄 문항만 출력

정답이 하나라도 어긋나면 종료 코드 1 이다(CI 게이트로 걸 수 있다).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EVALSET = ROOT / "rag_eval" / "fixtures" / "tooluse_evalset.json"


def _join(node, keys: list[str]) -> str:
    """한 dict 에서 키 여럿을 뽑아 `|` 로 잇는다. 리스트 값은 펼친다."""
    flat: list[str] = []
    for k in keys:
        v = node.get(k)
        flat.extend(str(x) for x in (v if isinstance(v, list) else [v]) if x is not None)
    return "|".join(flat)


def _dig(obj, path: str):
    """경량 경로. 문법은 넷뿐이다 — `.key` · `[N]` · `[]`(펼치기) · `k1|k2`(키 여럿).

    JSONPath 를 안 쓰는 이유는 의존성 하나 늘릴 값이 여기 없어서다. 마지막 구간에만
    `|` 를 허용한다: `candidates[].a_id|b_id` → 원소마다 "a|b" 문자열의 리스트.
    """
    import re
    # `paths[0].ids` 의 `[0]` 은 키에 **붙어 있다** — 점으로만 쪼개면 "paths[0]" 이
    # 통째로 키 이름이 된다. 그래서 토큰으로 뽑는다.
    parts = [t for t in re.findall(r"\[\d*\]|[^.\[\]]+", path) if t]
    cur = obj
    for part in parts:
        if part == "[]":
            cur = list(cur or [])
        elif part.startswith("["):
            cur = (cur or [])[int(part[1:-1])]
        elif isinstance(cur, list):
            keys = part.split("|")
            cur = [(_join(c, keys) if len(keys) > 1 else c.get(part)) for c in cur]
        else:
            keys = part.split("|")
            cur = _join(cur or {}, keys) if len(keys) > 1 else (cur or {}).get(part)
    return cur


def _call(q, store, owner, tool, args):
    """평가셋의 `verify.tool` 을 실제 함수 호출로 옮긴다.

    시그니처가 도구마다 달라서(위치 인자 vs 키워드, query 층 vs docstore 층) 여기서
    한 번에 흡수한다. 이 어댑터가 틀리면 정답 검증이 통째로 거짓이 되므로, 새 도구를
    평가셋에 넣을 때는 여기부터 본다."""
    kw = dict(args)
    if tool == "doc_get":                       # docstore 층 — store 가 위치 인자다
        from api import docstore
        return docstore.get_doc(store, kw["doc_id"], owner=owner)
    if tool == "search":                        # owner 는 filters 로 들어간다
        return q.search(kw.pop("query"), top_k=kw.pop("top_k", 10),
                        filters={"owner_id": owner}, store=store, **kw)
    fn = getattr(q, tool)
    if tool == "shortest_path":
        return fn(kw.pop("a"), kw.pop("b"), owner=owner, store=store, **kw)
    for pos_key in ("topic", "node", "query"):  # 위치 인자를 받는 것들
        if pos_key in kw and tool in ("recall", "decide", "neighbors"):
            return fn(kw.pop(pos_key), owner=owner, store=store, **kw)
    return fn(owner=owner, store=store, **kw)


@contextlib.contextmanager
def _fixture_env():
    """픽스처 전용 스토어를 가리키는 env 를 **빌려 쓰고 되돌린다**.

    되돌리지 않으면 같은 프로세스의 다음 테스트가 이 임시 스토어를 진짜 스토어로
    본다. 실제로 그랬다 — 전역을 그냥 덮었더니 무관한 test_server 2건이 깨졌고,
    단독 실행에서는 멀쩡해서 원인이 증상에서 안 보였다.
    """
    keep = {k: os.environ.get(k) for k in
            ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR", "MEMORY_EMBEDDING_MODEL")}
    os.environ["MEMORY_STORE_BACKEND"] = keep["MEMORY_STORE_BACKEND"] or "embedded"
    os.environ["MEMORY_DATA_DIR"] = os.path.join(tempfile.mkdtemp(), "store")
    os.environ["MEMORY_EMBEDDING_MODEL"] = keep["MEMORY_EMBEDDING_MODEL"] or "local-en"
    try:
        yield
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


#: 이 표지를 담은 문제는 **데이터나 도구의 어긋남이 아니라 기능 부재**다. ladybug
#: 0.20.x 의 algo 확장이 의존 라이브러리를 싣지 않아 구조 진단(vinc_diagnose)이 강등된다
#: (store.embedded_backend._ALGO_UNAVAILABLE_NOTE). 채점에서는 그 문항이 답할 수 없는
#: 문항이라는 뜻이고, 테스트는 이것만 남았을 때 실패 대신 건너뛴다.
ALGO_UNAVAILABLE = "algo_unavailable"


def _algo_unavailable(item_id: str, out) -> str | None:
    if (isinstance(out, dict) and out.get("executed") is False
            and "algo 확장 미가용" in str(out.get("note") or "")):
        return f"{item_id}: {ALGO_UNAVAILABLE} — {str(out.get('note'))[:160]}"
    return None


def verify(evalset: dict) -> list[str]:
    """정답을 픽스처에서 재도출해 대조한다. 어긋난 문항의 설명을 돌려준다."""
    with _fixture_env():
        return _verify_inner(evalset)


def _verify_inner(evalset: dict) -> list[str]:
    from store.config import load_config
    from store.backend import open_store
    from api import query as q
    sys.path.insert(0, str(ROOT / "scripts"))
    import seed_tooluse as S

    cfg = load_config()
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    S.seed(store)
    S.seed_doc(store)

    problems: list[str] = []
    try:
        for item in evalset["questions"]:
            v = item.get("verify")
            if not v:
                problems.append(f"{item['id']}: verify 절이 없다 — 정답이 검증 불가")
                continue
            res = _call(q, store, S.OWNER, v["tool"], v.get("args") or {})
            gone = _algo_unavailable(item["id"], res)
            if gone:
                problems.append(gone)
                continue
            got = _dig(res, v["path"])
            exp = item["expect"]
            kind, want = exp["kind"], exp["value"]
            if kind == "set":
                ok = set(got or []) == set(want)
            elif kind == "list":
                ok = list(got or []) == list(want)
            elif kind == "contains":
                ok = want in str(got or "")
            else:                                   # exact
                ok = str(got) == str(want)
            if not ok:
                problems.append(f"{item['id']}: 기대 {want!r} · 실제 {got!r}")
    finally:
        store.close()
    return problems


#: 평가셋의 `requires` 도구 이름 → 그 문항의 정답을 뽑을 MCP 인자.
#: verify 절은 `api.query` 를 직접 부르지만 이쪽은 **MCP 도구 층**을 통과한다
#: (스키마 검증 → 핸들러 → 브리지 → HTTP → 사이드카 라우트). 둘은 다른 주장이다:
#: 전자는 "데이터에 답이 있다", 후자는 "도구가 그 답을 실제로 내준다".
MCP_PROBE = {
    "Q1":  ("vinc_neighbors",        {"node": "concept:view"},                    "neighbors[].id"),
    "Q2":  ("vinc_path",             {"a": "concept:view", "b": "concept:invoice"}, "paths[0].ids"),
    "Q3":  ("vinc_diagnose",         {"kind": "components"},                       "orphans"),
    "Q4":  ("vinc_decide",           {"topic": "concept:view", "depth": 1},       "cross_domain[].source|target"),
    "Q5":  ("vinc_domains",          {},                                           "rows[0].domain|total"),
    "Q6":  ("vinc_recall",           {},                                           "rows[0].title|date"),
    "Q7":  ("vinc_merge_candidates", {},                                           "candidates[].a_id|b_id"),
    "Q8":  ("vinc_suggest_ties",     {"node": "concept:invoice"},                  "suggestions[0].id|shared_neighbors"),
    "Q9":  ("vinc_doc_get",          {"doc_id": "eval.tooluse.view-notes"},       "text"),
    "Q10": ("vinc_search",           {"query": "View 뷰"},                        "node_hits[].id"),
}


def via_mcp(evalset: dict) -> list[str]:
    """같은 정답을 **MCP 도구 표면**으로 다시 뽑아 대조한다.

    사이드카를 인프로세스로 띄우고 `tools.call()` 로 부른다 — 도구 스키마·핸들러·
    브리지·HTTP 라우트를 전부 지난다. `verify()` 가 통과해도 여기서 깨질 수 있고,
    그건 "데이터엔 답이 있는데 도구가 못 준다"는 뜻이라 사용자에게는 같은 고장이다.
    """
    import contextlib as _c
    import threading

    with _fixture_env():
        from store.config import load_config
        from store.backend import open_store
        from api.server import VincApp, make_server
        from vinc_mcp import tools
        from vinc_mcp.bridge import Bridge
        sys.path.insert(0, str(ROOT / "scripts"))
        import seed_tooluse as S

        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        S.seed(store)
        S.seed_doc(store)

        app = VincApp(cfg, store=store)
        httpd = make_server(app, host="127.0.0.1", port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        bridge = Bridge(base=f"http://127.0.0.1:{httpd.server_address[1]}",
                        token=getattr(app.auth, "token", None), owner=S.OWNER)

        problems: list[str] = []
        try:
            for item in evalset["questions"]:
                probe = MCP_PROBE.get(item["id"])
                if probe is None:
                    problems.append(f"{item['id']}: MCP_PROBE 미등재 — 도구 층 미검증")
                    continue
                name, args, path = probe
                if name not in item["requires"]:
                    problems.append(
                        f"{item['id']}: probe 도구 {name} 가 requires{item['requires']} 에 없다")
                    continue
                try:
                    out = tools.call(name, {**args, "owner": S.OWNER}, bridge)
                except Exception as e:                      # noqa: BLE001
                    problems.append(f"{item['id']}: {name} 호출 실패 — {type(e).__name__}: {e}")
                    continue
                gone = _algo_unavailable(item["id"], out)
                if gone:
                    problems.append(gone)
                    continue
                got = _dig(out, path)
                exp, kind, want = item["expect"], item["expect"]["kind"], item["expect"]["value"]
                if kind == "set":
                    ok = set(got or []) == set(want)
                elif kind == "list":
                    ok = list(got or []) == list(want)
                elif kind == "contains":
                    ok = want in str(got or "")
                else:
                    ok = str(got) == str(want)
                if not ok:
                    problems.append(f"{item['id']}: {name} → 기대 {want!r} · 실제 {got!r}")
        finally:
            with _c.suppress(Exception):
                httpd.shutdown()
            app.close()
        return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="E4 도구 사용 평가셋 정답 검증")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="채점하지 말고 호스트에 줄 문항만 출력")
    ap.add_argument("--via-mcp", dest="via", action="store_true",
                    help="api.query 대신 MCP 도구 층으로 정답을 뽑아 대조")
    args = ap.parse_args()
    evalset = json.loads(EVALSET.read_text(encoding="utf-8"))

    if args.show:
        print(evalset["note"])
        print(f"\n픽스처: {evalset['fixture']['seed']}  (owner={evalset['fixture']['owner']})\n")
        for it in evalset["questions"]:
            req = ", ".join(it["requires"]) or "-"
            fb = ", ".join(it["forbidden"]) or "-"
            print(f"{it['id']}. {it['ask']}\n    요구: {req}  금지: {fb}")
        return 0

    label = "MCP 도구 층" if args.via else "정답"
    problems = (via_mcp if args.via else verify)(evalset)
    n = len(evalset["questions"])
    if problems:
        print(f"{label} 검증 실패 {len(problems)}/{n}:")
        for p in problems:
            print("  " + p)
        return 1
    print(f"{label} 검증 통과 {n}/{n} — "
          + ("도구가 실제로 답을 내준다" if args.via else "평가셋이 픽스처와 맞다"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
