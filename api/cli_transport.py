"""Attach existing local CLI verbs to the sidecar holding their data file."""
from __future__ import annotations

import os
import sys
import time

from store.config import resolve_data_dir
from vinc_mcp.bridge import Bridge

from .cli_target import TOOL_CMDS


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def data_file(args) -> str:
    """이 명령이 프로세스 안에서 열 스토어 파일. 붙기 판정과 인프로세스 경로가 **같은 값**을
    써야 한다 — 둘이 어긋나면 앱이 떠 있어도 붙지 못하고 잠긴 스토어를 열다 실패한다.
    도구 동사·백업·graph-md·export 는 앱 기본 홈(`resolve_data_dir`)을, 나머지 옛 조회
    동사는 라이브러리 기본값(cwd/vinc_store)을 연다."""
    explicit = getattr(args, "data", None) or os.environ.get("MEMORY_DATA_DIR")
    if explicit:
        return resolve_data_dir(explicit, create=False)
    if args.cmd in TOOL_CMDS or args.cmd in {"backup", "graph-md", "export"}:
        return resolve_data_dir(create=False)
    return os.path.join(os.getcwd(), "vinc_store")


def attached_bridge(args) -> Bridge | None:
    probe = Bridge()
    session = probe._read_session()
    if not session or not session.get("token"):
        return None
    bridge = Bridge(base=f"http://127.0.0.1:{session['port']}", token=session["token"])
    if not bridge.health():
        return None
    try:
        config = bridge.request("GET", "/config", timeout=3, retries=0)
    except Exception:  # noqa: BLE001 — 설정을 못 읽는 사이드카에는 붙지 않는다
        return None
    if not config.get("executed") or not config.get("store_open"):
        return None
    served = config.get("data_file")
    if not served or not _same_file(str(served), data_file(args)):
        return None
    return bridge


def _route(args, stdin) -> tuple[str, str, dict | None, dict | None] | None:
    cmd = args.cmd
    if cmd == "learner":
        return "GET", "/learner", {"owner": args.owner}, None
    if cmd == "graph":
        return "GET", "/graph", {"owner": args.owner, "domain": args.domain}, None
    if cmd in ("pin", "tag", "touch"):
        body = {"episode": args.episode, "owner": args.owner}
        if cmd == "pin":
            body["off"] = args.off
        elif cmd == "tag":
            body["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
        else:
            body["now"] = args.now
        return "POST", f"/{cmd}", None, body
    if cmd == "activate":
        return "POST", "/activate", None, {
            "query": args.query, "owner": args.owner, "domain": args.domain,
            "tenant": args.tenant, "workspace": args.workspace, "rank": args.rank,
            "tags": [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None,
            "limit": args.limit, "now": args.now, "no_model": args.no_model, "out": args.out}
    if cmd == "share":
        return "GET", "/share", {"owner": args.owner, "domain": args.domain,
                                    "tags": args.tags, "seed": args.seed, "depth": args.depth,
                                    "no_records": "1" if args.no_records else ""}, None
    if cmd == "import":
        from . import packfile
        return "POST", "/ingest/pack", None, {"owner": args.owner,
                                                  "pack": packfile.read_file(args.pack)}
    if cmd == "backup":
        return "POST", "/backup", None, {"out": args.out}
    return None


def _finish_job(bridge: Bridge, result: dict) -> dict:
    job_id = result.get("job_id")
    if not job_id:
        return result
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        state = bridge.request("GET", f"/jobs/{job_id}", timeout=30)
        if state.get("status") == "done":
            return state.get("result") or {}
        if state.get("status") == "error":
            return {"error": state.get("error") or "sidecar job failed"}
        time.sleep(0.2)
    return {"error": "sidecar job timed out"}


def request_if_attached(args, *, stdin=None) -> dict | None:
    # 세션보다 라우트를 먼저 본다. 이 표 밖의 동사는 기존처럼 프로세스 안에서 실행한다.
    # 도구 동사(TOOL_CMDS)는 여기 오지 않는다 — 그쪽은 cli_tools 가 같은 판정으로 붙는다.
    if args.cmd not in {"learner", "graph", "pin", "tag", "touch", "activate", "share",
                        "import", "backup"}:
        return None
    if args.cmd == "backup" and (args.packs or args.profile):
        # /backup 은 사이드카에 설정된 첨부 파일을 쓴다. 명시한 덮어쓰기 인자를
        # 무시한 채 다른 파일을 담지 않도록 기존 인프로세스 경로를 따른다.
        return None
    bridge = attached_bridge(args)
    if bridge is None:
        return None
    route = _route(args, stdin or sys.stdin)
    if route is None:
        return None
    method, path, params, body = route
    result = _finish_job(bridge, bridge.request(method, path, params=params, body=body))
    if args.cmd == "share" and result.get("executed"):
        from . import packfile
        size = packfile.write_file(args.out, result)
        return {"out": args.out, "bytes": size, "manifest": result["manifest"]}
    return result
