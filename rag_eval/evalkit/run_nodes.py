# -*- coding: utf-8 -*-
"""노드 해소(G0)를 전송 셋으로 재고, 문항별로 대조한다 — RAG eval v2 의 F1(VIN-175).

    python -X utf8 -m rag_eval.evalkit.run_nodes --store m3-ort130-2026-09-28 \\
        --onnx <bge-m3-int8-rr.onnx> --out <dir>                  # inproc·cli·v1 셋 다
    python -X utf8 -m rag_eval.evalkit.run_nodes --live https://mcp.vincs.io --space <id>   # 호스티드

(`scripts` 를 작업 디렉터리나 PYTHONPATH 로 둔다.)

**거부하는 것 셋**(조용한 오답을 막는다):
- 락의 스토어 해시와 핀 파일이 다르다, 또는 옆에 `.wal` 이 있다 — 열린 적 있는 바이트다.
- 질의 인코더 런타임이 락이 적은 저장 벡터 런타임과 다르다(`evalkit.runtime`).
- 의미 팔이 안 돈 응답(`vector_note` 가 있거나 `arms` 에 vector 가 없다) — 강등된 답을 잰다.
호스티드(`--live`)는 핀이 안 되므로 판정용이 아니다: 실행 전후 지문이 다르면 무효로 적는다.

종료코드: 0 경로들이 문항별로 같다 · 1 거부 · 3 경로 사이에 문항 차이가 있다.
근거: [[vinc.plan.rag-eval-v2-0-9-6]] · [[record:three-transports-answer-g0-identically-on-the-settled-pin-2026-09-28]]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import time
import urllib.request

from rag_eval.evalkit import funnel, grading, runtime
from rag_eval.evalkit.transports import V1, Cli, InProcess, TransportError

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent if ROOT.parent.name != "_wt" else ROOT.parent.parent
sys.path.insert(0, str(ROOT))   # 채점용 토크나이저·전송이 코어 모듈을 쓴다
#: brief 가 안에서 search 에 요구하는 깊이와 같아야 합집합이 같은 풀에서 재어진다.
UNION_POOL = 5
#: 경로 사이에 같아야 하는 필드 — 채점이 쓰는 것 전부.
PARITY_KEYS = ("status", "node", "rank", "union_rank", "unreadable", "fell_through")


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def _flags() -> dict:
    return {k: v for k, v in sorted(os.environ.items()) if k.startswith("VINC_BRIEF_")
            or k in ("VINC_NODE_SEMANTIC_ARM", "VINC_NODE_SEM_RANK", "VINC_NODE_COLBERT")}


def _constants() -> dict:
    """판정에 들어가는 코드 상수를 **코드에서** 읽는다. 문서나 기억의 값(RRF k=60 같은)을
    싣지 않기 위해서다. `union_pool` 은 `brief_top_k` 와 같아야 합집합이 brief 의 풀이다."""
    from api import query as q   # noqa: PLC0415
    return {"chunk_rrf_k": q.CHUNK_RRF_K, "brief_top_k": q._BRIEF_TOP_K,
            "brief_candidates": q._BRIEF_CANDIDATES, "strict_margin": q._STRICT_MARGIN,
            "sem_gap": q._SEM_GAP, "union_pool": UNION_POOL,
            # 플래그로 바뀌는 값은 이 실행에서 실제로 쓰인 값을 싣는다(X1).
            "brief_expose": q._brief_expose_n(), "brief_list_resolved": q._brief_list_resolved_on()}


def _degraded(res: dict) -> str:
    """의미 팔이 안 돌았으면 그 사유. 돌았으면 빈 문자열.

    스탬프 없는 옛 벡터 경고는 빼고 본다. 고정 스토어는 런타임 스탬프가 생기기 전에 만들어져
    그 경고가 모든 행에 붙지만 의미 팔은 돌았고, 질의 런타임은 하네스가 시작할 때 잠금과 대조한다.
    다른 사유가 같이 붙어 있으면 그대로 강등이다."""
    from api.query import UNSTAMPED_RUNTIME_NOTE   # noqa: PLC0415
    note = str(res.get("vector_note") or "").replace(UNSTAMPED_RUNTIME_NOTE, "").strip("; ")
    if note:
        return note
    arms = str(res.get("arms") or "")
    return f"arms={arms}" if arms and "vector" not in arms else ""


def _title_reuse(query: str, title: str) -> float:
    # 제목 팔의 토크나이저 그대로 잰다(옛 하네스와 같은 정의). 이것만 코어에서 빌린다.
    from api.query import normalize_query_tokens as tok   # noqa: PLC0415
    tt = set(tok(title or ""))
    return round(len(set(tok(query or "")) & tt) / len(tt), 4) if tt else 0.0


def run_one(t, rows: list[dict], titles: dict) -> dict:
    graded, degraded, t0 = [], [], time.perf_counter()
    for i, r in enumerate(rows):
        res = t.brief(r["query"])
        srch = t.search(r["query"], UNION_POOL)
        why = _degraded(srch)
        if why:
            degraded.append({"row": i, "why": why})
        row = {**r, "_title_reuse": _title_reuse(r["query"], titles.get(r.get("gold_node_id") or "", ""))}
        # 의미 팔의 원시 거리는 기준 경로만 읽는다(`InProcess.sem_raw` 주석).
        sem = t.sem_raw(r["query"], UNION_POOL) if hasattr(t, "sem_raw") else None
        graded.append(grading.grade_one(res, row, srch.get("node_hits") or [], sem))
    s = grading.summarize(graded)
    s["wall_s"] = round(time.perf_counter() - t0, 2)
    return {"summary": s, "per_query": graded, "degraded": degraded}


def gold_titles(t, rows: list[dict]) -> tuple[dict, list[str]]:
    """정답 노드를 **제품 경로로** 읽는다(`vinc_brief` 에 id). 못 읽으면 그 정답은 이
    스토어에 없다 — 조용한 0점이 되기 전에 거부한다."""
    titles, missing = {}, []
    for gid in sorted({r["gold_node_id"] for r in rows if r.get("gold_node_id")}):
        res = t.brief(gid)
        node = res.get("node") or {}
        if res.get("status") == "resolved" and node.get("id") == gid:
            titles[gid] = node.get("title") or ""
        else:
            missing.append(gid)
    return titles, missing


def parity(runs: dict) -> list[dict]:
    names = list(runs)
    base = names[0]
    diffs = []
    for other in names[1:]:
        for i, (a, b) in enumerate(zip(runs[base]["per_query"], runs[other]["per_query"])):
            d = {k: (a.get(k), b.get(k)) for k in PARITY_KEYS if a.get(k) != b.get(k)}
            if d:
                diffs.append({"row": i, "pair": f"{base}/{other}", "diff": d})
    return diffs


def unsettled(data: str) -> list[str]:
    """이 코드가 기동 때 스토어에 가할 변경이 아직 안 된 것들. 빈 목록이면 안정하다.

    사이드카(와 허브)는 기동하면 MENTIONS 링크 규칙 표식을 대조해 다르면 **전량 재링크**
    한다(`engines.mentions_link.ensure_rule`, 비동기 잡). 인프로세스 하네스는 그 잡을 안
    돌린다. 그래서 규칙이 바뀐 뒤의 핀에서 인프로세스는 옛 링크를, 사이드카 경로는 기동
    수십 초 뒤부터 새 링크를 잰다 — 같은 질문의 그래프 팔이 시간에 따라 켜졌다 꺼졌다
    한다(2026-09-28 실측). 측정 전에 거부하고, 핀은 규칙을 먼저 적용해 둔다(`settled`).
    근거: [[record:the-in-process-harness-skips-the-boot-relink-so-pins-must-be-settled-2026-09-28]]"""
    from engines.mentions_link import MENTIONS_RULE, _RULE_KEY  # noqa: PLC0415
    from store.backend import open_store                        # noqa: PLC0415
    from store.config import load_config                        # noqa: PLC0415
    s = open_store(load_config({"data_dir": data}))
    if s is None:
        return [f"스토어를 못 열었다: {data}"]
    try:
        rows = s.run("MATCH (m:_SchemaMeta) WHERE m.key = $k RETURN m.value AS v",
                     {"k": _RULE_KEY}) or []
    finally:
        s.close()
    got = (rows[0].get("v") or "") if rows else ""
    return [] if got == MENTIONS_RULE else [f"MENTIONS 규칙 표식 {got or '(없음)'} != 코드 {MENTIONS_RULE}"]


def _fresh_copy(src: pathlib.Path, dst: pathlib.Path) -> str:
    shutil.copyfile(src, dst)
    os.chmod(dst, stat.S_IREAD | stat.S_IWRITE)
    return str(dst)


def _migrate_lex(path: str, dim_model: str) -> dict:
    """사본을 열어 v21 색인 이관을 돌리고 닫는다. 이 코드에 이관이 없으면 그렇다고 답한다.
    근거: [[vinc.plan.korean-retrieval-p1-p2-2026-10-02 §4]]"""
    from store.backend import open_store             # noqa: PLC0415
    from store.config import load_config             # noqa: PLC0415
    cfg = load_config({"data_dir": path, "embedding_model": dim_model})
    st = open_store(cfg)
    if st is None:
        return {"error": "사본을 열 수 없다"}
    try:
        st.provision(dim=cfg.embedding_dim)
        if not hasattr(st, "migrate_lex_index"):
            return {"error": "이 코드에는 v21 이관이 없다"}
        return st.migrate_lex_index()
    finally:
        st.close()


def _live_snapshot(v1: V1) -> dict:
    docs, cursor = [], None
    while True:
        page = v1._call("vinc_doc_list", {"limit": 200, **({"cursor": cursor} if cursor else {})})
        if not page.get("executed"):
            raise TransportError("live fingerprint: document listing failed")
        for d in page.get("docs") or []:
            # doc_list intentionally omits content_hash. Fetch it through the public read
            # tool instead of treating a timestamp/chunk count as a content fingerprint.
            got = v1._call("vinc_doc_get", {"doc_id": d["doc_id"]})
            if not got.get("executed") or not got.get("verified") or not got.get("content_hash"):
                raise TransportError(f"live fingerprint cannot verify {d['doc_id']}")
            docs.append((d["doc_id"], d.get("title"), d.get("updated_at"),
                         got["content_hash"]))
        cursor = page.get("next_cursor")
        if page.get("has_more") and not cursor:
            raise TransportError("live fingerprint: document page has no continuation cursor")
        if not page.get("has_more"):
            break
    dom = v1._call("vinc_domains", {})
    if not dom.get("executed"):
        raise TransportError("live fingerprint: domain counts failed")
    return {"docs": sorted(docs), "domains": sorted(
        (r.get("domain"), *(int(r.get(k) or 0) for k in
                            ("episodes", "concepts", "records", "decisions", "total")))
        for r in dom.get("rows") or [])}


def _snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _live_openapi_hash(base: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + "/v1/openapi.json", timeout=15) as response:
        return hashlib.sha256(response.read()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", help="eval_store.lock.json 의 스토어 키")
    ap.add_argument("--onnx", help="인코더 ONNX 경로(락의 encoder_sha256 과 대조)")
    ap.add_argument("--transports", default="inproc,cli,v1")
    ap.add_argument("--gold", action="append")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", required=True, help="결과 디렉터리(스토어 사본도 여기 생긴다)")
    ap.add_argument("--live", help="호스티드 v1 주소 — 판정용이 아니다")
    ap.add_argument("--space", default="personal")
    ap.add_argument("--allow-runtime-mismatch", action="store_true",
                    help="질의 런타임이 락과 달라도 잰다(결과에 불일치를 적는다)")
    ap.add_argument("--baseline", help="같은 문항으로 짝지어 비교할 이전 result.json")
    ap.add_argument("--migrate-lex", action="store_true",
                    help="사본마다 v21 색인 이관(migrate_lex_index) 뒤 잰다 — 이관 전 스토어는 오늘 색인 그대로다")
    a = ap.parse_args(argv)

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fp = runtime.fingerprint()
    header: dict = {"kind": "eval.nodes.v2", "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "commit": _commit(), "flags": _flags(), "runtime": fp,
                    "constants": _constants(),
                    "openapi_sha256": runtime.file_sha256(ROOT / "vinc_mcp/openapi_v1.json")}
    base = None
    if a.baseline:
        base = {"path": a.baseline, "sha256": runtime.file_sha256(pathlib.Path(a.baseline)),
                "rows": funnel._rows(a.baseline)}

    if a.live:
        key = os.environ.get("VINC_API_KEY")
        if not key:
            print("VINC_API_KEY 가 없다(읽기 전용 키면 된다)", file=sys.stderr)
            return 1
        gold_files = a.gold or [str(ROOT / "rag_eval/fixtures/brief_gold.jsonl")]
        rows = grading.load_gold(gold_files)[: a.limit or None]
        if _base_mismatch(base, rows):
            return 1
        v1 = V1(a.live, key, a.space)
        header["openapi_sha256"] = _live_openapi_hash(a.live)
        before = _live_snapshot(v1)
        titles, missing = gold_titles(v1, rows)
        runs = {"v1-live": run_one(v1, rows, titles)}
        after = _live_snapshot(v1)
        header.update(target={"live": a.live, "space": a.space}, missing_gold=missing,
                      live_stable=before == after,
                      live_fingerprint_before=_snapshot_hash(before),
                      live_fingerprint_after=_snapshot_hash(after),
                      note="호스티드는 핀이 아니다 — 판정에 쓰지 않는다; 런타임 판은 허브 빌드 로그로 본다")
        result = _finish(out, header, gold_files, runs, compare=False, base=base)
        return result if before == after else 1

    if not (a.store and a.onnx):
        print("--store 와 --onnx 가 필요하다(또는 --live)", file=sys.stderr)
        return 1
    entry = runtime.lock_entry(a.store)
    mism = runtime.runtime_mismatch(entry, fp)
    if mism and not a.allow_runtime_mismatch:
        print("질의 인코더 런타임이 저장 벡터와 다르다 — 거부:\n  " + "\n  ".join(mism), file=sys.stderr)
        return 1
    pinned = WORKSPACE / entry["path"]
    if pathlib.Path(str(pinned) + ".wal").exists():
        print(f"핀 옆에 WAL 이 있다 — 열린 적 있는 바이트다: {pinned}", file=sys.stderr)
        return 1
    sha = runtime.file_sha256(pinned)
    if sha != entry["sha256"]:
        print(f"핀 해시가 락과 다르다: {sha[:12]} != {entry['sha256'][:12]}", file=sys.stderr)
        return 1
    onnx_sha = runtime.file_sha256(pathlib.Path(a.onnx))
    if entry.get("encoder_sha256") and onnx_sha != entry["encoder_sha256"]:
        print(f"인코더 해시가 락과 다르다: {onnx_sha[:12]}", file=sys.stderr)
        return 1
    gold_files = a.gold or [str(ROOT / entry["gold"])]
    rows = grading.load_gold(gold_files)
    bad = grading.check_gold(rows)
    if bad:
        print("정답셋 오류:\n  " + "\n  ".join(bad), file=sys.stderr)
        return 1
    rows = rows[: a.limit or None]
    if _base_mismatch(base, rows):
        return 1
    header.update(target={"store": a.store, "sha256": sha, "encoder_sha256": onnx_sha,
                          "store_runtime": entry.get("encoder_runtime")},
                  runtime_mismatch=mism)

    probe = _fresh_copy(pinned, out / "probe.db")
    drift = unsettled(probe)
    for f in out.glob("probe.db*"):
        f.unlink(missing_ok=True)
    if drift:
        print("스토어가 이 코드에 안정돼 있지 않다 — 기동 잡이 측정 중에 데이터를 바꾼다:\n  "
              + "\n  ".join(drift), file=sys.stderr)
        return 1

    want = [x.strip() for x in a.transports.split(",") if x.strip()]
    runs: dict = {}
    owner, spec = entry["owner"], f"{entry['encoder']}={a.onnx}"
    titles: dict = {}
    try:
        if "inproc" in want:
            copy = _fresh_copy(pinned, out / "inproc.db")
            if a.migrate_lex:
                header.setdefault("migrate_lex", {})["inproc"] = _migrate_lex(copy, entry["encoder"])
            t = InProcess(copy, owner, spec)
            try:
                titles, missing = gold_titles(t, rows)
                if missing:
                    print(f"정답 노드 {len(missing)}건이 스토어에 없다 — 예: {missing[:3]}", file=sys.stderr)
                    return 1
                runs["inproc"] = run_one(t, rows, titles)
            finally:
                t.close()
        if {"cli", "v1"} & set(want):
            from rag_eval.evalkit.stack import LocalStack          # noqa: PLC0415
            data = _fresh_copy(pinned, out / "stack.db")
            if a.migrate_lex:
                header.setdefault("migrate_lex", {})["stack"] = _migrate_lex(data, entry["encoder"])
            with LocalStack(data, owner, entry["encoder"], a.onnx, out) as st:
                header["stack"] = {"encode": st.encode_url, "sidecar": st.sidecar_base,
                                   "v1": st.v1_base, "v1_auth": "loopback stub verify_key"}
                if "cli" in want:
                    cli = Cli(data, owner)
                    if not titles:
                        titles, missing = gold_titles(cli, rows)
                    runs["cli"] = run_one(cli, rows, titles)
                if "v1" in want:
                    v1 = V1(st.v1_base, st.api_key)
                    if not titles:
                        titles, missing = gold_titles(v1, rows)
                    runs["v1"] = run_one(v1, rows, titles)
    except TransportError as exc:
        print(f"전송 실패: {exc}", file=sys.stderr)
        return 1
    finally:
        for f in out.glob("*.db*"):
            f.unlink(missing_ok=True)
    return _finish(out, header, gold_files, runs, compare=len(runs) > 1, base=base)


def _base_mismatch(base: dict | None, rows: list[dict]) -> bool:
    """기준선이 이 실행과 같은 문항을 같은 순서로 가졌는가. 다르면 짝지을 수 없다 —
    측정을 다 돌린 뒤에 알면 늦으므로 먼저 거부한다."""
    if base is None:
        return False
    base["rows"] = base["rows"][: len(rows)]
    if [r["query"] for r in base["rows"]] == [r["query"] for r in rows]:
        return False
    print(f"기준선의 문항 목록이 정답셋과 다르다 — 짝지을 수 없다: {base['path']}", file=sys.stderr)
    return True


def _finish(out: pathlib.Path, header: dict, gold_files: list[str], runs: dict, *,
            compare: bool, base: dict | None = None) -> int:
    header["gold"] = {p: runtime.file_sha256(pathlib.Path(p)) for p in gold_files}
    diffs = parity(runs) if compare else []
    degraded = {k: v["degraded"] for k, v in runs.items() if v["degraded"]}
    for r in runs.values():
        r["funnel"] = funnel.report(r["per_query"])
    result = {"header": header, "parity": {"transports": list(runs), "diffs": diffs},
              "degraded": degraded, "runs": runs}
    if base is not None:
        # 재현 대조(채점 필드가 문항별로 같은가)와 짝지은 차이를 경로마다 싣는다.
        # 레버를 켠 실행에서는 재현 차이가 곧 그 레버가 바꾼 문항이다.
        header["baseline"] = {"path": base["path"], "sha256": base["sha256"]}
        result["paired"] = {name: {
            "reproduction_diffs": parity({"baseline": {"per_query": base["rows"]}, name: r}),
            "metrics": funnel.paired(base["rows"], r["per_query"])} for name, r in runs.items()}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, r in runs.items():
        s = r["summary"]
        ans = [x for x in r["per_query"] if x["answerable"] and x["status"] == "resolved"]
        print(json.dumps({"transport": name, "R@1": s["node_recall"]["R@1"],
                          "resolved_correct": sum(1 for x in ans if not x["wrong_node"]),
                          "resolved_wrong": sum(1 for x in ans if x["wrong_node"]),
                          "false_resolved": s["false_resolved_rate"],
                          "gold_in_union": s["gold_in_union_rate"],
                          "status": s["status_census"], "wall_s": s["wall_s"],
                          "degraded": len(r["degraded"])}, ensure_ascii=False))
        f = r["funnel"]
        print(json.dumps({"transport": name, "gates": {g: v["n"] for g, v in f["gates"].items()},
                          "first_fail": f["first_fail"], "aurc": f["risk_coverage"]["aurc"],
                          **({"reproduction_diffs": len(result["paired"][name]["reproduction_diffs"])}
                             if base is not None else {})}, ensure_ascii=False))
    print(json.dumps({"parity_diffs": len(diffs), "degraded_runs": sorted(degraded)}, ensure_ascii=False))
    if degraded:
        return 1
    return 3 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
