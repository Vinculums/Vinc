import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('D:/Projects/AIProjects/vinc-project/_wt/vin431-fresh')
OUT = Path('D:/Projects/AIProjects/vinc-project/_scratch/laya/out/community-fresh-v1')
CKPT = Path('D:/Projects/AIProjects/vinc-project/_scratch/laya/ckpt/laya-multilingual-vinc-community-fresh-v1')
S = ROOT / 'rag_eval/evalsets'
INPUT = S / 'laya-predicates-community-fresh-v1'
SETS = {'community': (INPUT / 'eval.jsonl', 140)}

sys.path.insert(0, str(ROOT))
from rag_eval.laya import metrics, report
from rag_eval.laya.run_gold import read_jsonl, load_calib


def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def write(p, data):
    with p.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=1, allow_nan=False)
        f.write('\n')


def freeze():
    for k in SETS:
        assert not (OUT / f'{k}.jsonl').exists()
    held = {p['pair_id']: p for p in read_jsonl(INPUT / 'calib.jsonl')}
    rows = read_jsonl(OUT / 'calib.jsonl')
    keys = {(p['pair_id'], p['direction'], p['predicate']) for p in rows}
    expected = {(pid, d, p) for pid in held for d, preds in [('ab', report.PREDICATES), ('ba', report.SYMMETRIC)] for p in preds}
    assert len(held) == 115 and len(rows) == len(keys) == 575 and keys == expected
    for row in rows:
        assert row['label'] == held[row['pair_id']]['labels'][row['predicate']]
    source_lines = (OUT / 'calib.jsonl').read_text(encoding='utf-8').splitlines()
    header = json.loads(source_lines[0][2:])
    timing = json.loads(source_lines[-1][2:])
    assert header['model_dir_sha256'] == sha(CKPT / 'model.safetensors')
    assert header['n_pairs'] == 115 and header['split'] == 'calib'
    assert header['backend'] == 'eager' and not header['cuda'] and header['torch'] == '2.14.1+cpu'
    assert timing['kind'] == 'timing' and timing['n_states'] == 230
    # 동결 전에 LF로 고정한다. Git 평가셋 속성과 같은 바이트를 해시한다.
    for p in (OUT / 'calib').glob('*.json'):
        p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))
    data = {
        'frozen_at': datetime.now(timezone.utc).isoformat(),
        'model_sha256': sha(CKPT / 'model.safetensors'),
        'holdout_sha256': sha(INPUT / 'calib.jsonl'),
        'training_sha256': sha(INPUT / 'training.jsonl'),
        'input_manifest_sha256': sha(INPUT / 'manifest.json'),
        'calibration_sha256': {p.name: sha(p) for p in sorted((OUT / 'calib').glob('*.json'))},
        'calibration_pairs': 115, 'min_bucket_n': 30, 'gate': report.GATE,
        'measurements': {k: {'gold_sha256': sha(p), 'split_sha256': sha(p.with_name(p.stem + '.split.json')), 'eval_pairs': n} for k, (p, n) in SETS.items()},
        'order': list(SETS), 'one_logical_pass_per_measurement': True,
    }
    assert data['holdout_sha256'] == '5fa17353824dfe7431be8a963769adf5b3a70058f4e9a65d6d0c5cebe4c736fa'
    assert data['training_sha256'] == '7cb7c4f9c9db6458cf01b60fbd812899e33d719e7a21a2308d669418f3281ba0'
    assert len(data['calibration_sha256']) == 7
    write(OUT / 'calibration-frozen.json', data)
    write(OUT / 'eval-once.lock.json', data)
    print(json.dumps(data))


if __name__ == '__main__':
    freeze()
