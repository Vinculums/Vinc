"""승인된 격리 사본에서만 실행하는 전환·영속 복구 검사.

근거: [[vinc.plan.large-topic-ownership-conversion-2026-10-14]] G6.
운영 계정 활성화나 운영 DB 접근은 하지 않는다.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import shutil
import sys
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import topic_conversion as legacy
from api.topic_conversion_batches import Batches
from store import durable_generation as generation, topic_batch_manifest as manifest
from store.backend import open_store
from store.config import load_config
from store.persistence import DurableAck, restore_newest
from store.scoped_topic_inventory import read_scoped_inventory, _Reader
from store.topic_manifest import TopicManifest
from store.topic_transfer import TransferRefused, preserved_reference_counts

# 원본 id 없이 종류·원인만 세는 차단. 근거: [[vinc.impl.topic-transfer-g6-rehearsal-2026-10-16]]
DETAILED_BLOCKERS = {"existing_tombstone_history_not_supported", "merge_rejection_topic_membership_unknown"}


def open_copy(path):
    store = open_store(load_config({"data_dir": str(path), "buffer_pool_mb": 512}))
    if store is None:
        raise RuntimeError("copy_unavailable")
    return store


def outside_owners(store, source, target):
    """식별자는 비공개 프로세스에만 두며 전체 행 조회는 페이지로 나눈다."""
    owners = set()
    reader = _Reader(store)
    for label, spec in manifest.SPECS.items():
        if "owner_id" not in spec["cols"]:
            continue
        for row in reader.scan(f"MATCH (n:{label}) WHERE n.owner_id IS NOT NULL",
                               "n.owner_id AS owner", "n.owner_id", {}, ordered=True):
            owner = row["owner"]
            if owner and owner not in (source, target):
                owners.add(owner)
                if len(owners) > 10000:
                    raise TransferRefused("rehearsal_owner_limit")
    return sorted(owners)


def rehearse(original, root, source, *, topic="vinc", max_rows=128):
    """원본 사본을 닫힌 채 보존하고 별도 작업본에서 같은 실행을 복구한다."""
    original, root = Path(original), Path(root)
    if original.is_symlink() or root.exists() or not original.is_file():
        raise ValueError("fresh_copy_required")
    root.mkdir(mode=0o700)
    before = generation._digest(original)
    sidecar_before = {suffix: generation._digest(Path(str(original) + suffix))
                      for suffix in (".wal", ".themes.json") if Path(str(original) + suffix).exists()}
    live = root / "first-local" / "db"
    live.parent.mkdir()
    shutil.copy2(original, live)
    for suffix in (".wal", ".themes.json"):
        sidecar = Path(str(original) + suffix)
        if sidecar.exists():
            if sidecar.is_symlink():
                raise ValueError("unsafe_sidecar")
            shutil.copy2(sidecar, str(live) + suffix)
    target = "team:" + str(uuid4())
    run = str(uuid4())
    snapshot = root / "persistent" / "db"
    snapshot.parent.mkdir()
    store = None
    started = time.monotonic()
    report = {"isolated_copy": True, "qualified": False, "account_activation": "simulated",
              "ordinary_durable_ack": False, "scoped_conversion_publishing": True,
              "buffer_pool_mb": 512, "max_batch_rows": max_rows}
    try:
        store = open_copy(live)
        with TopicManifest(root / "inventory.sqlite", deadline=time.monotonic() + 180) as scratch:
            with legacy._inventory_read_transaction(store):
                inventory = read_scoped_inventory(store, source=source, target=target, topic=topic, manifest=scratch)
                plan = legacy._plan(inventory, source, target, topic, conventions_policy="move_original")
                report.update(documents=len(plan.documents), selected_counts=dict(Counter(k for k, _ in plan.selected)),
                              preserved_references=preserved_reference_counts(plan),
                              retained_rows=inventory.retained_rows, retained_bytes=inventory.retained_bytes)
                if not plan.ready:
                    report["dependency_counts"] = dict(Counter(b.split(":", 1)[0] for b in plan.blockers))
                    report["dependency_detail"] = dict(Counter(":".join(b.split(":", 2)[:2]) for b in plan.blockers
                                                               if b.split(":", 1)[0] in DETAILED_BLOCKERS))
                    return report
                stamp = datetime.now(timezone.utc).isoformat()
                header = manifest.prepare(scratch, plan, run=run, stamp=stamp,
                                          indexes=legacy._index_inventory(store), store=store)
                owners = outside_owners(store, source, target)
                outside = {owner: manifest.untouched_summary(store, scratch, owner) for owner in owners}
                sealed_path = root / "sealed.jsonl"
                sealed = manifest.seal(scratch, sealed_path, header)
            report.update(selection_seconds=round(time.monotonic() - started, 3),
                          manifest_bytes=sealed["bytes"], other_owners_checked=len(owners))

            def make_flow():
                durable = DurableAck(lambda: store, str(live), snapshot_path=str(snapshot))
                def publish(_journal):
                    durable(SimpleNamespace(kind="topic-conversion.batch"), {})
                    return durable.sequence
                return Batches(store, root=durable.root, publisher=publish, run=run,
                               source=source, target=target, topic=topic, max_rows=max_rows,
                               qualified_indexes=True)

            flow = make_flow()
            flow.initialize(sealed_path, sealed["sha256"], manifest.authorized_hash(plan.before_hash, "move_original"),
                            conventions_policy="move_original")
            flow.step()
            checkpoint = flow.status()
            store.close(); store = None
            live = root / "missing-local" / "db"
            # 일반 ACK가 꺼져 있어도 실제 기동 복원 선택기가 같은 세대를 골라야 한다.
            restoration = restore_newest(str(live), str(snapshot))
            if not restoration.startswith("restored-generation:"):
                raise RuntimeError("missing_local_restore_failed")
            original_themes = Path(str(original) + ".themes.json")
            if original_themes.exists():
                shutil.copy2(original_themes, str(live) + ".themes.json")
            store = open_copy(live)
            flow = make_flow()
            if flow.status()["batches"] != checkpoint["batches"]:
                raise RuntimeError("restored_ledger_changed")
            if not legacy.fenced(store, source) or not legacy.fenced(store, target):
                raise RuntimeError("restored_fence_missing")
            report["missing_local_recovery"] = True
            max_step = 0.0
            while flow.status()["phase"] != "verifying":
                begin = time.monotonic()
                flow.step()
                max_step = max(max_step, time.monotonic() - begin)
            verified = flow.verify()
            if verified["status"] != "graph_committed":
                raise RuntimeError("verification_incomplete")
            # 계정 서버 호출 없이 사본에만 활성화 확인을 공급한다.
            result = flow.release(account_active=True)
            scratch.deadline = time.monotonic() + 180
            with legacy._inventory_read_transaction(store):
                unchanged = all(manifest.untouched_summary(store, scratch, owner) == outside[owner] for owner in owners)
            if not unchanged or result["status"] != "active" or legacy.fenced(store, source) or legacy.fenced(store, target):
                raise RuntimeError("copy_acceptance_failed")
            report.update(qualified=True, status=result["status"], batches=result["batches"],
                          retained_personal_history=result.get("retained_personal_history", {}),
                          other_owned_rows_unchanged=True, max_step_seconds=round(max_step, 3))
    finally:
        if store is not None:
            store.close()
        report["original_copy_unchanged"] = (generation._digest(original) == before and all(
            generation._digest(Path(str(original) + suffix)) == digest for suffix, digest in sidecar_before.items()))
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if not report["original_copy_unchanged"]:
            raise RuntimeError("original_copy_changed")
    return report


def main():
    # 경로·자격은 보호된 워크플로가 고정한다. 임의 DB 경로 인자는 없다.
    root = Path("/rehearsal")
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("VINC_COPY_REHEARSAL") != "approved":
        return 2
    report = {"isolated_copy": True, "qualified": False, "error": "rehearsal_failed"}
    try:
        from importlib.metadata import version
        owner = os.environ["REHEARSAL_OWNER_UUID"]
        if str(UUID(owner)) != owner or version("ladybug") != "0.21.2":
            raise ValueError("invalid_runtime")
        if os.environ.get("VINC_DURABLE_ACK") != "off" or os.environ.get("VINC_TOPIC_CONVERSION") != "1":
            raise ValueError("invalid_recovery_policy")
        report = rehearse(root / "db", root / "execution", "user:" + owner)
    except Exception:
        # 내용·식별자를 품을 수 있는 예외는 집계 보고서에 싣지 않는다.
        pass
    report["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    for key in ("memory.max", "memory.swap.max", "memory.peak", "cpu.max"):
        path = Path("/sys/fs/cgroup") / key
        report[key] = path.read_text().strip() if path.is_file() else None
    (root / "report.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    return 0 if report.get("qualified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
