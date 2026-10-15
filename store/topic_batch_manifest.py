"""Thin, sealed ownership instructions; canonical payload remains in the graph.

The scratch database is local only. Durable publication uses the existing
generation copy/fsync primitives. [[vinc.plan.large-topic-ownership-conversion-2026-10-14]]
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import OrderedDict
from pathlib import Path
from uuid import UUID

from . import durable_generation as generation, pkns
from .embedded_schema import SCHEMA_VERSION, meta_table_specs, node_table_specs
from .embedded_schema import rel_table_specs
from .topic_transfer import TransferRefused, _KINDS, _canonical, fingerprint, mapped_rejection_key, retained_history_counts

PROTOCOL = 3
LINE_BYTES = 16 * 1024
MANIFEST_BYTES = 128 * 1024 * 1024
SPECS = {**node_table_specs(), **meta_table_specs()}
_VALIDATED = OrderedDict()


def digest(row):
    return hashlib.sha256(_canonical(row).encode()).hexdigest()


def authorized_hash(before_hash, policy):
    if policy == "retain":
        return before_hash
    if policy != "move_original":
        raise TransferRefused("invalid_conventions_policy")
    return digest({"scope": before_hash, "conventions_policy": policy})


def mapped_key(label, key, source, target, row=None):
    if label == "_MergeRejection":
        return mapped_rejection_key(key, source, target)
    if label == "NodeVec":
        node = row["node_id"] if row is not None else key[len(source) + 1:]
        if key != source + "#" + node:
            raise TransferRefused("unsupported_node_vector_key")
        return target + "#" + node
    return target + pkns.SEP + pkns.strip(key) if key.startswith(source + pkns.SEP) else key


def transform_row(row, instruction, binding):
    """Apply only the same enumerated lifecycle differences as the legacy transform."""
    result = dict(row)
    label = instruction["label"]
    result[SPECS[label]["pk"]] = instruction["new"]
    if "owner_id" in result:
        result["owner_id"] = binding["target"]
    if label == "ChunkVec" and result["chunk_id"].startswith(binding["source"] + pkns.SEP):
        result["chunk_id"] = binding["target"] + pkns.SEP + pkns.strip(result["chunk_id"])
    if "sync_seq" in result:
        result["sync_seq"] = instruction["sync_seq"]
        result["updated_at"] = binding["stamp"]
        if "rev" in result:
            result["rev"] = int(result["rev"] or 0) + 1
    return result


def untouched_summary(store, manifest, source):
    """Bounded full-field proof for source rows/edges outside this mutation."""
    from .scoped_topic_inventory import _Reader, _node_projection
    reader = _Reader(store)
    db = manifest.db
    db.execute("CREATE TABLE IF NOT EXISTS affected(label TEXT,key TEXT,PRIMARY KEY(label,key))")
    for _, body in db.execute("SELECT ordinal,body FROM instructions WHERE kind IN ('node','generated')"):
        item = json.loads(body)
        keys = (item['old'], item['new']) if item['kind']=='node' else (item['row'][SPECS[item['label']]['pk']],)
        db.executemany("INSERT OR IGNORE INTO affected VALUES(?,?)", ((item['label'],key) for key in keys))
    def affected(label,key):
        return db.execute("SELECT 1 FROM affected WHERE label=? AND key=?",(label,key)).fetchone() is not None
    result = {}
    for label,spec in SPECS.items():
        pk=spec['pk']
        if 'owner_id' in spec['cols']:
            condition,params="n.owner_id=$source",{'source':source}
        elif label=='_SyncBase':
            condition,params="n.node_id STARTS WITH $prefix",{'prefix':source+pkns.SEP}
        elif label=='_SyncMeta':
            condition,params="n.key CONTAINS $source AND NOT n.key STARTS WITH $journal",{'source':source,'journal':'topic-conversion:'}
        else:
            continue
        sha,count=hashlib.sha256(),0
        def full_rows(keys):
            from .topic_manifest import ManifestLimit
            split=False
            try:
                values=reader.page(f'MATCH (n:{label}) WHERE n.{pk} IN $keys RETURN {_node_projection(label)} ORDER BY n.{pk}',{'keys':keys})
            except ManifestLimit as exc:
                if exc.reason!='page_bytes':raise
                if len(keys)==1:raise ManifestLimit('single_row_bytes',**exc.details) from None
                split=True
            if split:
                middle=len(keys)//2
                yield from full_rows(keys[:middle])
                yield from full_rows(keys[middle:])
                return
            if [value[pk] for value in values]!=keys:
                raise TransferRefused('conversion_outside_payload_changed')
            yield from values
        keys=[]
        def hash_rows(keys):
            nonlocal count
            for value in full_rows(keys):
                manifest.check_time()
                sha.update((_canonical(value)+'\n').encode());count+=1
        # 먼저 키만 읽는다. 선택된 본문과 벡터를 다시 역직렬화하지 않고,
        # 범위 밖의 행만 모든 열을 읽어 같은 순서와 전체 해시로 확인한다.
        for coordinate in reader.scan(f'MATCH (n:{label}) WHERE {condition}',f'n.{pk} AS {pk}',f'n.{pk}',params,ordered=True):
            manifest.check_time()
            if affected(label,coordinate[pk]):continue
            keys.append(coordinate[pk])
            if len(keys)==32:
                hash_rows(keys);keys=[]
        if keys:hash_rows(keys)
        result['rows:'+label]={'count':count,'sha256':sha.hexdigest()}
    for spec in rel_table_specs():
        src,dst,label=spec['from'],spec['to'],spec['label']
        conditions=[f'{alias}.owner_id=$source' for alias,kind in (('a',src),('b',dst)) if 'owner_id' in SPECS[kind]['cols']]
        if not conditions:continue
        src_pk,dst_pk=SPECS[src]['pk'],SPECS[dst]['pk']
        columns=[f'a.{src_pk} AS src',f'b.{dst_pk} AS dst']+[f'r.{field} AS {field}' for field in spec['props']]
        order=', '.join([f'a.{src_pk}',f'b.{dst_pk}']+[f'r.{field}' for field in spec['props']])
        sha,count=hashlib.sha256(),0
        for value in reader.scan(f"MATCH (a:{src})-[r:{label}]->(b:{dst}) WHERE {' OR '.join(conditions)}",', '.join(columns),order,{'source':source},ordered=True):
            manifest.check_time()
            if affected(src,value['src']) or affected(dst,value['dst']):continue
            sha.update((_canonical(value)+'\n').encode());count+=1
        result['edges:'+label+':'+src+':'+dst]={'count':count,'sha256':sha.hexdigest()}
    return result


def prepare(manifest, plan, *, run, stamp, indexes, store):
    """Stream thin instructions into the inventory's local scratch transaction."""
    UUID(run)
    if not plan.ready or fingerprint(manifest.snapshot) != plan.before_hash:
        raise TransferRefused("plan_stale" if plan.ready else "dependency_blocked")
    binding = {"source": plan.source, "target": plan.target, "topic": plan.topic,
               "run": run, "stamp": stamp}
    db = manifest.db
    db.execute("CREATE TABLE instructions(ordinal INTEGER PRIMARY KEY, kind TEXT, body TEXT NOT NULL)")
    db.execute("CREATE INDEX instruction_kinds ON instructions(kind,ordinal)")
    db.execute("CREATE TABLE edge_multiplicity(digest TEXT PRIMARY KEY,total INTEGER NOT NULL)")
    selected = set(plan.selected)
    seqs = {}
    for owner in (plan.source, plan.target):
        row = manifest.snapshot["meta"]["_SyncMeta"].get("seq:" + owner)
        seqs[owner] = int(row["value"]) if row else 0
    source_before, target_before = seqs[plan.source], seqs[plan.target]
    count = 0
    thin_bytes = 0
    projected_bytes = 0

    def emit(instruction):
        nonlocal count, thin_bytes
        body = _canonical(instruction)
        size = len(body.encode()) + 1
        if size > LINE_BYTES:
            raise TransferRefused("conversion_manifest_row_too_large")
        thin_bytes += size
        if thin_bytes > MANIFEST_BYTES:
            raise TransferRefused("conversion_manifest_storage_limit")
        db.execute("INSERT INTO instructions(kind,body) VALUES(?,?)", (instruction["kind"], body))
        count += 1

    def generated(label, row):
        nonlocal projected_bytes
        projected_bytes += len(_canonical(row).encode())
        emit({"kind": "generated", "label": label, "row": row, "after": digest(row)})

    for group in ("nodes", "meta"):
        for label, rows in manifest.snapshot[group].items():
            pk = SPECS[label]["pk"]
            for row in rows:
                old = row[pk]
                if (label, old) not in selected:
                    if label != "_SyncMeta" or not old.startswith("seq:"):
                        emit({"kind": "unchanged", "label": label, "key": old, "digest": digest(row)})
                    continue
                item = {"kind": "node", "label": label, "old": old,
                        "new": mapped_key(label, old, plan.source, plan.target, row), "before": digest(row)}
                if "sync_seq" in row:
                    seqs[plan.target] += 1
                    item["sync_seq"] = seqs[plan.target]
                after = transform_row(row, item, binding)
                item["after"] = digest(after)
                item["payload_bytes"] = len(_canonical(after).encode())
                projected_bytes += item["payload_bytes"]
                emit(item)
                if label in _KINDS + ("Bridge",):
                    seqs[plan.source] += 1
                    generated("_Tombstone", {"node_id": old, "label": label, "owner_id": plan.source,
                        "deleted_at": stamp, "rev": after.get("rev") or 1,
                        "sync_seq": seqs[plan.source], "origin": "topic-transfer"})
                if label == "_DocMeta":
                    seqs[plan.source] += 1
                    generated("_DocTombstone", {"doc_id": old, "owner_id": plan.source,
                        "deleted_at": stamp, "sync_seq": seqs[plan.source]})
                generated("_SyncMeta", {"key": "topic-transfer:moved:" + plan.source + ":" + label + ":" + pkns.strip(old),
                                        "value": plan.target})
    # Edges stay at the source until all rewiring batches finish. A sealed
    # endpoint locator and property digest preserve identical parallel edges.
    for edge in manifest.snapshot["edges"]:
        edge_hash = digest(edge)
        db.execute("INSERT INTO edge_multiplicity VALUES(?,1) ON CONFLICT(digest) DO UPDATE SET total=total+1", (edge_hash,))
        endpoints = [(edge["from_label"], edge["from"]), (edge["to_label"], edge["to"])]
        after = dict(edge)
        after["from"] = mapped_key(*endpoints[0], plan.source, plan.target) if endpoints[0] in selected else edge["from"]
        after["to"] = mapped_key(*endpoints[1], plan.source, plan.target) if endpoints[1] in selected else edge["to"]
        emit({"kind": "edge", "label": edge["label"], "from_label": edge["from_label"],
              "to_label": edge["to_label"], "from": edge["from"], "to": edge["to"],
              "mapped_from": after["from"], "mapped_to": after["to"],
              "before": edge_hash, "after": digest(after), "payload_bytes": len(_canonical(after).encode())})
        projected_bytes += len(_canonical(after).encode())
    for ordinal, body in db.execute("SELECT ordinal,body FROM instructions WHERE kind='edge'"):
        instruction = json.loads(body)
        instruction["multiplicity"] = db.execute("SELECT total FROM edge_multiplicity WHERE digest=?", (instruction["before"],)).fetchone()[0]
        db.execute("UPDATE instructions SET body=? WHERE ordinal=?", (_canonical(instruction), ordinal))
    for owner, value in seqs.items():
        generated("_SyncMeta", {"key": "seq:" + owner, "value": str(value)})
    header = {"protocol": PROTOCOL, "executor": "ownership-batches-v1", "schema_version": SCHEMA_VERSION,
              "binding": binding, "before_hash": plan.before_hash, "instructions": count,
              "indexes": list(indexes), "selected_rows": len(selected), "documents": len(plan.documents),
              "projected_payload_bytes": projected_bytes,
              "sequence_before": {"source": source_before, "target": target_before},
              "sequence_after": {"source": seqs[plan.source], "target": seqs[plan.target]}}
    header['untouched_source'] = untouched_summary(store,manifest,plan.source)
    header['conventions_policy'] = plan.conventions_policy
    header['retained_personal_history'] = retained_history_counts(manifest.snapshot, plan)
    return header


def seal(manifest, destination, header):
    destination = Path(destination)
    sha = hashlib.sha256()
    with os.fdopen(os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as out:
        for item in [header]:
            line = (_canonical(item) + "\n").encode()
            if len(line) > LINE_BYTES:
                raise TransferRefused("conversion_manifest_row_too_large")
            out.write(line); sha.update(line)
        for (body,) in manifest.db.execute("SELECT body FROM instructions ORDER BY ordinal"):
            manifest.check_time()
            line = (body + "\n").encode()
            out.write(line); sha.update(line)
        out.flush(); os.fsync(out.fileno())
    generation._sync_dir(destination.parent)
    return {"sha256": sha.hexdigest(), "bytes": destination.stat().st_size}


def publish(root, run, source, expected):
    """Publish once before the journal's durable DB generation can reference it."""
    UUID(run)
    root = Path(root) / "topic-manifests"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise TransferRefused("conversion_manifest_invalid")
    target = root / (run + ".jsonl")
    if target.exists():
        if target.is_symlink() or generation._digest(target)["sha256"] != expected:
            raise TransferRefused("conversion_manifest_invalid")
        return target
    stage = root / (run + ".stage")
    if stage.exists():
        if stage.is_symlink() or generation._digest(stage)["sha256"] != expected:
            raise TransferRefused("conversion_manifest_invalid")
        os.replace(stage, target)
        generation._sync_dir(root)
        return target
    try:
        actual = generation._copy_synced(Path(source), stage)
        os.chmod(stage, 0o600)
        if actual["sha256"] != expected:
            raise TransferRefused("conversion_manifest_invalid")
        os.replace(stage, target)
        generation._sync_dir(root)
    except FileExistsError:
        # An interrupted publish has no authoritative journal entry. Do not
        # overwrite it or claim that it is the sealed committed artifact.
        raise TransferRefused("conversion_manifest_publish_pending") from None
    return target


def read(path, expected, *, binding):
    """Verify the entire bounded immutable artifact before yielding instructions."""
    path = Path(path)
    try:
        if path.is_symlink() or path.stat().st_size > MANIFEST_BYTES:
            raise ValueError
        if generation._digest(path)["sha256"] != expected:
            raise ValueError
        if expected in _VALIDATED:
            header=json.loads(_VALIDATED[expected])
            if header['binding'] != binding:raise ValueError
            # Every byte was hashed again. Schema validation of those exact
            # immutable bytes can be reused without decoding every instruction.
            _VALIDATED.move_to_end(expected)
            return header
        with path.open("rb") as source:
            header_line = source.readline(LINE_BYTES + 1)
            header = json.loads(header_line)
            if (len(header_line) > LINE_BYTES or not header_line.endswith(b"\n")
                    or header.get("protocol") != PROTOCOL or header.get("schema_version") != SCHEMA_VERSION
                    or header.get("executor") != "ownership-batches-v1" or header.get("binding") != binding
                    or not isinstance(header.get('untouched_source'),dict)
                    or type(header.get("instructions")) is not int or header["instructions"] < 0):
                raise ValueError
            count = 0
            while line := source.readline(LINE_BYTES + 1):
                if len(line) > LINE_BYTES or not line.endswith(b"\n"):
                    raise ValueError
                item = json.loads(line)
                if item.get("kind") not in ("node", "edge", "generated", "unchanged"):
                    raise ValueError
                count += 1
            if count != header["instructions"]:
                raise ValueError
        _VALIDATED[expected]=_canonical(header)
        while len(_VALIDATED)>64:_VALIDATED.popitem(last=False)
    except (OSError, ValueError, AttributeError):
        raise TransferRefused("conversion_manifest_invalid") from None
    return header


def instructions(path, *, kind=None, after=0):
    """Internal reader; call read() against the journal's hash first."""
    with Path(path).open("rb") as source:
        source.readline(LINE_BYTES + 1)
        for ordinal, line in enumerate(source, 1):
            if ordinal <= after:
                continue
            item = json.loads(line)
            if kind is None or item["kind"] == kind:
                yield ordinal, item
