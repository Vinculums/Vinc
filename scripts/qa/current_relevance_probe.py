"""Calibrate only on an immutable current snapshot in the deployed image.

Only hashes, counts and aggregate validation results leave the disposable copy.
The profile is a simulation; this command never activates a production policy.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path.cwd()))
from store.backend import open_store
from store.config import load_config
from store.encoder_runtime import fingerprint
from vinc_rag.embed.encoder import Encoder
from vinc_rag import retrieval
from vinc_rag.search_relevance import classify_rows


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()


def split(query):
    return 'calibration' if int(hashlib.sha256(query.encode()).hexdigest(), 16) % 2 == 0 else 'holdout'


def inspect(rows, kind, limit):
    return classify_rows(rows, {'id': 'measurement-only', 'max_distance': {kind: limit}}, kind=kind)


def validation_metrics(baseline, candidates):
    """비교 결과는 실제 보정 검색에서 읽는다. 보정 전 그래프 후보를 재사용하지 않는다."""
    assert len(baseline) == len(candidates)
    metrics = {}
    for name in ('calibration', 'holdout'):
        counts = {'queries': 0, 'negative_queries': 0, 'negative_with_primary': 0,
                  'node_gold_baseline_found': 0, 'node_gold_lost': 0,
                  'chunk_gold_baseline_found': 0, 'chunk_gold_lost': 0,
                  'unclassified_negative_hits': 0, 'negative_primary_by_basis': {}}
        for (item, response), candidate in zip(baseline, candidates):
            if split(item['query']) != name:
                continue
            counts['queries'] += 1
            labelled = {}
            for kind, key, goldkey, identity in (('node', 'node_hits', 'gold_node_id', 'id'),
                                                ('chunk', 'rows', 'gold_doc_id', 'doc_id')):
                labelled[kind] = candidate[key]
                gold = item.get(goldkey)
                found = bool(gold) and any(h.get(identity) == gold for h in response[key])
                kept = bool(gold) and any(h.get(identity) == gold and h['relevance']['level'] != 'weak'
                                         for h in labelled[kind])
                counts[kind + '_gold_baseline_found'] += found
                counts[kind + '_gold_lost'] += found and not kept
            if item.get('negative'):
                counts['negative_queries'] += 1
                hits = labelled['node'] + labelled['chunk']
                counts['negative_with_primary'] += any(h['relevance']['level'] != 'weak' for h in hits)
                counts['unclassified_negative_hits'] += sum(h['relevance']['level'] == 'unclassified' for h in hits)
                for kind, rows in labelled.items():
                    for h in rows:
                        if h['relevance']['level'] != 'weak':
                            basis = kind + ':' + h['relevance']['basis']
                            counts['negative_primary_by_basis'][basis] = counts['negative_primary_by_basis'].get(basis, 0) + 1
        metrics[name] = counts
    return metrics


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--store', type=Path, required=True)
    ap.add_argument('--inputs', type=Path, required=True)
    ap.add_argument('--inputs-sha', required=True)
    ap.add_argument('--owner', required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    assert digest(args.inputs) == args.inputs_sha, 'preregistered inputs changed'
    items = json.loads(args.inputs.read_text(encoding='utf-8'))
    assert len({x['query'] for x in items}) == len(items)
    assert len(items) == 116 and sum(bool(x.get('negative')) for x in items) == 40
    model = Path(os.environ['VINC_EMBED_MODEL_PATH'])
    runtime = fingerprint()
    result = {'status': 'failed_not_a_measurement', 'activation': 'off',
              'eligible_for_production_activation': False, 'inputs_sha256': args.inputs_sha,
              'snapshot_sha256': digest(args.store), 'encoder_runtime': runtime,
              'model_sha256': digest(model), 'tokenizer_sha256': digest(model.with_name('tokenizer.json')),
              'encoder_build': os.environ.get('VINC_ENCODER_BUILD'),
              'image_source': os.environ.get('VINC_MEASURE_IMAGE_SHA'),
              'deployed_image_build_verified': False,
              'queries': len(items), 'completed_queries': 0, 'completed_candidate_queries': 0,
              'limits': ['Frozen current snapshot and deployed image, not production activation',
                         'No transport or native parity claim from this measurement']}
    store = None
    def close_store():
        nonlocal store
        if store is not None:
            try:
                store.close()
            finally:
                store = None
    try:
        assert runtime, 'unknown encoder runtime'
        if result['image_source']:
            assert str(result['encoder_build']).startswith(result['image_source'] + '-'), 'image build stamp mismatch'
            result['deployed_image_build_verified'] = True
        with ExitStack() as stack:
            folder = stack.enter_context(tempfile.TemporaryDirectory(prefix='vin343-current-'))
            copy = Path(folder) / 'db'
            shutil.copyfile(args.store, copy)
            wal = Path(str(args.store) + '.wal')
            if wal.exists():
                result['snapshot_wal_sha256'] = digest(wal)
                shutil.copyfile(wal, Path(str(copy) + '.wal'))
            cfg = load_config({'data_dir': str(copy), 'embedding_model': os.environ['VINC_EMBED_MODEL']})
            store = open_store(cfg)
            assert store is not None
            stack.callback(close_store)
            # Product provisioning on the disposable copy retains its existing vectors.
            store.provision(dim=cfg.embedding_dim)
            gold_docs = sorted({x['gold_doc_id'] for x in items if x.get('gold_doc_id')})
            result['labelled_documents_present'] = store.run(
                'MATCH (d:_DocMeta) WHERE d.owner_id=$o AND d.doc_id IN $ids RETURN count(d) AS n',
                {'o': args.owner, 'ids': gold_docs})[0]['n']
            result['labelled_documents_expected'] = len(gold_docs)
            for kind in ('ChunkVec', 'NodeVec'):
                stamps = store.run(f'MATCH (v:{kind}) RETURN v.encoder_runtime AS stamp, count(*) AS n')
                result[kind + '_runtime_counts'] = [{'stamp': x['stamp'], 'count': x['n']} for x in stamps]
            enc = Encoder(cfg.embedding_model, onnx_path=str(model), batch=2)
            baseline = []
            embeddings = []
            for item in items:
                embedding = enc.encode([item['query']], kind='query')[0]
                embeddings.append(embedding)
                response = retrieval.search(item['query'], query_embedding=embedding,
                                            query_runtime=runtime, top_k=20, owner=args.owner, cfg=cfg, store=store)
                if response.get('error') or response.get('vector_note') or response.get('encoder_runtime_warning'):
                    result['diagnostics'] = {k: response[k] for k in ('error', 'vector_note', 'encoder_runtime_warning') if k in response}
                    raise RuntimeError('degraded search is not a calibration measurement')
                baseline.append((item, response))
                result['completed_queries'] += 1
                if result['completed_queries'] % 10 == 0:
                    print(json.dumps({'progress': result['completed_queries']}), flush=True)
            bounds = {}
            evidence_counts = {}
            for kind, key, goldkey, identity in (('node', 'node_hits', 'gold_node_id', 'id'),
                                                 ('chunk', 'rows', 'gold_doc_id', 'doc_id')):
                positive = []
                for item, response in baseline:
                    if split(item['query']) != 'calibration' or not item.get(goldkey):
                        continue
                    for hit in inspect(response[key], kind, 0):
                        if hit.get(identity) == item[goldkey] and hit['relevance']['basis'] == 'calibrated_vector':
                            positive.append(hit['distance'])
                evidence_counts[kind] = len(positive)
                if positive:
                    bounds[kind] = max(positive)
            result['calibration_vector_gold_counts'] = evidence_counts
            result['baseline_gold_retrieval'] = {
                name: {kind: sum(bool(item.get(goldkey)) and any(h.get(identity) == item[goldkey] for h in response[key])
                                 for item, response in baseline if split(item['query']) == name)
                       for kind, key, goldkey, identity in (('node', 'node_hits', 'gold_node_id', 'id'),
                                                          ('chunk', 'rows', 'gold_doc_id', 'doc_id'))}
                for name in ('calibration', 'holdout')}
            assert set(bounds) == {'node', 'chunk'}, 'missing independently labelled semantic positive calibration for an arm'
            result['proposal'] = {'max_distance': bounds, 'status': 'experimental'}
            # active 상태는 이 네트워크 차단 사본에서만 제품 경로를 실행하기 위한 값이다.
            # 숫자는 여전히 실험 제안이며 어떤 파일·운영 설정에도 저장하지 않는다.
            revision = 'sha256:' + result['snapshot_sha256']
            simulation = {'version': 1, 'id': 'isolated-counterfactual', 'status': 'approved',
                          'encoder_model': cfg.embedding_model, 'encoder_runtime': runtime,
                          'corpus_revision': revision, 'evidence_sha256': args.inputs_sha,
                          'max_distance': bounds}
            candidates = []
            for item, embedding in zip(items, embeddings):
                candidate = retrieval.search(item['query'], query_embedding=embedding,
                    query_runtime=runtime, top_k=20, owner=args.owner, cfg=cfg, store=store,
                    relevance_profile=simulation, relevance_corpus_revision=revision)
                assert candidate.get('relevance_policy', {}).get('state') == 'active', 'candidate policy not exercised'
                assert not any(candidate.get(k) for k in ('error', 'vector_note', 'encoder_runtime_warning')), 'degraded candidate search'
                candidates.append(candidate)
                result['completed_candidate_queries'] += 1
            metrics = validation_metrics(baseline, candidates)
            result['validation'] = metrics
            result['status'] = 'measured'
            result['quality_pass'] = all(v['negative_queries'] > 0 and v['negative_with_primary'] == 0
                                        and v['node_gold_baseline_found'] > 0 and v['node_gold_lost'] == 0
                                        and v['chunk_gold_baseline_found'] > 0 and v['chunk_gold_lost'] == 0
                                        for v in metrics.values())
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        close_store()
        result['source_preserved'] = digest(args.store) == result['snapshot_sha256']
        args.out.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result['status'] == 'measured' and result['source_preserved'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
