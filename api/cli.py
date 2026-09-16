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


#: `--owner` 가 무슨 값인지 아무것도 알려 주지 않아서, 사람 이름·계정 이름을 넣어
#: 보고 전부 0건을 받는 일이 있었다(2026-09-03). 필수 인자인데 값의 모양을 모르면
#: "스토어가 비었다"로 오진하게 된다 — 실제로 그렇게 읽혔다. 그래서 값을 말한다.
#:
#: 응답에 실재 owner 목록을 붙이는 쪽을 먼저 해 봤는데, 그러면 **CLI 응답만** 달라져
#: HTTP==CLI 등가(`tests/test_server.py` EquivalenceTests)가 깨진다. 테스트된 불변식을
#: 편의 메시지와 맞바꾸지 않는다.
_OWNER_HELP = ("owner_id — 데스크탑 앱이 쓰는 값은 `user:local` 이고, 계정을 연결하면 "
               "`user:<uuid>` 가 섞인다. 사람 이름·계정 이름이 아니다(넣으면 조용히 "
               "0건). 이 스토어에 무엇이 있는지는 `domains`(--owner 생략 가능)로 본다")


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

    ap = argparse.ArgumentParser(description="Vinc query API")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("recall", help="recall a learner's episodes (owner + open domain scope)")
    r.add_argument("--query", default="")
    r.add_argument("--owner", default=None, help=_OWNER_HELP)
    r.add_argument("--domain", default=None, help="learning domain (free/user-defined: physics, math, art, …)")
    r.add_argument("--tenant", default=None, help="tenant_id scope (governance boundary)")
    r.add_argument("--workspace", default=None)
    r.add_argument("--rank", choices=["recency", "preference"], default="recency",
                   help="recency (default) or owner preference-aligned ranking")
    r.add_argument("--tags", default=None, help="comma-separated tags to boost (rank=preference)")
    r.add_argument("--limit", type=int, default=10)

    dm = sub.add_parser("domains", help="list a learner's domains (discovered from data, not a fixed list)")
    dm.add_argument("--owner", default=None, help="owner_id scope (omit for all learners)")

    # ⚠ **평시에는 부를 일이 없다** — 파생은 부팅 데이터 스텝이 하고 이후는 쓰기 경로가
    # 유지한다([[vinc.spec.dataplane §7]]). 이 명령의 존재 이유는 `--rename` 뿐이다:
    # 평면 도메인을 계층 아래로 옮기는 것은 사람의 결정이라 부팅이 대신하면 안 된다.
    # ⚠ 스토어가 다른 프로세스에 잠겨 있으면 안 된다(사이드카·앱 실행 중 금지).
    dh_ = sub.add_parser("domain-backfill",
                         help="move topics under a parent and re-derive the hierarchy "
                              "(--rename design=vinc/design). Store must not be locked "
                              "by a running sidecar or app.")
    dh_.add_argument("--owner", default=None, help="owner_id scope (omit for every owner)")
    dh_.add_argument("--rename", default=None, metavar="OLD=NEW",
                     action="append",
                     help="also move a topic, column and edges together "
                          "(e.g. --rename design=vinc/design). Repeatable.")
    dh_.add_argument("--dry-run", action="store_true",
                     help="report what would change without writing")

    lm = sub.add_parser("learner", help="a learner's method — how they learn (LearnerPattern / U-patterns)")
    lm.add_argument("--owner", required=True, help=_OWNER_HELP)

    gr = sub.add_parser("graph", help="learning knowledge-graph (concepts + relations) for a learner")
    gr.add_argument("--owner", required=True, help=_OWNER_HELP)
    gr.add_argument("--domain", default=None, help="narrow to one open/user-defined domain")

    # ⚠ 이 셋은 오래도록 `--owner` 가 **없었다**. SEC-1 이전 유물인데, PK
    # 네임스페이싱이 켜지면 그것이 무음 고장이 된다: `pkns.key(None, id)` 는 논리
    # 슬러그를 그대로 돌려주고, 그 값은 물리 키가 실린 행을 못 짚어 SET 이 0행을
    # 맞춘다(오류 없이 아무 일도 안 일어난다). 근거: [[vinc.plan.node-pk-namespacing]] §7
    pn = sub.add_parser("pin", help="pin/unpin an Episode (preference signal)")
    pn.add_argument("--episode", required=True)
    pn.add_argument("--owner", default=None, help="owner scope (SEC-1) — 네임스페이싱된 스토어에서는 필수")
    pn.add_argument("--off", action="store_true", help="unpin instead of pin")

    tg = sub.add_parser("tag", help="add owner tags to an Episode (preference signal)")
    tg.add_argument("--episode", required=True)
    tg.add_argument("--owner", default=None, help="owner scope (SEC-1) — 네임스페이싱된 스토어에서는 필수")
    tg.add_argument("--tags", required=True, help="comma-separated tags")

    tc = sub.add_parser("touch", help="record a recall hit (access-frequency signal)")
    tc.add_argument("--episode", required=True)
    tc.add_argument("--owner", default=None, help="owner scope (SEC-1) — 네임스페이싱된 스토어에서는 필수")
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
    # GM-14 — HTTP `/search` 와 1:1(게이트① 동등성): 커서 페이지네이션 + 요약·상세.
    s.add_argument("--cursor", default=None, help="continue from a previous page's next_cursor")
    s.add_argument("--detail", choices=["summary", "full"], default="summary",
                   help="summary = snippet rows (default); full = whole chunk text")

    b = sub.add_parser("brief", help="one question in, joined answer material out")
    b.add_argument("--query", required=True)
    b.add_argument("--owner", required=True)
    b.add_argument("--max-excerpts", type=int, default=3)

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
    sh.add_argument("--seed", default=None, help="scope: pack only the seed's neighborhood (View L2)")
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
                    help="db 파일 경로 (default: MEMORY_DATA_DIR, else ~/.vinc/db — D43). "
                         "디렉터리를 주면 열리지 않는다")
    sv.add_argument("--packs", default=None, help="exported packs dir (backup 동봉)")

    md = sub.add_parser("migrate-dim",
                        help="기동 전: 벡터 열의 차원이 이 배포의 모델과 다르면 재구축해 갈아 끼운다")
    md.add_argument("--data", default=None, help="db 파일 경로 (serve 와 같은 규칙)")

    bk = sub.add_parser("backup", help="consistent snapshot + packs/profile archive (R9)")
    bk.add_argument("--out", required=True, help="archive output dir")
    bk.add_argument("--data", default=None,
                    help="data home to back up (default: MEMORY_DATA_DIR or ~/.vinc/db)")
    bk.add_argument("--packs", default=None, help="exported packs dir to include")
    bk.add_argument("--profile", default=None, help="domain profile.yml to include")

    # ── 문서 정본(Chunk=SSoT, D45) — 파일 없는 doc 단위 CRUD ────────────────
    dc = sub.add_parser("doc", help="document SSoT — put/get/rm/ls raw text as chunks (D45)")
    dsub = dc.add_subparsers(dest="doccmd", required=True)
    dp = dsub.add_parser("put", help="ingest/update a document from raw text (stdin or --file)")
    dp.add_argument("--owner", required=True, help="owner_id — 정본은 무소유 불가(SEC-1)")
    dp.add_argument("--id", default=None, help="doc_id (omit on create — returned; pass to edit)")
    dp.add_argument("--title", default=None,
                    help="문서 제목. **생략하면 기존 제목을 보존한다**(빈 값으로 덮지 않는다). "
                         "지우려면 --title '' 를 명시할 것")
    dp.add_argument("--origin", default="", help="출처 표시(조인에 쓰지 않음 — display only)")
    dp.add_argument("--file", default=None,
                    help="read from file (.md/.txt/.rst/.pdf — 편의 입력, 정본은 스토어). "
                         "미지정 시 --title/--origin 은 파일명에서 채운다")
    dp.add_argument("--data", default=None, help="data home (default: MEMORY_DATA_DIR or ~/.vinc/db)")
    dg = dsub.add_parser("get", help="reconstruct a document from its chunks (+hash verify)")
    dg.add_argument("--id", required=True)
    dg.add_argument("--owner", default=None, help="owner guard (SEC-1)")
    dg.add_argument("--data", default=None)
    dr = dsub.add_parser("rm", help="delete a document (chunks + identity)")
    dr.add_argument("--id", required=True)
    dr.add_argument("--owner", default=None, help="owner guard (SEC-1)")
    dr.add_argument("--data", default=None)
    dl = dsub.add_parser("ls", help="list documents")
    dl.add_argument("--owner", default=None)
    dl.add_argument("--data", default=None)

    # ── GRAPH.md — 어시스턴트 쓰기 관례 ([[vinc.canon.graph-conventions]]) ──────────────────────
    gm = sub.add_parser("graph-md", help="assistant writing conventions — build/check GRAPH.md")
    gmsub = gm.add_subparsers(dest="gmcmd", required=True)
    gb = gmsub.add_parser("build", help="write the generated region from the store (prose untouched)")
    gb.add_argument("--owner", default=None, help="owner scope for the domain census")
    gb.add_argument("--file", default=None,
                    help="existing GRAPH.md to refresh in place; omit to print a new one")
    gb.add_argument("--out", default=None, help="write to this path instead of stdout")
    gb.add_argument("--doc", action="store_true",
                    help="read/write the conventions document in the store "
                         "(its doc_id is derived from the owner) instead of a file "
                         "— needs --owner")
    gb.add_argument("--lang", choices=("en", "ko"), default=None,
                    help="template language for a new file (default en; refresh keeps the file's own)")
    gb.add_argument("--data", default=None)
    gc = gmsub.add_parser("check", help="9 shape rules (0 pass · 1 fix · 2 block)")
    gc.add_argument("--file", default=None)
    gc.add_argument("--doc", action="store_true",
                    help="check the conventions document in the store instead of a file")
    gc.add_argument("--owner", default=None)
    gc.add_argument("--no-store", action="store_true",
                    help="shape only — skip the rules that compare against the store")
    gc.add_argument("--data", default=None)

    ob = sub.add_parser("onboard", help="create the local credential (argon2id passphrase, F4)")
    ob.add_argument("--profile", required=True, help="credential file path (auth.json)")
    ob.add_argument("--passphrase", default=None,
                    help="passphrase (omit to read from stdin/getpass — non-echoing)")
    ob.add_argument("--force", action="store_true", help="overwrite an existing credential")

    # ── MCP 어댑터(D11 · P5 선행) — stdio/HTTP 전송 + 클라이언트 등록 ────────
    mc = sub.add_parser("mcp", help="connect an AI assistant to this store (MCP)")
    msub = mc.add_subparsers(dest="mcpcmd", required=True)
    for name, hint in (("serve", "stdio transport — for local MCP hosts"),
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
    from vinc_mcp.install import CLIENTS as _MCP_CLIENTS
    for name, hint in (("install", "register this server with a local MCP host"),
                       ("uninstall", "remove this server from a local MCP host")):
        mx = msub.add_parser(name, help=hint)
        mx.add_argument("--client", choices=list(_MCP_CLIENTS), required=True)
        # --desktop-config 는 구 이름(하위호환) — 같은 dest 로 받는다.
        mx.add_argument("--config-path", "--desktop-config", dest="config_path",
                        default=None,
                        help="override the host config path (tests/portable installs)")
    msub.add_parser("manual",
                    help="print a paste-ready stdio entry for hosts without an installer")

    args = ap.parse_args(argv)

    if args.cmd == "recall":
        return _emit(q.recall(args.query, owner=args.owner, domain=args.domain, tenant=args.tenant,
                              workspace=args.workspace, tags=_csv(args.tags),
                              rank=args.rank, limit=args.limit))
    if args.cmd == "domains":
        return _emit(q.domains(owner=args.owner))
    if args.cmd == "domain-backfill":
        from engines import domain_hierarchy as _dh
        from store.backend import open_store as _open
        from store.config import load_config as _load
        pairs: dict[str, str] = {}
        for spec in (args.rename or []):
            if "=" not in spec:
                return _emit({"kind": "domain.backfill", "executed": False,
                              "error": f"--rename needs OLD=NEW, got {spec!r}"})
            old, new = spec.split("=", 1)
            pairs[old.strip()] = new.strip()
        _st = _open(_load())
        if _st is None:
            return _emit({"kind": "domain.backfill", "executed": False,
                          "error": "no reachable store"})
        try:
            if args.dry_run:
                # 쓰기 없이 무엇이 바뀔지만 센다. 이행 절차상 이 출력이 먼저 나오고
                # 사람이 보고 나서 실행하는 것이 기본이다.
                seen: dict[str, int] = {}
                for lbl, pk in _dh.DOMAIN_LABELS.items():
                    w = "WHERE n.domain IS NOT NULL AND n.domain <> ''"
                    p: dict = {}
                    if args.owner:
                        w += " AND n.owner_id = $owner"
                        p["owner"] = args.owner
                    for r in _st.run(f"MATCH (n:{lbl}) {w} RETURN n.domain AS d", p) or []:
                        cur = r.get("d") or ""
                        tgt = _dh.normalize(pairs.get(cur, cur))
                        seen[f"{cur} -> {tgt}"] = seen.get(f"{cur} -> {tgt}", 0) + 1
                return _emit({"kind": "domain.backfill", "executed": True,
                              "dry_run": True, "would_link": sum(seen.values()),
                              "moves": dict(sorted(seen.items()))})
            res = _dh.backfill(_st, owner=args.owner, rename=pairs)
            return _emit({"kind": "domain.backfill", "executed": True, **res})
        finally:
            _st.close()
    if args.cmd == "learner":
        return _emit(q.learner_model(owner=args.owner))
    if args.cmd == "graph":
        return _emit(q.graph(owner=args.owner, domain=args.domain))
    if args.cmd == "pin":
        return _emit(pref.pin(args.episode, pinned=not args.off, owner=args.owner))
    if args.cmd == "tag":
        return _emit(pref.tag(args.episode, _csv(args.tags) or [], owner=args.owner))
    if args.cmd == "touch":
        return _emit(pref.touch(args.episode, now=args.now, owner=args.owner))
    if args.cmd == "activate":
        return _emit(act.activate(args.query, owner=args.owner, domain=args.domain, tenant=args.tenant,
                                  workspace=args.workspace, tags=_csv(args.tags),
                                  rank=args.rank, limit=args.limit, include_model=not args.no_model,
                                  generated_at=args.now, out_dir=args.out))
    if args.cmd == "search":
        return _emit(q.search(args.query, top_k=args.top_k,
                              cursor=args.cursor, detail=args.detail))
    if args.cmd == "brief":
        return _emit(q.brief(args.query, owner=args.owner,
                             max_excerpts=args.max_excerpts))
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
            # D55 — 쓰기는 zip 컨테이너로 간다(읽기만 두 형식 호환). 원문을 실은
            # 팩이 통짜 JSON 이면 수 MB 로 붓고 문서 하나만 꺼낼 수도 없다.
            from . import packfile
            size = packfile.write_file(args.out, pack)
            return _emit({"out": args.out, "bytes": size, "manifest": pack["manifest"]})
        return _emit(pack)
    if args.cmd == "import":
        from . import share, packfile
        pack = packfile.read_file(args.pack)
        return _emit(share.import_share_pack(pack, owner=args.owner))
    if args.cmd == "migrate-dim":
        # 허브 엔트리포인트가 사이드카 **앞에서** 부른다. 복원은 serve 와 같은 함수·같은
        # 판정이라 여기서 했으면 serve 는 `present` 를 본다. 종료코드: 0 할 일 없음/끝남 ·
        # 1 복원 실패(기동 중단, serve 와 같은 fail-closed) · 3 재구축 실패(옛 스토어는
        # 그대로다 — 엔트리포인트가 워커를 꺼서 벡터를 지우지 않게 하고 기동은 계속한다).
        from store import migrate, persistence
        from store.config import load_config, resolve_data_dir
        data_dir = resolve_data_dir(args.data)
        _restore = persistence.restore_if_missing(data_dir)
        if _restore not in ("off", "present"):
            print(f"vinc-migrate-dim: store restore — {_restore}", file=sys.stderr, flush=True)
        _fatal = persistence.restore_state_is_fatal(_restore)
        if _fatal:
            print(f"vinc-migrate-dim: 기동 중단 — {_fatal}", file=sys.stderr, flush=True)
            return 1
        cfg = load_config({"data_dir": data_dir})
        model = (os.environ.get("VINC_EMBED_MODEL") or "").strip() or cfg.embedding_model
        try:
            out = migrate.migrate_dim_if_needed(data_dir, model=model,
                                                target_dim=cfg.embedding_dim)
        except Exception as exc:                     # noqa: BLE001 — 사유를 전부 내보낸다
            print(f"vinc-migrate-dim: ⚠ 재구축 실패 — {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
            return 3
        print(json.dumps(out, ensure_ascii=False), flush=True)
        return 0
    if args.cmd == "serve":
        from . import server
        from store.config import load_config, resolve_data_dir
        from store import persistence
        # D43 — 설치형 기본 데이터 홈: --data > MEMORY_DATA_DIR > ~/.vinc
        data_dir = resolve_data_dir(args.data)
        # 영속 스냅샷에서 복원(VINC_STORE_SNAPSHOT 설정 시에만). 스토어를 열기
        # **전에** 해야 한다 — 연 뒤에 파일을 갈아치우면 드라이버가 자기 밑의
        # 파일이 바뀐 것을 모른다. 미설정이면 'off' 로 즉시 빠진다.
        _restore = persistence.restore_if_missing(data_dir)
        if _restore not in ("off", "present"):
            print(f"vinc-sidecar: store restore — {_restore}", file=sys.stderr, flush=True)
        # ⚠ **복원 실패는 fail-closed 다.** 여기서 멈추면 사람이 보고 고치지만, 진행하면
        # 빈 DB 가 생기고 다음 스냅샷이 영속본을 덮는다:
        # [[record:a-failed-restore-must-stop-the-process]].
        _fatal = persistence.restore_state_is_fatal(_restore)
        if _fatal:
            print(f"vinc-sidecar: 기동 중단 — {_fatal}", file=sys.stderr, flush=True)
            return 1
        packs = args.packs
        if packs is None and data_dir.endswith(os.sep.join((".vinc", "db"))):
            packs = os.path.join(os.path.dirname(data_dir), "packs")
        server.serve(load_config({"data_dir": data_dir}),
                     host=args.host, port=args.port, parent_pid=args.parent_pid,
                     token=args.token, packs_dir=packs, profile_path=args.profile)
        return 0
    if args.cmd == "backup":
        # 단발(오프라인) 백업. ⚠ 서버 기동 중에는 임베디드 드라이버 배타 락으로 열리지
        # 않는다 — 떠 있으면 HTTP `POST /backup`(프로세스 내부 스냅샷)을 써야 한다.
        # 데이터 홈 해석은 serve 와 같다: --data > MEMORY_DATA_DIR > ~/.vinc/db.
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
    if args.cmd == "doc":
        # Chunk=SSoT(D45) doc CRUD — 파일 I/O 는 --file 편의 입력뿐, 정본은 스토어.
        from . import docstore
        from . import textextract
        from store.config import load_config, resolve_data_dir
        from store.backend import open_store
        cfg = load_config({"data_dir": resolve_data_dir(args.data)})
        store = open_store(cfg)
        if store is None:
            return _emit({"error": "no reachable store (드라이버 미설치/락)"})
        try:
            store.provision(dim=cfg.embedding_dim)   # 멱등 — v4 마이그레이션 포함
            if args.doccmd == "put":
                if args.file:
                    # 파일 읽기·디코드·형식 추출은 textextract 가 전담한다(put_doc 은
                    # 파일 I/O 없음이 계약). utf-8 하드코딩이던 시절엔 cp949 메모 하나가
                    # UnicodeDecodeError 로 적재를 죽였고 PDF 는 아예 길이 없었다.
                    got = textextract.read_file(args.file)
                    if not got.get("executed"):
                        return _emit(got)
                    text = got["text"]
                    if not args.origin:
                        args.origin = os.path.basename(args.file)
                    if not args.title:
                        args.title = os.path.splitext(os.path.basename(args.file))[0]
                else:
                    text = sys.stdin.read()
                return _emit(docstore.put_doc(store, owner=args.owner, text=text,
                                              doc_id=args.id, title=args.title,
                                              origin=args.origin))
            if args.doccmd == "get":
                return _emit(docstore.get_doc(store, args.id, owner=args.owner))
            if args.doccmd == "rm":
                return _emit(docstore.delete_doc(store, args.id, owner=args.owner))
            if args.doccmd == "ls":
                return _emit(docstore.list_docs(store, owner=args.owner))
        finally:
            store.close()
        return 2
    if args.cmd == "graph-md":
        # 관례 문서(GRAPH.md)의 생성·검증. 생성 구역만 스토어에서 오고, 산문은
        # 사람의 것이라 build 가 건드리지 않는다(graphmd.build 독스트링).
        from . import graphmd
        from datetime import date
        from store.config import load_config, resolve_data_dir
        from store.backend import open_store
        from store.theme_store import ThemeStore

        # --doc 는 스토어에서 읽고 스토어에 쓴다 — --no-store 와 함께 오면 --doc 이 이긴다
        # (읽을 곳 자체가 스토어라 끌 수가 없다).
        want_store = args.doc or not (args.gmcmd == "check" and args.no_store)
        store = themes = None
        cfg = None
        if want_store:
            cfg = load_config({"data_dir": resolve_data_dir(args.data, create=bool(args.doc))})
            store = open_store(cfg)
            if store is None:
                # 스토어가 없으면 build 는 할 일이 없고, check 는 형상 규칙만으로 뜻이 있다.
                if args.gmcmd == "build":
                    return _emit({"error": "no reachable store (드라이버 미설치/락) — "
                                           "생성 구역을 채울 수 없다"})
            else:
                store.provision(dim=cfg.embedding_dim)   # 멱등 — 없는 표를 읽으면 죽는다
                themes = ThemeStore(cfg.data_dir)
        try:
            if args.doc and not args.owner:
                return _emit({"error": "--doc needs --owner — 정본은 무소유로 두지 않는다(SEC-1)"})
            if args.gmcmd == "build":
                from . import docstore
                existing = None
                path = args.file
                if args.doc:
                    conv_id = docstore.resolve_conventions_doc_id(store, args.owner)
                    got = docstore.get_doc(store, conv_id, owner=args.owner)
                    existing = got.get("text") if got.get("executed") else None
                elif path and os.path.exists(path):
                    with open(path, encoding="utf-8") as fh:
                        existing = fh.read()
                res = graphmd.build(store, owner=args.owner, themes=themes,
                                    existing=existing, lang=args.lang,
                                    day=date.today().isoformat())
                if not res.get("executed"):
                    return _emit(res)
                slim = {k: v for k, v in res.items() if k != "text"}
                if args.doc:
                    # 스토어에 둘 때만 origin 표식을 단다 — 일반 자료와 구분하는 유일한 표시.
                    put = docstore.put_doc(store, owner=args.owner, text=res["text"],
                                           doc_id=conv_id,
                                           title="GRAPH.md", origin=graphmd.CONVENTIONS_ORIGIN)
                    return _emit(slim | {"doc": put})
                out = args.out or (path if existing is not None else None)
                if out:
                    with open(out, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(res["text"])
                    return _emit(slim | {"out": out})
                sys.stdout.write(res["text"])
                return 0
            if args.gmcmd == "check":
                if args.doc:
                    from . import docstore
                    got = docstore.get_doc(
                        store, docstore.resolve_conventions_doc_id(store, args.owner),
                        owner=args.owner)
                    if not got.get("executed"):
                        return _emit({"error": "no conventions document in this store "
                                               "(run `graph-md build --doc` first)"})
                    text = got.get("text") or ""
                elif args.file:
                    with open(args.file, encoding="utf-8") as fh:
                        text = fh.read()
                else:
                    return _emit({"error": "pass --file or --doc"})
                res = graphmd.check(text, store=store, themes=themes, owner=args.owner)
                _emit(res)
                return int(res["exit_code"])
        finally:
            if store is not None:
                store.close()
        return 2
    if args.cmd == "mcp":
        if args.mcpcmd == "install":
            from vinc_mcp.install import install
            return _emit(install(args.client, config_path_override=args.config_path))
        if args.mcpcmd == "uninstall":
            from vinc_mcp.install import uninstall
            return _emit(uninstall(args.client, config_path_override=args.config_path))
        if args.mcpcmd == "manual":
            from vinc_mcp.install import manual_entry
            return _emit(manual_entry())
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
                # 상태는 디스크 SQLite 로 영속(재기동에도 커넥터 유지). 작업본은
                # **로컬이어야 하고**(SMB 위 SQLite 는 `database is locked`), 영속은
                # VINC_MCP_STATE_SNAPSHOT 파일 복사다.
                # ⚠ **fail-soft** — 못 열면 인메모리로 강등해 살아서 답한다(재시작 시
                # 재인가). 죽이면 허브 전체가 404 다:
                # [[record:mcp-state-store-is-fail-soft-and-must-be-local]].
                try:
                    store = Store(os.path.join(resolve_state_dir(), "oauth.db"))
                except Exception as exc:   # noqa: BLE001 — 강등이 요점
                    print(f"[mcp] 상태 저장소를 열지 못했다 — 인메모리로 강등한다: "
                          f"{type(exc).__name__}: {exc}", file=sys.stderr)
                    print("[mcp] 재시작하면 커넥터 재인가가 필요하다. "
                          "경로가 네트워크 마운트가 아닌지 확인할 것.", file=sys.stderr)
                    store = Store(":memory:", snapshot=None)
                st = store.health()
                print(f"[mcp] oauth state: {st['path']} · journal={st['journal_mode']} "
                      f"· snapshot={st['snapshot'] or '(none)'} [{st['snapshot_state']}]"
                      + (f" — {st['snapshot_error']}" if st.get("snapshot_error") else ""),
                      file=sys.stderr)
                # 관리 키는 서버 env 로만(D36 — 번들·레포 반입 금지).
                gate = OAuthGate(issuer, os.environ.get("VINC_OAUTH_ADMIN_KEY"), store=store)
            # 회원 API 키(vinc_sk_*) 검증(B-B1) — VINC_MCP_ED25519_SEED 설정 시 활성.
            from vinc_mcp import entitlement
            vk = entitlement.verify_key if entitlement.enabled() else None
            # OAuth 커넥터의 팀 주소 접근(#120). 같은 어서션 시드를 쓰므로 활성 조건도
            # 같다 — 키 검증이 되는 배포면 이것도 된다. 꺼지면 팀 주소의 OAuth 만
            # 닫히고(404) 키 경로와 개인 OAuth 는 그대로다.
            vm = entitlement.verify_member if entitlement.enabled() else None
            # 티어 쿼터(B-D2) — 인증된 회원(owner) 의 tools/call 만 소비. 미설정 시
            # 무제한(로컬·파일럿 프로파일 무영향). free 초과 → LOCK_HOURS 잠금.
            quota_cfg = None
            if os.environ.get("VINC_QUOTA") == "on":
                quota_cfg = {
                    # AP-1: 무료 한도를 Pro 급으로 올렸다 — 차별축이 호출 횟수에서
                    # **주제 개수**로 옮겨갔기 때문이다(VINC_FREE_MAX_DOMAINS).
                    # 기본값을 정책값으로 둬야 env 를 빠뜨렸을 때 구 정책(15회)이
                    # 조용히 살아나지 않는다.
                    "free_limit": int(os.environ.get("VINC_FREE_DAILY_LIMIT", "2000")),
                    "pro_limit": int(os.environ.get("VINC_PRO_DAILY_LIMIT", "2000")),
                    "window_s": float(os.environ.get("VINC_QUOTA_WINDOW_HOURS", "24")) * 3600,
                    "lock_s": float(os.environ.get("VINC_QUOTA_LOCK_HOURS", "12")) * 3600,
                    # 면제 owner(쉼표 구분, `user:<public_id>`): 도그푸딩 계정처럼 그래프를
                    # 대량으로 재는 계정이 하루 한도에 스스로 막히지 않게 한다. 면제는
                    # quota_check 를 통째로 건너뛰므로 이미 걸린 잠금도 보지 않는다.
                    # 값은 hub-deploy 가 리포 변수 VINC_QUOTA_EXEMPT_OWNERS 에서 싣는다.
                    "exempt_owners": frozenset(
                        o.strip() for o in
                        os.environ.get("VINC_QUOTA_EXEMPT_OWNERS", "").split(",") if o.strip()),
                }
            # 무료 등급 주제 개수 상한(AP-1 §3.4) — 미설정이면 기능 자체가 꺼진다.
            # 쿼터와 달리 별도 on 스위치를 두지 않는다: 값의 존재가 곧 스위치다.
            cap_raw = os.environ.get("VINC_FREE_MAX_DOMAINS")
            try:
                free_cap = int(cap_raw) if cap_raw else None
            except ValueError:
                free_cap = None
            # 회원 승인 흐름(B-B3) — 우리 서명키 + 계정 공개키 핀 + 공홈 동의 URL.
            # 셋 다 있어야 활성(없으면 관리 키 경로만). 공개키만 교환 → B2 준수.
            from vinc_mcp import edverify as _ev
            seed_raw = os.environ.get("VINC_MCP_ED25519_SEED")
            acct_raw = os.environ.get("VINC_ACCOUNT_PUBLIC_KEY")
            return serve_http(
                bridge, host=args.host, port=args.port,
                static_token=static, oauth=gate, verify_key=vk, verify_member=vm,
                quota_config=quota_cfg,
                mcp_seed=_ev.load_seed(seed_raw) if seed_raw else None,
                account_public=_ev.load_public(acct_raw) if acct_raw else None,
                connect_url=os.environ.get("VINC_CONNECT_URL") or "https://vincs.io/connect",
                free_max_domains=free_cap)
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
