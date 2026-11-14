# -*- coding: utf-8 -*-
"""CLI for the query API — the entry point CP / PM call.

    python -m api.cli recall  --query "billing" --workspace planning --limit 5
    python -m api.cli search  --query "환불 정책" --top-k 5
    python -m api.cli decide  --topic g5.b.billing --depth 2
    python -m api.cli plan    recall --query x        # print plan only (no db)

Output is a single JSON object on stdout.
"""
from __future__ import annotations

import json
import os
import sys

from . import query as q
from . import preference as pref
from . import activate as act
from . import cli_output
# 인자 파서는 `api/cli_parser.py` 에 있다. 아래 이름은 같은 객체다(`main` 이 `_UsageError` 를 잡는다).
from .cli_parser import _Parser, _UsageError, build_parser  # noqa: F401  재수출
from store.querybase import split_csv as _csv


def _emit(obj: dict) -> int:
    unavailable = (isinstance(obj, dict) and
                   ("reachable store" in str(obj.get("error") or "") or
                    "reachable store" in str(obj.get("note") or "")))
    if unavailable:
        from store.embedded_backend import EmbeddedBackend
        reason = str(EmbeddedBackend.last_open_error or "")
        if "lock" in reason.lower() or "another process" in reason.lower():
            obj = {"kind": "error", "executed": False,
                   "error": {"code": "store_in_use", "message": "store is in use"}}
    return cli_output.emit(obj)


def _write_role_export(result: dict, out_dir: str) -> dict:
    """내보낸 파일을 DIR 아래에 쓴다. skill 은 `<이름>/SKILL.md` 로 써서 호스트의 스킬 폴더에 그대로 놓인다."""
    import base64
    from pathlib import Path
    rel = Path(result.get("path") or result["filename"])
    if rel.is_absolute() or ".." in rel.parts:
        return {"kind": "role_export", "executed": False, "error": "invalid export path"}
    target = Path(out_dir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (base64.b64decode(result["content"]) if result.get("encoding") == "base64"
            else str(result["content"]).encode("utf-8"))
    target.write_bytes(data)
    return {"kind": "role_export", "executed": True, "id": result.get("id"), "format": result.get("format"),
            "written": str(target), "bytes": len(data), "content_hash": result.get("content_hash"),
            "rev": result.get("rev"), "hash": result.get("hash")}


def _error_format(argv: list[str]) -> str | None:
    # 파서가 실패한 경우에도 루트·동사 뒤 출력 옵션을 읽는다.
    fmt = None
    for i, token in enumerate(argv):
        if token == "--":
            break
        if token == "--json":
            fmt = "json"
        elif token == "--format" and i + 1 < len(argv):
            if argv[i + 1] in ("human", "json", "ndjson"):
                fmt = argv[i + 1]
        elif token.startswith("--format="):
            if token.partition("=")[2] in ("human", "json", "ndjson"):
                fmt = token.partition("=")[2]
    return fmt


#: 동사 앞에 올 수 있는 루트 옵션 중 값을 받는 것.
_ROOT_VALUE_OPTIONS = frozenset({"--target", "--space", "--format"})


def _verb_index(argv: list[str]) -> int | None:
    """루트 옵션을 건너뛴 첫 위치 인자(동사)의 자리. 없으면 None."""
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _ROOT_VALUE_OPTIONS:
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        return i
    return None


def _expand_merge(argv: list[str]) -> list[str]:
    """`merge <keep> <drop>` 는 `merge nodes <keep> <drop>` 의 짧은 표기다. **동사 자리**에
    올 때만 바꾼다 — 인자 값의 `merge`(`search --query merge`)까지 바꾸면 그 명령이 깨진다."""
    at = _verb_index(argv)
    if at is not None and argv[at] == "merge" and at + 1 < len(argv) and argv[at + 1] not in {
            "nodes", "candidates", "reject", "rejections", "--help", "-h"}:
        return argv[:at + 1] + ["nodes"] + argv[at + 1:]
    return argv


def _main(argv: list[str] | None = None) -> int:
    argv = _expand_merge(list(sys.argv[1:] if argv is None else argv))
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

    ap = build_parser()
    args = ap.parse_args(argv)
    command = args.cmd
    for name in ("doccmd", "gmcmd", "mcpcmd", "nodecmd", "edgecmd", "mergecmd", "refscmd", "rolecmd",
                 "gatecmd", "shimcmd", "storagecmd"):
        if getattr(args, name, None):
            command += " " + getattr(args, name)
    cli_output.configure(args.console_format, command)
    from store import diagnostics
    diagnostics.configure(args.debug or os.environ.get("VINC_DEBUG") == "1")
    cli_output.chosen_format()  # 동사를 실행하기 전에 환경값을 검증한다.
    from . import cli_target

    if args.cmd == "refs":
        space = cli_target.resolve_space(args.space)
        from . import refs
        # 제품 동사의 기본은 **지금 디렉터리**다. 패키지 폴더를 기본으로 두면 다른 프로젝트에서
        # 부른 refresh 가 Vinc 리포의 매니페스트를 덮어쓴다.
        roots = args.roots or [os.getcwd()]
        manifest = args.manifest or os.path.join(roots[0], "scripts", "graph-refs.json")
        if any(not os.path.isdir(root) for root in roots):
            return cli_output.usage_error("--root must name an existing directory")
        if args.refscmd == "check":
            result = refs.offline_result(manifest=manifest, roots=roots)
            _emit(result)
            return result["status"]
        from . import remote_client
        from contextlib import redirect_stdout, redirect_stderr
        import io
        if not remote_client.api_key():
            result = {"error": {"code": "not_authenticated", "message":
                      "Set VINC_API_KEY to refresh graph citations.",
                      "request_id": "", "details": {}}}
            _emit(result)
            return 4
        def account_call(name, data):
            try:
                return remote_client.call(name, data, space=space)
            except remote_client.RemoteFailure as exc:
                if exc.result.get("error", {}).get("code") == "not_found":
                    return {"found": False}
                if exc.exit_code == 5:
                    # 일시 장애는 도구 오류 봉투로 돌려준다 — refs 의 재시도(TRIES)가 받는다.
                    return {"isError": True, "content": [{"type": "text", "text":
                            exc.result.get("error", {}).get("message", "unavailable")}]}
                raise
        try:
            progress = io.StringIO()
            with redirect_stdout(progress), redirect_stderr(progress):
                status = refs.refresh(
                    manifest=manifest, root=roots[0],
                    scan=lambda: refs.scan_roots(roots),
                    fetch=lambda: refs.fetch_docs(call=account_call),
                    verify=lambda ids, **kw: refs.verify_nodes(ids, call=account_call, **kw))
            if cli_output.chosen_format() == "human" and status == 0:
                sys.stderr.write(progress.getvalue())
            elif diagnostics.enabled():
                for line in progress.getvalue().splitlines():
                    if line.startswith("vinc: debug "):
                        print(line, file=sys.stderr)
            return _emit({"kind": "refs.refresh", "executed": status == 0,
                          "manifest": manifest, "status": status})
        except remote_client.RemoteFailure as exc:
            _emit(exc.result)
            return exc.exit_code
        except SystemExit as exc:
            return cli_output.internal_error(exc)

    if args.cmd not in cli_target.TOOL_CMDS:
        if cli_target.local_only(args):
            try:
                target = cli_target.resolve_target(args.target)
            except ValueError as exc:
                return cli_output.usage_error(str(exc))
            if target == "account":
                return cli_output.usage_error(f"{command} is local only; pass --target local")
        if args.space and args.space.strip().lower() != "personal":
            return cli_output.usage_error("--space requires --target account")
    else:
        try:
            target = cli_target.resolve_target(args.target)
        except ValueError as exc:
            return cli_output.usage_error(str(exc))
        if target == "local" and args.space and args.space.strip().lower() != "personal":
            return cli_output.usage_error("--space requires --target account")
        space = cli_target.resolve_space(args.space) if target == "account" else "personal"
        if args.cmd == "events":
            from .cli_events import tail

            def _call():
                if target == "account":
                    from .cli_account import run as run_account
                    return run_account(args, space=space)
                from .cli_tools import run as run_local
                return run_local(args)
            return tail(args, _call, _emit)
        if args.cmd == "gate":
            # 결정 문은 경로를 나눠 보내고 합친다. 한 도구 호출로 끝나는 동사와 길이 다르다.
            from .cli_gate import run as run_gate
            result, exit_code = run_gate(args, target=target, space=space)
        elif args.cmd == "shim":
            # 도구 하나로 끝나지 않는 동사라 여러 도구를 같은 대상에 잇는다.
            from .cli_shims import run as run_shim
            result, exit_code = run_shim(args, target=target, space=space)
        elif target == "account":
            from .cli_account import run as run_tool
            result, exit_code = run_tool(args, space=space)
        else:
            from .cli_tools import run as run_tool
            result, exit_code = run_tool(args)
        if result is None:
            return exit_code or 2
        if (args.cmd == "role" and args.rolecmd == "export" and getattr(args, "out", None)
                and isinstance(result, dict) and result.get("executed") and result.get("content") is not None):
            result = _write_role_export(result, args.out)
        exact = ("Node" if args.cmd == "node" and args.nodecmd == "get" else
                 "Node" if args.cmd == "edges" and args.id is not None else
                 "Role" if args.cmd == "role" else None)
        if (exact and isinstance(result, dict)
                and result.get("executed") is not False and result.get("found") is False):
            result = {**result, "error": {"code": "not_found",
                                         "message": f"{exact} not found: {args.id}"}}
        if args.cmd == "whoami" and isinstance(result, dict) and not result.get("error"):
            from .remote_client import api_key
            key = api_key() or ""
            result = {**result, "target": target,
                      "key_scope": ("local" if target == "local" else
                                    "read" if key.startswith("vinc_ro_") else "full")}
        rendered = _emit(result)
        return exit_code if exit_code is not None else rendered

    # 여기부터는 이 기기 전용 동사다. 도구 동사(recall·search·doc …)는 위에서 끝났다.
    # 열린 앱이 정확히 이 파일을 제공하면 그 단일 writer 에 요청한다.
    from .cli_transport import request_if_attached
    attached = request_if_attached(args, stdin=sys.stdin)
    if attached is not None:
        return _emit(attached)

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
    if args.cmd == "export":
        from . import export as export_module
        return _emit(export_module.export_store(args))
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
        from store.location import LocationError, env_location
        try:
            env_db = env_location()
        except LocationError:
            env_db = None              # 틀린 환경은 serve 가 사유를 말하고 스토어를 열지 않는다
        if env_db is not None or os.environ.get("VINC_DATABASE_URI"):
            # 폴더 스토어 전용이다. 데이터베이스 위치의 벡터 폭은 어댑터가 열 때 대조한다.
            print("vinc: migrate-dim skipped: the store is a database (VINC_DATABASE_URI)",
                  file=sys.stderr, flush=True)
            return 0
        from store import migrate, persistence
        from store.config import load_config, resolve_data_dir
        data_dir = resolve_data_dir(args.data)
        _restore = persistence.restore_newest(data_dir)
        if _restore not in ("off", "present"):
            print(f"vinc-migrate-dim: store restore — {_restore}", file=sys.stderr, flush=True)
        _fatal = persistence.restore_state_is_fatal(_restore)
        if _fatal:
            print(f"vinc-migrate-dim: 기동 중단 — {_fatal}", file=sys.stderr, flush=True)
            return 1
        cfg = load_config({"data_dir": data_dir})
        model = (os.environ.get("VINC_EMBED_MODEL") or "").strip() or cfg.embedding_model
        from . import docstore
        try:
            out = migrate.migrate_dim_if_needed(data_dir, model=model,
                                                target_dim=cfg.embedding_dim,
                                                requeue=docstore.switch_embed_model)
        except Exception as exc:                     # noqa: BLE001 — 사유를 전부 내보낸다
            print(f"vinc-migrate-dim: ⚠ 재구축 실패 — {type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
            return 3
        print(json.dumps(out, ensure_ascii=False), flush=True)
        return 0
    if args.cmd == "migrate-lex":
        # 허브는 서비스 중에 이관하지 않는다(DDL 가드가 프로세스 전체를 몇 분씩 막는다). 공유
        # 스냅샷에서 받은 사본에 이 명령을 돌리고 확인한 뒤 `VINC_DATA_DIR` 을 바꾼다.
        # 근거: [[vinc.plan.korean-retrieval-p1-p2-2026-10-02 §2.7]]
        from store.backend import open_store
        from store.config import load_config
        store = open_store(load_config({"data_dir": args.data}))
        if store is None:
            print("vinc-migrate-lex: 스토어를 열 수 없다", file=sys.stderr, flush=True)
            return 1
        try:
            cfg = load_config({"data_dir": args.data})
            store.provision(dim=cfg.embedding_dim)      # v21 열이 먼저 있어야 한다
            before = store.run("MATCH (v:ChunkVec) RETURN count(v) AS c")[0]["c"]
            out = store.migrate_lex_index(batch=args.batch)
            after = store.run("MATCH (v:ChunkVec) RETURN count(v) AS c")[0]["c"]
            from store.lexical import lex_text
            # 빈 lex 가 정상인 행(기호뿐인 청크)은 빼고 센다 — 그 행은 색인할 말이 없다.
            empty = sum(1 for r in store.run(
                "MATCH (c:Chunk) WHERE coalesce(c.lex, '') = '' RETURN c.text AS t", raw=True)
                if lex_text(r.get("t") or ""))
            out.update(lex_ready=store.lex_ready(), vectors_before=before,
                       vectors_after=after, chunks_without_lex=empty)
        finally:
            store.close()
        print(json.dumps(out, ensure_ascii=False), flush=True)
        ok = out["lex_ready"] and out["vectors_before"] == out["vectors_after"]             and out["chunks_without_lex"] == 0
        return 0 if ok else 3
    if args.cmd == "migrate-body-mentions":
        # 본문 스팬 열은 자동 provision 에 넣지 않는 opt-in DDL 이다. 승인은 함수가
        # 판정한다(`--approve` 없으면 ValueError → 쓰지 않고 사용 오류).
        from store import embedded_schema
        from store.backend import open_store
        from store.config import load_config
        store = open_store(load_config({"data_dir": args.data}))
        if store is None:
            return _emit({"kind": "migrate-body-mentions", "executed": False,
                          "error": "no reachable store"})
        try:
            out = embedded_schema.migrate_body_mentions(store, approved=args.approve)
        except ValueError as exc:
            return cli_output.usage_error(str(exc))
        finally:
            store.close()
        return _emit({"kind": "migrate-body-mentions", **out})
    if args.cmd == "store-rebuild":
        from store import migrate
        from store.backend import open_store
        from store.config import load_config
        from . import docstore
        return migrate._main_rebuild(args, open_store, load_config,
                                     requeue=docstore.switch_embed_model)
    if args.cmd == "store-probe":
        # 사이드카가 WAL 을 재생하기 전에 이 자식에게 먼저 시킨다(store/wal_guard.py).
        # 종료 코드가 곧 판정이다: 0 = 열고 닫았다 · PROBE_RAISED = 예외 · 그 밖 = 죽었다.
        # 닫기가 체크포인트라 0 이면 부모는 WAL 없이 연다.
        from store.backend import open_store
        from store.config import load_config
        from store.wal_guard import PROBE_RAISED
        store = open_store(load_config({"data_dir": args.data}))
        if store is None:
            return PROBE_RAISED
        store.close()
        return 0
    if args.cmd == "storage":
        from . import cli_storage
        return cli_storage.run(args, _emit)
    if args.cmd == "selfhost":
        from . import cli_selfhost
        return cli_selfhost.run(args)
    if args.cmd == "serve":
        # 자식 프로세스가 물려받기 전에 비밀값을 환경에서 꺼낸다. 수동 인자가 우선한다.
        inherited_token = os.environ.pop("VINC_SIDECAR_TOKEN", None)
        session_token = args.token or inherited_token or None
        from . import server
        from store.config import app_store_config
        from store.location import take_secret
        from store import persistence
        # 저장 위치: --data > MEMORY_DATA_DIR > 설정의 위치(폴더 또는 데이터베이스) >
        # ~/.vinc. 데이터베이스 비밀번호는 셸이 환경으로 넘긴 것을 읽자마자 지운다(자식에게 안 간다).
        secret = take_secret()
        if secret is None and not args.data:
            # 셸 없이 띄운 사이드카(CLI·자가 호스팅): 환경 변수가 없으면 `vinc storage connect` 가 넣은 키링을 본다.
            from .cli_storage import current_secret
            secret = current_secret()
        cfg = app_store_config(args.data, secret=secret)
        data_dir = cfg.data_dir or ""
        # 영속 스냅샷에서 복원(VINC_STORE_SNAPSHOT 설정 시에만). 스토어를 열기
        # **전에** 해야 한다 — 연 뒤에 파일을 갈아치우면 드라이버가 자기 밑의
        # 파일이 바뀐 것을 모른다. 미설정이면 'off' 로 즉시 빠진다. VINC_DURABLE_ACK 면
        # 스냅샷과 커밋 세대 중 새것을 고른다(persistence.restore_newest). 폴더 스토어만 해당한다.
        _restore = persistence.restore_newest(data_dir) if data_dir else "off"
        if _restore not in ("off", "present"):
            print(f"vinc: store restore — {_restore}", file=sys.stderr, flush=True)
        # ⚠ **복원 실패는 fail-closed 다.** 여기서 멈추면 사람이 보고 고치지만, 진행하면
        # 빈 DB 가 생기고 다음 스냅샷이 영속본을 덮는다:
        # [[record:a-failed-restore-must-stop-the-process]].
        _fatal = persistence.restore_state_is_fatal(_restore)
        if _fatal:
            print(f"vinc: 기동 중단 — {_fatal}", file=sys.stderr, flush=True)
            return 1
        packs = args.packs
        pinned = bool(args.data or os.environ.get("MEMORY_DATA_DIR"))
        if packs is None:
            if not pinned:
                # 저장 폴더를 옮겨도(VIN-130) packs 는 데이터 홈에 남는다: DB 만 간다.
                from store.config import vinc_home
                packs = str(vinc_home() / "packs")
            elif data_dir.endswith(os.sep.join((".vinc", "db"))):
                packs = os.path.join(os.path.dirname(data_dir), "packs")
        server.serve(cfg,
                     host=args.host, port=args.port, parent_pid=args.parent_pid,
                     token=session_token, packs_dir=packs, profile_path=args.profile,
                     data_pinned=pinned)
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
        if args.mcpcmd == "repair":
            from vinc_mcp.install import repair
            return _emit(repair())
        if args.mcpcmd == "manual":
            from vinc_mcp.install import manual_entry
            return _emit(manual_entry())
        # 앱이 계정 모드면 stdio 세션을 호스티드로 넘긴다. 사이드카는 띄우지 않는다:
        # 앱 화면이 보는 계정 그래프를 MCP 도 본다(VIN-132 K4). 키는 런처가 넣는다.
        # 근거: [[vinc.plan.mcp-follows-the-app-mode-vin-132-2026-09-29]]
        if args.mcpcmd == "serve":
            from vinc_mcp.proxy import serve_remote, wants_account_mode
            if wants_account_mode(base=args.base, token=args.token, owner=args.owner):
                # 키: 환경이 먼저(시험·수동), 없으면 앱이 키체인에 둔 기기별 MCP 키.
                from vinc_mcp import keychain
                return serve_remote(os.environ.get("VINC_REMOTE_MCP_TOKEN")
                                    or keychain.read(keychain.MCP_KEY) or "")
        from vinc_mcp.bridge import Bridge
        bridge = Bridge(base=args.base, token=args.token, owner=args.owner)
        # 붙잡기 모드(허브 인계): 사이드카보다 먼저 떠서 포트를 연다. 사이드카 확보는 조립 때 한다.
        from store.envflag import truthy
        held = args.mcpcmd == "serve-http" and truthy(os.environ.get("VINC_MCP_HOLD"))
        if not held:
            bridge.ensure(spawn=not args.no_spawn)
        if args.mcpcmd == "serve":
            from vinc_mcp.stdio import serve as serve_stdio
            return serve_stdio(bridge)
        if args.mcpcmd == "serve-http":
            from vinc_mcp.http_server import serve_http, serve_http_held
            if held:
                # 포트를 먼저 열고 요청을 붙잡는다. 앱은 사이드카가 뜬 뒤에 조립한다.
                def _build():
                    bridge.ensure(spawn=False, wait=5.0)
                    return _serve_http_kwargs(args)
                return serve_http_held(bridge, _build, host=args.host, port=args.port)
            return serve_http(bridge, host=args.host, port=args.port, **_serve_http_kwargs(args))
        return 2
    if args.cmd == "onboard":
        from . import auth
        passphrase = args.passphrase
        if passphrase is None:
            import getpass
            passphrase = getpass.getpass("passphrase: ")
        return _emit(auth.create_profile(passphrase, path=args.profile, force=args.force))
    return 2


def main(argv: list[str] | None = None) -> int:
    from store import diagnostics
    diagnostics.configure(False)
    cli_output.configure(None, "")
    backend_module = sys.modules.get("store.embedded_backend")
    if backend_module is not None:
        backend_module.EmbeddedBackend.last_open_error = ""
    from store.open_diagnostics import emit_open_failure
    diagnostic_token = emit_open_failure.set(
        "--debug" in (argv if argv is not None else sys.argv[1:]) or os.environ.get("VINC_DEBUG") == "1")
    try:
        return _main(argv)
    except _UsageError as exc:
        cli_output.configure(_error_format(argv if argv is not None else sys.argv[1:]), "")
        try:
            cli_output.chosen_format()
        except ValueError:
            cli_output.configure("human", "")
        return cli_output.usage_error(str(exc))
    except ValueError as exc:
        if str(exc).startswith("VINC_FORMAT must"):
            cli_output.configure("human", "")
            return cli_output.usage_error(str(exc))
        return cli_output.internal_error(exc, debug="--debug" in (argv or sys.argv[1:]))
    except SystemExit as exc:
        # argparse 의 --help 는 성공 종료이고, 나머지 종료 코드는 보존한다.
        # `sys.exit("메시지")` 는 코드가 문자열이다 — 메시지를 stderr 로 내고 1 로 끝낸다.
        if exc.code is None or isinstance(exc.code, int):
            return int(exc.code or 0)
        return cli_output.internal_error(exc)
    except Exception as exc:  # noqa: BLE001 — CLI 오류 경계를 한 곳에 둔다.
        return cli_output.internal_error(exc, debug="--debug" in (argv or sys.argv[1:]))
    finally:
        diagnostics.configure(False)
        emit_open_failure.reset(diagnostic_token)


def _serve_http_kwargs(args) -> dict:
    """`mcp serve-http` 의 앱 조립(OAuth 상태 복원·키 검증·할당량). 붙잡기 모드에서는
    사이드카가 준비된 **뒤에** 부른다 — OAuth 상태를 이 시점에 복원해야 구 리비전이 마지막으로
    쓴 상태를 받는다."""
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
    from vinc_mcp import community
    vc = community.resolve if entitlement.enabled() else None
    cw = community.write if entitlement.enabled() else None
    cr = community.read if entitlement.enabled() else None
    # 티어 쿼터(B-D2) — 인증된 회원(owner) 의 tools/call 만 소비. 미설정 시
    # 무제한(로컬·파일럿 프로파일 무영향).
    quota_cfg = None
    # 셀프호스트 허브(`vinc_mcp/selfhost.py`): 소유자 모양을 기동 때 확인하고, 한도·연결
    # 흐름은 싣지 않는다. 키·멤버십·커뮤니티는 `entitlement.enabled()` 가 이미 끈다.
    from vinc_mcp import selfhost
    selfhost.check()
    if os.environ.get("VINC_QUOTA") == "on" and not selfhost.enabled():
        quota_cfg = {
            # Curious(free) 만 센다: 스페이스당 하루 이만큼, 00:00 UTC 리셋, 잠금 없음.
            # Genius 는 세지 않는다. 창 길이와 잠금은 정책이라 env 로 열지 않는다.
            # 비준된 값 300 이다. 허브는 이 env 를 싣지 않으므로 이 기본값이 곧 운영
            # 한도다. 사이트(요금·약관·계정 화면)가 300 을 말하고 있어 같은 값으로 맞췄다.
            # 근거: [[vinc.canon.account-policy §8]]
            "free_limit": int(os.environ.get("VINC_FREE_DAILY_LIMIT", "300")),
            # 스페이스당 분당 상한. 모든 플랜에 같고(유료가 더 빠르다고 하지 않는다)
            # 0 이면 끈다. 값은 hub-deploy 가 리포 변수 VINC_BURST_PER_MINUTE 에서
            # 싣고, 비어 있으면 60 이다.
            # 근거: [[vinc.plan.plans-and-limits-redesign-2026-10-17 §2.5]]
            "burst_per_minute": int(os.environ.get("VINC_BURST_PER_MINUTE", "60")),
            # 면제 owner(쉼표 구분, `user:<public_id>`): 도그푸딩 계정처럼 그래프를
            # 대량으로 재는 계정이 하루 한도에 스스로 막히지 않게 한다. 면제는
            # quota_check 를 통째로 건너뛰므로 이미 걸린 잠금도 보지 않는다.
            # 값은 hub-deploy 가 리포 변수 VINC_QUOTA_EXEMPT_OWNERS 에서 싣는다.
            "exempt_owners": frozenset(
                o.strip() for o in
                os.environ.get("VINC_QUOTA_EXEMPT_OWNERS", "").split(",") if o.strip()),
        }
    # 무료 등급 주제 상한은 **없다**. Curious 의 경계는 하루 호출 수와 스페이스
    # 수이지 주제 수가 아니다.
    # `free_max_domains` 배선과 `api/fragment.py` 의 `max_domains` 가드는
    # 남겨 둔다(일반 장치이고 기본 None = 무제한이다). 정책이 여기서 끝난다:
    # env 로 되살릴 수 없고, 되살리려면 이 줄을 고쳐야 한다.
    # 근거: [[decision:the-plans-are-curious-genius-and-enterprise-2026-10-17]]
    free_cap = None
    # 회원 승인 흐름(B-B3) — 우리 서명키 + 계정 공개키 핀 + 공홈 동의 URL.
    # 셋 다 있어야 활성(없으면 관리 키 경로만). 공개키만 교환 → B2 준수.
    from vinc_mcp import edverify as _ev
    seed_raw = None if selfhost.enabled() else os.environ.get("VINC_MCP_ED25519_SEED")
    acct_raw = None if selfhost.enabled() else os.environ.get("VINC_ACCOUNT_PUBLIC_KEY")
    return dict(
        static_token=static, oauth=gate, verify_key=vk, verify_member=vm,
        verify_community=vc, community_write=cw, community_read=cr,
        quota_config=quota_cfg,
        mcp_seed=_ev.load_seed(seed_raw) if seed_raw else None,
        account_public=_ev.load_public(acct_raw) if acct_raw else None,
        connect_url=None if selfhost.enabled()
        else os.environ.get("VINC_CONNECT_URL") or "https://vincs.io/connect",
        free_max_domains=free_cap)


if __name__ == "__main__":
    sys.exit(main())
