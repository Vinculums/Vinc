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
import os
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


def main(argv: list[str] | None = None) -> int:
    # Windows 콘솔/파이프 기본 인코딩(cp949)에서 비ASCII 직렬화(ensure_ascii=False,
    # MCP stdio 포함)가 UnicodeEncodeError 로 죽는 문제 — MCP 규약(UTF-8)에 맞춰
    # 표준 스트림을 UTF-8 로 재구성한다(재구성 불가 스트림은 그대로 둠).
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass
    if hasattr(sys.stdin, "reconfigure"):
        try:
            sys.stdin.reconfigure(encoding="utf-8")
        except Exception:
            pass

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
    sh = sub.add_parser("share", help="build a share pack — hand your data to someone else (D19)")
    sh.add_argument("--owner", required=True, help="origin owner scope (whose data to pack)")
    sh.add_argument("--domain", default=None, help="scope: one dataset partition")
    sh.add_argument("--tags", default=None, help="scope: comma-separated tag filter (any-overlap)")
    sh.add_argument("--seed", default=None, help="scope: pack only the seed's neighborhood (Atlas L2)")
    sh.add_argument("--depth", type=int, default=2, help="seed neighborhood depth 1–4 (default 2)")
    sh.add_argument("--no-records", action="store_true", help="concepts/edges only (skip Episode records)")
    sh.add_argument("--out", required=True, help="pack file to write (JSON)")

    im = sub.add_parser("import", help="import a share pack under YOUR owner scope (idempotent MERGE)")
    im.add_argument("--pack", required=True, help="pack file (JSON) to import")
    im.add_argument("--owner", required=True, help="recipient owner scope to stamp")

    sv = sub.add_parser("serve", help="run the loopback sidecar HTTP server (P2)")
    sv.add_argument("--host", default="127.0.0.1", help="bind host (loopback only, §3-1)")
    sv.add_argument("--port", type=int, default=0, help="bind port (0 = ephemeral)")
    sv.add_argument("--parent-pid", type=int, default=None,
                    help="watch this PID; clean-shutdown when it exits (shell heartbeat, E6)")
    sv.add_argument("--token", default=None, help="session token (default: generate)")
    sv.add_argument("--profile", default=None, help="domain profile.yml path (backup 동봉)")
    sv.add_argument("--data", default=None,
                    help="data home (default: MEMORY_DATA_DIR, else ~/.vinc — D43)")
    sv.add_argument("--packs", default=None, help="exported packs dir (backup 동봉)")

    bk = sub.add_parser("backup", help="consistent snapshot + packs/profile archive (R9)")
    bk.add_argument("--out", required=True, help="archive output dir")
    bk.add_argument("--data", default=None,
                    help="data home to back up (default: MEMORY_DATA_DIR or ~/.vinc/db)")
    bk.add_argument("--packs", default=None, help="exported packs dir to include")
    bk.add_argument("--profile", default=None, help="domain profile.yml to include")

    ob = sub.add_parser("onboard", help="create the local credential (argon2id passphrase, F4)")
    ob.add_argument("--profile", required=True, help="credential file path (auth.json)")
    ob.add_argument("--passphrase", default=None,
                    help="passphrase (omit to read from stdin/getpass — non-echoing)")
    ob.add_argument("--force", action="store_true", help="overwrite an existing credential")

    # ── MCP 어댑터(D11 · P5 선행) — stdio/HTTP 전송 + 클라이언트 등록 ────────
    mc = sub.add_parser("mcp", help="connect Claude to this store (MCP)")
    msub = mc.add_subparsers(dest="mcpcmd", required=True)
    for name, hint in (("serve", "stdio transport — for Claude Code/Desktop"),
                       ("serve-http", "Streamable HTTP transport — remote profile (D36)")):
        mp = msub.add_parser(name, help=hint)
        mp.add_argument("--base", default=None, help="sidecar base URL (default 127.0.0.1:8787)")
        mp.add_argument("--token", default=None, help="sidecar session token")
        mp.add_argument("--owner", default=None, help="default owner scope (default user:local)")
        mp.add_argument("--no-spawn", action="store_true",
                        help="fail instead of starting a sidecar when none is running")
        if name == "serve-http":
            mp.add_argument("--host", default="127.0.0.1", help="bind host (loopback only — F4)")
            mp.add_argument("--port", type=int, default=8790)
            mp.add_argument("--auth-token", default=None,
                            help="static bearer token (default: env VINC_MCP_TOKEN)")
            mp.add_argument("--oauth", action="store_true",
                            help="enable the OAuth gate (admin key from env VINC_OAUTH_ADMIN_KEY)")
            mp.add_argument("--issuer", default=None,
                            help="public issuer URL for OAuth metadata (e.g. https://mcp.example.com)")
    mi = msub.add_parser("install", help="register this server with a local Claude client")
    mi.add_argument("--client", choices=["code", "desktop"], required=True)
    mi.add_argument("--desktop-config", default=None,
                    help="override claude_desktop_config.json path (tests/portable installs)")

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
        return _emit(act.activate(args.query, owner=args.owner, domain=args.domain, tenant=args.tenant,
                                  workspace=args.workspace, tags=_csv(args.tags),
                                  rank=args.rank, limit=args.limit, include_model=not args.no_model,
                                  generated_at=args.now, out_dir=args.out))
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
    if args.cmd == "share":
        from . import share
        pack = share.build_share_pack(owner=args.owner, domain=args.domain,
                                      tags=_csv(args.tags), seed=args.seed, depth=args.depth,
                                      include_records=not args.no_records)
        if pack.get("executed"):
            from pathlib import Path
            Path(args.out).write_text(
                json.dumps({k: v for k, v in pack.items() if k != "executed"},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            return _emit({"out": args.out, "manifest": pack["manifest"]})
        return _emit(pack)
    if args.cmd == "import":
        from . import share
        with open(args.pack, encoding="utf-8") as f:
            pack = json.load(f)
        return _emit(share.import_share_pack(pack, owner=args.owner))
    if args.cmd == "serve":
        from . import server
        from store.config import load_config, resolve_data_dir
        # D43 — 설치형 기본 데이터 홈: --data > MEMORY_DATA_DIR > ~/.vinc
        data_dir = resolve_data_dir(args.data)
        packs = args.packs
        if packs is None and data_dir.endswith(os.sep.join((".vinc", "db"))):
            packs = os.path.join(os.path.dirname(data_dir), "packs")
        server.serve(load_config({"data_dir": data_dir}),
                     host=args.host, port=args.port, parent_pid=args.parent_pid,
                     token=args.token, packs_dir=packs, profile_path=args.profile)
        return 0
    if args.cmd == "backup":
        # 단발(오프라인) 백업: 서버 없이 단일 스레드(동시 쓰기 없음 — 자연 정지).
        # ⚠ 서버 기동 중에는 kuzu 배타 락으로 열리지 않는다 — 서비스가 떠 있으면
        # HTTP `POST /backup`(서버 프로세스 내부 스냅샷)을 써야 무중단이다(deploy/
        # vinc-backup.sh). 이 CLI 경로는 서비스 정지 시의 오프라인 백업용.
        # D43 데이터 홈 해석(serve 와 동일): --data > MEMORY_DATA_DIR > ~/.vinc/db.
        from .backup import make_backup
        from store.config import load_config, resolve_data_dir
        from store.backend import open_store
        store = open_store(load_config({"data_dir": resolve_data_dir(args.data, create=False)}))
        if store is None:
            return _emit({"error": "no reachable store (드라이버 미설치/락) — 백업 불가"})
        try:
            return _emit(make_backup(store, args.out, packs_dir=args.packs,
                                     profile_path=args.profile))
        finally:
            store.close()
    if args.cmd == "mcp":
        if args.mcpcmd == "install":
            from vinc_mcp.install import install
            return _emit(install(args.client, desktop_path=args.desktop_config))
        from vinc_mcp.bridge import Bridge
        bridge = Bridge(base=args.base, token=args.token, owner=args.owner)
        bridge.ensure(spawn=not args.no_spawn)
        if args.mcpcmd == "serve":
            from vinc_mcp.stdio import serve as serve_stdio
            return serve_stdio(bridge)
        if args.mcpcmd == "serve-http":
            from vinc_mcp.http_server import serve_http
            from vinc_mcp.oauth import OAuthGate
            static = args.auth_token or os.environ.get("VINC_MCP_TOKEN") or None
            gate = None
            if args.oauth:
                from vinc_mcp.store import Store, resolve_state_dir
                issuer = args.issuer or f"http://{args.host}:{args.port}"
                # 상태는 디스크 SQLite 로 영속(B-A) — 재기동에도 커넥터 유지.
                # 경로: env VINC_MCP_STATE_DIR(프로덕션 = /var/lib/vinc-mcp) > ~/.vinc/mcp.
                store = Store(os.path.join(resolve_state_dir(), "oauth.db"))
                # 관리 키는 서버 env 로만(D36 — 번들·레포 반입 금지).
                gate = OAuthGate(issuer, os.environ.get("VINC_OAUTH_ADMIN_KEY"), store=store)
            # 회원 API 키(vinc_sk_*) 검증(B-B1) — VINC_MCP_ED25519_SEED 설정 시 활성.
            from vinc_mcp import entitlement
            vk = entitlement.verify_key if entitlement.enabled() else None
            # 티어 쿼터(B-D2) — 인증된 회원(owner) 의 tools/call 만 소비. 미설정 시
            # 무제한(로컬·파일럿 프로파일 무영향). free 초과 → LOCK_HOURS 잠금.
            quota_cfg = None
            if os.environ.get("VINC_QUOTA") == "on":
                quota_cfg = {
                    "free_limit": int(os.environ.get("VINC_FREE_DAILY_LIMIT", "15")),
                    "pro_limit": int(os.environ.get("VINC_PRO_DAILY_LIMIT", "2000")),
                    "window_s": float(os.environ.get("VINC_QUOTA_WINDOW_HOURS", "24")) * 3600,
                    "lock_s": float(os.environ.get("VINC_QUOTA_LOCK_HOURS", "12")) * 3600,
                }
            # 회원 승인 흐름(B-B3) — 우리 서명키 + 계정 공개키 핀 + 공홈 동의 URL.
            # 셋 다 있어야 활성(없으면 관리 키 경로만). 공개키만 교환 → B2 준수.
            from vinc_mcp import edverify as _ev
            seed_raw = os.environ.get("VINC_MCP_ED25519_SEED")
            acct_raw = os.environ.get("VINC_ACCOUNT_PUBLIC_KEY")
            return serve_http(
                bridge, host=args.host, port=args.port,
                static_token=static, oauth=gate, verify_key=vk, quota_config=quota_cfg,
                mcp_seed=_ev.load_seed(seed_raw) if seed_raw else None,
                account_public=_ev.load_public(acct_raw) if acct_raw else None,
                connect_url=os.environ.get("VINC_CONNECT_URL") or "https://vincs.app/connect")
        return 2
    if args.cmd == "onboard":
        from . import auth
        passphrase = args.passphrase
        if passphrase is None:
            import getpass
            passphrase = getpass.getpass("passphrase: ")
        return _emit(auth.create_profile(passphrase, path=args.profile, force=args.force))
    return 2


if __name__ == "__main__":
    sys.exit(main())
