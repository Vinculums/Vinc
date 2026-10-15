"""Sealed instructions preserve exact canonical payload without publishing it."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_topic_conversion import native
from test_topic_transfer import SOURCE, TARGET, TOPIC, STAMP
from store import topic_batch_manifest as batches
from store.embedded_schema import node_table_specs, meta_table_specs
from store.scoped_topic_inventory import read_scoped_inventory
from store.topic_manifest import TopicManifest
from store.topic_transfer import TransferRefused, plan_transfer, transformed_snapshot

RUN = "00000000-0000-4000-8000-000000000099"


def prepared(native, tmp_path, *, conventions_policy="retain"):
    manifest = TopicManifest(tmp_path / "scratch.sqlite")
    inventory = read_scoped_inventory(native, source=SOURCE, target=TARGET, topic=TOPIC, manifest=manifest)
    plan = plan_transfer(inventory.snapshot, source=SOURCE, target=TARGET, topic=TOPIC,
                         conventions_policy=conventions_policy)
    header = batches.prepare(manifest, plan, run=RUN, stamp=STAMP, indexes=[], store=native)
    path = tmp_path / "sealed.jsonl"
    sealed = batches.seal(manifest, path, header)
    return manifest, inventory, plan, header, path, sealed


def test_thin_instructions_match_every_approved_legacy_lifecycle_field(native, tmp_path):
    manifest, inventory, plan, header, path, sealed = prepared(native, tmp_path)
    try:
        # The reference is materialized only in this small synthetic test.
        before = {"schema_version": inventory.snapshot["schema_version"],
            "nodes": {label:list(rows) for label,rows in inventory.snapshot["nodes"].items()},
            "meta": {label:list(rows) for label,rows in inventory.snapshot["meta"].items()},
            "edges":list(inventory.snapshot["edges"])}
        from store.topic_transfer import fingerprint
        reference = transformed_snapshot(before, copy.copy(plan), stamp=STAMP)
        specs = {**node_table_specs(), **meta_table_specs()}
        rows = {label:{row[specs[label]["pk"]]:row for row in group[label]}
                for group in (reference["nodes"],reference["meta"]) for label in group}
        for _,item in batches.instructions(path):
            if item["kind"] == "node":
                original = next(row for group in (before["nodes"],before["meta"])
                                for row in group.get(item["label"],[]) if row[specs[item["label"]]["pk"]] == item["old"])
                after = batches.transform_row(original,item,header["binding"])
                assert after == rows[item["label"]][item["new"]]
                assert batches.digest(after) == item["after"]
            elif item["kind"] == "generated":
                row=item["row"]
                assert row == rows[item["label"]][row[specs[item["label"]]["pk"]]]
        assert batches.read(path,sealed["sha256"],binding=header["binding"]) == header
        text=path.read_text()
        assert "Synthetic body" not in text and "encoder_runtime" not in text
        assert "embedding" not in text and '"history"' not in text
        edges=[item for _,item in batches.instructions(path,kind="edge")]
        assert len(edges)==len(before["edges"])
        duplicates=[e for e in edges if e["label"]=="DERIVES_FROM" and e["from"].endswith("concept:a")]
        assert len(duplicates)==2 and duplicates[0]["before"]==duplicates[1]["before"]
    finally:
        manifest.close()


def test_missing_corrupt_foreign_and_truncated_artifacts_are_refused(native,tmp_path):
    manifest,_,_,header,path,sealed=prepared(native,tmp_path)
    try:
        original=path.read_bytes()
        foreign={**header["binding"],"source":"user:foreign"}
        with pytest.raises(TransferRefused,match="manifest_invalid"):
            batches.read(path,sealed["sha256"],binding=foreign)
        for data in [original[:-1],b"{}\n",original.replace(b'"protocol":3',b'"protocol":4')]:
            path.write_bytes(data)
            with pytest.raises(TransferRefused,match="manifest_invalid"):
                batches.read(path,sealed["sha256"],binding=header["binding"])
        path.unlink()
        with pytest.raises(TransferRefused,match="manifest_invalid"):
            batches.read(path,sealed["sha256"],binding=header["binding"])
    finally:
        manifest.close()


def test_publish_is_immutable_and_recovers_an_exact_synced_stage(native,tmp_path):
    manifest,_,_,header,path,sealed=prepared(native,tmp_path)
    try:
        root=tmp_path/"durable"
        staged=root/"topic-manifests"/(RUN+".stage")
        staged.parent.mkdir(parents=True)
        staged.write_bytes(path.read_bytes())
        durable=batches.publish(root,RUN,path,sealed["sha256"])
        assert not staged.exists()
        assert batches.read(durable,sealed["sha256"],binding=header["binding"])==header
        assert batches.publish(root,RUN,path,sealed["sha256"])==durable
        durable.write_bytes(b"corrupt")
        with pytest.raises(TransferRefused,match="manifest_invalid"):
            batches.publish(root,RUN,path,sealed["sha256"])
    finally:
        manifest.close()


def test_outside_vector_proof_reads_every_field_without_selected_payload_rereads(native,tmp_path,monkeypatch):
    outside=SOURCE+'#episode:untopiced-vector'
    native.run('CREATE (n:NodeVec {vec_id:$id,node_id:$node,owner_id:$owner,embedding:$embedding})',
               {'id':outside,'node':'episode:untopiced-vector','owner':SOURCE,'embedding':[1.0,2.0,3.0,4.0]},raw=True)
    disk,_,_,header,_,_=prepared(native,tmp_path)
    original=native.run
    def guarded(query,params=None,**kwargs):
        if 'MATCH (n:NodeVec)' in query and 'n.embedding AS embedding' in query:
            assert params=={'keys':[outside]}, 'Selected vector payload must not be reread for an outside proof'
        return original(query,params,**kwargs)
    monkeypatch.setattr(native,'run',guarded)
    try:
        before=batches.untouched_summary(native,disk,SOURCE)
        assert before==header['untouched_source']
        assert before['rows:NodeVec']['count']==1
        native.run('MATCH (n:NodeVec) WHERE n.vec_id=$id SET n.embedding=$embedding',
                   {'id':outside,'embedding':[1.0,2.0,3.0,-4.0]},raw=True)
        after=batches.untouched_summary(native,disk,SOURCE)
        assert after['rows:NodeVec']['count']==1
        assert after['rows:NodeVec']['sha256']!=before['rows:NodeVec']['sha256']
    finally:
        disk.close()
