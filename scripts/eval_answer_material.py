#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""N2 — 답변 재료(brief) 대 상위 청크(search) 대 호스트 단독.

Vinc 는 안에 모델이 없으므로 재는 대상은 **Vinc 와 호스트 모델의 짝**이다. 세 팔:

    (a) brief 가 내부에서 돌린 것과 같은 검색의 상위 청크(같은 풀, 집 규칙 5)
    (b) brief 가 첫 번째로 내놓은 노드의 재료. choose 면 1위 후보의 정확한 제목으로 되묻는다
    (c) 호스트 단독, 검색 없음

질문은 N1 정답셋에서 고른다: 답 가능한 질문 중 **노출 1위가 정답인 것**(나머지에서
팔 (b) 는 틀린 노드의 재료라 해소를 다시 재게 된다)과 답 불가 질문 전부. 2026-09-12
파일럿이 29 + 21 이었고 N3 가 같은 질문을 쓴다.
근거: [[record:n2-pilot-brief-material-beats-top-chunks-when-the-first-node-is-right-2026-09-12]]
계획: [[vinc.plan.n-wave-remaining-work-2026-09-11]]

단계마다 파일 하나를 쓰고 다음 단계가 읽는다:

    build   Vinc 쪽. 스토어 **사본**을 연다(단일 라이터)          -> materials.jsonl
    answer  호스트 모델, 행 x 팔마다 격리된 `claude -p` 한 번        -> answers.jsonl
    judge   채점 모델, 어느 팔인지 모른다                            -> judged.jsonl
    report  층별 표. 층이 BOOT_MIN 이상일 때만 짝지은 부트스트랩      -> report.json

⚠ 프롬프트는 `scripts/fixtures/answer_material/` 에 **고정**돼 있고 해시가 행마다 실린다.
   고치면 해시가 바뀌어 파일럿과 비교할 수 없게 된다 — 새 판은 새 파일로 낼 것.
⚠ 정답 질문은 그래프 밖에 산다. 결과를 옮겨 적는 노드도 질문을 인용하지 않는다.
⚠ 호스트·채점자·표본이 각각 하나이고 `claude -p` 에는 온도 조절이 없다(파일럿의 한계를
   그대로 물려받는다). 둘째 채점자는 N3 가 20 퍼센트 표본에 붙인다.
이 스크립트는 그래프에 쓰지 않는다(집 규칙 10).

사용:
    python scripts/eval_answer_material.py build --data <스토어 사본> --owner tenant:local \\
        --encoder intfloat/multilingual-e5-base=<onnx> --out materials.jsonl
    python scripts/eval_answer_material.py answer --materials materials.jsonl --out answers.jsonl
    python scripts/eval_answer_material.py judge --materials materials.jsonl \\
        --answers answers.jsonl --out judged.jsonl
    python scripts/eval_answer_material.py report --materials materials.jsonl \\
        --judged judged.jsonl --out report.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import pathlib
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PROMPTS = ROOT / "scripts" / "fixtures" / "answer_material"
GOLD = ROOT / "scripts" / "fixtures" / "brief_gold.jsonl"
#: 이 크기 아래 층에는 구간을 내지 않는다(파일럿에서 미리 정한 값).
BOOT_MIN = 15
ARMS = ("a", "b", "c")


def sha(p: pathlib.Path) -> str:
    """프롬프트 해시. 줄끝을 LF 로 맞춰서 잰다 — 윈도우 체크아웃이 CRLF 로 바꿔도 같은
    프롬프트는 같은 해시다(LF 파일이면 원시 바이트 해시와 같다)."""
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def file_sha(p: pathlib.Path) -> str:
    """스토어 파일 지문(열기 전에 잰다). 스토어는 본 파일과 `<파일>.wal` 두 파일이라 WAL 이
    남아 있으면 본 파일 바이트가 내용을 대표하지 못한다 — 그때는 해시 대신 "wal-present"
    를 돌려줘 어떤 고정값과도 같지 않게 한다."""
    if not p.is_file():
        return "not-a-file"
    if p.with_name(p.name + ".wal").exists():
        return "wal-present"
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree_ref() -> str:
    """집 규칙 13 — 어느 트리에서 돌았는지. 손으로 적게 두면 빠뜨린다."""
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "api", "store",
                                "scripts"], cwd=ROOT, capture_output=True,
                               text=True).stdout.strip()
        return head + ("+dirty" if dirty else "")
    except Exception:                                   # noqa: BLE001
        return "unknown"


def jl_read(p):
    return [json.loads(l) for l in pathlib.Path(p).read_text(encoding="utf-8").splitlines()
            if l.strip()]


def jl_write(p, rows):
    pathlib.Path(p).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                               encoding="utf-8")


def nbytes(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def strip_vectors(row: dict) -> dict:
    return {k: v for k, v in row.items()
            if not (isinstance(v, list) and v and all(isinstance(x, float) for x in v))}


# ---------------------------------------------------------------- build
def cmd_build(a):
    from api import query as q                      # noqa: PLC0415
    from store.backend import open_store            # noqa: PLC0415
    from store.config import load_config            # noqa: PLC0415
    from encoder import Encoder                     # noqa: PLC0415
    import eval_brief_resolution as n1              # noqa: PLC0415

    # 답가능성 게이트는 꺼진 채 나간다 — 켠 채로 잰 팔은 다른 제품을 잰다.
    gate = os.environ.get("VINC_BRIEF_ANSWERABILITY", "") or "unset (ships off)"
    name, path = a.encoder.split("=", 1)
    enc = Encoder(name, path)
    enc.encode(["warmup"], kind="query")
    emb = lambda t: enc.encode([t], kind="query")[0]   # noqa: E731
    commit, store_sha = tree_ref(), file_sha(pathlib.Path(a.data))

    store = open_store(load_config({"data_dir": a.data}))
    if store is None:
        raise SystemExit(f"스토어에 도달할 수 없다: {a.data}")
    rows = n1.load_gold([a.gold])
    out, excluded = [], 0
    for i, r in enumerate(rows):
        gold = r.get("gold_node_id") or ""
        answerable = bool(r.get("answerable", True))
        qv = emb(r["query"])
        res = q.brief(r["query"], owner=a.owner, store=store, query_embedding=qv)
        if answerable and n1.rank_of(gold, res) != 1:
            excluded += 1
            continue
        status = res.get("status")
        # 팔 (b): 사용자가 맨 위에서 만나는 노드의 재료. choose 는 제품이 되묻는 길 그대로.
        calls_b, recovery, first = 1, "", None
        if status == "choose" and res.get("candidates"):
            first = res["candidates"][0]
            mat_b = q.brief(first["title"], owner=a.owner, store=store,
                            query_embedding=emb(first["title"]))
            calls_b, path_b = 2, "choose-first"
            landed = (mat_b.get("status") == "resolved"
                      and (mat_b.get("node") or {}).get("id") == first["id"])
            recovery = "landed" if landed else f"missed:{mat_b.get('status')}"
            ret_b = nbytes(res) + nbytes(mat_b)
        else:
            mat_b, path_b, ret_b = res, status, nbytes(res)
        # 팔 (a): brief 가 요구하는 깊이 그대로의 청크 — 다른 풀에서 잰 두 팔은 풀을 잰다.
        sr = q.search(r["query"], top_k=q._BRIEF_TOP_K, owner=a.owner, store=store,
                      query_embedding=qv, detail="full")
        chunks = [strip_vectors(x) for x in sr.get("rows") or []]
        # 채점 기준: 정답 노드 자신의 재료. brief(id) 를 안 쓰는 이유는 직참조가 못 여는
        # id 모양이 있었기 때문이다 — 같은 꼬리(node_context + _brief_material)를 직접 탄다.
        # 근거: [[record:brief-direct-id-pattern-was-ascii-only-2026-09-12]]
        ref = None
        if gold:
            ctx = q.node_context(owner=a.owner, node=gold, limit=3, with_evidence=True,
                                 store=store)
            if not ctx.get("executed"):
                raise SystemExit(f"row {i}: 정답 노드가 안 열린다: {ctx.get('note')}")
            ref = q._brief_material({"grounds": []}, {"id": gold}, ctx)
        stratum = ("abstention" if not answerable
                   else "decision-rationale" if r.get("gold_kind") == "decision"
                   else "single-fact")
        out.append({
            "i": i, "query": r["query"], "answerable": answerable, "gold": gold,
            "gold_kind": r.get("gold_kind"), "axis": r.get("language_axis"),
            "unanswerable_kind": r.get("unanswerable_kind") or "", "stratum": stratum,
            "n1_status": status, "path_b": path_b, "recovery": recovery,
            "first_candidate": (first or {}).get("id", ""),
            "arms": res.get("arms", ""), "vector_note": res.get("vector_note", ""),
            "search_arms": sr.get("arms", ""), "search_vector_note": sr.get("vector_note", ""),
            "gate": gate, "commit": commit, "store_sha256": store_sha,
            "encoder": a.encoder.split("=")[0],
            "mat": {"a": {"rows": chunks}, "b": mat_b},
            "calls": {"a": 1, "b": calls_b, "c": 0},
            "returned_bytes": {"a": nbytes(sr), "b": ret_b, "c": 0},
            "reference": ({"node": ref.get("node"), "one_liner": ref.get("one_liner"),
                           "excerpts": [e.get("excerpt") for e in ref.get("excerpts") or []]}
                          if ref else None),
        })
    store.close()
    jl_write(a.out, out)
    print("kept", len(out), "excluded_answerable", excluded, "commit", commit)
    print(Counter(x["stratum"] for x in out), Counter(x["path_b"] for x in out),
          Counter(x["recovery"] for x in out), Counter(x["arms"] for x in out))


# ---------------------------------------------------------------- 호스트·채점 호출
def claude(model: str, system_file: pathlib.Path, user: str, cwd: str, timeout=300) -> dict:
    """격리된 `claude -p` 한 번. 도구·MCP·설정 원천을 전부 끄고 빈 디렉터리에서 돈다 —
    CLAUDE.md 나 메모리가 실리면 팔 (c) 가 호스트 단독이 아니게 된다(파일럿 실측: 입력 약
    920토큰, 환경 블록만 보였다)."""
    cmd = [shutil.which("claude") or "claude", "-p", "--model", model, "--tools", "",
           "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
           "--system-prompt-file", str(system_file), "--output-format", "json"]
    last = None
    for _ in range(3):
        try:
            p = subprocess.run(cmd, input=user.encode("utf-8"), capture_output=True,
                               cwd=cwd, timeout=timeout)
            d = json.loads(p.stdout.decode("utf-8"))
            if d.get("is_error"):
                raise RuntimeError(d.get("result"))
            return d
        except Exception as exc:                     # noqa: BLE001
            last = exc
    return {"is_error": True, "result": f"FAILED: {last}"}


def cli_version() -> str:
    try:
        return subprocess.run([shutil.which("claude") or "claude", "--version"],
                              capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:                                   # noqa: BLE001
        return "unknown"


def user_msg(row, arm) -> str:
    s = f"QUESTION:\n{row['query']}\n"
    if arm != "c":
        s += "\nMATERIAL:\n" + json.dumps(row["mat"][arm], ensure_ascii=False, indent=1) + "\n"
    return s


def parse_answer(text: str) -> tuple[str, list[str]]:
    m = re.search(r"ANSWER:\s*(.*?)(?:\n\s*CITED:|$)", text, re.S)
    c = re.search(r"CITED:\s*(.*)", text)
    ans = m.group(1).strip() if m else text.strip()
    cited = [x.strip().strip("`") for x in (c.group(1) if c else "").split(",")]
    return ans, [x for x in cited if x and x.upper() != "NONE"]


def cmd_answer(a):
    rows = jl_read(a.materials)
    sys_m, sys_c = PROMPTS / "answer_v1_material.txt", PROMPTS / "answer_v1_alone.txt"
    cwd, cli = tempfile.mkdtemp(prefix="n2-host-"), cli_version()

    def run(job):
        r, arm = job
        pf = sys_c if arm == "c" else sys_m
        d = claude(a.model, pf, user_msg(r, arm), cwd)
        ans, cited = parse_answer(d.get("result") or "")
        u = d.get("usage") or {}
        return {"i": r["i"], "arm": arm, "model": a.model, "host_cli": cli,
                "raw": d.get("result"), "error": bool(d.get("is_error")),
                "answer": ans, "cited": cited,
                "in_tokens": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                + (u.get("cache_creation_input_tokens") or 0),
                "out_tokens": u.get("output_tokens") or 0, "cost_usd": d.get("total_cost_usd"),
                "prompt_sha": sha(pf)}
    with cf.ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(run, [(r, arm) for r in rows for arm in ARMS]))
    jl_write(a.out, res)
    print("answers", len(res), "errors", sum(x["error"] for x in res))


def cmd_judge(a):
    rows = {r["i"]: r for r in jl_read(a.materials)}
    jf, cwd = PROMPTS / "judge_v1.txt", tempfile.mkdtemp(prefix="n2-judge-")

    def run(x):
        r = rows[x["i"]]
        ref = json.dumps(r["reference"], ensure_ascii=False, indent=1) if r["answerable"] else ""
        mat = ("NONE" if x["arm"] == "c"
               else json.dumps(r["mat"][x["arm"]], ensure_ascii=False, indent=1))
        msg = (f"QUESTION:\n{r['query']}\n\nANSWERABLE: {'yes' if r['answerable'] else 'no'}\n\n"
               f"REFERENCE:\n{ref}\n\nMATERIAL:\n{mat}\n\nANSWER:\n{x['raw']}\n")
        for _ in range(2):
            m = re.search(r"\{.*\}", claude(a.model, jf, msg, cwd).get("result") or "", re.S)
            try:
                return {**x, "judge_model": a.model, "judge_sha": sha(jf),
                        "verdict": json.loads(m.group(0)) if m else None}
            except ValueError:
                continue
        return {**x, "judge_model": a.model, "judge_sha": sha(jf), "verdict": None}
    with cf.ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(run, jl_read(a.answers)))
    jl_write(a.out, res)
    print("judged", len(res), "unparsed", sum(x["verdict"] is None for x in res))


# ---------------------------------------------------------------- report
def shown_ids(r, arm) -> set[str]:
    m = r["mat"][arm]
    if arm == "a":
        return {str(x.get(k)) for x in m["rows"] for k in ("id", "doc_id") if x.get(k)}
    ids = set(m.get("grounds") or []) | {(m.get("node") or {}).get("id", "")}
    ids |= {e.get("doc_id", "") for e in m.get("excerpts") or []}
    ids |= {it.get("id", "") for it in (m.get("links") or {}).get("items") or []}
    ids |= {c.get("id", "") for c in m.get("candidates") or []}
    return ids - {""}


def cited_bytes(r, arm, cited: set[str]) -> int:
    m = r["mat"][arm]
    if arm == "a":
        return sum(nbytes(x) for x in m["rows"] if {x.get("id"), x.get("doc_id")} & cited)
    n = (nbytes({"node": m.get("node"), "one_liner": m.get("one_liner")})
         if (m.get("node") or {}).get("id") in cited else 0)
    n += sum(nbytes(e) for e in m.get("excerpts") or [] if e.get("doc_id") in cited)
    n += sum(nbytes(it) for it in (m.get("links") or {}).get("items") or []
             if it.get("id") in cited)
    return n


def boot(d: list[float], n=10000, seed=0) -> list[float]:
    """짝지은 부트스트랩 95 퍼센트(집 규칙 8). [평균, 하한, 상한]."""
    rng, m = random.Random(seed), len(d)
    means = sorted(sum(d[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return [round(sum(d) / m, 4), round(means[int(0.025 * n)], 4),
            round(means[int(0.975 * n) - 1], 4)]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.fmean(xs), 4) if xs else None


def med(xs):
    return round(statistics.median(xs), 1) if xs else None


def grade_rows(materials: list[dict], judged: list[dict]) -> dict:
    """판정 한 줄을 층 x 팔로 모은다. 채점자가 틀려도 규칙으로 정해지는 것은 규칙으로 센다."""
    rows = {r["i"]: r for r in materials}
    by: dict = {}
    for x in judged:
        r, v, arm = rows[x["i"]], x["verdict"] or {}, x["arm"]
        cited = set(x["cited"])
        shown = shown_ids(r, arm) if arm != "c" else set()
        grounds = set(r["mat"]["b"].get("grounds") or []) if arm == "b" else set()
        hall = v.get("hallucinated")
        by.setdefault((r["stratum"], arm), []).append({
            "i": x["i"], "arm": arm, "path_b": r["path_b"],
            "correct": v.get("correct"), "abstained": v.get("abstained"),
            "faithful": v.get("faithful"), "hallucinated": hall,
            # 판정 규약 자체가 "답 불가에 실질 답을 하면 환각"이라 정한다. 파일럿에서 채점자가
            # 이 규칙을 한 행에서 어겼다 — 그래서 규약값을 따로 센다.
            "hallucinated_protocol": (1 if (not r["answerable"] and v.get("abstained") == 0)
                                      else hall),
            # 어디에 있든 센다: 한국어 답은 ABSTAIN 을 끝에 붙인다(startswith 로 세면 틀린다).
            "abstain_literal": 1 if "ABSTAIN" in x["answer"].upper() else 0,
            "cite_p": (len(cited & shown) / len(cited)) if (cited and arm != "c") else None,
            "cite_r_grounds": (len(cited & grounds) / len(grounds)) if grounds else None,
            "gold_cited": (1 if r["gold"] in cited else 0) if (arm == "b" and r["gold"]) else None,
            "returned_bytes": r["returned_bytes"][arm],
            "cited_bytes": cited_bytes(r, arm, cited) if arm != "c" else 0,
            "in_tokens": x["in_tokens"], "out_tokens": x["out_tokens"],
            "calls": r["calls"][arm]})
    return by


def summarize(by: dict) -> dict:
    table: dict = {}
    for (s, arm), L in sorted(by.items()):
        table.setdefault(s, {})[arm] = {
            "n": len(L), "accuracy": mean([x["correct"] for x in L]),
            "abstain_rate": mean([x["abstained"] for x in L]),
            "faithfulness": mean([x["faithful"] for x in L]),
            "hallucination_rate": mean([x["hallucinated"] for x in L]),
            "hallucination_rate_protocol": mean([x["hallucinated_protocol"] for x in L]),
            "cite_precision": mean([x["cite_p"] for x in L]),
            "cite_recall_vs_grounds": mean([x["cite_r_grounds"] for x in L]),
            "gold_cited_rate": mean([x["gold_cited"] for x in L]),
            "returned_bytes_median": med([x["returned_bytes"] for x in L]),
            "cited_bytes_median": med([x["cited_bytes"] for x in L]),
            "host_in_tokens_median": med([x["in_tokens"] for x in L]),
            "host_out_tokens_median": med([x["out_tokens"] for x in L]),
            "vinc_calls_mean": mean([x["calls"] for x in L])}
    paired: dict = {}
    for s in table:
        acc = {arm: {x["i"]: x["correct"] or 0 for x in by.get((s, arm), [])} for arm in ARMS}
        ids = sorted(set.intersection(*(set(acc[arm]) for arm in ARMS)))
        if len(ids) < BOOT_MIN:
            paired[s] = f"n={len(ids)} below {BOOT_MIN}: no interval"
            continue
        paired[s] = {f"{x}-{y}": boot([acc[x][i] - acc[y][i] for i in ids])
                     for x, y in (("b", "a"), ("b", "c"), ("a", "c"))}
    by_path: dict = {}
    for x in by.get(("single-fact", "b"), []) + by.get(("decision-rationale", "b"), []):
        by_path.setdefault(x["path_b"], []).append(x)
    allrec = [x for L in by.values() for x in L]
    return {"table": table, "paired_bootstrap_accuracy_delta": paired,
            "arm_b_answerable_by_path": {p: {"n": len(L), "accuracy": mean([x["correct"] for x in L])}
                                         for p, L in by_path.items()},
            "judge_abstain_vs_literal_agreement":
                mean([1 if x["abstain_literal"] == x["abstained"] else 0 for x in allrec])}


def cmd_report(a):
    J = jl_read(a.judged)
    out = summarize(grade_rows(jl_read(a.materials), J))
    out["unparsed_verdicts"] = sum(1 for x in J if x["verdict"] is None)
    pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--data", required=True, help="스토어 사본 경로(단일 라이터)")
    b.add_argument("--owner", required=True)
    b.add_argument("--encoder", required=True, help="이름=onnx 경로, 프로세스 안 인코더")
    b.add_argument("--gold", default=str(GOLD))
    b.add_argument("--out", required=True)
    for name, model in (("answer", "claude-sonnet-5"), ("judge", "claude-opus-5")):
        s = sub.add_parser(name)
        s.add_argument("--materials", required=True)
        if name == "judge":
            s.add_argument("--answers", required=True)
        s.add_argument("--out", required=True)
        s.add_argument("--model", default=model)
        s.add_argument("--workers", type=int, default=6)
    r = sub.add_parser("report")
    r.add_argument("--materials", required=True)
    r.add_argument("--judged", required=True)
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    {"build": cmd_build, "answer": cmd_answer, "judge": cmd_judge, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
