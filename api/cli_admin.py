# -*- coding: utf-8 -*-
"""`vinc` CLI 의 관리 동사: `refs`·`serve`·`migrate-dim`·`migrate-lex`·`migrate-body-mentions`·
`store-rebuild`·`store-probe`·`graph-md`·`mcp`.

처리기는 해석한 인자(`args`)를 받아 종료 코드를 돌려준다. `api.cli` 의 `_main` 이 표(`_DEVICE_VERBS`)에서
찾아 부른다. 동사 이름과 종료 코드는 허브 워크플로(`migrate-lex`·`store-rebuild`)와 앱이 부르는 계약이다.
"""
from __future__ import annotations

import json
import os
import sys

from . import cli_output


def _emit(obj: dict) -> int:
    """`api.cli._emit` 을 부를 때마다 찾아 부른다(결과 봉투와 스토어 잠김 문장이 한 곳에 있다)."""
    from .cli import _emit as emit
    return emit(obj)


def _serve_http_kwargs(args) -> dict:
    """`api.cli._serve_http_kwargs`(`mcp serve-http` 의 앱 조립)를 부를 때마다 찾아 부른다."""
    from .cli import _serve_http_kwargs as build
    return build(args)


def run_refs(args) -> int:
    from . import cli_target
    from store import diagnostics
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


def run_migrate_dim(args) -> int:
    # 허브 엔트리포인트가 사이드카 **앞에서** 부른다. 복원은 serve 와 같은 함수·같은
    # 기준이라 여기서 했으면 serve 는 `present` 를 본다. 종료코드: 0 할 일 없음/끝남 ·
    # 1 복원 실패(기동 중단, serve 와 같은 fail-closed) · 3 재구축 실패(원래 스토어는
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


def run_migrate_lex(args) -> int:
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


def run_migrate_body_mentions(args) -> int:
    # 본문 스팬 열은 자동 provision 에 넣지 않는 opt-in DDL 이다. 승인은 함수가
    # 가른다(`--approve` 없으면 ValueError → 쓰지 않고 사용 오류).
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


def run_store_rebuild(args) -> int:
    from store import migrate
    from store.backend import open_store
    from store.config import load_config
    from . import docstore
    return migrate._main_rebuild(args, open_store, load_config,
                                 requeue=docstore.switch_embed_model)


def run_store_probe(args) -> int:
    # 사이드카가 WAL 을 재생하기 전에 이 자식에게 먼저 시킨다(store/wal_guard.py).
    # 종료 코드가 곧 결과다: 0 = 열고 닫았다 · PROBE_RAISED = 예외 · 그 밖 = 죽었다.
    # 닫기가 체크포인트라 0 이면 부모는 WAL 없이 연다.
    from store.backend import open_store
    from store.config import load_config
    from store.wal_guard import PROBE_RAISED
    store = open_store(load_config({"data_dir": args.data}))
    if store is None:
        return PROBE_RAISED
    store.close()
    return 0


def run_serve(args) -> int:
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
            # 저장 폴더를 옮겨도 packs 는 데이터 홈에 남는다: DB 만 간다.
            from store.config import vinc_home
            packs = str(vinc_home() / "packs")
        elif data_dir.endswith(os.sep.join((".vinc", "db"))):
            packs = os.path.join(os.path.dirname(data_dir), "packs")
    server.serve(cfg,
                 host=args.host, port=args.port, parent_pid=args.parent_pid,
                 token=session_token, packs_dir=packs, profile_path=args.profile,
                 data_pinned=pinned)
    return 0


def run_graph_md(args) -> int:
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


def run_mcp(args) -> int:
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
    # 앱 화면이 보는 계정 그래프를 MCP 도 본다. 키는 런처가 넣는다.
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
