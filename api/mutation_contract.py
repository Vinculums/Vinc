"""선택적인 충돌 검사. 기존 교체 쓰기와 명시적인 속성 patch를 구분한다."""
from __future__ import annotations

import copy
import json


class MutationConflict(ValueError):
    def __init__(self, code="write_conflict", **details):
        status = ("conflict" if code == "write_conflict" else "unavailable"
                  if code in ("document_unavailable", "write_target_unavailable")
                  else "query_failed" if code.endswith("unavailable") else "unsupported_kind"
                  if code.endswith("not_supported") else "invalid_request")
        self.result = {"executed": False, "error": code, "status": status, **details}
        if code == "write_conflict":
            # 응답이 다음 할 일을 말한다(도구 설명은 짧은 요약이다).
            # 근거: [[decision:mcp-tool-descriptions-are-short-summaries-with-a-docs-link]]
            self.result["hint"] = WRITE_CONFLICT_HINT
        super().__init__(code)


WRITE_CONFLICT_HINT = ("Nothing was changed: the stored version is not the one you read, or the id already "
                       "exists. Read it again, merge your change, and resend with the current rev or hash.")


def merge_patch(current, patch):
    """JSON Merge Patch: null은 키 삭제, 그 밖의 값은 그대로 저장한다."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(current) if isinstance(current, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def _whole_run_props(store, nodes, owner: str) -> None:
    """프로토콜이 가진 실행 기록에 props 를 통째로 쓰는 노드가 있으면 아무것도 쓰기 전에 거절한다.

    통째의 props 는 저장된 props 를 갈아 끼우므로 대기·승인·답과 역할·작업·outcome 이 지워진다(목록·
    수·JSON 글도 같다). 프로토콜 자신의 저장은 실행 상태(`state`)를 싣고 도구 쓰기는 상태 키를 못 싣으므로
    (`run_state.writes`), 상태 없는 통째의 props 만 저장된 행을 읽어 본다. `props_patch` 는 저장된 props 에
    합쳐지므로 여기를 지나지 않는다. 같은 쓰기 잡 안에서 읽으므로 그 사이에 든 답이 없다."""
    from api import query
    from store import run_state

    for entry in nodes if isinstance(nodes, list) else []:
        if not isinstance(entry, dict) or entry.get("props") is None or "props_patch" in entry:
            continue
        rid = run_state.run_id(entry.get("id"))
        if rid is None or run_state.held(entry["props"]):
            continue
        found = query.execute(query.node_plan(rid, owner=owner), store=store)
        if not found.get("executed") or found.get("rows") is None:
            raise MutationConflict("write_precondition_unavailable", node_id=rid)
        rows = found.get("rows") or []
        if rows and run_state.held(rows[0].get("props")):
            raise MutationConflict(run_state.CODE, node_id=rid, nodes=[rid],
                                   note=run_state.whole_props_refusal(rid))


def _waiting_card(store, nodes, owner: str) -> None:
    """사람을 기다리는 실행의 카드(제목과 `run_state.CARD_KEYS`)를 바꾸는 노드가 있으면 아무것도 쓰기 전에 거절한다.

    사람은 승인함의 카드에서 제목·작업·대상·호스트를 읽고 답한다. 대기 중에 그것이 바뀌면 사람은 물은 것과
    다른 글을 보고 답한다. 제목만 실은 노드도, `props_patch` 도 여기를 지난다(통째의 props 는
    `_whole_run_props` 가 이미 거절했다). 프로토콜 자신의 저장(상태를 싣는 props)과 같은 값을 다시 보내는
    쓰기(제목을 되싣는 `props_patch`)는 지나간다. 같은 쓰기 잡 안에서 읽으므로 그 사이에 든 답이 없다."""
    from api import query
    from store import run_state

    for entry in nodes if isinstance(nodes, list) else []:
        if not isinstance(entry, dict) or run_state.held(entry.get("props")):
            continue
        rid = run_state.run_id(entry.get("id"))
        patch = entry.get("props_patch")
        if rid is None or (entry.get("title") is None and not (
                isinstance(patch, dict) and set(patch) & set(run_state.CARD_KEYS))):
            continue
        found = query.execute(query.node_plan(rid, owner=owner), store=store)
        if not found.get("executed") or found.get("rows") is None:
            raise MutationConflict("write_precondition_unavailable", node_id=rid)
        rows = found.get("rows") or []
        if not rows or not run_state.waiting(rows[0].get("props")):
            continue
        after = (merge_patch(run_state.props_of(rows[0].get("props")), patch)
                 if isinstance(patch, dict) else None)
        changed = run_state.card_changes(rows[0], entry.get("title"), after)
        if changed:
            raise MutationConflict(run_state.CODE, node_id=rid, nodes=[rid], fields=changed,
                                   note=run_state.card_refusal(rid, changed))


def prepare_fragment(store, fragment: dict, owner: str) -> dict:
    """모든 사전 조건을 읽은 뒤에만 쓰기 입력을 만든다. 실패하면 변경은 없다."""
    from api import query

    global_create = fragment.get("create_only", False)
    if type(global_create) is not bool:
        raise MutationConflict("invalid_create_only")
    nodes = fragment.get("nodes") or []
    _whole_run_props(store, nodes, owner)
    _waiting_card(store, nodes, owner)
    guarded = global_create or any(isinstance(n, dict) and any(
        field in n for field in ("expected_rev", "create_only", "props_patch")) for n in nodes)
    if not guarded:
        return fragment
    if not owner or not isinstance(nodes, list) or len(nodes) > 512:
        raise MutationConflict("conditional_write_unavailable")
    prepared = copy.deepcopy(fragment)
    prepared.setdefault("nodes", [])
    seen = set()
    for entry in prepared["nodes"]:
        if not isinstance(entry, dict):
            raise MutationConflict("invalid_conditional_node")
        ident = entry.get("id")
        if not isinstance(ident, str) or not ident or ident in seen:
            raise MutationConflict("duplicate_or_invalid_conditional_target")
        seen.add(ident)
        local_create = entry.pop("create_only", False)
        create = global_create or local_create
        has_expected = "expected_rev" in entry
        has_patch = "props_patch" in entry
        expected = entry.pop("expected_rev", None)
        patch = entry.pop("props_patch", None)
        if type(local_create) is not bool or (has_expected and
                                       (type(expected) is not int or expected < 0)):
            raise MutationConflict("invalid_write_precondition")
        if has_patch and (not isinstance(patch, dict) or "props" in entry):
            raise MutationConflict("invalid_props_patch")
        if not (create or expected is not None or patch is not None):
            continue
        if not isinstance(entry.get("id"), str) or not entry.get("type"):
            raise MutationConflict("invalid_conditional_node")
        if "\x1f" in entry["id"]:
            raise MutationConflict("invalid_conditional_identity")
        kind = str(entry["type"]).lower()
        if entry["id"].partition(":")[0] != kind:
            raise MutationConflict("invalid_conditional_identity")
        if kind == "domain":
            raise MutationConflict("write_precondition_not_supported")
        found = query.execute(query.node_plan(entry["id"], owner=owner), store=store)
        if not found.get("executed") or found.get("rows") is None:
            raise MutationConflict("write_precondition_unavailable")
        rows = found.get("rows") or []
        existing = rows[0] if rows else None
        if create and existing is not None:
            raise MutationConflict(node_id=entry["id"], current_rev=existing.get("rev"))
        if create and existing is None:
            from store import pkns
            label, pk = query._seed_table(entry["id"])
            # 비네임스페이스 저장소의 다른 소유자 PK도 쓰기 전에 거절한다.
            # 행의 제목·속성·소유자·revision은 읽거나 돌려주지 않는다.
            occupied = store.run(f"MATCH (n:{label}) WHERE n.{pk}=$id RETURN count(n) AS n",
                                 {"id": pkns.key(owner, entry["id"])})
            if not occupied:
                raise MutationConflict("write_precondition_unavailable")
            if occupied[0]["n"]:
                raise MutationConflict("write_target_unavailable", node_id=entry["id"])
        if expected is not None:
            if existing is None:
                raise MutationConflict("write_target_unavailable", node_id=entry["id"])
            if existing.get("rev") is None:
                raise MutationConflict("write_precondition_not_supported", node_id=entry["id"])
            if existing["rev"] != expected:
                raise MutationConflict(node_id=entry["id"], current_rev=existing["rev"])
        if patch is not None:
            if existing is None or "props" not in existing:
                raise MutationConflict("props_patch_not_supported", node_id=entry["id"])
            if expected is None:
                raise MutationConflict("props_patch_requires_expected_rev")
            props = existing.get("props") or {}
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except ValueError:
                    raise MutationConflict("stored_props_unavailable") from None
            if not isinstance(props, dict):
                raise MutationConflict("stored_props_unavailable")
            entry["props"] = merge_patch(props, patch)
    prepared.pop("create_only", None)
    return prepared


def document_guard(previous: dict | None, *, owner: str, create_only=False,
                   expected_content_hash=None):
    if type(create_only) is not bool:
        raise MutationConflict("invalid_create_only")
    if previous is not None and previous.get("owner_id") != owner:
        # 다른 소유자의 원문·해시는 조건 실패의 상세에도 싣지 않는다.
        raise MutationConflict("document_unavailable")
    if expected_content_hash is not None and not isinstance(expected_content_hash, str):
        raise MutationConflict("invalid_expected_content_hash")
    if create_only and previous is not None:
        raise MutationConflict()
    if expected_content_hash is not None:
        actual = previous.get("content_hash") if previous is not None else ""
        if actual != expected_content_hash:
            raise MutationConflict(current_content_hash=actual)
