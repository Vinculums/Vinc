import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path('D:/Projects/AIProjects/vinc-project/_wt/vin431-fresh')
OUT = Path('D:/Projects/AIProjects/vinc-project/_scratch/laya/out/community-fresh-v1')
INPUT = ROOT / 'rag_eval/evalsets/laya-predicates-community-fresh-v1'
sys.path.insert(0, str(ROOT))
from rag_eval.laya.community_inputs import assemble
from rag_eval.laya.pins import PREDICATES, SYMMETRIC
from rag_eval.laya.run_gold import read_jsonl, load_calib
from rag_eval.laya.metrics import softmax


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


for name, data in assemble(ROOT / 'rag_eval/evalsets').items():
    assert (INPUT / name).read_bytes() == data, name
frozen = json.loads((OUT / 'calibration-frozen.json').read_text(encoding='utf-8'))
assert frozen == json.loads((OUT / 'eval-once.lock.json').read_text(encoding='utf-8'))
for name, digest in frozen['calibration_sha256'].items():
    assert sha(OUT / 'calib' / name) == digest, name
calib = load_calib(OUT / 'calib')
for split, name, n in [('calib', 'calib', 115), ('eval', 'community', 140)]:
    gold = {p['pair_id']: p for p in read_jsonl(INPUT / f'{split}.jsonl')}
    rows = read_jsonl(OUT / f'{name}.jsonl')
    expected = {(pid, direction, pred) for pid in gold for direction, preds in [('ab', PREDICATES), ('ba', SYMMETRIC)] for pred in preds}
    assert len(rows) == 5*n and len(expected) == len(rows)
    assert {(r['pair_id'], r['direction'], r['predicate']) for r in rows} == expected
    for r in rows:
        assert r['label'] == gold[r['pair_id']]['labels'][r['predicate']]
        assert r['lang'] == gold[r['pair_id']]['lang']
        assert r['k'] == 2 and all(math.isfinite(x) for x in r['logits'])
        assert abs(r['p_raw'] - softmax(r['logits'])[1]) < 1e-12
        if split == 'eval':
            c = calib[(r['predicate'], 'en' if r['lang'] == 'mixed' else r['lang'])]
            assert r['calibration_id'] == c['calibration_id'] and r['abstain_below'] == c['min_confidence']
            assert abs(r['p_cal'] - softmax([x/c['temperature'] for x in r['logits']])[1]) < 1e-12
    attempt = json.loads((OUT / f'{"calibration" if split == "calib" else name}-attempt.json').read_text(encoding='utf-8'))
    assert attempt['physical_attempt'] == 1 and attempt['exit_code'] == 0
result = json.loads((OUT / 'community-report.json').read_text(encoding='utf-8'))
assert result['gate'] == {'auroc_margin': .05, 'ece_max': .1, 'coverage_min': .5}
assert result['rows'] == 700 and result['rows_unlabeled_excluded'] == 0
eval_rows = read_jsonl(OUT / 'community.jsonl')
assert result['rows_truncated_excluded'] == sum(bool(r['truncated']) for r in eval_rows)
expected_groups = {(r['predicate'], lang) for r in eval_rows if r['direction'] == 'ab' and not r['truncated'] for lang in (r['lang'], 'all')}
assert len(result['table']) == len(expected_groups)
assert {(r['predicate'], r['lang']) for r in result['table']} == expected_groups
for row in result['table']:
    eligible = [r for r in eval_rows if r['direction'] == 'ab' and r['predicate'] == row['predicate'] and not r['truncated'] and (row['lang'] == 'all' or r['lang'] == row['lang'])]
    assert row['n'] == len(eligible) and row['positives'] == sum(r['label'] for r in eligible)
    g = row['gate']
    assert g['auroc_ok'] == (row['auroc'] >= row['best_baseline_auroc'] + .05)
    assert g['ece_ok'] == (row['ece'] <= .1)
    assert g['coverage_ok'] == (row['frozen']['no_threshold'] == 0 and row['frozen']['coverage'] >= .5)
    assert g['pass'] == all(g[k] for k in ('auroc_ok', 'ece_ok', 'coverage_ok'))
lines = (OUT / 'community.jsonl').read_text(encoding='utf-8').splitlines()
header, timing = json.loads(lines[0][2:]), json.loads(lines[-1][2:])
assert header['model_dir_sha256'] == frozen['model_sha256'] and header['n_pairs'] == 140
assert header['backend'] == 'eager' and header['torch'] == '2.14.1+cpu' and not header['cuda']
assert timing['n_states'] == 280 and timing['kind'] == 'timing'


def finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    return value


verification = {'model_sha256': frozen['model_sha256'], 'gate': result['gate'], 'calibration_rows': 575, 'frozen_inputs_valid': True, 'frozen_calibration_valid': True, 'sets': {'community': {'pairs': 140, 'records': 700, 'labels_and_logits_valid': True, 'physical_attempts': 1, 'logical_passes': 1, 'timing': timing, 'table': finite(result['table'])}}, 'source_report_sha256': sha(OUT / 'community-report.json')}
with (OUT / 'verification.json').open('x', encoding='utf-8', newline='\n') as fh:
    json.dump(verification, fh, ensure_ascii=False, indent=1, allow_nan=False)
    fh.write('\n')
print('PASS: frozen inputs, 575 calibration and 700 evaluation records, labels, logits, temperatures, thresholds, attempts and gates')
