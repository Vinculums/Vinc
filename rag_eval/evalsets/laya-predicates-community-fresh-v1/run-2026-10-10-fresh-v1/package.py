import hashlib
import json
from pathlib import Path

ROOT = Path('D:/Projects/AIProjects/vinc-project/_wt/vin431-fresh')
OUT = Path('D:/Projects/AIProjects/vinc-project/_scratch/laya/out/community-fresh-v1')
CKPT = Path('D:/Projects/AIProjects/vinc-project/_scratch/laya/ckpt/laya-multilingual-vinc-community-fresh-v1')
DEST = ROOT / 'rag_eval/evalsets/laya-predicates-community-fresh-v1/run-2026-10-10-fresh-v1'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    verify = json.loads((OUT / 'verification.json').read_text(encoding='utf-8'))
    frozen = json.loads((OUT / 'calibration-frozen.json').read_text(encoding='utf-8'))
    assert verify['model_sha256'] == frozen['model_sha256']
    files = {str(p.relative_to(OUT)).replace('\\', '/'): p for p in OUT.rglob('*') if p.is_file()
             and p.suffix != '.md' and p.name != 'community-report.log'}
    files['training.json'] = CKPT.with_name(CKPT.name + '.log.json')
    files['training-config.json'] = CKPT / 'rl_agent_config.json'
    for name, source in [('execute.py','vin431_pipeline.py'),('freeze.py','vin431_freeze.py'),('validate.py','vin431_validate.py'),('package.py','vin431_package.py')]:
        files[name] = Path('D:/Projects/AIProjects/vinc-project/_scratch/laya') / source
    assert not any(p.suffix in {'.safetensors', '.pt', '.pth', '.bin', '.onnx'} for p in files.values())
    for name in ['community']:
        event = json.loads((OUT / f'{name}-attempt.json').read_text(encoding='utf-8'))
        assert event['exit_code'] == 0 and event['physical_attempt'] == 1
        assert name in verify['sets']
    DEST.mkdir(exist_ok=False)
    source_hashes = {}
    for name, path in sorted(files.items()):
        data = path.read_bytes()
        data.decode('utf-8')
        source_hashes[name] = sha(data)
        target = DEST / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == 'community-report.json':
            import math
            def finite(v):
                if isinstance(v,float) and not math.isfinite(v): return None
                if isinstance(v,dict): return {k:finite(x) for k,x in v.items()}
                if isinstance(v,list): return [finite(x) for x in v]
                return v
            data = (json.dumps(finite(json.loads(data)),ensure_ascii=False,indent=1,allow_nan=False)+'\n').encode()
        target.write_bytes(data.replace(b'\r\n', b'\n'))
    hashes = {str(p.relative_to(DEST)).replace('\\', '/'): sha(p.read_bytes()) for p in sorted(DEST.rglob('*')) if p.is_file()}
    for name, h in frozen['calibration_sha256'].items():
        assert hashes['calib/' + name] == h
    manifest = {
        'issue': 'VIN-431', 'run_id': 'oc-20261010-vin431-1',
        'plan': 'vinc.plan.laya-fresh-community-training-2026-10-10',
        'input_commit': '924a12102f04a742bddea934bf643f7786515280',
        'input_manifest_sha256': frozen['input_manifest_sha256'],
        'training_sha256': frozen['training_sha256'],
        'model_sha256': frozen['model_sha256'], 'weights_committed': False,
        'gate': frozen['gate'], 'measurements': frozen['measurements'],
        'logical_measurement_passes': {k: 1 for k in verify['sets']},
        'physical_measurement_attempts': {k: 1 for k in verify['sets']},
        'line_endings': 'LF', 'calibration_lf_before_freeze': True, 'nonfinite_report_values_encoded_as': 'null',
        'report_pins_field': 'base', 'measured_model_field': 'verification.model_sha256',
        'sha256': hashes, 'scratch_source_sha256': source_hashes,
    }
    with (DEST / 'manifest.json').open('x', encoding='utf-8', newline='\n') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print(json.dumps({'path': str(DEST), 'files': len(hashes), 'manifest_sha256': sha((DEST / 'manifest.json').read_bytes())}))


if __name__ == '__main__':
    main()
