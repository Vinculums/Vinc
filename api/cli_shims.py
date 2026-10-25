"""`vinc shim ls|watch`: 심 레코드를 읽고, 발동이 멈춘 심의 은퇴 초안을 만든다.

모델을 부르지 않는다. 읽기는 도구 네 개(vinc_role 로스터·vinc_edges·vinc_node·vinc_recall)로만
하고, 쓰기는 `watch --write` 의 vinc_ingest 하나다. 로직은 SDK 원본에서 생성한 `shim_registry`
가 갖고 여기는 대상(로컬·계정)에 도구 호출을 잇는 일만 한다.
"""
from __future__ import annotations

import datetime as _dt
from contextlib import ExitStack

from vinc_mcp import tools

from . import shim_registry as reg
from .cli_output import usage_error


class _Failed(Exception):
    def __init__(self, result: dict, exit_code: int):
        super().__init__(str((result.get("error") or {}).get("message") or "tool call failed"))
        self.result, self.exit_code = result, exit_code


class _Tools:
    """`shim_registry` 가 부르는 덕 타입 클라이언트. space 는 호출 함수에 이미 묶여 있다."""

    def __init__(self, call):
        self._call = call

    def edges(self, *, nodes, type, dir, limit, space=None):
        return self._call("vinc_edges", {"nodes": list(nodes), "type": type, "dir": dir,
                                         "limit": limit})

    def node(self, node_id, *, space=None):
        return self._call("vinc_node", {"node_id": node_id})

    def recall(self, *, tags, limit, cursor=None, space=None):
        return self._call("vinc_recall", {"tags": list(tags), "limit": limit, "cursor": cursor})


def _caller(args, *, target: str, space: str, owner: str | None):
    """(호출 함수, 닫기 함수). 실패한 도구 결과는 `_Failed` 로 올린다. 없는 노드는 `found: False`."""
    common = {"owner": owner} if owner else {}
    if target == "account":
        from .remote_client import RemoteFailure, call as remote

        def call(name, data):
            data = {k: v for k, v in {**common, **data}.items() if v is not None}
            try:
                return remote(name, data, space=space)
            except RemoteFailure as exc:
                if name == "vinc_node" and (exc.result.get("error") or {}).get("code") == "not_found":
                    return {"executed": True, "found": False, "node": None}
                raise _Failed(exc.result, exc.exit_code) from None
        return call, lambda: None
    from .cli_tools import _bridge
    stack = ExitStack()
    bridge = stack.enter_context(_bridge(args))

    def call(name, data):
        data = {k: v for k, v in {**common, **data}.items() if v is not None}
        result = tools.call(name, data, bridge)
        if isinstance(result, dict) and (result.get("error") or result.get("executed") is False):
            error = result.get("error")
            raise _Failed(result if isinstance(error, dict) else {
                **result, "error": {"code": "tool_error", "message": str(error or result.get("note")
                                                                       or "tool call failed")}}, 1)
        return result
    return call, stack.close


def _day(value: str | None) -> str:
    if value is None:
        return _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    try:
        return _dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError("--today must be YYYY-MM-DD") from None


def _role_id(value: str) -> str:
    value = value.strip()
    return value if value.startswith("concept:") else "concept:" + value


def _roster(call) -> list[str]:
    ids, offset = [], 0
    while True:
        page = call("vinc_role", {"limit": 100, "offset": offset})
        ids.extend(str(row["id"]) for row in page.get("roles") or [] if row.get("id"))
        offset = page.get("next_offset")
        if offset is None:
            return ids


def _row(shim: reg.Shim) -> dict:
    return {key: value for key, value in shim.as_dict().items()
            if key in ("id", "mechanism", "applies_to", "installed_because", "valid_from",
                       "attached_to", "patches", "measured_by") and value not in (None, "")}


def shim_ls(client: _Tools, call, *, role: str | None, model_family: str | None,
            today: str) -> dict:
    targets = [_role_id(role)] if role else _roster(call)
    shims = reg.attached_shims(client, [*targets, reg.HARNESS_CONCEPT], today=today)
    shims = [s for s in shims if s.applies(model_family)]
    return {"kind": "shim.ls", "executed": True, "today": today,
            "role": targets[0] if role else None, "shims": [_row(s) for s in shims],
            "count": len(shims)}


def shim_watch(client: _Tools, call, *, window_days: int, min_runs: int,
               families: list[str] | None, today: str, write: bool) -> dict:
    start = (_dt.date.fromisoformat(today) - _dt.timedelta(days=window_days)).isoformat()
    runs = reg.read_runs(client, since=start)
    seen: dict[str, int] = {}
    for run in runs:
        family = str((run.get("props") or {}).get("model_family") or "").casefold()
        if family:
            seen[family] = seen.get(family, 0) + 1
    in_use = [f.casefold() for f in families] if families else sorted(seen)
    roles = sorted({str(run["props"]["role"]) for run in runs if run["props"].get("role")})
    targets = [*roles, reg.HARNESS_CONCEPT]
    # 실행 기록이 가리키는 역할이 지워졌어도 감시는 계속한다.
    shims = reg.attached_shims(client, targets, optional=targets, today=today)
    drafts = (reg.retirement_drafts(shims, runs, window_start=start, min_runs=min_runs,
                                    families_in_use=in_use) if in_use else [])
    out = {"kind": "shim.watch", "executed": True, "today": today, "window_start": start,
           "min_runs": min_runs, "runs": len(runs), "families": seen,
           "families_in_use": in_use, "shims": [s.id for s in shims], "drafts": drafts}
    if not write:
        return out
    written, kept, warnings = [], [], []
    for draft in drafts:
        did = draft["nodes"][0]["id"]
        existing = client.node(did)
        node = existing.get("node") if existing.get("found") is not False else None
        state = ((node or {}).get("props") or {}).get("state") if node else None
        if node and state != reg.DRAFT:
            # 사람이 이미 판정한 결정은 덮지 않는다(props 는 통째로 바뀐다).
            kept.append({"id": did, "state": state})
            continue
        result = call("vinc_ingest", {"nodes": draft["nodes"], "edges": draft["edges"]})
        written.append(did)
        warnings.extend(str(w) for w in result.get("warnings") or [])
    return {**out, "written": written, "kept": kept, "warnings": warnings}


def run(args, *, target: str, space: str) -> tuple[dict | None, int | None]:
    try:
        today = _day(getattr(args, "today", None))
    except ValueError as exc:
        return None, usage_error(str(exc))
    if args.shimcmd == "watch":
        if args.window_days < 1 or args.min_runs < 1:
            return None, usage_error("--window-days and --min-runs must be 1 or more")
        families = [f.strip() for f in (args.families or "").split(",") if f.strip()] or None
        if args.write and target == "account":
            from .remote_client import api_key
            if not (api_key() or "").startswith("vinc_sk_"):
                return {"kind": "shim.watch", "executed": False, "error": {
                    "code": "read_only_key",
                    "message": "--write needs a full-access key (vinc_sk_) in VINC_API_KEY."}}, 4
    call, close = _caller(args, target=target, space=space, owner=getattr(args, "owner", None))
    client = _Tools(call)
    try:
        if args.shimcmd == "ls":
            return shim_ls(client, call, role=args.role, model_family=args.model_family,
                           today=today), None
        return shim_watch(client, call, window_days=args.window_days, min_runs=args.min_runs,
                          families=families, today=today, write=bool(args.write)), None
    except _Failed as exc:
        return exc.result, exc.exit_code
    except LookupError as exc:
        return {"kind": f"shim.{args.shimcmd}", "executed": False,
                "error": {"code": "not_found", "message": str(exc).strip("'\"")}}, 1
    except RuntimeError as exc:
        return {"kind": f"shim.{args.shimcmd}", "executed": False,
                "error": {"code": "incomplete_read", "message": str(exc)}}, 1
    except (ValueError, tools.InvalidArgument) as exc:
        return None, usage_error(str(exc))
    finally:
        close()
