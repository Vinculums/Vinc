#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""검색 품질 평가 — FTS · 벡터 · RRF 하이브리드를 같은 질의로 비교한다.

**왜 필요한가.** 이 리포에는 검색 평가셋이 없었다. 그래서 "벡터 검색이 FTS 보다 나은가",
"모델을 바꾸면 나아지는가" 를 **누구도 말할 수 없었다**. 실제로 임베딩은 오래
write-only 였는데(호출부가 query_embedding 을 안 넘겼다) 그 사실조차 품질 저하로는
드러나지 않았다. 측정 수단이 없으면 개선도 회귀도 보이지 않는다.

이 도구는 정답 라벨이 없는 상태에서 **부트스트랩 평가셋**을 만들고 세 조건을 잰다.

    조건            어떻게
    fts             q.search(query)                      — 현행 사용자 경험
    vector          store.vector_search(query_vec)        — 의미 팔 단독
    rrf             q.search(query, query_embedding=...)  — 실제 하이브리드 경로

⚠ **질의 생성 방식의 편향을 알고 써야 한다.** 기본(`--from title`)은 문서 제목을 질의로
쓰고 그 문서의 청크를 정답으로 본다. 제목 어휘는 본문에도 자주 나오므로 이 방식은
**어휘 매칭(FTS)에 유리하다** — 즉 벡터의 이점을 **과소평가**한다. 보수적 편향이라
"이 조건에서도 벡터가 이긴다"면 신뢰할 수 있고, 반대로 벡터가 지더라도 그것만으로
벡터가 쓸모없다고 결론 내릴 수는 없다. 사람이 쓴 질의를 `--queries` 로 넣으면 그 편향이
사라진다(권장 — 20~50개면 충분하다).

벡터 조건은 **임베더가 있어야** 돈다. 사이드카에는 모델이 없으므로 워커의 루프백 창구
(`VINC_EMBED_ENCODE`)를 쓴다. 없으면 벡터·RRF 조건을 **조용히 빼지 않고** 그렇다고 적는다.

사용:
    # 부트스트랩(제목 질의) — 편향 있음, 기준선 확보용
    python scripts/eval_search.py --data ~/.vinc/db --owner u

    # 사람이 쓴 질의로 — 권장. JSONL {"query": "...", "doc_id": "..."} 한 줄에 하나
    python scripts/eval_search.py --data ~/.vinc/db --owner u --queries eval.jsonl

    # 벡터 조건까지
    VINC_EMBED_ENCODE=http://127.0.0.1:8791 python scripts/eval_search.py --data ... --owner u
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q            # noqa: E402
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402


# ── 지표 ────────────────────────────────────────────────────────────────────
def _dcg(gains: list[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def score(ranked_doc_ids: list[str], truth: str, k: int) -> dict:
    """정답 문서가 상위 k 안 어디에 있었나. 정답이 하나뿐인 설정이라
    nDCG 의 이상적 DCG 는 1.0(1위) 이고, 그때 nDCG = 1/log2(rank+1) 이 된다."""
    top = ranked_doc_ids[:k]
    hit = truth in top
    rank = top.index(truth) + 1 if hit else 0
    return {
        "recall": 1.0 if hit else 0.0,
        "mrr": 1.0 / rank if rank else 0.0,
        "ndcg": _dcg([1.0 if d == truth else 0.0 for d in top]) if hit else 0.0,
    }


def _mean(rows: list[dict], key: str) -> float:
    return sum(r[key] for r in rows) / len(rows) if rows else 0.0


# ── 임베더(워커 인코딩 창구) ────────────────────────────────────────────────
def encode_query(text: str, url: str, timeout: float = 20.0) -> list[float] | None:
    """`/encode` 클라이언트는 리포에 정확히 셋이다 — 워커 `_RemoteEncoder` ·
    사이드카 `_query_embedding` · 여기. 창구가 루프백을 벗어나며 인증이 붙었을 때
    (vinc-encode 분리, 2026-08-07) 셋 중 둘만 고쳐져 질의 임베딩이 전부 401 이었다.
    **클라이언트를 새로 만들면 토큰부터 실을 것** — 없으면 헤더를 붙이지 않는다
    (루프백 창구는 무토큰이라 빈 헤더가 오히려 401 사유가 된다)."""
    body = json.dumps({"texts": [text], "kind": "query"}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    tok = os.environ.get("VINC_EMBED_ENCODE_TOKEN", "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url.rstrip("/") + "/encode", data=body,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        vecs = (json.load(resp) or {}).get("vectors") or []
    return list(vecs[0]) if vecs else None


# ── 평가셋 ──────────────────────────────────────────────────────────────────
def bootstrap_queries(store, owner: str | None, limit: int) -> list[dict]:
    """문서 제목을 질의로 쓰는 부트스트랩(편향은 모듈 docstring 참조)."""
    where = "WHERE d.owner_id = $o " if owner else ""
    rows = store.run(
        f"MATCH (d:_DocMeta) {where}RETURN d.doc_id AS doc_id, d.title AS title "
        f"ORDER BY d.doc_id LIMIT $lim",
        {"o": owner, "lim": int(limit)} if owner else {"lim": int(limit)}) or []
    return [{"query": (r.get("title") or "").strip(), "doc_id": r["doc_id"]}
            for r in rows if (r.get("title") or "").strip()]


def load_queries(path: str) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        o = json.loads(line)
        if o.get("query") and o.get("doc_id"):
            out.append({"query": str(o["query"]), "doc_id": str(o["doc_id"])})
    return out


def load_evalset(path: str) -> list[dict]:
    """정본 평가셋(`scripts/fixtures/evalset.json`) — `make_queries.py` 산출물.

    JSONL(`--queries`)과 달리 생성 규칙·코퍼스·집계까지 함께 들고 있어, 어떤 조건에서
    만든 질의인지가 파일 하나로 확인된다. 그게 이 파일이 존재하는 이유다(E1)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [{"query": str(q["query"]), "doc_id": str(q["doc_id"]),
             "lang": q.get("lang")}
            for q in (data.get("queries") or []) if q.get("query") and q.get("doc_id")]


def assert_gold_present(store, qs: list[dict], owner: str | None) -> None:
    """정답 문서가 **이 스토어에 실제로 있는지** 먼저 본다.

    🔴 없으면 즉시 세운다. 평가셋의 doc_id 는 `seed_bilingual.py` 가 심은 코퍼스를
    가리키므로, 다른 스토어에 대고 돌리면 전 질의가 miss 가 되어 Recall 0.000 이
    **정상 출력처럼** 나온다. 이 리포가 반복해 당한 실패 양식(기동 정상·수치 정상 모양·
    내용만 틀림)이 평가 도구에서 재현되는 것이라, 조용한 0 점 대신 오류로 끝낸다."""
    want = {q["doc_id"] for q in qs}
    where = "WHERE d.owner_id = $o " if owner else ""
    rows = store.run(f"MATCH (d:_DocMeta) {where}RETURN d.doc_id AS id",
                     {"o": owner} if owner else None) or []
    have = {r["id"] for r in rows}
    hit = want & have
    if not hit:
        raise SystemExit(
            f"평가셋의 정답 문서가 이 스토어에 하나도 없다(정답 {len(want)}개, "
            f"스토어 문서 {len(have)}개). scripts/seed_bilingual.py 로 코퍼스를 먼저 "
            f"심거나, --owner 를 확인할 것. 이대로 돌리면 전부 miss 라 Recall 0.000 이 "
            f"정상 결과처럼 보인다.")
    if len(hit) < len(want):
        print(f"⚠ 정답 문서 {len(want) - len(hit)}/{len(want)}개가 스토어에 없다 — "
              f"그 질의는 필연적으로 miss 다(부분 시딩으로 보인다).", file=sys.stderr)


def lang_of(text: str) -> str:
    """질의의 문자 계열. 언어별로 갈라 봐야 토큰 청킹·다국어 모델의 효과가 보인다
    (영어는 512 초과 손실이 전체 토큰의 5.7% 인데 한국어는 82% 다 — 섞어 재면 묻힌다)."""
    ko = sum(1 for c in text if "가" <= c <= "힣")
    cjk = sum(1 for c in text if "぀" <= c <= "ヿ" or "一" <= c <= "鿿")
    n = max(1, len(text))
    if ko / n > 0.15:
        return "ko"
    if cjk / n > 0.15:
        return "cjk"
    return "en"


# ── 실행 ────────────────────────────────────────────────────────────────────
def _docs_of(store, rows: list[dict]) -> list[str]:
    """검색 결과(청크) → 문서 id 순서(중복 제거, 순위 보존)."""
    out, seen = [], set()
    for r in rows:
        cid = r.get("id")
        if not cid:
            continue
        got = store.run("MATCH (c:Chunk) WHERE c.chunk_id = $i RETURN c.doc_id AS d",
                        {"i": cid}) or []
        d = got[0].get("d") if got else None
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


# ── 실행 매니페스트 ─────────────────────────────────────────────────────────
#: 관심 있는 명령어 집합만 남긴다. 정본은 `.github/workflows/rerank-probe.yml` 의 같은
#: 목록이고 여기 사본이 있는 이유는 그 워크플로가 CI 안에서만 돌기 때문이다 — 로컬에서
#: 돌린 측정에는 CPU 신원이 아예 안 붙어 있었다.
_CPU_FLAGS = ("avx", "avx2", "avx512f", "avx512_vnni", "avx_vnni", "amx_int8", "f16c", "fma")


def _pkg_version(name: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return None


def _cpu_identity() -> dict:
    """CPU 모델과 int8 커널이 타는 명령어 집합. 리눅스 밖에서는 flags 가 None 이다."""
    import platform
    cpu: dict = {"model": None, "cores": os.cpu_count(), "flags": None}
    try:
        txt = Path("/proc/cpuinfo").read_text(encoding="utf-8")
        m = re.search(r"^model name\s*:\s*(.+)$", txt, re.M)
        cpu["model"] = m.group(1).strip() if m else None
        f = re.search(r"^flags\s*:\s*(.+)$", txt, re.M)
        have = set((f.group(1) if f else "").split())
        cpu["flags"] = [x for x in _CPU_FLAGS if x in have]
    except OSError:
        # 윈도우·맥에는 /proc 이 없다. 모델명만이라도 남긴다 — flags 가 None 이면
        # "안 쟀다"는 뜻이고 빈 리스트(= 하나도 없다)와 구분된다.
        cpu["model"] = platform.processor() or None
    return cpu


def run_manifest(store, owner: str | None, data: str | None) -> dict:
    """이 측정이 **무엇 위에서** 났는지. 두 결과를 비교해도 되는지 이것으로 가른다.

    없이는 두 JSON 이 사람 기억으로만 비교된다 — 실제로 그 상태였다. 코퍼스가 같은지,
    split 스토어인지, 청커가 같은지, 어떤 CPU 였는지가 어느 출력에도 없었다.

    **비교 규칙**: 순위 지표(nDCG·Recall·MRR)는 ``corpus_sha256`` · ``split_inferred`` ·
    ``docstore_sha256`` · ``ladybug`` 가 같을 때만 비교한다. 시간 지표는 거기에
    ``cpu.model`` · ``threads`` · ``onnxruntime`` 이 더 같아야 한다(같은 아티팩트가
    러너 CPU 에 따라 147ms 와 271ms 로 갈린 실측이 있다).

    ``corpus_sha256`` 은 doc_id 와 content_hash 만 읽는다 — 본문을 다시 읽지 않고도
    내용 변경이 잡힌다(`_DocMeta.content_hash` 가 정본 텍스트의 sha256 이다).
    """
    import hashlib
    man: dict = {"data": data, "owner": owner}

    cy = ("MATCH (d:_DocMeta) WHERE $o IS NULL OR d.owner_id = $o "
          "RETURN d.doc_id AS doc_id, d.content_hash AS h ORDER BY d.doc_id")
    try:
        rows = store.run(cy, {"o": owner}) or []
        h = hashlib.sha256()
        for r in rows:
            h.update(f"{r.get('doc_id')}\t{r.get('h')}\n".encode())
        man["corpus_sha256"] = h.hexdigest()
        man["docs"] = len(rows)
    except Exception as exc:
        man["corpus_sha256"] = None
        man["corpus_error"] = f"{type(exc).__name__}: {exc}"
        man["docs"] = -1

    for key, cypher in (("chunks", "MATCH (c:Chunk) RETURN count(c) AS c"),
                        ("vecs", "MATCH (v:ChunkVec) RETURN count(v) AS c")):
        try:
            r = store.run(cypher)
            man[key] = int(r[0]["c"]) if r else 0
        except Exception:
            man[key] = -1
    # 청크당 벡터 수가 split 여부를 말한다: 비-split 은 정확히 1.0, split 은 ~2.08.
    # `reindex --split` 을 안 쓴 스토어로 낸 품질 수치는 프로덕션 모양이 아니다.
    man["vecs_per_chunk"] = (round(man["vecs"] / man["chunks"], 3)
                             if man.get("chunks") else None)
    man["split_inferred"] = (man["vecs"] > man["chunks"]
                             if man.get("chunks", 0) > 0 and man.get("vecs", -1) >= 0
                             else None)

    root = Path(__file__).resolve().parents[1]
    try:
        import subprocess
        man["code_rev"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root,
            capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:
        man["code_rev"] = None
    # 청커에 버전 상수가 없다 — 파일 바이트가 곧 버전이다(`api/docstore.py::chunk_doc`).
    try:
        import hashlib as _h
        man["docstore_sha256"] = _h.sha256(
            (root / "api" / "docstore.py").read_bytes()).hexdigest()[:16]
    except OSError:
        man["docstore_sha256"] = None

    man["ladybug"] = _pkg_version("ladybug")
    man["onnxruntime"] = _pkg_version("onnxruntime")
    man["python"] = sys.version.split()[0]
    man["threads"] = int(os.environ.get("VINC_EVAL_THREADS", "1") or "1")
    man["cpu"] = _cpu_identity()
    return man


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="스토어 데이터 경로(미지정 시 기본 해석)")
    ap.add_argument("--owner")
    ap.add_argument("--queries", help="사람이 쓴 질의 JSONL")
    ap.add_argument("--evalset", nargs="?", const=str(
        Path(__file__).resolve().parent / "fixtures" / "evalset.json"),
        help="정본 평가셋 JSON(권장). 값 없이 주면 scripts/fixtures/evalset.json")
    ap.add_argument("--limit", type=int, default=50, help="부트스트랩 질의 수")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--json", action="store_true", help="결과를 JSON 으로")
    args = ap.parse_args()

    cfg = load_config({"data_dir": args.data} if args.data else None)
    store = open_store(cfg)
    if store is None:
        print("스토어에 도달할 수 없다", file=sys.stderr)
        return 1

    if args.evalset:
        qs, src = load_evalset(args.evalset), args.evalset
    elif args.queries:
        qs, src = load_queries(args.queries), args.queries
    else:
        qs = bootstrap_queries(store, args.owner, args.limit)
        src = f"bootstrap(title, n={len(qs)})"
    if not qs:
        print("질의가 없다 — 코퍼스가 비었거나 제목이 없다", file=sys.stderr)
        return 1
    # 라벨이 있는 평가셋은 정답 실존을 먼저 확인한다(조용한 0 점 금지).
    if args.evalset or args.queries:
        assert_gold_present(store, qs, args.owner)

    encode_url = os.environ.get("VINC_EMBED_ENCODE", "").strip()
    vec_note = ""
    if not encode_url:
        vec_note = ("임베더 없음(VINC_EMBED_ENCODE 미설정) — 벡터·RRF 조건 미측정. "
                    "워커의 인코딩 창구를 띄우고 그 주소를 주면 잰다.")

    per: dict[str, list[dict]] = {"fts": [], "vector": [], "rrf": []}
    by_lang: dict[str, dict[str, list[dict]]] = {}
    failures = 0
    arm_notes: dict[str, int] = {}       # "arms|vector_note" -> 횟수
    vector_dropped = 0                   # 분모에서 사라질 뻔한 질의 수
    tm: dict[str, list[float]] = {"enc": [], "search_fts": [], "ann": [], "rrf_total": []}

    # 워밍업 1회. 확장 LOAD 와 HNSW 첫 접촉이 첫 질의에 통째로 실린다(실측 621ms 대
    # 8.1ms). 그것을 표본에 넣으면 첫 질의 하나가 p95 를 혼자 정한다.
    if qs:
        _w = qs[0]["query"]
        if encode_url:
            try:
                store.vector_search(encode_query(_w, encode_url), 200,
                                    {"owner_id": args.owner})
            except Exception:
                pass
        q.search(_w, top_k=args.top_k, owner=args.owner, cfg=cfg, store=store)

    for item in qs:
        text, truth = item["query"], item["doc_id"]
        # 평가셋이 라벨을 실었으면 그것을 쓴다 — 교차언어 셋(ko2en: 한국어 질의·영어
        # 정답)을 문자 계열로 재판정하면 "ko" 로 뭉개져 동일언어 ko 와 섞인다.
        lang = item.get("lang") or lang_of(text)
        by_lang.setdefault(lang, {"fts": [], "vector": [], "rrf": []})

        qvec = None
        if encode_url:
            _t = time.perf_counter()
            try:
                qvec = encode_query(text, encode_url)
                tm["enc"].append((time.perf_counter() - _t) * 1000)
            except Exception as exc:
                failures += 1
                if not vec_note:
                    vec_note = f"임베딩 실패 {failures}건 — 마지막: {type(exc).__name__}"

        _t = time.perf_counter()
        res = q.search(text, top_k=args.top_k, owner=args.owner, cfg=cfg, store=store)
        # ⚠ 이 시계는 FTS 팔만이 아니다. `q.search` 는 노드 폴백까지 도는 봉투이고,
        # 실측 분해가 `_node_fallback 7.1ms · _chunk_arms(FTS) 6.6ms · 전체 20.8ms` 다.
        # 그래서 `rrf_total - (enc + search_fts + ann)` 을 "융합 비용"이라 부르면 음수가
        # 나온다. 그 뺄셈을 하지 말 것 — 이 넷은 겹치는 구간이지 분해가 아니다.
        tm["search_fts"].append((time.perf_counter() - _t) * 1000)
        # 🔴 백엔드가 실패를 말했으면 **그것을 측정값으로 바꾸지 않는다.** `search` 는
        # index_missing·extension_unavailable 같은 사유를 `error` 로 정직하게 싣는데,
        # 그걸 무시하면 전 질의가 0 행이 되어 "Recall 0.000" 이라는 **정상 모양의 표**가
        # 나온다. 검색이 나쁜 것과 검색이 죽은 것은 다른 사실이고, 이 리포가 반복해서
        # 당한 실패가 정확히 그 둘을 못 가린 것이다(무음 강등 금지).
        if res.get("error"):
            raise SystemExit(
                f"검색 백엔드가 실패를 보고했다 — 측정이 성립하지 않는다.\n"
                f"  질의: {text!r}\n  사유: {res['error']}\n"
                f"이 상태로 나온 0.000 은 품질이 아니라 고장이다. 원인을 먼저 없앨 것.")
        s = score(_docs_of(store, res.get("rows") or []), truth, args.top_k)
        per["fts"].append(s)
        by_lang[lang]["fts"].append(s)

        if qvec is not None:
            _t = time.perf_counter()
            try:
                # 🔴 프로덕션 파라미터로 부른다. `api/query.py::_chunk_arms` 는 pool cap
                # 200 과 owner 필터로 부르는데, 여기서 `(top_k, None)` 으로 부르면 다른
                # 일을 재게 된다 — 481청크에서 6.2ms 대 13.4ms, 2.2배다. 그 값을 ANN
                # 몫으로 인용하면 프로덕션의 절반으로 그린다.
                vrows = store.vector_search(qvec, 200, {"owner_id": args.owner})
                tm["ann"].append((time.perf_counter() - _t) * 1000)
                s = score(_docs_of(store, vrows[:args.top_k]), truth, args.top_k)
                per["vector"].append(s)
                by_lang[lang]["vector"].append(s)
            except Exception:
                # 🔴 `pass` 로 넘기면 실패한 질의가 **분모에서 사라진다**(생존편향).
                # 남은 질의만으로 계산된 평균이 정상 모양의 표로 나온다.
                vector_dropped += 1
            _t = time.perf_counter()
            res2 = q.search(text, query_embedding=qvec, top_k=args.top_k,
                            owner=args.owner, cfg=cfg, store=store)
            tm["rrf_total"].append((time.perf_counter() - _t) * 1000)
            # 🔴 RRF 조건도 검사한다. 위의 FTS 호출에만 `error` 검사가 있어서, 벡터 팔이
            # 죽은 창에서 나온 FTS 단독 결과를 "rrf" 라고 이름 붙여 기록하고 있었다.
            if res2.get("error"):
                raise SystemExit(f"RRF 조건이 실패를 보고했다 — 측정 불성립: {res2['error']}")
            _a = res2.get("arms") if isinstance(res2.get("arms"), str) else "?"
            _k = f"{_a}|{res2.get('vector_note') or ''}"
            arm_notes[_k] = arm_notes.get(_k, 0) + 1
            s = score(_docs_of(store, res2.get("rows") or []), truth, args.top_k)
            per["rrf"].append(s)
            by_lang[lang]["rrf"].append(s)

    out = {
        "kind": "eval.search",
        "queries": len(qs),
        "source": src,
        "top_k": args.top_k,
        "note": vec_note,
        "overall": {c: {"n": len(v), "recall": round(_mean(v, "recall"), 4),
                        "mrr": round(_mean(v, "mrr"), 4), "ndcg": round(_mean(v, "ndcg"), 4)}
                    for c, v in per.items() if v},
        "by_lang": {lg: {c: {"n": len(v), "recall": round(_mean(v, "recall"), 4),
                             "mrr": round(_mean(v, "mrr"), 4),
                             "ndcg": round(_mean(v, "ndcg"), 4)}
                         for c, v in conds.items() if v}
                    for lg, conds in by_lang.items()},
    }

    def _pct(v, frac):
        if not v:
            return None
        srt = sorted(v)
        return round(srt[min(int(len(srt) * frac), len(srt) - 1)], 2)

    out["timing_ms"] = {k: {"n": len(v), "p50": _pct(v, 0.5), "p95": _pct(v, 0.95)}
                        for k, v in tm.items() if v}
    # 벡터 팔이 **실제로** 붙은 질의의 비율. 이것이 없으면 강등된 창에서 나온 rrf 행을
    # 정상 rrf 로 읽는다 — 라이브에서 오늘 세 번 연속 그 상태였다.
    out["arms_census"] = arm_notes
    out["vector_dropped"] = vector_dropped
    # 무엇 위에서 쟀는지 — 이게 없으면 다음 실행과 비교가 불가능하다.
    out["manifest"] = run_manifest(store, args.owner, args.data)
    store.close()

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"질의 {out['queries']}개 · 출처 {src} · top_k={args.top_k}")
    if vec_note:
        print(f"⚠ {vec_note}")
    print(f"\n{'조건':<10}{'n':>5}{'Recall@k':>11}{'MRR':>9}{'nDCG':>9}")
    for c, m in out["overall"].items():
        print(f"{c:<10}{m['n']:>5}{m['recall']:>11.3f}{m['mrr']:>9.3f}{m['ndcg']:>9.3f}")
    if len(out["by_lang"]) > 1:
        print("\n언어별 (섞어 재면 CJK 손실이 묻힌다)")
        for lg, conds in sorted(out["by_lang"].items()):
            for c, m in conds.items():
                print(f"  {lg:<4}{c:<10}{m['n']:>5}{m['recall']:>11.3f}"
                      f"{m['mrr']:>9.3f}{m['ndcg']:>9.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
