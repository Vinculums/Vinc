"""Replay calibration cuts and compare cached reports without model inference."""
import hashlib
import json
import math
from pathlib import Path
import sys

DEST = Path(__file__).resolve().parent
OUT = Path(sys.argv[1])
SOURCE = Path(sys.argv[2])


def sha(data):
    return hashlib.sha256(data).hexdigest()


def finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    return value


manifest_path = DEST / 'manifest.json'
old_bytes = manifest_path.read_bytes()
manifest = json.loads(old_bytes)
assert 'precision_diagnosis' not in manifest
for name, digest in manifest['sha256'].items():
    assert sha((DEST / name).read_bytes()) == digest
rows = [json.loads(x) for x in (DEST / 'calib.jsonl').read_text(encoding='utf-8').splitlines() if x and not x.startswith('#')]
cuts = []
for path in sorted((DEST / 'calib').glob('calib-*.json')):
    c = json.loads(path.read_text(encoding='utf-8'))
    group = [r for r in rows if not r['truncated'] and r['predicate'] == c['predicate'] and ('en' if r['lang'] == 'mixed' else r['lang']) == c['lang']]
    pairs = []
    for r in group:
        z = [x/c['temperature'] for x in r['logits']]
        p0, p1 = [math.exp(x-max(z)) for x in z]
        p = p1/(p0+p1)
        pairs.append((max(p, 1-p), int((p > .5) == bool(r['label']))))
    rounded = [(round(p, 4), ok) for p, ok in pairs]
    safe = []
    for threshold in sorted({p for p, _ in rounded}):
        accepted = [ok for p, ok in rounded if p >= threshold]
        risk = (len(accepted)-sum(accepted)+1)/(len(accepted)+1)
        if risk <= .05:
            safe.append({'threshold': threshold, 'accepted': len(accepted), 'conservative_risk': risk})
    assert (safe[0]['threshold'] if safe else 1.0) == c['min_confidence']
    cuts.append({'predicate': c['predicate'], 'lang': c['lang'], 'n': len(pairs), 'errors': sum(1-ok for _, ok in pairs), 'frozen_threshold': c['min_confidence'], 'safe_cut_exists': bool(safe), 'selected_safe_cut': safe[0] if safe else None, 'full_precision_accepted': sum(p >= c['min_confidence'] for p, _ in pairs), 'rounded_accepted': sum(p >= c['min_confidence'] for p, _ in rounded)})
primary = json.loads((DEST / 'community-report.json').read_text(encoding='utf-8'))
current = finite(json.loads((OUT / 'runtime-aligned-report.json').read_text(encoding='utf-8')))
assert 'confidence_decimals' not in primary and current['confidence_decimals'] == 4
assert primary['gate'] == current['gate'] == manifest['gate']
before = {(r['predicate'], r['lang']): r for r in primary['table']}
after = {(r['predicate'], r['lang']): r for r in current['table']}
assert set(before) == set(after)
changes = []
for key in before:
    a, b = before[key], after[key]
    for field in ('n', 'positives', 'brier', 'ece', 'auroc', 'brier_raw', 'ece_raw', 'auroc_raw', 'best_baseline_auroc', 'calibration_ids'):
        assert a[field] == b[field], (key, field)
    changes.append({'predicate': key[0], 'lang': key[1], 'registered_covered': a['frozen']['covered'], 'runtime_aligned_covered': b['frozen']['covered'], 'n': a['n'], 'registered_gate': a['gate'], 'runtime_aligned_gate': b['gate']})
data = {'source_package': 'laya==0.3.28', 'source_function': 'fit_abstention_thresholds', 'source_file_sha256': sha(SOURCE.read_bytes()), 'calibration_cuts': cuts, 'confidence_decimals': 4, 'gate_values_changed': False, 'temperature_values_changed': False, 'model_inference_repeated': False, 'report_changes': changes}
new_files = {'diagnose.py': Path(__file__).read_bytes(), 'diagnostics.json': (json.dumps(data, ensure_ascii=False, indent=1, allow_nan=False)+'\n').encode(), 'runtime-aligned-report.json': (json.dumps(current, ensure_ascii=False, indent=1, allow_nan=False)+'\n').encode(), 'runtime-aligned-report-attempt.json': (OUT / 'runtime-aligned-report-attempt.json').read_bytes().replace(b'\r\n', b'\n')}
for name, value in new_files.items():
    if name != 'diagnose.py':
        with (DEST / name).open('xb') as fh:
            fh.write(value)
    manifest['sha256'][name] = sha(value)
manifest['precision_diagnosis'] = {'previous_manifest_sha256': sha(old_bytes), 'script': 'diagnose.py', 'registered_report': 'community-report.json', 'supplementary_report': 'runtime-aligned-report.json', 'data': 'diagnostics.json'}
manifest_path.write_bytes((json.dumps(manifest, ensure_ascii=False, indent=1, allow_nan=False)+'\n').encode())
print(json.dumps({'files': len(manifest['sha256']), 'manifest_sha256': sha(manifest_path.read_bytes()), 'changes': [r for r in changes if r['lang'] == 'all']}))
