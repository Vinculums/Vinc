# -*- coding: utf-8 -*-
"""CLI for the query API — the entry point CP / PM call.

    python -m api.cli recall  --query "billing" --workspace planning --limit 5
    python -m api.cli search  --query "환불 정책" --top-k 5
    python -m api.cli decide  --topic g5.b.billing --depth 2
    python -m api.cli plan    recall --query x        # print plan only (no db)

Output is a single JSON object on stdout.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import query as q
from . import preference as pref
from . import activate as act


def _csv(val: str | None) -> list[str] | None:
    """Parse a comma-separated CLI value into a list (None/'' -> None)."""
    if not val:
        return None
    return [s.strip() for s in val.split(",") if s.strip()]


def _emit(obj: dict) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return 0


def _read_passphrase(value: str | None) -> str:
    """패스프레이즈 소스 해소(F4 — 로컬만). ``None`` → getpass(비에코),
    ``'-'`` → stdin 1줄(셸/파이프 주입: argv 노출 회피), 그 외 → 리터럴."""
    if value == "-":
        return sys.stdin.readline().rstrip("\n")
    if value is None:
        import getpass
        return getpass.getpass("passphrase: ")
    return value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="weg-9000-memory query API")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("recall", help="recall a learner's episodes (owner + open domain scope)")
    r.add_argument("--query", default="")
    r.add_argument("--owner", default=None, help="owner_id scope (the individual learner — 'my memory')")
    r.add_argument("--domain", default=None, help="learning domain (free/user-defined: physics, math, art, …)")
    r.add_argument("--tenant", default=None, help="tenant_id scope (governance boundary)")
    r.add_argument("--workspace", default=None)
    r.add_argument("--rank", choices=["recency", "preference"], default="recency",
                   help="recency (default) or owner preference-aligned ranking")
    r.add_argument("--tags", default=None, help="comma-separated tags to boost (rank=preference)")
    r.add_argument("--limit", type=int, default=10)

    dm = sub.add_parser("domains", help="list a learner's domains (discovered from data, not a fixed list)")
    dm.add_argument("--owner", default=None, help="owner_id scope (omit for all learners)")

    lm = sub.add_parser("learner", help="a learner's method — how they learn (LearnerPattern / U-patterns)")
    lm.add_argument("--owner", required=True, help="owner_id (the individual learner)")

    gr = sub.add_parser("graph", help="learning knowledge-graph (concepts + relations) for a learner")
    gr.add_argument("--owner", required=True)
    gr.add_argument("--domain", default=None, help="narrow to one open/user-defined domain")

    pn = sub.add_parser("pin", help="pin/unpin an Episode (preference signal)")
    pn.add_argument("--episode", required=True)
    pn.add_argument("--off", action="store_true", help="unpin instead of pin")

    tg = sub.add_parser("tag", help="add owner tags to an Episode (preference signal)")
    tg.add_argument("--episode", required=True)
    tg.add_argument("--tags", required=True, help="comma-separated tags")

    tc = sub.add_parser("touch", help="record a recall hit (access-frequency signal)")
    tc.add_argument("--episode", required=True)
    tc.add_argument("--now", default=None, help="ISO-8601 timestamp for last_access (optional)")

    av = sub.add_parser("activate", help="export owned memory as a portable context pack (+ MCP manifest)")
    av.add_argument("--query", default="")
    av.add_argument("--owner", required=True, help="owner_id to export ('my memory')")
    av.add_argument("--domain", default=None, help="learning domain (free/user-defined) — omit to export all domains")
    av.add_argument("--tenant", default=None)
    av.add_argument("--workspace", default=None)
    av.add_argument("--rank", choices=["recency", "preference"], default="preference")
    av.add_argument("--tags", default=None, help="comma-separated tags to boost (rank=preference)")
    av.add_argument("--limit", type=int, default=20)
    av.add_argument("--now", default=None, help="ISO-8601 generated_at stamp (optional)")
    av.add_argument("--no-model", action="store_true", help="exclude the learning method (LearnerPattern) from the pack")
    av.add_argument("--theme", default=None, help="theme_id to attach its authored type vocabulary (proposed excluded)")
    av.add_argument("--out", default=None, help="dir to write pack.md/pack.json/mcp.json (else emit inline)")

    s = sub.add_parser("search", help="hybrid vector+BM25 search")
    s.add_argument("--query", required=True)
    s.add_argument("--top-k", type=int, default=5)

    d = sub.add_parser("decide", help="decision-support graph traversal")
    d.add_argument("--topic", required=True)
    d.add_argument("--depth", type=int, default=2)

    p = sub.add_parser("plan", help="print a query plan without executing")
    p.add_argument("plankind", choices=["recall", "search", "decide"])
    p.add_argument("--query", default="")
    p.add_argument("--topic", default="")

    # ── 사이드카(M4 · P2) — HTTP 레이어·백업·온보딩 ──────────────────────────
    sv = sub.add_parser("serve", help="run the loopback sidecar HTTP server (P2)")
    sv.add_argument("--host", default="127.0.0.1", help="bind host (loopback only, §3-1)")
    sv.add_argument("--port", type=int, default=0, help="bind port (0 = ephemeral)")
    sv.add_argument("--parent-pid", type=int, default=None,
                    help="watch this PID; clean-shutdown when it exits (shell heartbeat, E6)")
    sv.add_argument("--token", default=None, help="session token (default: generate)")
    sv.add_argument("--profile", default=None, help="domain profile.yml path (backup 동봉)")
    sv.add_argument("--packs", default=None, help="exported packs dir (backup 동봉)")

    bk = sub.add_parser("backup", help="consistent snapshot + packs/profile archive (R9)")
    bk.add_argument("--out", required=True, help="archive output dir")
    bk.add_argument("--packs", default=None, help="exported packs dir to include")
    bk.add_argument("--profile", default=None, help="domain profile.yml to include")

    ob = sub.add_parser("onboard", help="create the local credential (argon2id passphrase, F4)")
    ob.add_argument("--profile", required=True, help="credential file path (auth.json)")
    ob.add_argument("--passphrase", default=None,
                    help="passphrase ('-' = read one line from stdin; omit = getpass — non-echoing)")
    ob.add_argument("--force", action="store_true", help="overwrite an existing credential")

    vf = sub.add_parser("verify", help="verify a passphrase against the local credential (F4, no network)")
    vf.add_argument("--profile", required=True, help="credential file path (auth.json)")
    vf.add_argument("--passphrase", default=None,
                    help="passphrase ('-' = read one line from stdin; omit = getpass — non-echoing)")

    args = ap.parse_args(argv)

    if args.cmd == "recall":
        return _emit(q.recall(args.query, owner=args.owner, domain=args.domain, tenant=args.tenant,
                              workspace=args.workspace, tags=_csv(args.tags),
                              rank=args.rank, limit=args.limit))
    if args.cmd == "domains":
        return _emit(q.domains(owner=args.owner))
    if args.cmd == "learner":
        return _emit(q.learner_model(owner=args.owner))
    if args.cmd == "graph":
        return _emit(q.graph(owner=args.owner, domain=args.domain))
    if args.cmd == "pin":
        return _emit(pref.pin(args.episode, pinned=not args.off))
    if args.cmd == "tag":
        return _emit(pref.tag(args.episode, _csv(args.tags) or []))
    if args.cmd == "touch":
        return _emit(pref.touch(args.episode, now=args.now))
    if args.cmd == "activate":
        themes = None
        if args.theme:
            from store.theme_store import ThemeStore
            from store.config import load_config
            themes = ThemeStore(load_config().data_dir)
        return _emit(act.activate(args.query, owner=args.owner, domain=args.domain, tenant=args.tenant,
                                  workspace=args.workspace, tags=_csv(args.tags),
                                  rank=args.rank, limit=args.limit, include_model=not args.no_model,
                                  generated_at=args.now, out_dir=args.out,
                                  theme_id=args.theme, themes=themes))
    if args.cmd == "search":
        return _emit(q.search(args.query, top_k=args.top_k))
    if args.cmd == "decide":
        return _emit(q.decide(args.topic, depth=args.depth))
    if args.cmd == "plan":
        if args.plankind == "recall":
            plan = q.recall_plan(args.query)
        elif args.plankind == "search":
            # search 는 단일 Cypher 플랜이 아니라 store.fulltext_search +
            # (선택)vector_search + RRF 융합이다(§4-2) — 인스펙션용 기술 dict.
            return _emit({"kind": "search", "arms": ["fulltext(BM25)", "vector(주입 시)"],
                          "fusion": "RRF(k=60)", "note": "store 메서드 경유 — 단일 Cypher 아님"})
        else:
            plan = q.decide_plan(args.topic)
        return _emit(plan.to_dict())
    if args.cmd == "serve":
        from . import server
        server.serve(host=args.host, port=args.port, parent_pid=args.parent_pid,
                     token=args.token, packs_dir=args.packs, profile_path=args.profile)
        return 0
    if args.cmd == "backup":
        # 단발 백업: 서버 없이 단일 스레드(동시 쓰기 없음 — 자연 정지). 워커 잡
        # 경유가 아니어도 프로세스가 하나라 일관성 성립.
        from .backup import make_backup
        from store.config import load_config
        from store.backend import open_store
        store = open_store(load_config())
        if store is None:
            return _emit({"error": "no reachable store (드라이버 미설치) — 백업 불가"})
        try:
            return _emit(make_backup(store, args.out, packs_dir=args.packs,
                                     profile_path=args.profile))
        finally:
            store.close()
    if args.cmd == "onboard":
        from . import auth
        passphrase = _read_passphrase(args.passphrase)
        return _emit(auth.create_profile(passphrase, path=args.profile, force=args.force))
    if args.cmd == "verify":
        # F4 로컬 검증: 일치 시 종료코드 0, 불일치 시 1(셸/IPC 가 성공여부로 읽음).
        from . import auth
        passphrase = _read_passphrase(args.passphrase)
        ok = auth.verify_passphrase(passphrase, path=args.profile)
        _emit({"verified": bool(ok)})
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
