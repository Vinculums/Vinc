"""한 회차 — 핀 확인 → 로컬 스택 → A1 검색 → (회차 × 과제 × 호스트 × 비교군) 실행 → 채점 → 기록.

거부하는 것(조용한 오답을 막는다):
- 핀 파일의 해시가 핀 정보와 다르거나 옆에 ``.wal`` 이 있다(열린 적 있는 바이트다).
- 이 인터프리터의 질의 인코더 런타임이나 엔진 판이 저장 벡터를 만든 판과 다르다.
- 스토어가 이 코드에 안정돼 있지 않다(사이드카 기동 잡이 측정 중에 데이터를 바꾼다).
- 과제 파일의 분할과 실행이 밝힌 분할이 다르다. 보류 세트(test)는 ``--heldout`` 없이 안 돈다.
- 결과 디렉터리에 이미 결과가 있다(덮어쓰지 않는다).

후보 디렉터리(제안자가 읽는 자리)는 ``<out>/candidate/<id>/`` 이고 ``source/`` · ``config.json`` ·
``scores.json`` · ``traces/`` · ``runs.jsonl`` · ``grades.jsonl`` 를 담는다. 보류 세트는 같은 꼴로
``<out>/heldout/<id>/`` 에 쓴다. 비교용 결과 한 장은 ``<out>/result.json`` 이다.

실행 기록 조각은 기본으로 파일(``runs.jsonl``)에만 쓴다. 호스티드 그래프에 쓰려면
``--record hosted --write-hosted`` 둘 다와 쓰기 키가 있어야 한다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata as md
import json
import os
import pathlib
import shutil
import stat
import subprocess
import time
import uuid

from rag_eval.agent_tasks import arms as arms_mod
from rag_eval.agent_tasks import checkers, hosts as hosts_mod, metrics, models, record, tasks as tasks_mod
from rag_eval.evalkit import runtime

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent if ROOT.parent.name != "_wt" else ROOT.parent.parent
SOURCE_FILES = ("__init__.py", "__main__.py", "tasks.py", "checkers.py", "models.py", "arms.py",
                "hosts.py", "record.py", "metrics.py", "runner.py")
#: 실행 기록 조각의 주제. 이 갈래의 하네스 실행은 여기 모인다.
RUN_DOMAIN = "vinc/agent/harness"
DEFAULT_ROLE = "concept:agent-research"
PACKAGES = ("vinc-client", "vinc-langgraph", "vinc-agent-framework", "langgraph", "langchain",
            "langchain-core", "agent-framework-core", "httpx", "pydantic")


class Refused(RuntimeError):
    pass


def _commit() -> dict:
    def git(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "--short", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--", "rag_eval", "integrations"))}


def _versions() -> dict:
    out = {}
    for p in PACKAGES:
        try:
            out[p] = md.version(p)
        except md.PackageNotFoundError:
            out[p] = ""
    return out


def store_entry(a) -> dict:
    """락 항목(``--store``) 또는 같은 꼴의 핀 정보 파일(``--store-meta``)."""
    if a.store:
        entry = dict(runtime.lock_entry(a.store))
        entry["label"] = a.store
        entry["source"] = "eval_store.lock.json"
        entry["file"] = str(WORKSPACE / entry["path"])
        return entry
    meta = pathlib.Path(a.store_meta)
    entry = json.loads(meta.read_text(encoding="utf-8"))
    entry.setdefault("label", meta.parent.name)
    entry["source"] = str(meta)
    entry["file"] = str((meta.parent / entry["path"]).resolve()) if not pathlib.Path(
        entry["path"]).is_absolute() else entry["path"]
    return entry


def verify_store(entry: dict, onnx: str, fp: dict) -> dict:
    pinned = pathlib.Path(entry["file"])
    if pathlib.Path(str(pinned) + ".wal").exists():
        raise Refused(f"핀 옆에 WAL 이 있다 — 열린 적 있는 바이트다: {pinned}")
    sha = runtime.file_sha256(pinned)
    if sha != entry["sha256"]:
        raise Refused(f"핀 해시가 핀 정보와 다르다: {sha[:12]} != {entry['sha256'][:12]}")
    onnx_sha = runtime.file_sha256(pathlib.Path(onnx))
    if entry.get("encoder_sha256") and onnx_sha != entry["encoder_sha256"]:
        raise Refused(f"인코더 해시가 핀 정보와 다르다: {onnx_sha[:12]}")
    mism = runtime.runtime_mismatch(entry, fp)
    if mism:
        raise Refused("질의 인코더 런타임이 저장 벡터와 다르다:\n  " + "\n  ".join(mism))
    want_engine = (entry.get("engine") or "").replace("ladybug", "").strip()
    if want_engine and want_engine != fp["engine"]["ladybug"]:
        raise Refused(f"엔진 판이 다르다: 핀 {want_engine} / 이 인터프리터 {fp['engine']['ladybug']}")
    return {"sha256": sha, "encoder_sha256": onnx_sha}


def _copy(src: pathlib.Path, dst: pathlib.Path) -> str:
    shutil.copyfile(src, dst)
    os.chmod(dst, stat.S_IREAD | stat.S_IWRITE)
    return str(dst)


def fingerprint(header: dict) -> str:
    """비교 가능성을 정하는 것만 담은 해시. 시각·경로·커밋은 담지 않는다(헤더에는 있다)."""
    keep = {k: header[k] for k in ("store", "runtime", "packages", "hosts", "model", "tasks",
                                   "arms", "limits")}
    keep["runtime"] = {k: v for k, v in header["runtime"].items() if k != "executable"}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def model_judge(runner: models.ModelRunner):
    """서술형 채점 모델. 과제·채점 기준·산출물만 받는다. 검색 재료는 받지 않는다."""
    def judge(prompt: str, spec: dict, body: str) -> bool:
        turn = runner.step([
            {"role": "system", "content": "You grade a work product against a rubric. Reply PASS "
                                          "or FAIL on the first line, then one sentence."},
            {"role": "user", "content": f"TASK:\n{prompt}\n\nRUBRIC:\n{spec['rubric']}\n\n"
                                        f"WORK PRODUCT:\n{body}"}], [])
        return turn.text.strip().upper().startswith("PASS")
    return judge


def _write_source(dest: pathlib.Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    here = pathlib.Path(__file__).resolve().parent
    manifest = {}
    for name in SOURCE_FILES:
        data = (here / name).read_bytes()
        (dest / name).write_bytes(data)
        manifest[name] = hashlib.sha256(data).hexdigest()
    (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True),
                                        encoding="utf-8")


def run(a) -> int:
    out = pathlib.Path(a.out)
    if (out / "result.json").exists():
        raise Refused(f"결과가 이미 있다(덮어쓰지 않는다): {out / 'result.json'}")
    ts = tasks_mod.load(a.tasks)
    tasks_mod.require_split(ts, a.split, heldout=a.heldout)
    if a.disjoint_from:
        shared = tasks_mod.overlap(ts, tasks_mod.load(a.disjoint_from))
        if shared:
            raise Refused(f"다른 세트와 과제가 겹친다: {shared[:5]}")
    todo = [t for t in ts.tasks if t.family != "handoff"]
    handoff = len(ts.tasks) - len(todo)
    if handoff and not a.skip_handoff:
        raise Refused(f"handoff 과제 {handoff}개는 이 실행기가 아직 돌리지 못한다(세션 둘과 쓰기 경로가 "
                      "필요하다) — --skip-handoff 로 빼고 그 수를 헤더에 남길 것")
    want_arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    want_hosts = [x.strip() for x in a.hosts.split(",") if x.strip()]
    for x in want_arms:
        if x not in arms_mod.ARMS:
            raise Refused(f"모르는 비교군 {x!r}")
    host_objs = [hosts_mod.make(h) for h in want_hosts]
    prices = tuple(float(x) for x in a.price.split(",")) if a.price else None
    model = models.make(a.model, prices=prices)

    fp = runtime.fingerprint()
    entry = store_entry(a)
    hashes = verify_store(entry, a.onnx, fp)
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir(exist_ok=True)
    pinned = pathlib.Path(entry["file"])

    from rag_eval.evalkit.run_nodes import unsettled   # noqa: PLC0415 — 스토어 코드를 든다
    probe = _copy(pinned, work / "probe.db")
    drift = unsettled(probe)
    for f in work.glob("probe.db*"):
        f.unlink(missing_ok=True)
    if drift:
        raise Refused("스토어가 이 코드에 안정돼 있지 않다:\n  " + "\n  ".join(drift))

    owner = entry["owner"]
    a1 = None
    header: dict = {
        "kind": "eval.agent-tasks.v1", "candidate": a.candidate, "split": ts.split,
        "round": a.round or out.name,
        "store": {"label": entry["label"], "sha256": hashes["sha256"], "engine": entry.get("engine"),
                  "encoder": entry.get("encoder"), "encoder_sha256": hashes["encoder_sha256"],
                  "encoder_runtime": {k: v for k, v in (entry.get("encoder_runtime") or {}).items()
                                      if k in runtime.RUNTIME_PACKAGES},
                  "owner": owner},
        "store_choice": entry.get("choice") or a.store_note or "",
        "runtime": fp, "packages": _versions(),
        "hosts": {h.name: h.describe() for h in host_objs},
        "model": model.describe(),
        "tasks": {"sha256": ts.sha256, "split": ts.split, "author": ts.author,
                  "authored_on": ts.authored_on, "written_on_store": ts.store,
                  "n": len(todo), "handoff_skipped": handoff},
        "arms": {"list": want_arms, "base_system_sha256": hashlib.sha256(
                     arms_mod.BASE_SYSTEM.encode()).hexdigest(),
                 "a3": dict(arms_mod.A3_SETTINGS), "a1": None},
        "limits": {"runs": a.runs, "tool_calls_per_run": a.cap, "max_steps": a.max_steps},
    }
    rows: list[dict] = []
    layout = out / ("heldout" if ts.split == "test" else "candidate") / a.candidate
    traces = layout / "traces"
    stack_db = _copy(pinned, work / "stack.db")
    from rag_eval.evalkit.stack import LocalStack   # noqa: PLC0415
    try:
        with LocalStack(stack_db, owner, entry["encoder"], a.onnx, work) as st:
            header["stack"] = {"v1_auth": "loopback stub verify_key, read scope"}
            mcp = arms_mod.McpClient(st.v1_base, st.api_key)
            mcp.initialize()
            # 과제가 받아 주는 근거가 이 스토어에 없으면 그 과제는 조용히 0점이 된다. 먼저 거부한다.
            gone = sorted({s for t in todo for c in t.checks if c["type"] == "sources"
                           for s in c["support"] if not mcp.exists(checkers.canon_id(s))})
            if gone:
                raise Refused(f"과제의 근거 id {len(gone)}개가 이 스토어에 없다: {gone[:5]}")
            a2_tools = mcp.list_tools() if "A2" in want_arms else []
            header["arms"]["a2"] = {"tools": len(a2_tools), "tools_sha256": hashlib.sha256(
                json.dumps(a2_tools, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
            if "A1" in want_arms:
                a1 = arms_mod.A1Retriever(_copy(pinned, work / "a1.db"), owner, st.encode_url,
                                          st.encode_token, k=a.a1_k, chunk_chars=a.a1_chars)
                header["arms"]["a1"] = a1.settings()
                a1_blocks = a1.blocks([t.prompt for t in todo])
            vinc = arms_mod.VincEndpoint(st.v1_base, st.api_key)
            judge = checkers.fake_judge if a.model == "fake" else model_judge(model)
            ctx = checkers.Context(resolve=mcp.exists, judge=judge)
            for r in range(1, a.runs + 1):
                for task in todo:
                    for host in host_objs:
                        for arm in want_arms:
                            surface = arms_mod.Surface(
                                arm=arm, mcp=mcp, vinc=vinc,
                                context=a1_blocks[task.prompt]["block"] if arm == "A1" else None,
                                tools=a2_tools if arm == "A2" else [])
                            rows.append(_one(a, header, ts, task, host, arm, r, surface, model,
                                             ctx, traces))
                            if not a.quiet:
                                x = rows[-1]
                                print(json.dumps({"run": x["run_id"], "success": x["grade"]["success"],
                                                  "class": x["failure_class"]}), flush=True)
    finally:
        for f in work.glob("*.db*"):
            f.unlink(missing_ok=True)
    return _finish(a, out, layout, header, rows, ts)


def _one(a, header, ts, task, host, arm, r, surface, model, ctx, traces) -> dict:
    trace = record.Trace()
    trace.add({"t": "start", "task": task.id, "family": task.family, "arm": arm, "host": host.name,
               "model": header["model"]["model"], "run": r, "prompt": task.prompt})
    t0 = time.perf_counter()
    host_error = ""
    try:
        res = host.run(prompt=task.prompt, system=arms_mod.BASE_SYSTEM, surface=surface,
                       model=model, trace=trace, max_steps=a.max_steps, cap=a.cap)
    except Exception as exc:   # noqa: BLE001 — 실행 하나의 고장은 그 행에 적고 다음으로 간다
        host_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        res = hosts_mod.RunOutput(final_text="", host_error=host_error)
    latency = round(time.perf_counter() - t0, 3)
    trace.add({"t": "final", "text": res.final_text, "model_error": res.model_error,
               "host_error": host_error})
    path = trace.write(traces)
    grade = checkers.grade(task, res.final_text, ctx,
                           model_failed=bool(res.model_error or host_error))
    cls = "host_error" if host_error else record.failure_class(grade, trace.events, res.model_error)
    cost = model.cost(res.tokens_in, res.tokens_out)
    rid = record.run_id(header["round"], a.candidate, task.id, arm, host.name,
                        header["model"]["model"], f"r{r}")
    row = {"run_id": rid, "task": task.id, "family": task.family, "arm": arm, "host": host.name,
           "model": header["model"]["model"], "run": r, "trace_sha256": path.stem,
           "trace_bytes": path.stat().st_size, "grade": grade, "failure_class": cls,
           "steps": res.steps, "tool_calls": res.tool_calls, "tokens_in": res.tokens_in,
           "tokens_out": res.tokens_out, "cost_usd": cost, "latency_s": latency,
           "model_versions": res.model_versions, "host_error": host_error,
           "tool_events": [{"name": e["name"], "kind": e["kind"]} for e in trace.events
                           if e["t"] == "tool"]}
    outcome = ("error" if cls in ("host_error", "model_error")
               else "success" if grade["success"] else "failure")
    gates = {f"{i}:{c['type']}": ("pass" if c["passed"] else "fail")
             for i, c in enumerate(grade["checks"], 1)}
    row["fragment"] = record.run_fragment(
        rid=rid, role=task.role or a.role, outcome=outcome, trace_path=path,
        task_ref=f"{ts.split}:{task.id}@{ts.sha256[:12]}", host=host.name,
        model_family=header["model"]["family"], steps=res.steps, tool_calls=res.tool_calls,
        tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost_usd=cost, gate_results=gates,
        cited_ids=grade["cited"], date=dt.date.today().isoformat(), domain=RUN_DOMAIN)
    return row


def _write_hosted(rows: list[dict]) -> dict:
    from vinc_client import VincClient   # noqa: PLC0415
    key = os.environ.get("VINC_SK") or os.environ.get("VINC_API_KEY") or ""
    if not key.startswith("vinc_sk_"):
        raise Refused("호스티드 기록에는 쓰기 키(VINC_SK 또는 VINC_API_KEY, vinc_sk_)가 필요하다")
    done, warnings = 0, []
    with VincClient(key) as client:
        for role in sorted({e["to"] for r in rows for e in r["fragment"]["edges"]}):
            client.node(role)
        for r in rows:
            frag = r["fragment"]
            op = str(uuid.uuid5(uuid.NAMESPACE_URL, "vinc-agent-task-run:" + r["run_id"]))
            res = client.ingest(frag["nodes"], frag["edges"], operation_id=op)
            warnings.extend(res.get("warnings") or [])
            done += 1
    return {"written": done, "warnings": warnings[:20]}


def _finish(a, out, layout, header, rows, ts) -> int:
    # 원격 모델의 판은 응답을 받아야 안다. 지문은 본 판까지 담아 마지막에 낸다.
    header["model"]["versions_seen"] = sorted({v for r in rows for v in r["model_versions"]})
    header["fingerprint"] = fingerprint(header)
    scores = metrics.summarize(rows)
    layout.mkdir(parents=True, exist_ok=True)
    cfg = layout / "config.json"
    if cfg.exists():
        old = json.loads(cfg.read_text(encoding="utf-8"))
        if old.get("split") != header["split"]:
            raise Refused(f"후보 디렉터리 {layout} 는 split {old.get('split')!r} 의 것이다")
    header.update(at=dt.datetime.now(dt.timezone.utc).isoformat(), **_commit())
    _write_source(layout / "source")
    cfg.write_text(json.dumps(header, indent=1, ensure_ascii=False), encoding="utf-8")
    (layout / "scores.json").write_text(json.dumps(scores, indent=1, ensure_ascii=False),
                                        encoding="utf-8")
    with open(layout / "grades.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps({k: v for k, v in r.items() if k != "fragment"},
                               ensure_ascii=False, sort_keys=True) + "\n")
    sink = {"mode": a.record}
    if a.record == "file":
        with open(layout / "runs.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps({"run_id": r["run_id"], "fragment": r["fragment"]},
                                   ensure_ascii=False, sort_keys=True) + "\n")
        sink["path"] = str(layout / "runs.jsonl")
    elif a.record == "hosted":
        if not a.write_hosted:
            raise Refused("--record hosted 는 --write-hosted 와 함께만 쓴다")
        sink.update(_write_hosted(rows))
    sink["fragments"] = len(rows)
    header["record_sink"] = sink
    result = {"header": header, "metrics": scores,
              "rows": [{k: v for k, v in r.items() if k != "fragment"} for r in rows]}
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False),
                                     encoding="utf-8")
    for key, cell in scores["cells"].items():
        print(json.dumps({"cell": key, "success": cell["success"]["rate"],
                          "n": cell["success"]["n"], "parse_failures": cell["parse_failures"],
                          "evidence": cell["evidence_correct"]["rate"],
                          "fabrication": cell["fabrication"]["rate"],
                          "tool_calls": cell["cost"]["tool_calls_mean"],
                          "failure_class": cell["failure_class"]}, ensure_ascii=False))
    print(json.dumps({"fingerprint": header["fingerprint"], "runs": len(rows),
                      "result": str(out / "result.json"), "record": sink}, ensure_ascii=False))
    return 0


#: 두 회차의 채점이 같은지 볼 때 대조하는 행 필드.
COMPARE_KEYS = ("grade", "failure_class", "steps", "tool_calls")


def compare(path_a: str, path_b: str) -> int:
    a = json.loads(pathlib.Path(path_a).read_text(encoding="utf-8"))
    b = json.loads(pathlib.Path(path_b).read_text(encoding="utf-8"))
    fa, fb = a["header"]["fingerprint"], b["header"]["fingerprint"]

    def key(r):
        return (r["task"], r["arm"], r["host"], r["model"], r["run"])
    ra, rb = {key(r): r for r in a["rows"]}, {key(r): r for r in b["rows"]}
    diffs = []
    for k in sorted(set(ra) | set(rb)):
        x, y = ra.get(k), rb.get(k)
        if x is None or y is None:
            diffs.append({"run": "|".join(map(str, k)), "missing_in": "a" if x is None else "b"})
            continue
        d = [f for f in COMPARE_KEYS if x.get(f) != y.get(f)]
        if d:
            diffs.append({"run": "|".join(map(str, k)), "fields": d})
    same_trace = sum(1 for k in set(ra) & set(rb) if ra[k]["trace_sha256"] == rb[k]["trace_sha256"])
    out = {"fingerprint_equal": fa == fb, "fingerprint": [fa, fb], "runs": [len(ra), len(rb)],
           "score_diffs": len(diffs), "diffs": diffs[:20],
           "trace_bytes_equal": f"{same_trace}/{len(set(ra) & set(rb))}",
           "commit": [a["header"].get("commit"), b["header"].get("commit")]}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if fa != fb:
        return 1
    return 3 if diffs else 0


def check(path: str, disjoint_from: str | None = None) -> int:
    ts = tasks_mod.load(path)
    fam: dict[str, int] = {}
    for t in ts.tasks:
        fam[t.family] = fam.get(t.family, 0) + 1
    out = {"path": path, "sha256": ts.sha256, "split": ts.split, "tasks": len(ts.tasks),
           "families": fam, "store": ts.store}
    if disjoint_from:
        out["overlap"] = tasks_mod.overlap(ts, tasks_mod.load(disjoint_from))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 1 if out.get("overlap") else 0


def env_report() -> dict:
    """실제 모델 키가 있는가(값은 보지 않는다)."""
    return models.configured_keys()


__all__ = ["run", "compare", "check", "Refused", "fingerprint", "env_report"]
