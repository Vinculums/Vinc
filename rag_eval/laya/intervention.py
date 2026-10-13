"""양성 균형 개발 표본과 고정 선택 규칙. 근거: [[vinc.plan.laya-training-data-pipeline-2026-10-11]] §19."""
from __future__ import annotations

import random
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from .public_sources import digest, encoded

SELECTION = 'core_supports_f1_then_recall_then_nll'
SCHEMA = 'vinc.laya.positive-auxiliary-development.v1'
LOW_SCHEMA = 'vinc.laya.low-mixture-development.v1'


def positive_f1(values):
    precision, recall = values['positive_precision'], values['positive_recall']
    if precision is None or recall is None or precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def select_checkpoint(history, selection):
    if selection == 'core_supports_nll_then_positive_recall':
        return min(history, key=lambda h: (h['development']['supports']['nll'],
                                           -h['development']['supports']['positive_recall'], h['epoch']))
    if selection != SELECTION:
        raise ValueError('unknown registered selection')
    return min(history, key=lambda h: (-positive_f1(h['development']['supports']),
                                      -h['development']['supports']['positive_recall'],
                                      h['development']['supports']['nll'], h['epoch']))


def balanced_rows(core, native, arm, seed):
    if arm not in ('core', 'mixed', 'low-original', 'low-expanded'):
        raise ValueError('unknown data intervention arm')
    groups = defaultdict(dict)
    for tier, rows in [('core', core), ('native', native if arm != 'core' else [])]:
        for row in rows:
            for task, question in row['questions'].items():
                target = row['gold'][task]
                y = target['probabilities']['true']
                if y not in (0., 1.) or target['probabilities']['false'] != 1-y:
                    raise ValueError('hard native/predicate label required')
                meta = dict(row['meta'], task=task, sampling_tier=tier)
                if tier == 'native':
                    if meta.get('vinc_predicate_gold') is not False or not task.startswith('aux_'):
                        raise ValueError('native task relabeling refused')
                    key = (tier, task, meta['source'], meta['language'], int(y))
                else:
                    if task not in ('supports', 'contradicts', 'same_as'):
                        raise ValueError('unexpected core task')
                    key = (tier, task, '', '', int(y))
                # 기존 repetition은 새 표본의 독립 관측으로 세지 않는다.
                identity = digest(encoded({'state': row['state'], 'question': question, 'gold': target}))
                meta.pop('repetition', None)
                meta['training_identity'] = identity
                one = {'state': row['state'], 'questions': {task: question}, 'gold': {task: target}, 'meta': meta}
                groups[key].setdefault(identity, one)
    expected_core = {('core', task, '', '', y) for task in ('supports', 'contradicts', 'same_as') for y in (0, 1)}
    if {key for key in groups if key[0] == 'core'} != expected_core:
        raise ValueError('missing core task/class stratum')
    native_cells = sorted({key[:-1] for key in groups if key[0] == 'native'})
    if arm != 'core' and (len(native_cells) != 9 or any((*key, y) not in groups for key in native_cells for y in (0, 1))):
        raise ValueError('frozen native task/source/language strata changed')
    selected, exposure = [], []
    for key, candidates in sorted(groups.items()):
        if key[0] == 'core':
            quota = 400 if arm.startswith('low-') else 502 if arm == 'core' else 251
        else:
            quota = 34 if arm.startswith('low-') else 84 if native_cells.index(key[:-1]) < 6 else 83
        ordered = [candidates[i] for i in sorted(candidates)]
        rng = random.Random(digest(encoded({'seed': seed, 'stratum': key})))
        draws = []
        while len(draws) < quota:
            cycle = list(ordered)
            rng.shuffle(cycle)
            draws.extend(cycle[:quota-len(draws)])
        selected.extend(draws)
        exposure.append({'stratum': list(key), 'available_unique_items': len(ordered), 'draws': quota,
                         'observed_unique_items': len({r['meta']['training_identity'] for r in draws})})
    random.Random(seed).shuffle(selected)
    if len(selected) != 3012:
        raise ValueError('registered balanced budget mismatch')
    receipt = {'schema': 'vinc.laya.balanced-exposure.v1', 'arm': arm, 'seed': seed,
               'items': len(selected), 'strata': exposure,
               'unique_items': len({r['meta']['training_identity'] for r in selected}),
               'unique_pairs': {tier: len({r['meta']['pair_id'] for r in selected
                   if r['meta']['sampling_tier'] == tier}) for tier in ('core', 'native')},
               'positive_items': sum(int(next(iter(r['gold'].values()))['probabilities']['true']) for r in selected),
               'native_labels_are_vinc_gold': False}
    return selected, receipt


def jsonl_bytes(rows):
    return b''.join((json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n').encode('utf-8') for r in rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=20261007)
    args = parser.parse_args(argv)
    core, native = [[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
                    for path in (args.core, args.native)]
    outputs = {}
    for arm in ('core', 'mixed'):
        rows, receipt = balanced_rows(core, native, arm, args.seed)
        outputs[arm] = (jsonl_bytes(rows), receipt)
    args.out.mkdir(parents=True, exist_ok=False)
    for arm, (raw, receipt) in outputs.items():
        receipt['training_sha256'] = digest(raw)
        receipt['input_hashes'] = {'core': digest(args.core.read_bytes()), 'native': digest(args.native.read_bytes())}
        (args.out/f'{arm}-training.jsonl').write_bytes(raw)
        (args.out/f'{arm}-exposure.json').write_bytes(encoded(receipt))
        print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
