"""저장된 개발 출력과 선택을 모델 호출 없이 검증한다."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from .public_sources import digest, encoded
from .intervention import SCHEMA, balanced_rows, jsonl_bytes


def score(outputs):
    groups=defaultdict(list)
    for row in outputs:
        a,b=row['logits']
        if not all(math.isfinite(v) for v in (a,b)):
            raise ValueError('nonfinite saved logits')
        m=max(a,b)
        p=math.exp(b-m)/(math.exp(a-m)+math.exp(b-m))
        y=int(row['target'][1])
        if row['target'] != [float(1-y),float(y)] or y not in (0,1):
            raise ValueError('saved binary target changed')
        groups[row['meta']['task']].append((p,y))
    out={}
    for task,rows in groups.items():
        tp=sum(p>=.5 and y for p,y in rows)
        fp=sum(p>=.5 and not y for p,y in rows)
        pos=sum(y for _,y in rows)
        out[task]={'n':len(rows),'positives':pos,
                   'nll':-sum(math.log(max(1e-12,p if y else 1-p)) for p,y in rows)/len(rows),
                   'brier':sum((p-y)**2 for p,y in rows)/len(rows),
                   'accuracy':sum((p>=.5)==bool(y) for p,y in rows)/len(rows),
                   'positive_recall':tp/pos if pos else None,
                   'positive_precision':tp/(tp+fp) if tp+fp else None}
        positives=[p for p,y in rows if y]
        negatives=[p for p,y in rows if not y]
        out[task]['auroc'] = (sum((p>q)+.5*(p==q) for p in positives for q in negatives)/(len(positives)*len(negatives))
                             if positives and negatives else float('nan'))
        calibration = 0.0
        for i in range(15):
            lo,hi=.5+.5*i/15,.5+.5*(i+1)/15
            bucket=[(max(p,1-p), (p>=.5)==bool(y)) for p,y in rows
                    if (lo<=max(p,1-p)<=hi if i==0 else lo<max(p,1-p)<=hi)]
            calibration += abs(sum(p-int(correct) for p,correct in bucket))/len(rows)
        out[task]['ece'] = calibration
    return out


def verify(root):
    summary=json.loads((root/'summary.json').read_bytes())
    history=json.loads((root/'history.json').read_bytes())
    checks=0
    saved={}
    for path in [root/'original-development.json',*sorted(root.glob('epoch-*/development.json'))]:
        data=json.loads(path.read_bytes())
        saved[path.parent.name if path.parent != root else 'original'] = data
        for task,values in score(data['outputs']).items():
            for key,value in values.items():
                expected=data['metrics'][task][key]
                if expected is not None if value is None else expected is None or abs(expected-value)>1e-9:
                    raise ValueError('saved development metric differs from logits')
                checks+=1
    selection=summary.get('selection','core_supports_nll_then_positive_recall')
    if selection == 'core_supports_nll_then_positive_recall':
        chosen=min(history,key=lambda h:(h['development']['supports']['nll'],-h['development']['supports']['positive_recall'],h['epoch']))
    elif selection == 'core_supports_f1_then_recall_then_nll':
        def key(h):
            s=h['development']['supports']
            p,r=s['positive_precision'] or 0.,s['positive_recall']
            f1=2*p*r/(p+r) if p+r else 0.
            return -f1,-r,s['nll'],h['epoch']
        chosen=min(history,key=key)
        registration_bytes=(root/'registration.json').read_bytes()
        reg=json.loads(registration_bytes)
        from .development import validate_registration, checked_rows
        validate_registration(reg)
        if reg['schema'] != SCHEMA or reg['selection'] != selection or digest(registration_bytes) != summary['registration_sha256']:
            raise ValueError('saved registration changed')
        if summary['loss'] != reg['loss']:
            raise ValueError('registered objective changed')
        core,native=(checked_rows(reg['sampling_inputs'][tier]) for tier in ('core','native'))
        rows,exposure=balanced_rows(core,native,reg['arm'],reg['seed'])
        train=Path(reg['training'][0]['path']).read_bytes()
        if train != jsonl_bytes(rows) or digest(train) != reg['training'][0]['sha256']:
            raise ValueError('balanced sampling replay changed')
        exposure_raw=Path(reg['exposure']['path']).read_bytes()
        recorded=json.loads(exposure_raw)
        if digest(exposure_raw) != reg['exposure']['sha256'] or any(recorded[k] != v for k,v in exposure.items()):
            raise ValueError('sampling exposure changed')
        expected=[]
        for row in checked_rows(reg['development']):
            for task in row['questions']:
                gold=row['gold'][task]['probabilities']
                expected.append({'meta':dict(row.get('meta',{}),task=task), 'target':[gold['false'],gold['true']]})
        for data in saved.values():
            if len(data['outputs']) != len(expected) or any(r['meta'] != e['meta'] or r['target'] != e['target'] for r,e in zip(data['outputs'],expected)):
                raise ValueError('development identities or labels changed')
        if summary['baseline'] != saved['original']['metrics']:
            raise ValueError('baseline summary differs from outputs')
        if len(history) != reg['epochs'] or {h['epoch'] for h in history} != {1,2}:
            raise ValueError('registered epochs incomplete')
        for h in history:
            if h['development'] != saved[f'epoch-{h["epoch"]}']['metrics']:
                raise ValueError('history differs from saved outputs')
        if history[-1]['executed_updates_cumulative'] != summary['executed_updates']:
            raise ValueError('history update accounting changed')
        checks += 10
    else:
        raise ValueError('unknown saved selection')
    if chosen['epoch'] != summary['selected_epoch'] or chosen['development'] != summary['selected']:
        raise ValueError('registered checkpoint selection changed')
    if digest((root/f'epoch-{chosen["epoch"]}'/'model.safetensors').read_bytes()) != summary['model_sha256']:
        raise ValueError('checkpoint bytes changed')
    if summary['executed_updates']+summary['AMP_skips'] != summary['scheduled_updates'] or summary['scheduled_updates'] != 96:
        raise ValueError('update accounting changed')
    if summary['sealed_test_opened'] is not False or summary['production_approved'] is not False:
        raise ValueError('development authority changed')
    return {'verified':True,'checks':checks,'selected_epoch':chosen['epoch'],'model_sha256':summary['model_sha256'],
            'summary_sha256':digest((root/'summary.json').read_bytes()),'executed_updates':summary['executed_updates'],
            'AMP_skips':summary['AMP_skips']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args(argv)
    print(json.dumps(verify(args.run),sort_keys=True))


if __name__=='__main__':
    main()
