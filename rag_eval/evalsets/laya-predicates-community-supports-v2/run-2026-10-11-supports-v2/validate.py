import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path('D:/Projects/AIProjects/vinc-project/_wt/vin439-supports-run')
OUT = Path('D:/Projects/AIProjects/vinc-project/_scratch/laya/out/community-supports-v2')
INPUT = ROOT / 'rag_eval/evalsets/laya-predicates-community-supports-v2'
sys.path.insert(0, str(ROOT))
from rag_eval.laya.supports_inputs import assemble
from rag_eval.laya.pins import PREDICATES, SYMMETRIC
from rag_eval.laya.run_gold import read_jsonl, load_calib
from rag_eval.laya.metrics import softmax


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


for name, data in assemble(ROOT / 'rag_eval/evalsets').items():
    assert (INPUT / name).read_bytes() == data, name
frozen = json.loads((OUT / 'calibration-frozen.json').read_text(encoding='utf-8'))
assert frozen == json.loads((OUT / 'eval-once.lock.json').read_text(encoding='utf-8'))
receipt = json.loads((OUT / 'freeze-record-receipt.json').read_text(encoding='utf-8'))
assert receipt['verified'] and receipt['frozen_sha256'] == sha(OUT / 'calibration-frozen.json')
events = {name: json.loads((OUT / (name + '-attempt.json')).read_text(encoding='utf-8'))
          for name in ['training', 'calibration', 'fit-calibration', 'community']}
for event in events.values():
    assert event['physical_attempt'] == 1 and event['exit_code'] == 0
stamp = lambda value: datetime.fromisoformat(value)
verified_at = datetime.strptime(receipt['verified_at'], '%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc)
assert stamp(events['training']['finished_at']) <= stamp(events['calibration']['started_at'])
assert stamp(events['calibration']['finished_at']) <= stamp(events['fit-calibration']['started_at'])
assert stamp(events['fit-calibration']['finished_at']) <= stamp(frozen['frozen_at']) <= verified_at
assert verified_at <= stamp(events['community']['started_at'])
train = json.loads(Path('D:/Projects/AIProjects/vinc-project/_scratch/laya/ckpt/laya-multilingual-vinc-community-supports-v2.log.json').read_text(encoding='utf-8'))
assert len(train['epochs']) == 8 and train['done']['model_sha256'] == frozen['model_sha256']
assert train['header']['items'] == 3012 and not train['header']['skipped']
assert train['header']['update_budget']['opportunities_total'] == 384
assert train['done']['optimizer_updates_executed'] + train['done']['optimizer_updates_skipped'] == 384
for name, digest in frozen['calibration_sha256'].items():
    assert sha(OUT / 'calib' / name) == digest, name
calib = load_calib(OUT / 'calib')
for split, name, n in [('calib', 'calib', 235), ('eval', 'community', 300)]:
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
assert result['rows'] == 1500 and result['rows_unlabeled_excluded'] == 0
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
assert result['confidence_decimals'] == 4 and result['asymmetry_abstain'] == .2
lines = (OUT / 'community.jsonl').read_text(encoding='utf-8').splitlines()
header, timing = json.loads(lines[0][2:]), json.loads(lines[-1][2:])
assert header['model_dir_sha256'] == frozen['model_sha256'] and header['n_pairs'] == 300
assert header['backend'] == 'eager' and header['torch'] == '2.14.1+cpu' and not header['cuda']
assert timing['n_states'] == 600 and timing['kind'] == 'timing'


def finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    return value


verification = {'model_sha256': frozen['model_sha256'], 'gate': result['gate'], 'calibration_rows': 1175, 'frozen_inputs_valid': True, 'frozen_calibration_valid': True, 'sets': {'community': {'pairs': 300, 'records': 1500, 'labels_and_logits_valid': True, 'physical_attempts': 1, 'logical_passes': 1, 'timing': timing, 'table': finite(result['table'])}}, 'source_report_sha256': sha(OUT / 'community-report.json')}
verification.update(stage_order_verified=True, training_epochs=8, training_items=3012,
                    optimizer_opportunities=384, optimizer_updates_executed=train['done']['optimizer_updates_executed'],
                    optimizer_updates_skipped=train['done']['optimizer_updates_skipped'])
with (OUT / 'verification.json').open('x', encoding='utf-8', newline='\n') as fh:
    json.dump(verification, fh, ensure_ascii=False, indent=1, allow_nan=False)
    fh.write('\n')
print('PASS: frozen inputs, 1175 calibration and 1500 evaluation records, labels, logits, temperatures, thresholds, attempts and gates')
