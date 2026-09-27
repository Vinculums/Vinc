"""Attach existing local CLI verbs to the sidecar holding their data file."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from store.config import resolve_data_dir
from vinc_mcp.bridge import Bridge


def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _data_file(args) -> str:
    # 기존 조회 동사는 cwd/vinc_store 를 기본으로 연다. 정확히 그 파일과 대조해야
    # 다른 그래프의 사이드카가 조용히 답하지 않는다.
    explicit = getattr(args, "data", None) or os.environ.get("MEMORY_DATA_DIR")
    if explicit:
        return resolve_data_dir(explicit, create=False)
    if args.cmd in {"doc", "backup", "graph-md", "export"}:
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
    config = bridge.request("GET", "/config", timeout=3, retries=0)
    if not config.get("executed") or not config.get("store_open"):
        return None
    served = config.get("data_file")
    if not served or not _same_file(str(served), _data_file(args)):
        return None
    return bridge


def _route(args, stdin) -> tuple[str, str, dict | None, dict | None] | None:
    cmd = args.cmd
    if cmd == "recall":
        return "GET", "/recall", {k: getattr(args, k) for k in
                                  ("query", "owner", "domain", "tenant", "workspace", "rank", "tags", "limit")}, None
    if cmd == "domains":
        return "GET", "/domains", {"owner": args.owner}, None
    if cmd == "learner":
        return "GET", "/learner", {"owner": args.owner}, None
    if cmd == "graph":
        return "GET", "/graph", {"owner": args.owner, "domain": args.domain}, None
    if cmd == "search":
        return "GET", "/search", {"query": args.query, "top_k": args.top_k,
                                    "cursor": args.cursor, "detail": args.detail}, None
    if cmd == "brief":
        return "GET", "/brief", {"query": args.query, "owner": args.owner,
                                   "max_excerpts": args.max_excerpts}, None
    if cmd == "decide":
        return "GET", "/decide", {"topic": args.topic, "depth": args.depth}, None
    if cmd in ("pin", "tag", "touch"):
        body = {"episode": args.episode, "owner": args.owner}
        if cmd == "pin":
            body["off"] = args.off
        elif cmd == "tag":
            body["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
        else:
            body["now"] = args.now
        return "POST", f"/{cmd}", None, body
    if cmd == "doc":
        common = {"id": getattr(args, "id", None), "owner": args.owner}
        if args.doccmd == "get":
            return "GET", "/doc", common, None
        if args.doccmd == "ls":
            return "GET", "/docs", {"owner": args.owner}, None
        if args.doccmd == "rm":
            return "POST", "/doc/delete", None, common
        if args.doccmd == "put":
            if args.file:
                from . import textextract
                got = textextract.read_file(args.file)
                if not got.get("executed"):
                    return "LOCAL", "", None, got
                content = got["text"]
                origin = args.origin or os.path.basename(args.file)
                title = args.title or Path(args.file).stem
            else:
                content, origin, title = stdin.read(), args.origin, args.title
            return "POST", "/doc/put", None, {**common, "text": content,
                                                  "origin": origin, "title": title}
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
    if args.cmd not in {"recall", "domains", "learner", "graph", "search", "brief", "decide",
                        "pin", "tag", "touch", "doc", "activate", "share", "import", "backup"}:
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
    if method == "LOCAL":
        return body
    result = _finish_job(bridge, bridge.request(method, path, params=params, body=body))
    if args.cmd == "share" and result.get("executed"):
        from . import packfile
        size = packfile.write_file(args.out, result)
        return {"out": args.out, "bytes": size, "manifest": result["manifest"]}
    return result
