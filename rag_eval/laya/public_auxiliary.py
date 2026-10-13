"""원래 과제 라벨을 보존한 보조 자료. 근거: [[vinc.plan.laya-training-data-pipeline-2026-10-11]] §17."""
from __future__ import annotations

import argparse
import json
import tarfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from .data_contracts import normalized
from .public_sources import digest, encoded, verify as verify_sources


def question(instructions, yes, no):
    return {'type': 'noul', 'instructions': instructions, 'criteria': {'true': yes, 'false': no}}


QUESTIONS = {
    'aux_entails': question('Does premise A entail hypothesis B?', 'B follows from A.', 'A conflicts with B.'),
    'aux_nli_contradicts': question('Does premise A contradict hypothesis B?', 'A and B conflict.', 'B follows from A.'),
    'aux_fact_supports': question('Does evidence A support claim B in the source fact verification task?', 'The source evidence supports the claim.', 'The source evidence refutes the claim.'),
    'aux_fact_refutes': question('Does evidence A refute claim B in the source fact verification task?', 'The source evidence refutes the claim.', 'The source evidence supports the claim.'),
    'aux_paraphrase': question('Are A and B paraphrases in the PAWS Wikipedia task?', 'A and B convey equivalent meaning.', 'A and B have different meanings.'),
    'aux_sts_ge3': question('Is the semantic similarity of A and B at least 3 on the KLUE 0 to 5 scale?', 'Their semantic similarity is at least 3.', 'Their semantic similarity is below 3.'),
}


def native_targets(task, label):
    if task == 'nli':
        if label == 'neutral':
            return {'aux_entails': None, 'aux_nli_contradicts': None}
        if label not in ('entailment', 'contradiction'):
            raise ValueError('unknown NLI label')
        return {'aux_entails': int(label == 'entailment'), 'aux_nli_contradicts': int(label == 'contradiction')}
    if task == 'fact':
        if label in ('NOT ENOUGH INFO', 'NEI'):
            return {'aux_fact_supports': None, 'aux_fact_refutes': None}
        if label not in ('SUPPORT', 'SUPPORTS', 'CONTRADICT', 'REFUTES'):
            raise ValueError('unknown fact label')
        value = int(label in ('SUPPORT', 'SUPPORTS'))
        return {'aux_fact_supports': value, 'aux_fact_refutes': 1 - value}
    if task in ('paraphrase', 'sts'):
        if type(label) is not int or label not in (0, 1):
            raise ValueError('native binary label required')
        return {'aux_paraphrase' if task == 'paraphrase' else 'aux_sts_ge3': label}
    raise ValueError('unknown native task')


def pair(source, upstream_id, role, task, label, a, b, language, families, provenance, locator):
    if role not in ('train', 'dev') or not a.strip() or not b.strip():
        raise ValueError('native labeled train/development pair required')
    return {'pair_id': source + ':' + str(upstream_id), 'source': source, 'upstream_id': str(upstream_id),
            'upstream_role': role, 'task': task, 'native_label': label, 'targets': native_targets(task, label),
            'a': a, 'b': b, 'language': language, 'family_keys': sorted(set(families)),
            'provenance_tier': provenance, 'locator': locator,
            'vinc_predicate_gold': False, 'human_review_in_this_run': False}


def json_lines(stream):
    for line in stream:
        if line.strip():
            yield json.loads(line)


def scifact(root):
    with tarfile.open(root / 'scifact/data.tar.gz') as archive:
        corpus = {str(r['doc_id']): r for r in json_lines(archive.extractfile('data/corpus.jsonl'))}
        for role in ('dev', 'train'):
            name = f'data/claims_{role}.jsonl'
            for row in json_lines(archive.extractfile(name)):
                if not row['evidence']:
                    continue
                families = ['scifact:doc:' + str(d) for d in row['cited_doc_ids']]
                for doc_id, annotations in sorted(row['evidence'].items()):
                    for i, annotation in enumerate(annotations):
                        sentences = annotation['sentences']
                        if not sentences or any(type(s) is not int or not 0 <= s < len(corpus[doc_id]['abstract']) for s in sentences):
                            raise ValueError('invalid scientific rationale span')
                        evidence = '\n'.join(corpus[doc_id]['abstract'][s] for s in sentences)
                        yield pair('scifact', f'{row["id"]}:{doc_id}:{i}', role, 'fact', annotation['label'], evidence,
                                   row['claim'], 'en_en', families + ['scifact:doc:' + doc_id],
                                   'source_expert_annotations', {'archive': 'scifact/data.tar.gz', 'member': name,
                                   'claim_id': row['id'], 'doc_id': doc_id, 'sentence_indices': sentences})


def vitaminc(root):
    with zipfile.ZipFile(root / 'vitaminc/data.zip') as archive:
        for role in ('dev', 'train'):
            member = 'vitaminc/' + role + '.jsonl'
            if archive.getinfo(member).file_size > 200_000_000:
                raise ValueError('fact source expansion exceeds budget')
            for row in json_lines(archive.open(member)):
                if row['revision_type'] != 'real':
                    continue
                yield pair('vitaminc', row['unique_id'], role, 'fact', row['label'], row['evidence'], row['claim'],
                           'en_en', ['wikipedia:page:' + normalized(row['page'])], 'source_real_revision_annotations',
                           {'archive': 'vitaminc/data.zip', 'member': member, 'unique_id': row['unique_id'],
                            'case_id': row['case_id'], 'page': row['page'], 'wiki_revision_id': row['wiki_revision_id']})


def klue(root):
    for role in ('dev', 'train'):
        for task in ('nli', 'sts'):
            name = f'klue/{task}-{role}.json'
            for row in json.loads((root / name).read_bytes()):
                if task == 'nli':
                    a, b, label = row['premise'], row['hypothesis'], row['gold_label']
                    families = ['klue:premise:' + digest(normalized(a).encode())]
                else:
                    a, b, label = row['sentence1'], row['sentence2'], row['labels']['binary-label']
                    families = ['klue:sentence:' + digest(normalized(t).encode()) for t in (a, b)]
                yield pair('klue-' + task, row['guid'], role, task, label, a, b, 'ko_ko', families,
                           'source_annotations_with_corpus_or_translation_method', {'file': name, 'guid': row['guid'],
                           'source_corpus': row.get('source', row.get('genre')), 'native_annotations_sha256': digest(encoded(row))})


def pawsx(root, exclusions=None):
    import pyarrow.parquet as pq  # 수집용 격리 환경에만 설치한다. 학습 환경은 바꾸지 않는다.
    for role, split in (('dev', 'validation'), ('train', 'train')):
        for language in ('en', 'ko'):
            name = f'pawsx/{language}-{split}.parquet'
            for row in pq.read_table(root / name).to_pylist():
                if any(not row[k].strip() or row[k].strip() == 'NS' for k in ('sentence1', 'sentence2')):
                    if exclusions is not None:
                        exclusions['paws_blank_or_NS_excluded'] += 1
                    continue
                provenance = 'source_original_english' if language == 'en' else (
                    'source_machine_translated_training' if role == 'train' else 'source_human_translated_development')
                yield pair('pawsx', f'{split}:{row["id"]}:{language}', role, 'paraphrase', row['label'],
                           row['sentence1'], row['sentence2'], language + '_' + language,
                           [f'pawsx:translation:{split}:{row["id"]}'], provenance,
                           {'file': name, 'id': row['id'], 'original_split': split})


def identity_keys(row):
    return set(row['family_keys']) | {'endpoint:' + digest(normalized(t).encode()) for t in (row['a'], row['b'])}


def separate(rows):
    """공유 문장·번역·출처로 연결된 전체 묶음을 같은 분할에 둔다."""
    # 다른 문장을 거쳐 개발 자료와 연결된 근거도 학습에서 제외한다.
    parents = {}

    def find(k):
        parents.setdefault(k, k)
        while parents[k] != k:
            parents[k] = parents[parents[k]]
            k = parents[k]
        return k

    for row in rows:
        keys = sorted(identity_keys(row))
        first = find(keys[0])
        for key in keys[1:]:
            other = find(key)
            if other != first:
                parents[other] = first
    dev_components = {find(next(iter(identity_keys(r)))) for r in rows if r['upstream_role'] == 'dev'}
    result, counts = [], Counter()
    seen = set()
    for row in rows:
        component = find(next(iter(identity_keys(row))))
        if row['upstream_role'] == 'train' and component in dev_components:
            counts['train_reserved_for_dev_component'] += 1
            continue
        key = (row['task'], normalized(row['a']), normalized(row['b']), row['upstream_role'])
        if key in seen:
            counts['duplicate_pair'] += 1
            continue
        seen.add(key)
        if all(v is None for v in row['targets'].values()):
            counts['native_unknown_excluded'] += 1
            continue
        result.append(dict(row, role=row['upstream_role'], independence_group='component:' + digest(component.encode())))
    return result, dict(counts)


def select(rows, per_cell):
    buckets = defaultdict(list)
    for row in rows:
        buckets[(row['source'], row['language'], row['task'], str(row['native_label']))].append(row)
    chosen = []
    for key in sorted(buckets):
        chosen.extend(sorted(buckets[key], key=lambda r: digest(r['pair_id'].encode()))[:per_cell])
    return sorted(chosen, key=lambda r: r['pair_id'])


def training_row(row):
    targets = {k: v for k, v in row['targets'].items() if v is not None}
    if not targets or any(type(v) is not int or v not in (0, 1) for v in targets.values()):
        raise ValueError('native known targets required')
    return {'state': 'A: ' + row['a'] + '\nB: ' + row['b'], 'questions': {k: QUESTIONS[k] for k in targets},
            'gold': {k: {'probabilities': {'false': float(1 - v), 'true': float(v)}} for k, v in targets.items()},
            'meta': {'pair_id': row['pair_id'], 'source': row['source'], 'language': row['language'],
                     'independence_group': row['independence_group'], 'tier': 'native-auxiliary',
                     'vinc_predicate_gold': False, 'provenance_tier': row['provenance_tier']}}


def validate_exports(train, dev):
    if not train or not dev or any(r['role'] != role for role, rows in [('train', train), ('dev', dev)] for r in rows):
        raise ValueError('nonempty native train/development exports required')
    keys = [{k for row in rows for k in identity_keys(row)} for rows in (train, dev)]
    groups = [{r['independence_group'] for r in rows} for rows in (train, dev)]
    if keys[0] & keys[1] or groups[0] & groups[1]:
        raise ValueError('source/endpoint/component crosses roles')
    ids = [r['pair_id'] for rows in (train, dev) for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate exported pair id')
    for row in train + dev:
        if row['vinc_predicate_gold'] is not False or row['human_review_in_this_run'] is not False or row['targets'] != native_targets(row['task'], row['native_label']):
            raise ValueError('native-label authority changed')
        training_row(row)
    return {'train_pairs': len(train), 'dev_pairs': len(dev), 'cross_role_identity_keys': 0,
            'train_components': len(groups[0]), 'dev_components': len(groups[1]),
            'semantic_cross_language_independence_proven': False, 'underlying_document_ids_complete': False}


def build(sources, paws, out, train_per_cell=100, dev_per_cell=25):
    freeze = verify_sources(sources)
    paws_freeze = verify_sources(paws)
    if out.exists() or type(train_per_cell) is not int or type(dev_per_cell) is not int or min(train_per_cell, dev_per_cell) < 1:
        raise ValueError('new output and positive selection quotas required')
    adapter_exclusions = Counter()
    rows = list(scifact(sources)) + list(vitaminc(sources)) + list(klue(sources)) + list(pawsx(paws, adapter_exclusions))
    eligible, excluded = separate(rows)
    train = select([r for r in eligible if r['role'] == 'train'], train_per_cell)
    dev = select([r for r in eligible if r['role'] == 'dev'], dev_per_cell)
    report = validate_exports(train, dev)
    files = {}
    out.mkdir(parents=True, exist_ok=False)
    for name, content in [('train-pairs.jsonl', train), ('dev-pairs.jsonl', dev),
                          ('training.jsonl', [training_row(r) for r in train]), ('development.jsonl', [training_row(r) for r in dev])]:
        raw = b''.join((json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n').encode() for r in content)
        (out / name).write_bytes(raw)
        files[name] = digest(raw)
    manifest = {'schema': 'vinc.laya.native-auxiliary.v1', 'source_freezes': [freeze, paws_freeze],
                'question_sha256': digest(encoded(QUESTIONS)), 'selection': {'train_per_cell': train_per_cell, 'dev_per_cell': dev_per_cell},
                'parsed_native_pairs': len(rows), 'eligible_pairs': len(eligible), 'exclusions': excluded,
                'adapter_exclusions': dict(adapter_exclusions), 'validation': report,
                'counts': {role: dict(Counter(r['source'] + ':' + r['language'] for r in selected)) for role, selected in [('train', train), ('dev', dev)]},
                'native_labels': {role: dict(Counter(r['source'] + ':' + str(r['native_label']) for r in selected)) for role, selected in [('train', train), ('dev', dev)]},
                'files': files, 'production_weights_approved': False, 'new_human_reviews': 0, 'teacher_calls': 0,
                'no_new_mixed_language_gold': True, 'source_reader': {'pyarrow': '23.0.1'},
                'selection_digest': digest(encoded([(r['pair_id'], r['independence_group']) for r in train + dev]))}
    (out / 'manifest.json').write_bytes(encoded(manifest))
    return manifest


def verify(root):
    manifest = json.loads((root / 'manifest.json').read_bytes())
    if manifest['schema'] != 'vinc.laya.native-auxiliary.v1' or manifest['question_sha256'] != digest(encoded(QUESTIONS)):
        raise ValueError('auxiliary question contract mismatch')
    for name, expected in manifest['files'].items():
        from .public_sources import safe_path
        if digest(safe_path(root, name).read_bytes()) != expected:
            raise ValueError('auxiliary export hash mismatch')
    train, dev = [list(json_lines((root / name).open('rb'))) for name in ['train-pairs.jsonl', 'dev-pairs.jsonl']]
    report = validate_exports(train, dev)
    if report != manifest['validation'] or manifest['production_weights_approved'] is not False:
        raise ValueError('auxiliary validation/authority mismatch')
    for name, rows in [('training.jsonl', train), ('development.jsonl', dev)]:
        if list(json_lines((root / name).open('rb'))) != [training_row(r) for r in rows]:
            raise ValueError('auxiliary target export differs from native labels')
    return {'verified': True, **report, 'manifest_sha256': digest((root / 'manifest.json').read_bytes())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path)
    parser.add_argument('--paws', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--train-per-cell', type=int, default=100)
    parser.add_argument('--dev-per-cell', type=int, default=25)
    args = parser.parse_args(argv)
    if bool(args.sources) != bool(args.paws):
        parser.error('both source freezes required')
    print(json.dumps(build(args.sources, args.paws, args.out, args.train_per_cell, args.dev_per_cell) if args.sources else verify(args.out), sort_keys=True))


if __name__ == '__main__':
    main()
