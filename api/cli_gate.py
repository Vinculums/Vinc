"""`vinc gate check` — 결정 문을 터미널과 CI 에서 부른다. 모델은 없다.

바뀐 경로는 `git diff --name-only --no-renames <ref>...HEAD` 에서 읽고(이름이 바뀐 파일은 이전 경로와
새 경로가 둘 다 나온다), 리포가 없는 경로는 `repo:path` 로 한정해 `vinc_decide_paths` 에
`DECIDE_PATHS_MAX` 개씩 나눠 보낸 뒤 한 결과로 합친다. 같은 도구를 MCP·REST 와 함께 쓰므로
맞춤 규칙은 하나다(`api.decide_read.decide_paths`).

`--run-id` 를 주면 verdict 하나를 실행 기록 하나(`vinc_client.run.build_run_fragment`)로 남긴다.
계정 그래프에 쓰기 키가 있을 때만 쓴다. 읽기 전용 키(`vinc_ro_`)면 쓰지 않고 그렇다고 말한다.
근거: [[vinc.plan.agent-harness-work-order-2026-10-25 §2]] ·
[[decision:agent-run-record-keeps-the-raw-trace-as-a-hashed-file-2026-10-25]]
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess

from vinc_mcp import toolcall, tools

from .cli_output import usage_error
from .fragment import _split_subject

ROLE = "concept:agent-decision-gate"
#: 실행 기록의 주제. 하네스 줄기(D8)와 같은 자리다.
RUN_DOMAIN = "vinc/agent/harness"
#: 읽기 전용 키의 접두(`vinc_mcp.entitlement.KEY_PREFIXES` 의 둘째).
_READ_ONLY_PREFIX = "vinc_ro_"


def _git(args: list[str], cwd: str | None) -> str:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=60,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"git {' '.join(args)}: {exc}") from None
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise ValueError(f"git {' '.join(args)} failed: {detail[-1] if detail else done.returncode}")
    return done.stdout.decode("utf-8", errors="surrogateescape")


def diff_paths(ref: str, *, cwd: str | None = None) -> list[str]:
    """`ref` 와 HEAD 의 공통 조상 이후 HEAD 가 바꾼 파일들(PR 이 건드린 파일과 같다)."""
    out = _git(["diff", "--name-only", "--no-renames", "-z", f"{ref}...HEAD"], cwd)
    return [p for p in out.split("\0") if p]


def default_repo(*, cwd: str | None = None) -> str:
    """origin 원격의 리포 이름(소문자, `.git` 뺌). 원격이 없으면 작업 트리 폴더 이름."""
    try:
        url = _git(["remote", "get-url", "origin"], cwd).strip()
    except ValueError:
        url = ""
    if not url:
        url = _git(["rev-parse", "--show-toplevel"], cwd).strip()
    name = url.rstrip("/").replace("\\", "/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return name.removesuffix(".git").lower()


def qualify(path: str, repo: str) -> str:
    """이미 `repo:path` 면 그대로, 아니면 `repo:` 를 붙인다."""
    return path if _split_subject(path.strip())[0] else f"{repo}:{path}"


def merge(results: list[dict]) -> dict:
    """조각 결과들을 하나로. 셈은 더하고, 목록은 잇고, verdict 는 하나라도 걸리면 flag."""
    if not results:
        return {"kind": "decide_paths", "executed": True, "verdict": "pass", "paths": [],
                "path_count": 0, "flagged_paths": 0, "governing_count": 0,
                "retired_excluded": 0, "results_excluded": 0, "unqualified_paths": [],
                "note": "no touched paths"}
    bad = next((r for r in results if not isinstance(r, dict) or r.get("executed") is False
                or r.get("error")), None)
    if bad is not None:
        return bad
    first = results[0]
    out = {**first, "paths": [p for r in results for p in r.get("paths") or []],
           "unqualified_paths": [p for r in results for p in r.get("unqualified_paths") or []]}
    for key in ("path_count", "flagged_paths"):
        out[key] = sum(int(r.get(key) or 0) for r in results)
    out["governing_count"] = len({item["id"] for p in out["paths"]
                                  for item in p.get("governed_by") or []})
    if len(results) > 1:
        # 은퇴·결과 셈은 경로와 무관한 스토어 전수다. 조각마다 같은 값이므로 더하지 않는다.
        out["note"] = (f"sent in {len(results)} calls of at most {tools.DECIDE_PATHS_MAX} paths; "
                       "governing_count counts ids across every list shown, so a cut list "
                       "(truncated) can hide ids")
    out["verdict"] = "flag" if out["flagged_paths"] else "pass"
    return out


def markdown(result: dict) -> str:
    """잡 요약(GITHUB_STEP_SUMMARY)과 사람용 출력. 가부가 아니라 읽을 목록이다."""
    if not isinstance(result, dict):
        return str(result)
    if result.get("executed") is False or result.get("error"):
        err = result.get("error")
        message = err.get("message") if isinstance(err, dict) else err or result.get("note")
        return f"### Decision gate\n\nThe gate did not run: {message or 'unknown error'}.\n"
    paths = result.get("paths") or []
    flagged = [p for p in paths if p.get("count")]
    lines = ["### Decision gate", "",
             f"{len(flagged)} of {result.get('path_count', len(paths))} touched paths are governed "
             f"by {result.get('governing_count', 0)} recorded claims and decisions. Read them "
             "before merging; the gate does not decide whether this change breaks them.", ""]
    for p in flagged:
        lines.append(f"**`{p['path']}`** ({p['count']})")
        for item in p.get("governed_by") or []:
            how = (f"via `{item['via']}` ({item.get('relation', '')})" if item["match"] == "related"
                   else f"{item['match']} `{item.get('subject_path', '')}`")
            when = item.get("measured_at") or item.get("valid_from") or ""
            title = str(item.get("title") or "").replace("\n", " ")
            if len(title) > 160:
                title = title[:157] + "..."
            lines.append(f"- {item['kind']} `{item['id']}`: {title} ({how}"
                         + (f", {when}" if when else "") + ")")
        if p.get("truncated"):
            lines.append(f"- ... {p['count'] - len(p.get('governed_by') or [])} more")
        lines.append("")
    if not flagged:
        lines.append("No recorded claim or decision names these paths.")
        lines.append("")
    extra = []
    if result.get("retired_excluded"):
        extra.append(f"{result['retired_excluded']} retired claims left out")
    if result.get("unqualified_paths"):
        extra.append(f"{len(result['unqualified_paths'])} paths without a repository")
    if result.get("run_record"):
        rec = result["run_record"]
        extra.append("run recorded as `" + rec["episode_id"] + "`" if rec.get("recorded")
                     else "run not recorded: " + str(rec.get("reason") or ""))
    if extra:
        lines.append("_" + "; ".join(extra) + "._")
    return "\n".join(lines).rstrip() + "\n"


def _build_run_fragment():
    """실행 기록 도우미. 리포 체크아웃에서는 `vinc_client`, `vincs` 휠 안에서는 `vinc.sdk` 다."""
    try:
        from vinc_client.run import build_run_fragment
    except ImportError:
        try:
            from vinc.sdk.run import build_run_fragment  # type: ignore[no-redef]
        except ImportError:
            return None
    return build_run_fragment


def record(result: dict, *, run_id: str, target: str, space: str, call, trace: dict | None,
           task_ref: str | None, host: str | None) -> dict:
    """verdict 하나를 실행 기록 하나로. 쓰지 않을 때는 이유를 돌려준다(조용히 건너뛰지 않는다)."""
    if target != "account":
        return {"recorded": False, "reason": "run records go to the account graph; "
                                             "pass --target account"}
    from .remote_client import api_key
    key = api_key() or ""
    if not key or key.startswith(_READ_ONLY_PREFIX):
        return {"recorded": False, "reason": "the key is read-only, so the gate writes nothing"}
    build = _build_run_fragment()
    if build is None:
        return {"recorded": False, "reason": "vinc_client is not installed"}
    ok = result.get("executed") is not False and not result.get("error")
    cited = sorted({item["id"] for p in result.get("paths") or []
                    for item in p.get("governed_by") or []})
    fragment = build(
        run_id, role=ROLE, outcome="success" if ok else "error",
        title=(f"Decision gate run {run_id}: {result.get('verdict', 'error')}, "
               f"{result.get('flagged_paths', 0)} of {result.get('path_count', 0)} paths governed"),
        summary=(f"verdict {result.get('verdict', 'error')}; "
                 f"{result.get('governing_count', 0)} governing ids"),
        task_ref=task_ref, host=host, model_family="none", steps=1,
        tool_calls=max(1, -(-int(result.get("path_count") or 0) // tools.DECIDE_PATHS_MAX)),
        gate_results={"decision_gate": str(result.get("verdict") or "error")},
        cited_ids=cited[:200], trace=trace, domain=RUN_DOMAIN,
        date=_dt.date.today().isoformat())
    wrote = call("vinc_ingest", {"nodes": fragment["nodes"], "edges": fragment["edges"]})
    if not isinstance(wrote, dict) or wrote.get("error") or wrote.get("executed") is False:
        return {"recorded": False, "reason": "the write was refused",
                "detail": wrote.get("error") if isinstance(wrote, dict) else str(wrote)}
    return {"recorded": True, "episode_id": fragment["nodes"][0]["id"],
            "warnings": wrote.get("warnings") or []}


def _trace_ref(path: str) -> dict:
    import hashlib
    with open(path, "rb") as handle:
        data = handle.read()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "name": os.path.basename(path)}


def run(args, *, target: str, space: str) -> tuple[dict | None, int | None]:
    if (args.diff is None) == (args.paths is None):
        return None, usage_error("pass exactly one of --diff REF or --paths PATH...")
    try:
        raw = diff_paths(args.diff) if args.diff is not None else list(args.paths)
        repo = (args.repo or default_repo()).strip().lower() if any(
            not _split_subject(p)[0] for p in raw) else ""
    except ValueError as exc:
        return None, usage_error(str(exc))
    paths = list(dict.fromkeys(qualify(p, repo) for p in raw if p.strip()))
    owner = getattr(args, "owner", None)
    common = {"owner": owner} if owner else {}
    if args.member_limit is not None:
        common["member_limit"] = args.member_limit
    chunks = [paths[i:i + tools.DECIDE_PATHS_MAX]
              for i in range(0, len(paths), tools.DECIDE_PATHS_MAX)]
    for chunk in chunks:
        try:
            tools.validate_args("vinc_decide_paths", {**common, "paths": chunk})
        except tools.InvalidArgument as exc:
            return None, usage_error(str(exc))

    if target == "account":
        from .remote_client import RemoteFailure, call as remote

        def call(name, data):
            return remote(name, data, space=space)
        try:
            result = merge([call("vinc_decide_paths", {**common, "paths": c}) for c in chunks])
            return _finish(args, result, target=target, space=space, call=call), None
        except RemoteFailure as exc:
            return exc.result, exc.exit_code
        except ValueError as exc:
            return None, usage_error(str(exc))
    from .cli_tools import _bridge
    with _bridge(args) as bridge:
        def call(name, data):
            return toolcall.call(name, data, bridge)
        result = merge([call("vinc_decide_paths", {**common, "paths": c}) for c in chunks])
        return _finish(args, result, target=target, space=space, call=call), None


def _finish(args, result: dict, *, target: str, space: str, call) -> dict:
    trace = None
    if getattr(args, "trace", None):
        # 원본 기록은 결과 그대로다(요약을 넣지 않는다). 실행 기록에는 해시·크기·이름만 간다.
        with open(args.trace, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        trace = _trace_ref(args.trace)
    if getattr(args, "run_id", None):
        try:
            result = {**result, "run_record": record(
                result, run_id=args.run_id, target=target, space=space, call=call, trace=trace,
                task_ref=getattr(args, "task_ref", None), host=getattr(args, "host", None))}
        except ValueError as exc:
            result = {**result, "run_record": {"recorded": False, "reason": str(exc)}}
        except Exception as exc:   # noqa: BLE001 — 기록 실패가 verdict 를 지우지 않는다
            result = {**result, "run_record": {"recorded": False,
                                               "reason": f"{type(exc).__name__}: {exc}"}}
    if getattr(args, "summary", None):
        with open(args.summary, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown(result) + "\n")
    return result
