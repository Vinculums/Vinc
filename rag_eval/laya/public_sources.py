"""공개 원문을 고정한다. 근거: [[vinc.plan.laya-training-data-pipeline-2026-10-11]] §17."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def safe_path(root: Path, name: str) -> Path:
    target = root / name
    if not name or '\\' in name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('relative snapshot path required')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('snapshot path escaped root')
    return target


def validate_plan(plan):
    if plan.get('schema') != 'vinc.laya.public-source-plan.v1' or plan.get('scope') != 'local-auxiliary-development':
        raise ValueError('local auxiliary source plan required')
    if not plan.get('authority') or plan.get('production_weights_approved') is not False:
        raise ValueError('explicit bounded authority required')
    names = set()
    for row in plan['files']:
        safe_path(Path('.'), row['name'])
        if row['name'] in names:
            raise ValueError('duplicate snapshot name')
        names.add(row['name'])
        parsed = urlsplit(row['url'])
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('plain public HTTPS URL required')
        if not row.get('license') or not row.get('revision') or type(row['max_bytes']) is not int or not 0 < row['max_bytes'] <= 120_000_000:
            raise ValueError('rights, revision and bounded bytes required')
    for source in {r['source'] for r in plan['files']}:
        if {r['kind'] for r in plan['files'] if r['source'] == source} < {'rights', 'readme'}:
            raise ValueError('primary rights and readme required')


def capture(plan_path: Path, out: Path):
    raw = plan_path.read_bytes()
    plan = json.loads(raw)
    validate_plan(plan)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'source-plan.json').write_bytes(raw)
    receipts = []
    for row in plan['files']:
        target = safe_path(out, row['name'])
        target.parent.mkdir(parents=True, exist_ok=True)
        request = Request(row['url'], headers={'User-Agent': 'Vinc-local-dataset-capture'})
        with urlopen(request, timeout=60) as response, target.open('xb') as stream:
            length = response.headers.get('Content-Length')
            if length and int(length) > row['max_bytes']:
                raise ValueError('declared download exceeds budget')
            count, h = 0, hashlib.sha256()
            while block := response.read(1 << 20):
                count += len(block)
                if count > row['max_bytes']:
                    raise ValueError('download exceeds budget')
                stream.write(block)
                h.update(block)
            if length and count != int(length):
                raise ValueError('incomplete source download')
        receipts.append(dict(row, sha256=h.hexdigest(), bytes=count, captured_at=datetime.now(timezone.utc).isoformat()))
        print(json.dumps({'captured': row['name'], 'bytes': count, 'sha256': h.hexdigest()}), flush=True)
    manifest = {'schema': 'vinc.laya.public-source-freeze.v1', 'plan_sha256': digest(raw),
                'authority': plan['authority'], 'scope': plan['scope'], 'production_weights_approved': False,
                'files': receipts}
    # 마지막 파일이 있어야 완료된 캡처로 읽는다. 중간 실패를 완성된 원문으로 취급하지 않는다.
    (out / 'source-freeze.json').write_bytes(encoded(manifest))
    return verify(out)


def verify(root: Path):
    plan_raw = (root / 'source-plan.json').read_bytes()
    plan = json.loads(plan_raw)
    validate_plan(plan)
    manifest = json.loads((root / 'source-freeze.json').read_bytes())
    if manifest.get('schema') != 'vinc.laya.public-source-freeze.v1' or manifest['plan_sha256'] != digest(plan_raw):
        raise ValueError('source plan/freeze mismatch')
    if manifest.get('production_weights_approved') is not False or manifest.get('scope') != plan['scope'] or manifest.get('authority') != plan['authority']:
        raise ValueError('source authority mismatch')
    if len(manifest['files']) != len(plan['files']):
        raise ValueError('source inventory mismatch')
    for expected, receipt in zip(plan['files'], manifest['files']):
        if any(receipt[k] != v for k, v in expected.items()):
            raise ValueError('source metadata mismatch')
        content = safe_path(root, receipt['name']).read_bytes()
        if len(content) != receipt['bytes'] or digest(content) != receipt['sha256']:
            raise ValueError('source bytes changed')
    return {'verified': True, 'files': len(manifest['files']), 'bytes': sum(r['bytes'] for r in manifest['files']),
            'freeze_sha256': digest((root / 'source-freeze.json').read_bytes())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(capture(args.plan, args.out) if args.plan else verify(args.out), sort_keys=True))


if __name__ == '__main__':
    main()
