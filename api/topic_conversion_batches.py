"""Native bounded ownership batches, kept disconnected until qualification passes.

Each native transaction includes its ledger. An uncertain publisher resumes that
ledger forward, never replays CREATE. [[vinc.plan.large-topic-ownership-conversion-2026-10-14]]
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from uuid import UUID

from store import durable_generation as dg
from store import topic_batch_manifest as manifest
from store.embedded_backend import DDL_GUARD, _import_driver
from store.embedded_schema import rel_table_specs
from store.topic_transfer import TransferRefused, _canonical
from . import topic_conversion as legacy

RELATIONS = {(r["label"], r["from"], r["to"]): r for r in rel_table_specs()}
PHASES = ("creating_target_keys", "rewiring", "retiring_source_keys", "verifying", "graph_committed")


def restore_terminal_visibility(store, root):
    """기동 전에 완료 영수증을 재검증한다. 그래프 쓰기나 배치 재개는 하지 않는다."""
    result = {"verified": 0, "blocked": 0}
    if root is None:
        return result
    entries = store.run(
        "MATCH (m:_SyncMeta) WHERE m.key STARTS WITH $prefix RETURN m.key AS key, m.value AS value",
        {"prefix": legacy.PREFIX}, raw=True)

    def no_publication(_journal):
        raise TransferRefused("conversion_restore_is_read_only")

    for entry in entries:
        try:
            journal = json.loads(entry["value"])
            if not isinstance(journal, dict):
                raise ValueError("invalid conversion journal")
            if journal.get("inventory_version") != manifest.PROTOCOL or journal.get("status") not in ("active", "cancelled"):
                continue
            flow = Batches(store, root=root, publisher=no_publication,
                           run=entry["key"][len(legacy.PREFIX):],
                           **{key: journal[key] for key in ("source", "target", "topic")})
            status = flow.status()
            result["verified" if status["status"] in ("active", "cancelled") else "blocked"] += 1
        except (TransferRefused, OSError, ValueError, TypeError, KeyError):
            # 해당 실행의 울타리는 남긴다. 다른 소유자의 정상 기동은 막지 않는다.
            result["blocked"] += 1
    return result


def row(store, label, key):
    if label not in manifest.SPECS:
        raise TransferRefused("conversion_manifest_invalid")
    spec = manifest.SPECS[label]
    fields = ", ".join(f"n.{column} AS {column}" for column in spec["cols"])
    result = store.run(f"MATCH (n:{label}) WHERE n.{spec['pk']}=$key RETURN {fields} LIMIT 2", {"key": key}, raw=True)
    if len(result) > 1:
        raise TransferRefused("duplicate_primary_key")
    return result[0] if result else None


def _put(store, label, value, *, existing=False):
    spec = manifest.SPECS[label]
    pk = spec["pk"]
    setters = ", ".join(f"n.{field}=${field}" for field in value if field != pk)
    if existing:
        query = f"MATCH (n:{label}) WHERE n.{pk}=${pk}"
    else:
        query = f"CREATE (n:{label} {{{pk}:${pk}}})"
    store.run(query + (" SET " + setters if setters else ""), value, raw=True)


class Batches:
    def __init__(self, store, *, root, publisher, run, source, target, topic,
                 max_rows=128, max_bytes=4 * 1024 * 1024, qualified_indexes=False):
        self.store, self.root, self.publisher = store, Path(root), publisher
        self.run = run
        try:
            if str(UUID(run)) != run:raise ValueError
        except (ValueError,TypeError,AttributeError):
            raise TransferRefused('invalid_conversion_run') from None
        self.identity = {"source": source, "target": target, "topic": topic}
        self.max_rows, self.max_bytes = max_rows, max_bytes
        self.qualified_indexes = qualified_indexes
        if max_rows < 1 or max_bytes < 1:
            raise ValueError("positive batch limits required")
        driver, _ = _import_driver()
        if driver is None or str(getattr(driver, "__version__", "")) != "0.21.2":
            raise TransferRefused("conversion_engine_unqualified")

    def _journal(self):
        journal = legacy._get(self.store, self.run)
        if journal is None:
            raise TransferRefused("run_not_prepared")
        legacy._identity(journal, **self.identity)
        if journal.get("inventory_version") != manifest.PROTOCOL:
            raise TransferRefused("unsupported_conversion_inventory_version")
        return journal

    def _artifact(self, journal):
        path = self.root / "topic-manifests" / (self.run + ".jsonl")
        binding = {**self.identity, "run": self.run, "stamp": journal["stamp"]}
        try:
            if self.root.is_symlink() or path.parent.is_symlink():
                raise TransferRefused('conversion_manifest_invalid')
            header = manifest.read(path, journal["manifest_hash"], binding=binding)
            policy = header.get("conventions_policy", "retain")
            if (manifest.authorized_hash(header["before_hash"], policy) != journal["expected"]
                    or policy != journal.get("conventions_policy", "retain")
                    or header["indexes"] != journal["indexes"]):
                raise TransferRefused("conversion_manifest_invalid")
        except TransferRefused:
            self._forget_terminal(journal)
            raise
        return path, header

    def _forget_terminal(self, journal):
        getattr(self.store,'_topic_conversion_terminals',set()).discard(_canonical(journal))

    def initialize(self, sealed, sha, expected, *, conventions_policy="retain"):
        """Caller has revalidated a fenced complete inventory against expected."""
        previous = legacy._get(self.store, self.run)
        if previous is not None:
            journal = self._journal()
            if journal.get("conventions_policy", "retain") != conventions_policy:
                raise TransferRefused("run_conventions_policy_changed")
            if journal["expected"] != expected or journal["manifest_hash"] != sha:
                raise TransferRefused("run_id_reused")
            self._artifact(journal)
            self._publish(journal)
            return
        legacy.check_read(self.store, self.identity["source"])
        legacy.check_read(self.store, self.identity["target"])
        # Validate the source-generated binding before publishing an artifact.
        with Path(sealed).open("rb") as source:
            header = json.loads(source.readline(manifest.LINE_BYTES + 1))
        binding = header.get("binding", {})
        if any(binding.get(k) != v for k, v in {**self.identity, "run": self.run}.items()):
            raise TransferRefused("conversion_manifest_invalid")
        header = manifest.read(sealed, sha, binding=binding)
        if header["indexes"] and not self.qualified_indexes:
            # Indexed production execution is deliberately closed until the
            # native drop/restore and shared-maintenance qualification passes.
            raise TransferRefused("conversion_index_execution_unqualified")
        if header.get("conventions_policy", "retain") != conventions_policy:
            raise TransferRefused("run_conventions_policy_changed")
        if manifest.authorized_hash(header["before_hash"], conventions_policy) != expected:
            raise TransferRefused("plan_stale")
        from store.topic_manifest import TopicManifest
        from store.scoped_topic_inventory import read_scoped_inventory
        from store.topic_transfer import fingerprint, plan_transfer
        with tempfile.TemporaryDirectory(prefix="vinc-conversion-recheck-") as directory:
            with TopicManifest(Path(directory) / "source.sqlite", deadline=time.monotonic() + 180) as current:
                with legacy._inventory_read_transaction(self.store):
                    inventory = read_scoped_inventory(self.store, **self.identity, manifest=current)
                    plan = legacy._plan(inventory, **self.identity, conventions_policy=conventions_policy)
                    if not plan.ready or fingerprint(inventory.snapshot) != header["before_hash"]:
                        raise TransferRefused("plan_stale")
                    if legacy._index_inventory(self.store) != header["indexes"]:
                        raise TransferRefused("conversion_index_inventory_changed")
                    if 'untouched_source' in header:
                        current.db.execute("CREATE TABLE instructions(ordinal INTEGER PRIMARY KEY,kind TEXT,body TEXT)")
                        current.db.executemany("INSERT INTO instructions VALUES(?,?,?)",((ordinal,item['kind'],_canonical(item)) for ordinal,item in manifest.instructions(sealed)))
                        if manifest.untouched_summary(self.store,current,self.identity['source']) != header['untouched_source']:
                            raise TransferRefused('plan_stale')
        manifest.publish(self.root, self.run, sealed, sha)
        journal = {**self.identity, "stamp": binding["stamp"], "expected": expected,
                   "conventions_policy": conventions_policy,
                   "manifest_hash": sha, "inventory_version": manifest.PROTOCOL,
                   "indexes": header["indexes"], "status": "prepared", "phase": PHASES[0],
                   "cursor": 0, "batches": 0, "business_batches": 0, "chain": "0" * 64}
        legacy._save(self.store, self.run, journal)
        self._publish(journal)

    def _receipt_path(self, journal):
        directory=self.root / 'topic-receipts' / self.run
        if any(path.is_symlink() for path in (self.root,directory.parent,directory)):
            self._forget_terminal(journal)
            raise TransferRefused('conversion_receipt_invalid')
        return directory / (str(journal["batches"]) + '.json')

    def _receipt_fields(self, journal):
        return {key: journal[key] for key in ("manifest_hash", "phase", "cursor", "batches", "chain", "status")}

    def _publish(self, journal):
        # Native commit may already have happened. Publish exactly that physical
        # ledger before permitting another batch or acknowledging progress.
        path = self._receipt_path(journal)
        fields = self._receipt_fields(journal)
        if path.exists():
            receipt = self._receipt(journal)
            return receipt["generation"]
        generation = self.publisher(journal)
        if type(generation) is not int or generation <= 0:
            raise TransferRefused("conversion_durability_pending")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stage = path.with_suffix(".stage")
        receipt = {**fields, "generation": generation}
        data = (_canonical(receipt) + "\n").encode()
        if stage.exists():
            try:
                staged = json.loads(stage.read_text())
            except (OSError, ValueError):
                raise TransferRefused("conversion_receipt_invalid") from None
            if (stage.is_symlink() or any(staged.get(k) != v for k, v in fields.items())
                    or type(staged.get("generation")) is not int or not 0 < staged["generation"] <= generation):
                raise TransferRefused("conversion_receipt_invalid")
        else:
            with os.fdopen(os.open(stage, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as out:
                out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(stage, path)
        dg._sync_dir(path.parent)
        return generation

    def _receipt(self, journal):
        path = self._receipt_path(journal)
        try:
            if path.is_symlink() or path.stat().st_size > manifest.LINE_BYTES:
                raise ValueError
            receipt = json.loads(path.read_text())
            if (any(receipt.get(k) != v for k, v in self._receipt_fields(journal).items())
                    or type(receipt.get("generation")) is not int or receipt["generation"] <= 0
                    or receipt["generation"] > dg.latest_sequence(self.root)):
                raise ValueError
        except (OSError, ValueError, AttributeError):
            self._forget_terminal(journal)
            raise TransferRefused("conversion_durability_pending") from None
        return receipt

    def _confirm_terminal(self, journal):
        self._artifact(journal)
        self._receipt(journal)
        if not hasattr(self.store, '_topic_conversion_terminals'):
            self.store._topic_conversion_terminals=set()
        self.store._topic_conversion_terminals.add(_canonical(journal))

    def _commit_journal(self, journal):
        self.store.run('BEGIN TRANSACTION',raw=True)
        try:
            legacy._save(self.store,self.run,journal)
            self.store.run('COMMIT',raw=True)
        except BaseException:
            try:
                self.store.run('ROLLBACK',raw=True)
            except RuntimeError as exc:
                if 'No active transaction' not in str(exc):raise
            raise

    def status(self):
        journal = self._journal()
        _, header = self._artifact(journal)
        try:
            receipt = self._receipt(journal)
        except TransferRefused:
            return {"status": "durability_pending", "run": self.run, "phase": journal["phase"]}
        if journal['status'] in ('active','cancelled'):
            self._confirm_terminal(journal)
        return {"status": journal["status"], "run": self.run, "phase": journal["phase"],
                "conventions_policy": header.get("conventions_policy", "retain"),
                "retained_personal_history": header.get("retained_personal_history", {}),
                "batches": journal["batches"], "durable_generation": receipt["generation"]}

    def _node(self, item, header):
        before = row(self.store, item["label"], item["old"])
        if before is None or manifest.digest(before) != item["before"]:
            raise TransferRefused("conversion_payload_changed")
        after = manifest.transform_row(before, item, header["binding"])
        if manifest.digest(after) != item["after"]:
            raise TransferRefused("conversion_manifest_invalid")
        if item["new"] != item["old"] and row(self.store, item["label"], item["new"]) is not None:
            raise TransferRefused("key_collision:" + item["label"])
        _put(self.store, item["label"], after, existing=item["new"] == item["old"])

    def _generated(self, item, header):
        label, value = item["label"], item["row"]
        if manifest.digest(value) != item["after"] or set(value) != set(manifest.SPECS[label]["cols"]):
            raise TransferRefused("conversion_manifest_invalid")
        current = row(self.store, label, value[manifest.SPECS[label]["pk"]])
        counter = label == "_SyncMeta" and value["key"].startswith("seq:")
        if counter:
            owner = value["key"][4:]
            side = "source" if owner == self.identity["source"] else "target" if owner == self.identity["target"] else ""
            if not side or int(current["value"] if current else 0) != header["sequence_before"][side]:
                raise TransferRefused("conversion_payload_changed")
        elif current is not None:
            raise TransferRefused("key_collision:" + label)
        _put(self.store, label, value, existing=current is not None)

    def _edge_value(self, item):
        spec = RELATIONS.get((item["label"], item["from_label"], item["to_label"]))
        if spec is None:
            raise TransferRefused("conversion_manifest_invalid")
        src_pk = manifest.SPECS[spec["from"]]["pk"]
        dst_pk = manifest.SPECS[spec["to"]]["pk"]
        projection = ", ".join(f"r.{field} AS {field}" for field in spec["props"]) or "1 AS _present"
        order = ", ".join(f"r.{field}" for field in spec["props"])
        head = f"MATCH (a:{spec['from']})-[r:{spec['label']}]->(b:{spec['to']}) WHERE a.{src_pk}=$src AND b.{dst_pk}=$dst"
        params = {"src": item["from"], "dst": item["to"]}
        offset, matches, matched = 0, 0, None
        while True:
            rows = self.store.run(head + " RETURN " + projection + (" ORDER BY " + order if order else "")
                                  + f" SKIP {offset} LIMIT 32", params, raw=True)
            if len(_canonical(rows).encode()) > self.max_bytes:
                raise TransferRefused("conversion_batch_byte_limit")
            for properties in rows:
                if not spec["props"]:
                    properties = {}
                candidate = {key: item[key] for key in ("label", "from_label", "to_label", "from", "to")}
                candidate["props"] = properties
                if manifest.digest(candidate) == item["before"]:
                    matches += 1
                    matched = candidate
            if len(rows) < 32:
                if matches != item["multiplicity"] or matched is None:
                    raise TransferRefused("conversion_relationship_changed")
                return matched
            offset += len(rows)

    def _edge(self, item):
        edge = self._edge_value(item)
        edge["from"], edge["to"] = item["mapped_from"], item["mapped_to"]
        if manifest.digest(edge) != item["after"]:
            raise TransferRefused("conversion_manifest_invalid")
        if (edge["from"], edge["to"]) == (item["from"], item["to"]):
            return
        src_pk, dst_pk = (manifest.SPECS[edge[k]]["pk"] for k in ("from_label", "to_label"))
        if row(self.store, edge["from_label"], edge["from"]) is None or row(self.store, edge["to_label"], edge["to"]) is None:
            raise TransferRefused("conversion_endpoint_missing")
        props = ", ".join(f"{field}:${field}" for field in edge["props"])
        self.store.run(f"MATCH (a:{edge['from_label']}), (b:{edge['to_label']}) WHERE a.{src_pk}=$src AND b.{dst_pk}=$dst "
                       + f"CREATE (a)-[:{edge['label']}" + (" {" + props + "}" if props else "") + "]->(b)",
                       {"src": edge["from"], "dst": edge["to"], **edge["props"]}, raw=True)

    def _retire(self, item):
        if item["old"] == item["new"]:
            return
        current = row(self.store, item["label"], item["old"])
        if current is None or manifest.digest(current) != item["before"]:
            raise TransferRefused("conversion_payload_changed")
        spec = manifest.SPECS[item["label"]]
        self.store.run(f"MATCH (n:{item['label']}) WHERE n.{spec['pk']}=$key DETACH DELETE n", {"key": item["old"]}, raw=True)

    def step(self, *, fail_at=None):
        journal = self._journal()
        path, header = self._artifact(journal)
        self._publish(journal)
        if journal["status"] in ("cancelled", "active", "graph_committed"):
            return self.status()
        if journal["phase"] == "verifying":
            raise TransferRefused("conversion_verification_required")
        phase = journal["phase"]
        if legacy._index_inventory(self.store) and not self.qualified_indexes:
            raise TransferRefused("conversion_index_execution_unqualified")
        if phase not in PHASES[:3]:
            raise TransferRefused("conversion_manifest_invalid")
        selected, size = [], 0
        for ordinal, item in manifest.instructions(path, after=journal["cursor"]):
            wanted = item["kind"] in ("node", "generated") if phase == PHASES[0] else item["kind"] == ("edge" if phase == PHASES[1] else "node")
            if not wanted:
                continue
            cost = item.get("payload_bytes", len(_canonical(item).encode()))
            if cost > self.max_bytes:
                raise TransferRefused("conversion_single_row_too_large")
            if selected and (len(selected) >= self.max_rows or size + cost > self.max_bytes):
                break
            selected.append((ordinal, item)); size += cost
        next_journal = dict(journal)
        if not selected:
            next_journal.update(phase=PHASES[PHASES.index(phase) + 1], cursor=0)
        else:
            next_journal["cursor"] = selected[-1][0]
            next_journal["business_batches"] += 1
        next_journal["status"] = next_journal["phase"]
        next_journal["batches"] += 1
        next_journal["chain"] = hashlib.sha256((journal["chain"] + _canonical(selected)).encode()).hexdigest()
        with DDL_GUARD.exclusive():
            if self.qualified_indexes:
                self._drop_indexes()
            if fail_at == "after_index_drop":
                raise RuntimeError("injected_after_index_drop")
            self.store.run("BEGIN TRANSACTION", raw=True)
            try:
                for _, item in selected:
                    if phase == PHASES[0]:
                        self._node(item, header) if item["kind"] == "node" else self._generated(item, header)
                    elif phase == PHASES[1]:
                        self._edge(item)
                    else:
                        self._retire(item)
                if fail_at == "before_ledger":
                    raise RuntimeError("injected_before_ledger")
                legacy._save(self.store, self.run, next_journal)
                if fail_at == "before_commit":
                    raise RuntimeError("injected_before_commit")
                self.store.run("COMMIT", raw=True)
            except BaseException:
                try:
                    self.store.run("ROLLBACK", raw=True)
                except RuntimeError as exc:
                    if "No active transaction" not in str(exc):
                        raise
                raise
        if fail_at == "after_commit":
            raise RuntimeError("injected_after_commit")
        self._publish(next_journal)
        return self.status()

    def _drop_indexes(self):
        inventory = legacy._index_inventory(self.store)
        if "ChunkVec" in inventory:
            self.store.drop_vector_index()
        if "NodeVec" in inventory:
            self.store.drop_node_vector_index()
        if "Chunk" in inventory:
            self.store._load_ext("fts")
            self.store._drop_index(f"CALL DROP_FTS_INDEX('Chunk','{self.store._FTS_INDEX}')", "conversion FTS")

    def verify(self):
        """Compare every final row and the complete directed relationship multiset."""
        journal = self._journal()
        path, header = self._artifact(journal)
        self._publish(journal)
        if journal['phase'] != 'verifying':
            raise TransferRefused('conversion_verification_required')
        self._verify_payload(path,header)
        next_journal = {**journal, "status": "graph_committed", "phase": "graph_committed",
                        "batches": journal["batches"] + 1}
        self._commit_journal(next_journal)
        self._publish(next_journal)
        return self.status()

    def _verify_payload(self, path, header):
        from store.topic_manifest import TopicManifest
        from store.scoped_topic_inventory import read_scoped_inventory
        with tempfile.TemporaryDirectory(prefix="vinc-conversion-verify-") as directory:
            with TopicManifest(Path(directory) / "observed.sqlite", deadline=time.monotonic() + 180) as observed:
                with legacy._inventory_read_transaction(self.store):
                    read_scoped_inventory(self.store, **self.identity, after=True, manifest=observed)
                    db = observed.db
                    db.execute("CREATE TABLE expected_rows(label TEXT,key TEXT,digest TEXT,PRIMARY KEY(label,key))")
                    db.execute("CREATE TABLE expected_edges(digest TEXT)")
                    db.execute("CREATE TABLE retired_keys(label TEXT,key TEXT,PRIMARY KEY(label,key))")
                    expected_rows = expected_edges = 0
                    for _,item in manifest.instructions(path):
                        observed.check_time()
                        if item["kind"] == "edge":
                            db.execute("INSERT INTO expected_edges VALUES(?)", (item["after"],))
                            expected_edges += 1
                            continue
                        label = item["label"]
                        if item["kind"] == "generated":
                            key, sha = item["row"][manifest.SPECS[label]["pk"]], item["after"]
                        elif item["kind"] == "node":
                            key, sha = item["new"], item["after"]
                            if item['old'] != key:
                                db.execute('INSERT INTO retired_keys VALUES(?,?)',(label,item['old']))
                        else:
                            key, sha = item["key"], item["digest"]
                        db.execute("INSERT INTO expected_rows VALUES(?,?,?)", (label, key, sha))
                        expected_rows += 1
                    for (label,) in db.execute('SELECT DISTINCT label FROM retired_keys'):
                        cursor=db.execute('SELECT key FROM retired_keys WHERE label=? ORDER BY key',(label,))
                        while keys := cursor.fetchmany(32):
                            observed.check_time()
                            pk=manifest.SPECS[label]['pk']
                            if self.store.run(f'MATCH (n:{label}) WHERE n.{pk} IN $keys RETURN n.{pk} AS id LIMIT 1',
                                              {'keys':[value[0] for value in keys]},raw=True):
                                raise TransferRefused('conversion_source_retirement_incomplete')
                    if 'untouched_source' in header:
                        db.execute("CREATE TABLE instructions(ordinal INTEGER PRIMARY KEY,kind TEXT,body TEXT)")
                        db.executemany("INSERT INTO instructions VALUES(?,?,?)",((ordinal,item['kind'],_canonical(item)) for ordinal,item in manifest.instructions(path)))
                        if manifest.untouched_summary(self.store,observed,self.identity['source']) != header['untouched_source']:
                            raise TransferRefused('conversion_outside_payload_changed')
                    actual_rows = db.execute("SELECT count(*) FROM payload").fetchone()[0]
                    if (actual_rows != expected_rows or len(observed.snapshot["edges"]) != expected_edges
                        or db.execute("SELECT 1 FROM expected_rows e LEFT JOIN payload p ON e.label=p.label AND e.key=p.pk "
                                      "WHERE p.digest IS NULL OR p.digest!=e.digest LIMIT 1").fetchone()
                        or db.execute("SELECT digest,count(*) FROM expected_edges GROUP BY digest "
                                      "EXCEPT SELECT digest,count(*) FROM edges GROUP BY digest LIMIT 1").fetchone()
                        or db.execute("SELECT digest,count(*) FROM edges GROUP BY digest "
                                      "EXCEPT SELECT digest,count(*) FROM expected_edges GROUP BY digest LIMIT 1").fetchone()):
                        raise TransferRefused("conversion_payload_verification_failed")
        with DDL_GUARD.exclusive():
            legacy._indexes(self.store, header["indexes"])
            if sorted(legacy._index_inventory(self.store)) != sorted(header["indexes"]):
                raise TransferRefused("conversion_index_restore_failed")

    def release(self, *, account_active=False):
        """Authorized account activation precedes a durable, revalidated release."""
        if account_active is not True:
            raise TransferRefused('conversion_account_activation_required')
        journal=self._journal()
        path,header=self._artifact(journal)
        if journal['status'] not in ('graph_committed','active'):
            raise TransferRefused('run_not_committed')
        self._publish(journal)
        if journal['status']!='active':
            self._verify_payload(path,header)
            journal={**journal,'status':'active','phase':'active','batches':journal['batches']+1}
            self._commit_journal(journal)
            self._publish(journal)
        self._confirm_terminal(journal)
        return self.status()

    def cancel(self):
        """Only an unchanged run with no committed business batch can cancel."""
        journal=self._journal()
        _,header=self._artifact(journal)
        if journal['status']=='cancelled':
            self._publish(journal)
            self._confirm_terminal(journal)
            return self.status()
        if journal.get('business_batches')!=0 or journal['phase']!=PHASES[0]:
            raise TransferRefused('conversion_resume_required')
        from store.topic_manifest import TopicManifest
        from store.scoped_topic_inventory import read_scoped_inventory
        from store.topic_transfer import fingerprint
        with tempfile.TemporaryDirectory(prefix='vinc-conversion-cancel-') as directory:
            with TopicManifest(Path(directory)/'source.sqlite',deadline=time.monotonic()+180) as current:
                with legacy._inventory_read_transaction(self.store):
                    inventory=read_scoped_inventory(self.store,**self.identity,manifest=current)
                    if fingerprint(inventory.snapshot)!=header['before_hash']:
                        raise TransferRefused('conversion_payload_changed')
        with DDL_GUARD.exclusive():
            legacy._indexes(self.store,header['indexes'])
            if sorted(legacy._index_inventory(self.store))!=sorted(header['indexes']):
                raise TransferRefused('conversion_index_restore_failed')
        journal={**journal,'status':'cancelled','phase':'cancelled','batches':journal['batches']+1}
        self._commit_journal(journal)
        self._publish(journal)
        self._confirm_terminal(journal)
        return self.status()
