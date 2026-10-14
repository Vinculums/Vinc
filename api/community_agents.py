"""공유 버전 검증과 조건 가져오기. 권한·공개 여부는 서명한 계정 서버가 결정한다."""
from __future__ import annotations

import copy
import base64
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL

from . import fragment, operation_receipts, query, role_contract
from store import pkns

MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_SAFE_INTEGER = 2**53 - 1
ROLE_PROPS = frozenset(role_contract.REQUIRED) | frozenset({
    "reads", "gates", "test_command", "repos", "paths", "tools", "disallowed_tools",
    "isolation", "delegate_rung", "team", "team_role", "job_title", "writes_graph",
    "never_checks", "pre_merge_check", "speaks_to_the_person", "verify", "trap_note",
    "engine_note", "baseline_note", "claim_note", "tool_note", "enforcement_note",
    "session_close",
})
LICENSES = frozenset({"CC-BY-SA-4.0", "CC-BY-4.0", "CC0-1.0", "MIT", "Apache-2.0"})
_HASH = re.compile(r"[0-9a-f]{64}")
_ROLE = re.compile(r"concept:agent-[a-z0-9]+(?:-[a-z0-9]+)*")
_OWNER = re.compile(r"(?:user|team):[0-9a-fA-F-]{36}")


def canonical(value):
    """JS와 같은 해시가 되는 JSON만 받는다. 수·문자열을 조용히 정규화하지 않는다."""
    def check(v, depth=0):
        if depth > 24:
            raise ValueError("snapshot_too_deep")
        if v is None or type(v) is bool:
            return
        if type(v) is int and abs(v) <= MAX_SAFE_INTEGER:
            return
        if isinstance(v, str):
            v.encode("utf-8", errors="strict")
            return
        if isinstance(v, list):
            if len(v) > 1024:
                raise ValueError("snapshot_array_too_large")
            for item in v:
                check(item, depth + 1)
            return
        if isinstance(v, dict):
            if len(v) > 128 or any(not isinstance(k, str) or not k.isascii() for k in v):
                raise ValueError("snapshot_keys_invalid")
            for item in v.values():
                check(item, depth + 1)
            return
        raise ValueError("snapshot_value_invalid")
    try:
        check(value)
    except UnicodeError:
        raise ValueError("snapshot_unicode_invalid") from None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _string(value, *, empty=False, limit=65536):
    return isinstance(value, str) and (empty or bool(value.strip())) and len(value) <= limit


def validate(snapshot, source):
    """알 수 없는 필드나 불완전한 계약은 거절한다. 부분 렌더링으로 성공하지 않는다."""
    if not isinstance(snapshot, dict) or not {"role", "prompt", "tools", "references", "license"} <= set(snapshot) \
            or set(snapshot) - {"role", "prompt", "tools", "references", "license", "parent"}:
        raise ValueError("invalid_agent_snapshot")
    if len(canonical(snapshot).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError("agent_snapshot_too_large")
    role = snapshot["role"]
    required = {"id", "name", "label", "domain", "tags", "props"}
    if not isinstance(role, dict) or not required <= set(role) or set(role) - required - {"works_with"}:
        raise ValueError("invalid_agent_role")
    rid = role["id"]
    if not isinstance(rid, str) or len(rid) > 120 or not _ROLE.fullmatch(rid) \
            or not role_contract.is_role_id(rid) or role["name"] != rid[8:] \
            or not _string(role["label"], limit=200) or role["domain"] != "agent":
        raise ValueError("invalid_agent_identity")
    props = role["props"]
    if not isinstance(props, dict) or set(props) - ROLE_PROPS or not set(role_contract.REQUIRED) <= set(props):
        raise ValueError("invalid_agent_props")
    node = {"id": rid, "type": "concept", "title": role["label"], "domain": "agent",
            "one_liner": props["mission"], "props": props}
    if role_contract.check(node, created=True) or not role_contract.has_contract(props):
        raise ValueError("invalid_agent_contract")
    list_keys = set(role_contract.LIST_KEYS)
    for key, value in props.items():
        if key not in list_keys and not _string(value):
            raise ValueError("invalid_agent_prop_type")
    for key in list_keys - {"gates", "person_only", "never_checks"}:
        if key in props and (not isinstance(props[key], list) or any(not _string(v, limit=4096) for v in props[key])):
            raise ValueError("invalid_agent_prop_list")
    if not isinstance(role["tags"], list) or role["tags"] != ["agent", props["tier"], "role"]:
        raise ValueError("invalid_agent_tags")
    works = role.get("works_with", {})
    if not isinstance(works, dict) or set(works) - {"hands_to", "receives_from", "works_beside", "returns_to", "receives_returns_from"} \
            or any(not isinstance(v, list) or any(not _string(x, limit=200) for x in v) for v in works.values()):
        raise ValueError("invalid_agent_works_with")
    if not _string(snapshot["prompt"], empty=True) or not isinstance(snapshot["tools"], list) \
            or any(not _string(t, limit=200) for t in snapshot["tools"]):
        raise ValueError("invalid_agent_prompt_or_tools")
    if not isinstance(snapshot["license"], str) or snapshot["license"] not in LICENSES or not isinstance(snapshot["references"], list):
        raise ValueError("invalid_agent_license_or_references")
    for ref in snapshot["references"]:
        if not isinstance(ref, dict) or not _uuid(ref.get("community")) \
                or not _string(ref.get("title"), limit=500) or not _string(ref.get("url"), limit=300):
            raise ValueError("invalid_agent_reference")
        kind = ref.get("kind")
        if kind == "page":
            if set(ref) != {"kind", "community", "page", "url", "title"} \
                    or not isinstance(ref["page"], str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", ref["page"]) \
                    or len(ref["page"]) > 80:
                raise ValueError("invalid_agent_reference")
            route = r"/community/[a-z0-9]+(?:-[a-z0-9]+)*/p/" + re.escape(ref["page"])
            if ref["page"] == "rules":
                route = r"/community/[a-z0-9]+(?:-[a-z0-9]+)*/(?:p/rules|rules)"
        elif kind == "thread":
            if set(ref) != {"kind", "community", "page", "id", "url", "title"} or not _uuid(ref["id"]) \
                    or not isinstance(ref["page"], str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", ref["page"]) \
                    or len(ref["page"]) > 80:
                raise ValueError("invalid_agent_reference")
            route = r"/community/[a-z0-9]+(?:-[a-z0-9]+)*/p/" + re.escape(ref["page"]) + r"/talk/" + re.escape(ref["id"])
        elif kind == "pack":
            if set(ref) != {"kind", "community", "id", "url", "title"} or not _uuid(ref["id"]):
                raise ValueError("invalid_agent_reference")
            route = r"/community/packs/" + re.escape(ref["id"])
        else:
            raise ValueError("invalid_agent_reference")
        if not re.fullmatch(route, ref["url"]):
            raise ValueError("invalid_agent_reference_url")
    parent = snapshot.get("parent")
    if parent is not None and (not isinstance(parent, dict) or set(parent) != {"shareId", "version", "hash"}
            or not _uuid(parent["shareId"]) or type(parent["version"]) is not int or not 1 <= parent["version"] <= MAX_SAFE_INTEGER
            or not isinstance(parent["hash"], str) or not _HASH.fullmatch(parent["hash"])):
        raise ValueError("invalid_agent_parent")
    if not isinstance(source, dict) or set(source) != {"url", "share_id", "version", "hash", "author"}:
        raise ValueError("invalid_agent_source")
    canonical(source)
    author = source["author"]
    if not _uuid(source["share_id"]) or type(source["version"]) is not int or not 1 <= source["version"] <= MAX_SAFE_INTEGER \
            or not isinstance(source["hash"], str) or source["hash"] != digest(snapshot) \
            or source["url"] != f"https://vincs.io/community/a/{source['share_id']}/v/{source['version']}" \
            or not isinstance(author, dict) or set(author) != {"handle", "deleted"} or type(author["deleted"]) is not bool \
            or (author["handle"] is not None and not _string(author["handle"], limit=40)):
        raise ValueError("invalid_agent_provenance")
    return snapshot, source


def _target(store, owner, ident):
    got = query.node(ident, owner=owner, store=store)
    if not got.get("executed"):
        raise ValueError("agent_target_unavailable")
    return got["node"] if got.get("found") else None


def _target_receipt(node):
    # 시간 범위 판정은 읽을 때 바뀔 수 있으므로 저장 열만 해시에 넣는다.
    stored = {k: v for k, v in node.items() if k not in {"temporal", "kind"}}
    return {"id": pkns.strip(node["id"]), "hash": hashlib.sha256(
        operation_receipts.canonical(stored).encode("utf-8")).hexdigest(), "rev": node.get("rev")}


def _destination(owner, local=False):
    if local:
        if not isinstance(owner, str) or not owner.startswith("user:") or not 5 < len(owner) <= 120 or "\x1f" in owner:
            raise ValueError("invalid_agent_destination")
        return
    if not isinstance(owner, str) or not _OWNER.fullmatch(owner) or not _uuid(owner.split(":", 1)[1]):
        raise ValueError("invalid_agent_destination")
    if not pkns.enabled():
        raise ValueError("agent_import_requires_namespaced_keys")


def preview(store, *, owner, snapshot, source, target_id=None, local=False):
    _destination(owner, local)
    validate(snapshot, source)
    original = snapshot["role"]["id"]
    ident = target_id or original
    if not isinstance(ident, str) or not _ROLE.fullmatch(ident) or len(ident) > 220 or not role_contract.is_role_id(ident):
        raise ValueError("invalid_agent_target")
    target = _target(store, owner, ident)
    candidate = original
    # 동일한 짧은 해시도 기존 역할을 덮지 않는다. 끝까지 겹치면 명시적으로 멈춘다.
    for width in [0, 6, 12, 24, 64]:
        candidate = original + ("-" + source["hash"][:width] if width else "")
        occupied = _target(store, owner, candidate) is not None
        if local and not pkns.enabled():
            rows = store.run("MATCH (n:Concept) WHERE n.concept_id=$id RETURN count(n) AS n", {"id": candidate})
            if not rows:
                raise ValueError("agent_target_unavailable")
            occupied = bool(rows[0]["n"])
        if not occupied:
            break
    else:
        raise ValueError("agent_separate_id_collision")
    return {"kind": "community-agent-preview", "executed": True, "node_id": candidate,
            "target": _target_receipt(target) if target else None, "source_hash": source["hash"],
            "version": source["version"], "status": "proposed"}


def validate_import(*, owner, snapshot, source, mode="separate", expected_target=None, local=False):
    _destination(owner, local)
    validate(snapshot, source)
    if mode not in {"separate", "replace"}:
        raise ValueError("invalid_agent_import_mode")
    if mode == "replace":
        if not isinstance(expected_target, dict) or set(expected_target) != {"id", "hash", "rev"} \
                or not isinstance(expected_target["id"], str) or not _ROLE.fullmatch(expected_target["id"]) \
                or len(expected_target["id"]) > 220 or not role_contract.is_role_id(expected_target["id"]) \
                or not isinstance(expected_target["hash"], str) or not _HASH.fullmatch(expected_target["hash"]) \
                or type(expected_target["rev"]) is not int or expected_target["rev"] < 1:
            raise ValueError("agent_replace_requires_target_snapshot")
    elif expected_target is not None:
        raise ValueError("agent_separate_rejects_replace_target")


def apply_import(store, *, owner, snapshot, source, mode="separate", expected_target=None, local=False, max_domains=None):
    validate_import(owner=owner, snapshot=snapshot, source=source, mode=mode, expected_target=expected_target, local=local)
    if mode == "replace":
        existing = _target(store, owner, expected_target["id"])
        if existing is None or _target_receipt(existing) != expected_target:
            return {"executed": False, "error": "agent_target_changed", "status": "conflict"}
        ident = expected_target["id"]
    else:
        ident = preview(store, owner=owner, snapshot=snapshot, source=source, local=local)["node_id"]
    role = snapshot["role"]
    props = copy.deepcopy(role["props"])
    props.update(active=False, status="proposed", community_source=copy.deepcopy(source),
                 shared_snapshot=copy.deepcopy(snapshot))
    node = {"id": ident, "type": "concept", "title": role["label"], "domain": "agent",
            "tags": role["tags"], "one_liner": props["mission"], "props": props}
    if mode == "replace":
        node["expected_rev"] = expected_target["rev"]
    else:
        node["create_only"] = True
    result = fragment.merge_fragment({"owner": owner, "nodes": [node], "edges": []}, store=store, max_domains=max_domains)
    if not result.get("executed") or result.get("partial") or result.get("warnings"):
        return {"executed": False, "error": result.get("error") or "agent_import_not_complete", "warnings": result.get("warnings", [])}
    got = _target(store, owner, ident)
    if got is None or got.get("props") != props:
        return {"executed": False, "error": "agent_import_readback_failed"}
    return {"kind": "community-agent-import", "executed": True, "node_id": ident,
            "owner": got["owner_id"],
            "rev": got["rev"], "status": "proposed", "source_hash": source["hash"],
            "version": source["version"], "mode": mode}


def import_result(store, *, owner, mutation_id, durability_root=None):
    _destination(owner)
    result = operation_receipts.status(store, owner=owner, operation_id=mutation_id)
    if result.get("found") and result.get("operation") != "community-agent-import":
        return {"executed": False, "error": "agent_mutation_not_found", "state": "unknown"}
    proof = result.get("agent_durable_proof")
    historical = result.get("result") or {}
    if (isinstance(proof, dict) and proof.get("owner") == owner and proof.get("operation_id") == mutation_id
            and proof.get("request_hash") == result.get("request_hash") and result.get("state") == "committed"
            and result.get("applied") is True and proof.get("source_hash") == historical.get("source_hash")
            and proof.get("version") == historical.get("version") and type(proof.get("sequence")) is int
            and proof["sequence"] > 0):
        # 표식은 첫 게시 뒤 쓰므로 그보다 뒤의 검증된 세대가 있어야 표식까지 영속적이다.
        if durability_root:
            from store import durable_generation as dg
            for sequence, marker in dg._commit_paths(Path(durability_root)):
                if sequence <= proof["sequence"]:
                    continue
                try:
                    dg._validated(Path(durability_root), sequence, marker)
                except (dg.GenerationError, OSError, ValueError):
                    continue
                result.update(durability="published_generation", durable_sequence=sequence)
                break
    return result


def import_file(pack, *, owner, store, max_domains=None):
    """표시한 Agent 파일만 안전 경로로 보낸다. 일반 팩 MERGE로 강등하지 않는다."""
    try:
        if (type(pack.get("vinc_pack")) is not int or pack["vinc_pack"] != 1
                or pack.get("kind") != "community-agent"
                or set(pack) - {"vinc_pack", "kind", "manifest", "fragment", "executed"}):
            raise ValueError("invalid_agent_pack")
        nodes = pack["fragment"]["nodes"]
        if not isinstance(nodes, list) or len(nodes) != 1 or pack["fragment"].get("edges"):
            raise ValueError("invalid_agent_pack")
        snapshot = nodes[0]["props"]["shared_snapshot"]
        source = pack["manifest"]["agent_source"]
        validate(snapshot, source)
        if pack["manifest"].get("license") != snapshot["license"]:
            raise ValueError("invalid_agent_pack_license")
        local = not pkns.enabled()
        validate_import(owner=owner, snapshot=snapshot, source=source, local=local)
        arguments = {"snapshot": snapshot, "source": source, "mode": "separate", "local_file": True}
        mutation = str(uuid5(NAMESPACE_URL, owner + ":community-agent-file:" + digest(arguments)))
        def apply():
            try:
                return apply_import(store, owner=owner, snapshot=snapshot, source=source,
                                    local=local, max_domains=max_domains)
            except ValueError as exc:
                return {"executed": False, "error": str(exc)}
        return operation_receipts.execute(store, owner=owner, operation="community-agent-import",
            arguments=arguments, operation_id=mutation, apply=apply)
    except (KeyError, TypeError, ValueError):
        return {"executed": False, "error": "invalid_community_agent_pack"}


def inspect_pack(pack):
    """계정 게시 전에 기존 컨테이너를 제한적으로 읽는다. 적재나 파일 추출은 하지 않는다."""
    from . import packfile
    maximum = 4 * 1024 * 1024
    if not isinstance(pack, dict) or set(pack) != {"encoding", "content"} or pack["encoding"] != "base64" \
            or not isinstance(pack["content"], str) or len(pack["content"]) > maximum:
        raise ValueError("invalid_community_pack_input")
    try:
        raw = base64.b64decode(pack["content"], validate=True)
        if packfile.is_zip(raw):
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]
                if len(entries) > 1024 or len(names) != len(set(names)) \
                        or sum(entry.file_size for entry in entries) > maximum \
                        or any(entry.flag_bits & 1 or len(entry.filename) > 100 or not re.fullmatch(
                            r"(?:manifest\.json|fragment\.json|documents/[0-9]{4,8}\.json)", entry.filename) for entry in entries):
                    raise ValueError("community_pack_container_refused")
        decoded = packfile.from_bytes(raw)
        if (not isinstance(decoded, dict) or type(decoded.get("vinc_pack")) is not int or decoded["vinc_pack"] != 1
                or not isinstance(decoded.get("fragment"), dict)):
            raise ValueError("invalid_community_pack")
        result = {"executed": True, "pack": decoded, "content_hash": hashlib.sha256(raw).hexdigest()}
        if len(operation_receipts.canonical(result).encode("utf-8")) > maximum:
            raise ValueError("community_pack_response_too_large")
        return result
    except (ValueError, TypeError, UnicodeError, zipfile.BadZipFile, KeyError):
        raise ValueError("community_pack_inspection_refused") from None
