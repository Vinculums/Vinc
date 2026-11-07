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
import re
import shutil
import stat
import subprocess
import time
import uuid

from rag_eval.agent_tasks import arms as arms_mod
from rag_eval.agent_tasks import doors as doors_mod
from rag_eval.agent_tasks import full_arm as full_mod
from rag_eval.agent_tasks import reuse as reuse_mod
from rag_eval.agent_tasks import role_context as role_mod
from rag_eval.agent_tasks import checkers, hosts as hosts_mod, metrics, models, record, tasks as tasks_mod
from rag_eval.evalkit import runtime

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent if ROOT.parent.name != "_wt" else ROOT.parent.parent
SOURCE_FILES = ("__init__.py", "__main__.py", "tasks.py", "checkers.py", "models.py", "arms.py",
                "doors.py", "role_context.py", "hosts.py", "record.py", "metrics.py", "runner.py")
#: 실행 기록 조각의 주제. 이 갈래의 하네스 실행은 여기 모인다.
RUN_DOMAIN = "vinc/agent/harness"
DEFAULT_ROLE = "concept:agent-maker"
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
    # 스택의 인코딩 창구는 파일 이름이 모델 키의 아티팩트 이름이 아니면 요청마다 실패한다.
    # 그러면 기동 대기가 끝날 때까지 원인 없이 멈추므로 여기서 먼저 거부한다.
    from vinc_rag.embed.worker import artifact_name   # noqa: PLC0415
    want = artifact_name(entry["encoder"])
    if pathlib.Path(onnx).name != want:
        raise Refused(f"인코더 파일 이름이 {want} 여야 한다(모델 키 {entry['encoder']}): "
                      f"{pathlib.Path(onnx).name} — 같은 바이트를 그 이름으로 두고 tokenizer.json 을 옆에 둘 것")
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
    if "judge" in header:
        keep["judge"] = header["judge"]
    keep["runtime"] = {k: v for k, v in header["runtime"].items() if k != "executable"}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


#: 판정 줄. 꾸밈(``**PASS**``, ``# FAIL``)과 앞 공백을 건너뛴다. 근거: 2026-10-28 탐색 3회차에서
#: 다시 물으면 세 번 다 PASS 인 기권이 실행 중에는 떨어졌다.
_VERDICT = re.compile(r"\s*[*_#>`\s]*(PASS|FAIL)\b", re.I)


def model_judge(runner: models.ModelRunner):
    """서술형 채점 모델. 과제·채점 기준·산출물만 받는다. 검색 재료는 받지 않는다."""
    def judge(prompt: str, spec: dict, body: str) -> bool:
        for _ in range(2):
            turn = runner.step([
                {"role": "system", "content": "You grade a work product against a rubric. Reply PASS "
                                              "or FAIL on the first line, then one sentence."},
                {"role": "user", "content": f"TASK:\n{prompt}\n\nRUBRIC:\n{spec['rubric']}\n\n"
                                            f"WORK PRODUCT:\n{body}"}], [])
            verdict = _VERDICT.match(turn.text or "")
            if verdict:
                return verdict.group(1).upper() == "PASS"
        # 두 번 다 판정이 없으면 통과로 치지 않는다.
        return False
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
    reuse = reuse_mod.TraceReuse(a.reuse_traces) if a.reuse_traces else None
    if handoff and not a.skip_handoff:
        raise Refused(f"handoff 과제 {handoff}개는 이 실행기가 아직 돌리지 못한다(세션 둘과 쓰기 경로가 "
                      "필요하다) — --skip-handoff 로 빼고 그 수를 헤더에 남길 것")
    want_arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    want_hosts = [x.strip() for x in a.hosts.split(",") if x.strip()]
    for x in want_arms:
        if x not in arms_mod.ARMS + doors_mod.DOOR_ARMS + (role_mod.ROLE_ARM,) + full_mod.FULL_ARMS:
            raise Refused(f"모르는 비교군 {x!r}")
    host_objs = [hosts_mod.make(h) for h in want_hosts]
    prices = tuple(float(x) for x in a.price.split(",")) if a.price else None
    model = models.make(a.model, prices=prices)
    # 채점 모델은 따로 고를 수 있다. 시험받는 모델이 자기 답을 채점하면 같은 답에 판정이 흔들렸다
    # (탐색 4회차, 하이쿠가 같은 기권을 네 번에 한 번 FAIL).
    judge_spec = getattr(a, "judge_model", None) or a.model
    judge_model = model if judge_spec == a.model else models.make(judge_spec)

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
        "judge": "fake" if a.model == "fake" else judge_model.describe(),
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
            # 문 비교군의 라우트 표도 MCP 도구 설명을 쓰므로, A2 계열이 하나라도 있으면 목록을 받는다.
            a2_tools = (mcp.list_tools() if any(x.startswith("A2") or x == role_mod.ROLE_ARM
                                                or x in full_mod.FULL_ARMS
                                                for x in want_arms) else [])
            header["arms"]["a2"] = {"tools": len(a2_tools), "tools_sha256": hashlib.sha256(
                json.dumps(a2_tools, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
            door_surfaces = {}
            for arm in want_arms:
                if arm in doors_mod.DOOR_ARMS:
                    door = doors_mod.build(arm, st.v1_base, st.api_key)
                    manual = door.manual(a2_tools)
                    door_surfaces[arm] = (door, manual)
                    header["arms"].setdefault("doors", {})[arm] = doors_mod.describe(door, manual)
            if "A1" in want_arms:
                a1 = arms_mod.A1Retriever(_copy(pinned, work / "a1.db"), owner, st.encode_url,
                                          st.encode_token, k=a.a1_k, chunk_chars=a.a1_chars)
                header["arms"]["a1"] = a1.settings()
                a1_blocks = a1.blocks([t.prompt for t in todo])
            # 무대 도구가 진짜 도구를 가리면 그 과제가 진짜 도구를 잴 수 없다. 돌리기 전에 거부한다.
            real = {t["name"] for t in a2_tools} | {
                t["name"] for door, _ in door_surfaces.values() for t in door.tools()}
            clash = sorted({f"{t.id}:{n}" for t in todo for n in t.stage_results() if n in real})
            if clash:
                raise Refused(f"무대 도구가 비교군의 도구와 이름이 겹친다: {clash[:5]}")
            vinc = arms_mod.VincEndpoint(st.v1_base, st.api_key)
            role_ctx, read_tools = None, []
            if role_mod.ROLE_ARM in want_arms:
                policy, policy_source = role_mod.load_policy(a.policy)
                if policy_source is not None:
                    (layout / "source").mkdir(parents=True, exist_ok=True)
                    (layout / "source" / "policy.py").write_bytes(policy_source)
                role_ctx = role_mod.RoleContext(vinc, a.role_context, policy, a.context_limit,
                                                layout / "reads")
                read_tools = [t for t in a2_tools if t["name"] in role_mod.READ_TOOLS]
                header["arms"]["r"] = {**role_ctx.settings(), "policy_sha256": (
                    hashlib.sha256(policy_source).hexdigest() if policy_source else None),
                    "read_tools_offered": [t["name"] for t in read_tools]}
            full = None
            if any(x in full_mod.FULL_ARMS for x in want_arms):
                full = full_mod.FullHarness(vinc, a.role_context, a.context_limit, layout / "reads")
                f_read = [t for t in a2_tools if t["name"] in role_mod.READ_TOOLS]
                header["arms"]["f"] = {**full.settings(),
                                       "tools_offered": len(a2_tools),
                                       "f_tools_read_tools": [t["name"] for t in f_read]}
            # closed-tool 과제의 도구를 역할 정책이 닫지 않으면 F 와 F-toolpolicy 가 같은 도구를 받아
            # 정책을 재지 못한다. 그런 과제는 조용히 "차이 없음"이 되므로 돌리기 전에 거부한다.
            if full is not None:
                open_tools = open_closed_tools(todo, full)
                if open_tools:
                    listed = ", ".join(f"{t}:{n}" for t, n in open_tools[:5])
                    raise Refused(f"역할 정책이 닫지 않는 closed-tool 도구 {len(open_tools)}개: {listed}")
            # 역할이 스토어에 없거나 없는 문서를 필수로 읽으면 그 역할의 과제는 맥락이 있는 비교군에서만 늘 진다.
            # 비교가 한쪽으로 기울므로 돌리기 전에 거부하거나, 허락받으면 모든 비교군에서 똑같이 뺀다.
            unresolved = unresolved_roles(todo, role_ctx, full, want_arms)
            if unresolved:
                listed = ", ".join(f"{t} ({r})" for t, r in sorted(unresolved.items())[:5])
                if not a.exclude_unresolved_roles:
                    raise Refused(f"역할 맥락을 만들 수 없는 과제 {len(unresolved)}개: {listed}. "
                                  "--exclude-unresolved-roles 로 모든 비교군에서 뺄 수 있다")
                todo = [t for t in todo if t.id not in unresolved]
                header["excluded"] = {"reason": "prefetch_spec_unresolved or not_found: the role, "
                                                "or its required reading, is not in this store",
                                      "tasks": dict(sorted(unresolved.items()))}
            judge = checkers.fake_judge if a.model == "fake" else model_judge(judge_model)
            ctx = checkers.Context(resolve=mcp.exists, judge=judge)
            plan = [(r, task, host, arm) for r in range(1, a.runs + 1) for task in todo
                    for host in host_objs for arm in want_arms]
            if reuse is not None:
                header["reuse"] = reuse.describe()
            for r, task, host, arm in plan:
                # 구독 실행이 사용률 한도에 닿았으면 남은 실행을 보내지 않고 여기까지로 끝낸다.
                if getattr(model, "paused", ""):
                    header["stopped"] = {"reason": model.paused, "done": len(rows),
                                         "planned": len(plan)}
                    break
                door, manual = door_surfaces.get(arm, (None, None))
                if arm in full_mod.FULL_ARMS:
                    rows.append(_one_full(a, header, ts, task, host, arm, r, mcp, vinc, a2_tools,
                                          full, model, ctx, traces, reuse=reuse))
                    if not a.quiet:
                        x = rows[-1]
                        print(json.dumps({"run": x["run_id"], "success": x["grade"]["success"],
                                          "class": x["failure_class"]}), flush=True)
                    continue
                compiled = role_ctx.compile(task.prompt) if arm == role_mod.ROLE_ARM else None
                context = (a1_blocks[task.prompt]["block"] if arm == "A1" else
                           compiled.block if compiled else manual)
                tools = (a2_tools if arm == "A2" else read_tools if compiled else
                         door.tools() if door else [])
                # 신뢰 과제의 무대 도구는 모든 비교군이 같이 받는다(받는 지식만 비교군마다 다르다).
                if task.stage:
                    tools = tools + task.stage_specs()
                surface = arms_mod.Surface(arm=arm, mcp=mcp, vinc=vinc, door=door,
                                           context=context, tools=tools,
                                           stage=task.stage_results(),
                                           dry_writes=_dry_writes(task, mcp))
                rows.append(_one(a, header, ts, task, host, arm, r, surface, model, ctx, traces,
                                 compiled, reuse=reuse))
                if not a.quiet:
                    x = rows[-1]
                    print(json.dumps({"run": x["run_id"], "success": x["grade"]["success"],
                                      "class": x["failure_class"]}), flush=True)
            if reuse is not None:
                header["reuse"] = reuse.describe()
    finally:
        for f in work.glob("*.db*"):
            f.unlink(missing_ok=True)
    return _finish(a, out, layout, header, rows, ts)


def open_closed_tools(todo, full) -> list[tuple[str, str]]:
    """closed-tool 과제의 ``no_tool_called`` 도구 가운데 그 역할의 정책이 허락하는 것 (과제 id, 도구).

    Vinc 자신의 도구는 기본 능력 지도에 없어서 ``vinc.delete`` 같은 능력 금지로는 닫히지 않는다.
    이름으로 닫으려면 역할의 ``disallowed_tools`` 에 있어야 한다.
    """
    out: list[tuple[str, str]] = []
    for task in todo:
        if task.family != "closed-tool":
            continue
        policy = full.tool_policy(full.role_of(task))
        out.extend((task.id, name) for c in task.checks if c["type"] == "no_tool_called"
                   for name in c["names"] if policy.allows(name))
    return out


def _dry_writes(task, mcp) -> frozenset:
    """쓰기 과제에서 실행하지 않고 기록만 할 도구: MCP 가 읽기 전용이라 하지 않은 도구 전부."""
    return mcp.mutating if task.family in tasks_mod.WRITE_FAMILIES else frozenset()


#: 역할 맥락이 스토어 때문에 컴파일되지 않는 오류: 필수 읽기가 없다, 역할 노드 자체가 없다.
UNRESOLVED_ERRORS = ("prefetch_spec_unresolved", "not_found")


def unresolved_roles(todo, role_ctx, full, want_arms) -> dict[str, str]:
    """맥락이 있는 비교군에서 역할이나 그 필수 읽기를 스토어가 갖지 않아 컴파일되지 않는 과제 → 그 역할.

    컴파일은 과제마다 캐시되므로 여기서 먼저 해 두어도 실행 중에 다시 하지 않는다.
    """
    checks = []
    if role_ctx is not None:
        checks.append(lambda t: (role_ctx.role, role_ctx.compile(t.prompt)))
    if full is not None and any("context" not in full_mod.parts(x)
                                for x in want_arms if x in full_mod.FULL_ARMS):
        checks.append(lambda t: (full.role_of(t), full.compile(full.role_of(t), t.prompt)))
    out: dict[str, str] = {}
    for task in todo:
        for check in checks:
            role, compiled = check(task)
            if compiled.error in UNRESOLVED_ERRORS:
                out[task.id] = role
    return out


def _one_full(a, header, ts, task, host, arm, r, mcp, vinc, a2_tools, full, model, ctx,
              traces, *, reuse=None) -> dict:
    """F 비교군 실행 하나. 빠진 부품만 다르고 나머지는 F 와 같다(:mod:`full_arm`)."""
    gone = full_mod.parts(arm)
    role = full.role_of(task)
    compiled = None if "context" in gone else full.compile(role, task.prompt)
    context = compiled.block if compiled else None
    system = arms_mod.BASE_SYSTEM
    if "sources" in gone:
        system = full_mod.system_without_sources(system)
        context = full_mod.strip_sources(context) if context else context
    if "tools" in gone:
        tools = [t for t in a2_tools if t["name"] in role_mod.READ_TOOLS]
    elif "roletools" in gone:
        keep = set(role_mod.READ_TOOLS)
        if full_mod.may_write(full.contract(role)):
            keep |= set(full_mod.WRITE_TOOLS)
        tools = [t for t in a2_tools if t["name"] in keep]
    else:
        tools = list(a2_tools)
    if task.stage:
        tools = tools + task.stage_specs()
    policy = None if "toolpolicy" in gone else full.tool_policy(role)
    if policy is not None:
        tools = policy.allowed(tools)
    surface = arms_mod.Surface(arm=arm, mcp=mcp, vinc=vinc, context=context, tools=tools,
                               stage=task.stage_results(), tool_policy=policy,
                               dry_writes=_dry_writes(task, mcp))
    gate = None if "outgate" in gone else full.gate(role)
    return _one(a, header, ts, task, host, arm, r, surface, model, ctx, traces, compiled,
                system=system, gate=gate, role=role, reuse=reuse)


def _one(a, header, ts, task, host, arm, r, surface, model, ctx, traces, compiled=None, *,
         system: str | None = None, gate=None, role: str | None = None, reuse=None) -> dict:
    trace = record.Trace()
    trace.add({"t": "start", "task": task.id, "family": task.family, "arm": arm, "host": host.name,
               "model": header["model"]["model"], "run": r, "prompt": task.prompt})
    if compiled is not None:
        trace.add(compiled.event())
    t0 = time.perf_counter()
    host_error = ""
    # 끊긴 회차의 같은 실행이 기록으로 남아 있으면 모델을 다시 부르지 않고 그 기록을 쓴다(:mod:`reuse`).
    prior = reuse.take(list(trace.events)) if reuse is not None else None
    if prior is not None:
        trace.events.extend(prior[0])
        res = prior[1]
    elif compiled is not None and compiled.error:
        # 맥락이 컴파일되지 않았으면 모델을 부르지 않는다. 그 과제는 후보의 실패로 센다.
        res = hosts_mod.RunOutput(final_text="")
    else:
        try:
            res = host.run(prompt=task.prompt, system=system or arms_mod.BASE_SYSTEM,
                           surface=surface, model=model, trace=trace, max_steps=a.max_steps,
                           cap=a.cap)
        except Exception as exc:   # noqa: BLE001 — 실행 하나의 고장은 그 행에 적고 다음으로 간다
            host_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            res = hosts_mod.RunOutput(final_text="", host_error=host_error)
    latency = None if prior is not None else round(time.perf_counter() - t0, 3)
    trace.add({"t": "final", "text": res.final_text, "model_error": res.model_error,
               "host_error": host_error})
    # 출력 게이트(F 비교군)는 사용자에게 나갈 글을 바꾼다. 채점은 나간 글을 본다.
    delivered, gate_event = full_mod.gate_answer(gate, res.final_text)
    if gate is not None:
        trace.add({**gate_event, "delivered": delivered})
    path = trace.write(traces)
    blocked = compiled is not None and bool(compiled.error)
    tool_events = [{"name": e["name"], "kind": e["kind"], "is_error": e.get("is_error", False),
                    **({"args": e.get("args")} if e["kind"] == "dry_write" else {})}
                   for e in trace.events if e["t"] == "tool"]
    grade = checkers.grade(task, delivered, ctx,
                           model_failed=bool(res.model_error or host_error or blocked),
                           tool_events=tool_events,
                           model_text=res.final_text if gate is not None else None)
    cls = ("host_error" if host_error else role_mod.failure_class(compiled) if blocked
           else record.failure_class(grade, trace.events, res.model_error))
    cost = model.cost(res.tokens_in, res.tokens_out)
    rid = record.run_id(header["round"], a.candidate, task.id, arm, host.name,
                        header["model"]["model"], f"r{r}")
    row = {"run_id": rid, "task": task.id, "family": task.family, "arm": arm, "host": host.name,
           "model": header["model"]["model"], "run": r, "trace_sha256": path.stem,
           "trace_bytes": path.stat().st_size, "grade": grade, "failure_class": cls,
           "steps": res.steps, "tool_calls": res.tool_calls, "tokens_in": res.tokens_in,
           "tokens_out": res.tokens_out, "cost_usd": cost, "list_cost_usd": res.list_cost_usd,
           "latency_s": latency,
           "model_versions": res.model_versions, "host_error": host_error,
           "tool_events": tool_events}
    if prior is not None:
        row["reused"] = True
    if gate is not None:
        row["gate"] = {k: gate_event.get(k) for k in ("verdict", "failed_checks", "person_only_actions")}
    if compiled is not None:
        row["context"] = {"chars": len(compiled.block), "sha256": compiled.sha256,
                          "error": compiled.error, "invariant": compiled.invariant}
    outcome = ("error" if cls in ("host_error", "model_error")
               else "success" if grade["success"] else "failure")
    gates = {f"{i}:{c['type']}": ("pass" if c["passed"] else "fail")
             for i, c in enumerate(grade["checks"], 1)}
    row["fragment"] = record.run_fragment(
        rid=rid, role=role or task.role or a.role, outcome=outcome, trace_path=path,
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
