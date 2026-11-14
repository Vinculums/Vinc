# -*- coding: utf-8 -*-
"""CLI for the query API — the entry point CP / PM call.

    python -m api.cli recall  --query "billing" --workspace planning --limit 5
    python -m api.cli search  --query "환불 정책" --top-k 5
    python -m api.cli decide  --topic g5.b.billing --depth 2
    python -m api.cli plan    recall --query x        # print plan only (no db)

Output is a single JSON object on stdout.
"""
from __future__ import annotations

import os
import sys

from . import query as q
from . import preference as pref
from . import activate as act
from . import cli_admin
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
        return cli_admin.run_refs(args)

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

    handler = _DEVICE_VERBS.get(args.cmd)
    if handler is not None:
        return handler(args)
    return 2


def _run_domain_backfill(args) -> int:
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


def _run_learner(args) -> int:
    return _emit(q.learner_model(owner=args.owner))


def _run_graph(args) -> int:
    return _emit(q.graph(owner=args.owner, domain=args.domain))


def _run_pin(args) -> int:
    return _emit(pref.pin(args.episode, pinned=not args.off, owner=args.owner))


def _run_tag(args) -> int:
    return _emit(pref.tag(args.episode, _csv(args.tags) or [], owner=args.owner))


def _run_touch(args) -> int:
    return _emit(pref.touch(args.episode, now=args.now, owner=args.owner))


def _run_activate(args) -> int:
    return _emit(act.activate(args.query, owner=args.owner, domain=args.domain, tenant=args.tenant,
                              workspace=args.workspace, tags=_csv(args.tags),
                              rank=args.rank, limit=args.limit, include_model=not args.no_model,
                              generated_at=args.now, out_dir=args.out))


def _run_export(args) -> int:
    from . import export as export_module
    return _emit(export_module.export_store(args))


def _run_plan(args) -> int:
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


def _run_share(args) -> int:
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


def _run_import(args) -> int:
    from . import share, packfile
    pack = packfile.read_file(args.pack)
    return _emit(share.import_share_pack(pack, owner=args.owner))


def _run_storage(args) -> int:
    from . import cli_storage
    return cli_storage.run(args, _emit)


def _run_selfhost(args) -> int:
    from . import cli_selfhost
    return cli_selfhost.run(args)


def _run_backup(args) -> int:
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


def _run_onboard(args) -> int:
    from . import auth
    passphrase = args.passphrase
    if passphrase is None:
        import getpass
        passphrase = getpass.getpass("passphrase: ")
    return _emit(auth.create_profile(passphrase, path=args.profile, force=args.force))


#: 이 기기 전용 동사의 처리기. `_main` 은 도구 동사(`cli_target.TOOL_CMDS`)와 붙잡기 요청을 먼저 거른 뒤
#: 여기서 찾고, 없으면 2 로 끝난다. `refs` 는 대상 검사 앞에서 따로 갈린다(`cli_admin.run_refs`).
_DEVICE_VERBS = {
    "domain-backfill": _run_domain_backfill,
    "learner": _run_learner,
    "graph": _run_graph,
    "pin": _run_pin,
    "tag": _run_tag,
    "touch": _run_touch,
    "activate": _run_activate,
    "export": _run_export,
    "plan": _run_plan,
    "share": _run_share,
    "import": _run_import,
    "migrate-dim": cli_admin.run_migrate_dim,
    "migrate-lex": cli_admin.run_migrate_lex,
    "migrate-body-mentions": cli_admin.run_migrate_body_mentions,
    "store-rebuild": cli_admin.run_store_rebuild,
    "store-probe": cli_admin.run_store_probe,
    "storage": _run_storage,
    "selfhost": _run_selfhost,
    "serve": cli_admin.run_serve,
    "backup": _run_backup,
    "graph-md": cli_admin.run_graph_md,
    "mcp": cli_admin.run_mcp,
    "onboard": _run_onboard,
}


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
