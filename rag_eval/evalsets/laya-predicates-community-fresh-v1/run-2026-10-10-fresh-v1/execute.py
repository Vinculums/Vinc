import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('D:/Projects/AIProjects/vinc-project/_wt/vin431-fresh')
OUT = Path('D:/Projects/AIProjects/vinc-project/_scratch/laya/out/community-fresh-v1')
CKPT = Path('D:/Projects/AIProjects/vinc-project/_scratch/laya/ckpt/laya-multilingual-vinc-community-fresh-v1')
PY = 'D:/Projects/AIProjects/vinc-project/_scratch/laya/venv/Scripts/python.exe'
S = ROOT / 'rag_eval/evalsets'
INPUT = S / 'laya-predicates-community-fresh-v1'
SETS = {'community': (INPUT / 'eval.jsonl', 140)}



def now():
    return datetime.now(timezone.utc).isoformat()


def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def save(p, data, exclusive=False):
    with p.open('x' if exclusive else 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write('\n')


def call(name, module, args):
    OUT.mkdir(parents=True, exist_ok=True)
    marker = OUT / f'{name}-attempt.json'
    cmd = [PY, '-u', '-m', module, *map(str, args)]
    event = {'physical_attempt': 1, 'started_at': now(), 'command': cmd}
    save(marker, event, exclusive=True)
    env = dict(os.environ)
    env.pop('PYTHONHOME', None)
    env.pop('PYTHONPATH', None)
    env.update(PYTHONUTF8='1', HF_HOME='D:/Projects/AIProjects/vinc-project/_scratch/hf-home', HF_HUB_OFFLINE='1')
    with (OUT / f'{name}.log').open('xb') as log:
        code = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
    event.update(finished_at=now(), exit_code=code)
    save(marker, event)
    print(f'{name}: exit {code}', flush=True)
    if code:
        raise SystemExit(code)


def calibrate():
    training = json.loads(CKPT.with_name(CKPT.name + '.log.json').read_text(encoding='utf-8'))
    assert len(training['epochs']) == 4 and training['done']['model_sha256'] == sha(CKPT / 'model.safetensors')
    if (OUT / 'calib.jsonl').exists() or (OUT / 'calib').exists():
        raise FileExistsError('Calibration already started')
    call('calibration', 'rag_eval.laya.run_gold', ['--gold', INPUT / 'calib.jsonl', '--split', 'calib',
         '--model-dir', CKPT, '--out', OUT / 'calib.jsonl'])
    call('fit-calibration', 'rag_eval.laya.fit_calib', ['--in', OUT / 'calib.jsonl', '--out', OUT / 'calib'])


def measure():
    frozen = json.loads((OUT / 'calibration-frozen.json').read_text())
    assert frozen == json.loads((OUT / 'eval-once.lock.json').read_text())
    receipt = json.loads((OUT / 'freeze-record-receipt.json').read_text())
    assert receipt['verified'] and receipt['frozen_sha256'] == sha(OUT / 'calibration-frozen.json')
    assert frozen['model_sha256'] == sha(CKPT / 'model.safetensors')
    assert frozen['gate'] == {'auroc_margin': 0.05, 'ece_max': 0.1, 'coverage_min': 0.5}
    for name, (gold, count) in SETS.items():
        assert sha(gold) == frozen['measurements'][name]['gold_sha256']
        assert sha(gold.with_name(gold.stem + '.split.json')) == frozen['measurements'][name]['split_sha256']
        for f, h in frozen['calibration_sha256'].items():
            assert sha(OUT / 'calib' / f) == h
        output = OUT / f'{name}.jsonl'
        if output.exists():
            raise FileExistsError(output)
        call(name, 'rag_eval.laya.run_gold', ['--model-dir', CKPT, '--calib', OUT / 'calib', '--split', 'eval',
             '--gold', gold, '--out', output])
        text = output.read_text(encoding='utf-8').splitlines()
        header = json.loads(text[0][2:])
        timing = json.loads(text[-1][2:])
        assert header['model_dir_sha256'] == frozen['model_sha256'] and header['n_pairs'] == count
        assert timing['kind'] == 'timing' and timing['n_states'] == 2 * count
        call(name + '-report', 'rag_eval.laya.report', ['--in', output, '--gold', gold,
             '--out', OUT / f'{name}-report.md'])


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=['calibrate', 'measure'])
    args = p.parse_args()
    calibrate() if args.stage == 'calibrate' else measure()
