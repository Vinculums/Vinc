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
        super().__init__(code)


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


def prepare_fragment(store, fragment: dict, owner: str) -> dict:
    """모든 사전 조건을 읽은 뒤에만 쓰기 입력을 만든다. 실패하면 변경은 없다."""
    from api import query

    global_create = fragment.get("create_only", False)
    if type(global_create) is not bool:
        raise MutationConflict("invalid_create_only")
    nodes = fragment.get("nodes") or []
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
        kind = str(entry["type"]).lower()
        if entry["id"].partition(":")[0] != kind:
            raise MutationConflict("invalid_conditional_identity")
        if kind == "domain":
            raise MutationConflict("write_precondition_not_supported")
        found = query.node(entry["id"], owner=owner, store=store)
        if not found.get("executed"):
            raise MutationConflict("write_precondition_unavailable")
        existing = found.get("node") if found.get("found") else None
        if create and existing is not None:
            raise MutationConflict(node_id=entry["id"], current_rev=existing.get("rev"))
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
