"""격리된 운영 사본의 토픽 의존성을 식별자 없는 집계로 조사한다.

근거: [[vinc.plan.large-topic-ownership-conversion-2026-10-14]] G0/G6.
이 검사는 이전 실행이나 용량 합격 판정이 아니다.
"""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import topic_conversion as conversion
from store.scoped_topic_inventory import read_scoped_inventory
from store.topic_manifest import TopicManifest
from store.topic_transfer import TransferRefused


def audit(store, source, topic="vinc"):
    """동일 읽기 트랜잭션 안에서 검사하고 행·해시·소유자 식별자는 내보내지 않는다."""
    with tempfile.TemporaryDirectory(prefix="vinc-topic-audit-") as directory:
        with TopicManifest(Path(directory) / "inventory.sqlite", deadline=time.monotonic() + 180) as scratch:
            with conversion._inventory_read_transaction(store):
                inventory = read_scoped_inventory(store, source=source,
                    target=conversion.PREFLIGHT_TARGET, topic=topic, manifest=scratch)
                plan = conversion._plan(inventory, source, conversion.PREFLIGHT_TARGET, topic)
                if not conversion._scope_response_safe(inventory.snapshot, source):
                    return {"diagnostic_only": True, "complete": False, "error": "scope_inconsistent"}
                selected = set(plan.selected)
                boundary = Counter()
                resource_nodes = Counter()
                labels_by_id = {}
                for label, key in selected:
                    labels_by_id.setdefault(key, set()).add(label)
                for blocker in plan.blockers:
                    code, _, key = blocker.partition(":")
                    if code == "external_attachments_not_supported":
                        key = key.partition(":")[2]
                    if code in ("external_source_file_not_supported", "external_attachments_not_supported",
                                "external_resource_reference_not_supported"):
                        for label in labels_by_id.get(key, ("unresolved",)):
                            resource_nodes[code + ":" + label] += 1
                for edge in inventory.snapshot["edges"]:
                    a = (edge["from_label"], edge["from"])
                    b = (edge["to_label"], edge["to"])
                    if (a in selected) != (b in selected):
                        outside = b if a in selected else a
                        # 이름은 검증된 스키마 라벨뿐이다. 끝점 값은 집계에 싣지 않는다.
                        boundary[edge["label"] + ":" + outside[0]] += 1
                history = {}
                for label in ("_Tombstone", "_EdgeTombstone", "_DocTombstone", "_MergeRejection"):
                    history[label] = int(store.run(
                        f"MATCH (n:{label}) WHERE n.owner_id=$owner RETURN count(n) AS count",
                        {"owner": source}, raw=True)[0]["count"])
                return {"diagnostic_only": True, "complete": True, "transfer_qualified": False,
                    "documents": len(plan.documents), "selected_counts": dict(Counter(k for k, _ in plan.selected)),
                    "retained_rows": inventory.retained_rows, "retained_bytes": inventory.retained_bytes,
                    "inspected_rows": inventory.inspected_rows,
                    "unique_dependency_counts": dict(Counter(b.split(":", 1)[0] for b in plan.blockers)),
                    "resource_dependency_node_counts": dict(resource_nodes),
                    "boundary_relationship_counts": dict(boundary), "source_history_counts": history,
                    "document_ownership": conversion._document_scope_summary(inventory.snapshot, plan, source),
                    "document_missing_evidence": conversion._missing_document_evidence(inventory.snapshot, plan, source)}


def main():
    # 운영 사본은 GitHub 러너의 고정 위치만 받는다. 로컬 운영 DB 인자는 없다.
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", "")).resolve()
    if os.environ.get("GITHUB_ACTIONS") != "true" or not workspace.is_dir():
        return 2
    report = {"diagnostic_only": True, "complete": False, "error": "audit_failed"}
    store = None
    try:
        owner = os.environ["AUDIT_OWNER_UUID"]
        if str(UUID(owner)) != owner:
            raise ValueError("invalid owner")
        path = workspace / "restored" / "db"
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise ValueError("invalid restored copy")
        from importlib.metadata import version
        if version("ladybug") != "0.21.2":
            raise ValueError("unqualified engine")
        from store.backend import open_store
        from store.config import load_config
        store = open_store(load_config({"data_dir": str(path)}))
        if store is None:
            raise ValueError("store unavailable")
        report = audit(store, "user:" + owner)
    except TransferRefused:
        report["error"] = "inventory_refused"
    except Exception:
        # 예외에는 DB 행이나 경로가 들어갈 수 있으므로 원문을 출력하지 않는다.
        pass
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                report = {"diagnostic_only": True, "complete": False, "error": "close_failed"}
        (workspace / "topic-audit-report.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
