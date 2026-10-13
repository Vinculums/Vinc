"""저장된 개발 출력과 선택을 모델 호출 없이 검증한다."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from .public_sources import digest, encoded


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
    return out


def verify(root):
    summary=json.loads((root/'summary.json').read_bytes())
    history=json.loads((root/'history.json').read_bytes())
    checks=0
    for path in [root/'original-development.json',*sorted(root.glob('epoch-*/development.json'))]:
        data=json.loads(path.read_bytes())
        for task,values in score(data['outputs']).items():
            for key,value in values.items():
                expected=data['metrics'][task][key]
                if expected is not None if value is None else expected is None or abs(expected-value)>1e-9:
                    raise ValueError('saved development metric differs from logits')
                checks+=1
    chosen=min(history,key=lambda h:(h['development']['supports']['nll'],-h['development']['supports']['positive_recall'],h['epoch']))
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
