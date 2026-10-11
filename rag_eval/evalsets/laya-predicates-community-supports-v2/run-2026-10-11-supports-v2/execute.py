"""Bounded execution of the VIN-439 registered recipe; weights stay local."""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path('D:/Projects/AIProjects/vinc-project')
ROOT = HOME / '_wt/vin439-supports-run'
OUT = HOME / '_scratch/laya/out/community-supports-v2'
CKPT = HOME / '_scratch/laya/ckpt/laya-multilingual-vinc-community-supports-v2'
SNAP = HOME / '_scratch/hf-home/hub/models--convaiinnovations--laya-multilingual/snapshots/e4e9ddf21a7b1903b7acffd8814ad4307bf63a67'
HF = HOME / '_scratch/hf-home'
CPU = HOME / '_scratch/laya/venv/Scripts/python.exe'
CUDA = HOME / '_scratch/laya/venv-cuda/Scripts/python.exe'
INPUT = ROOT / 'rag_eval/evalsets/laya-predicates-community-supports-v2'
BASE_SHA = '9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204'
TRAIN_SHA = '7896874db0eaa0e9075d7a24c26be0f468e21ba9d0e76dd9b8d08a0c0be85409'

def now():
    return datetime.now(timezone.utc).isoformat()

def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()

def save(p, data, exclusive=True):
    with p.open('x' if exclusive else 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=1, ensure_ascii=False, allow_nan=False)
        f.write('\n')

def environment():
    env = dict(os.environ)
    env.pop('PYTHONHOME', None)
    env.pop('PYTHONPATH', None)
    env.update(PYTHONUTF8='1', HF_HOME=str(HF), HF_HUB_OFFLINE='1')
    return env

def call(name, module, args, python=CPU):
    OUT.mkdir(parents=True, exist_ok=True)
    marker = OUT / f'{name}-attempt.json'
    cmd = [str(python), '-u', '-m', module, *map(str, args)]
    event = {'physical_attempt': 1, 'started_at': now(), 'command': cmd}
    save(marker, event)
    with (OUT / f'{name}.log').open('xb') as log:
        code = subprocess.run(cmd, cwd=ROOT, env=environment(), stdout=log, stderr=subprocess.STDOUT).returncode
    event.update(finished_at=now(), exit_code=code)
    save(marker, event, False)
    print(f'{name}: exit {code}', flush=True)
    if code:
        raise SystemExit(code)

def check_inputs():
    manifest = json.loads((INPUT / 'manifest.json').read_text(encoding='utf-8'))
    assert sha(INPUT / 'manifest.json') == '81ad849f5a2d889c1d32dfffbf3d692151a3cf93943afe1104a17ceba44df242'
    for name, digest in manifest['files'].items():
        assert sha(INPUT / name) == digest, name
    assert sha(INPUT / 'training.jsonl') == TRAIN_SHA
    assert sha(SNAP / 'model.safetensors') == BASE_SHA
    assert sha(SNAP / 'rl_agent_config.json') == '25061739243b617ad88d1219ba6f8a9c86c5881ca28df024fa2d9b3b2fcc30c6'
    return manifest

def train():
    check_inputs()
    assert not CKPT.exists() and not CKPT.with_name(CKPT.name + '.log.json').exists()
    assert json.loads((OUT / 'execution-authority.json').read_text(encoding='utf-8'))['verified']
    call('training', 'rag_eval.laya.finetune', ['--snapshot', SNAP, '--data', INPUT / 'training.jsonl',
         '--out', CKPT, '--hf-home', HF, '--epochs', 8, '--seed', 20261007,
         '--expected-items', 3012, '--expected-updates', 384,
         '--expected-data-sha256', TRAIN_SHA, '--expected-base-sha256', BASE_SHA], CUDA)

def check_training():
    check_inputs()
    t = json.loads(CKPT.with_name(CKPT.name + '.log.json').read_text(encoding='utf-8'))
    assert len(t['epochs']) == 8 and t['done']['model_sha256'] == sha(CKPT / 'model.safetensors')
    assert t['header']['items'] == 3012 and not t['header']['skipped']
    assert t['header']['data_sha256'] == TRAIN_SHA
    assert t['header']['base_snapshot'] == str(SNAP)
    assert t['header']['update_budget']['opportunities_total'] == 384
    assert t['done']['optimizer_updates_executed'] + t['done']['optimizer_updates_skipped'] == 384
    return t

def calibrate():
    check_training()
    assert not (OUT / 'calib.jsonl').exists() and not (OUT / 'calib').exists()
    call('calibration', 'rag_eval.laya.run_gold', ['--gold', INPUT / 'calib.jsonl', '--split', 'calib',
         '--model-dir', CKPT, '--out', OUT / 'calib.jsonl'])
    call('fit-calibration', 'rag_eval.laya.fit_calib', ['--in', OUT / 'calib.jsonl', '--out', OUT / 'calib', '--min-bucket-n', 30])

def freeze():
    sys.path.insert(0, str(ROOT))
    from rag_eval.laya.run_gold import read_jsonl
    from rag_eval.laya.pins import PREDICATES, SYMMETRIC
    t = check_training()
    assert not (OUT / 'community.jsonl').exists()
    held = {p['pair_id']: p for p in read_jsonl(INPUT / 'calib.jsonl')}
    rows = read_jsonl(OUT / 'calib.jsonl')
    expected = {(pid, d, p) for pid in held for d, preds in [('ab', PREDICATES), ('ba', SYMMETRIC)] for p in preds}
    assert len(held) == 235 and len(rows) == 1175
    assert {(r['pair_id'], r['direction'], r['predicate']) for r in rows} == expected
    assert not any(r['truncated'] for r in rows)
    for r in rows:
        assert r['label'] == held[r['pair_id']]['labels'][r['predicate']]
    lines = (OUT / 'calib.jsonl').read_text(encoding='utf-8').splitlines()
    h, timing = json.loads(lines[0][2:]), json.loads(lines[-1][2:])
    assert h['model_dir_sha256'] == t['done']['model_sha256'] and h['n_pairs'] == 235
    assert h['backend'] == 'eager' and h['torch'] == '2.14.1+cpu' and not h['cuda']
    assert timing['kind'] == 'timing' and timing['n_states'] == 470
    for p in (OUT / 'calib').glob('*.json'):
        p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))
    files = {p.name: sha(p) for p in sorted((OUT / 'calib').glob('*.json'))}
    assert len(files) == 7 and len([n for n in files if n.startswith('calib-')]) == 6
    data = {'frozen_at': now(), 'issue': 'VIN-439', 'run_id': 'oc-20261011-vin439-1',
        'model_sha256': t['done']['model_sha256'], 'holdout_sha256': sha(INPUT / 'calib.jsonl'),
        'training_sha256': TRAIN_SHA, 'input_manifest_sha256': sha(INPUT / 'manifest.json'),
        'calibration_sha256': files, 'calibration_pairs': 235, 'min_bucket_n': 30,
        'gate': {'auroc_margin': .05, 'ece_max': .1, 'coverage_min': .5},
        'confidence_decimals': 4, 'symmetry_cutoff': .2,
        'measurements': {'community': {'gold_sha256': sha(INPUT / 'eval.jsonl'),
        'split_sha256': sha(INPUT / 'eval.split.json'), 'eval_pairs': 300}},
        'order': ['community'], 'one_logical_pass_per_measurement': True}
    save(OUT / 'calibration-frozen.json', data)
    save(OUT / 'eval-once.lock.json', data)
    print(json.dumps(data), flush=True)

def measure():
    check_training()
    frozen = json.loads((OUT / 'calibration-frozen.json').read_text(encoding='utf-8'))
    assert frozen == json.loads((OUT / 'eval-once.lock.json').read_text(encoding='utf-8'))
    receipt = json.loads((OUT / 'freeze-record-receipt.json').read_text(encoding='utf-8'))
    assert receipt['verified'] and receipt['frozen_sha256'] == sha(OUT / 'calibration-frozen.json')
    assert frozen['model_sha256'] == sha(CKPT / 'model.safetensors')
    assert frozen['gate'] == {'auroc_margin': .05, 'ece_max': .1, 'coverage_min': .5}
    for name, digest in frozen['calibration_sha256'].items():
        assert sha(OUT / 'calib' / name) == digest
    assert sha(INPUT / 'eval.jsonl') == frozen['measurements']['community']['gold_sha256']
    assert sha(INPUT / 'eval.split.json') == frozen['measurements']['community']['split_sha256']
    assert not (OUT / 'community.jsonl').exists()
    call('community', 'rag_eval.laya.run_gold', ['--model-dir', CKPT, '--calib', OUT / 'calib',
         '--split', 'eval', '--gold', INPUT / 'eval.jsonl', '--out', OUT / 'community.jsonl'])
    call('community-report', 'rag_eval.laya.report', ['--in', OUT / 'community.jsonl',
         '--gold', INPUT / 'eval.jsonl', '--out', OUT / 'community-report.md'])

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('stage', choices=['train', 'calibrate', 'freeze', 'measure', 'check'])
    stage = ap.parse_args().stage
    {'train': train, 'calibrate': calibrate, 'freeze': freeze, 'measure': measure, 'check': check_inputs}[stage]()
