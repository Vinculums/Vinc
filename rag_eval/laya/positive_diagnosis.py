"""이전 개발 출력의 양성 오류를 다시 집계한다. 모델이나 닫힌 평가셋은 읽지 않는다."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from .public_sources import digest, encoded


def slices(outputs):
    groups=defaultdict(list)
    for row in outputs:
        if row['meta']['task'] != 'supports':
            continue
        a,b=row['logits']
        p=1/(1+math.exp(a-b))
        y=int(row['target'][1])
        groups['all'].append((p,y,row['meta']))
        groups[row['meta'].get('lang','unknown')].append((p,y,row['meta']))
    result={}
    for language,rows in sorted(groups.items()):
        counts=Counter('tp' if p>=.5 and y else 'fp' if p>=.5 else 'fn' if y else 'tn' for p,y,_ in rows)
        result[language]={'n':len(rows),'confusion':{k:counts[k] for k in ('tp','fp','fn','tn')},
                          'false_negative_ids':[m['pair_id'] for p,y,m in rows if y and p<.5],
                          'positive_mean_probability':sum(p for p,y,_ in rows if y)/sum(y for _,y,_ in rows),
                          'negative_mean_probability':sum(p for p,y,_ in rows if not y)/sum(not y for _,y,_ in rows),
                          'positive_nll':-sum(math.log(max(1e-12,p)) for p,y,_ in rows if y)/sum(y for _,y,_ in rows),
                          'negative_nll':-sum(math.log(max(1e-12,1-p)) for p,y,_ in rows if not y)/sum(not y for _,y,_ in rows)}
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='append',type=Path,required=True)
    parser.add_argument('--training',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv)
    rows=[json.loads(line) for line in args.training.read_text(encoding='utf-8').splitlines() if line.strip()]
    counts=Counter((task,int(row['gold'][task]['probabilities']['true'])) for row in rows for task in row['questions'])
    report={'schema':'vinc.laya.saved-positive-diagnosis.v1','threshold':.5,
            'training_sha256':digest(args.training.read_bytes()),
            'training_task_classes':[{ 'task':task,'label':y,'items':n,
                'unique_pairs':len({r['meta']['pair_id'] for r in rows if task in r['gold'] and int(r['gold'][task]['probabilities']['true'])==y})}
                for (task,y),n in sorted(counts.items())],
            'slices':[], 'causal_attribution_established':False,'sealed_test_opened':False}
    for root in args.run:
        for path in [root/'original-development.json',*sorted(root.glob('epoch-*/development.json'))]:
            report['slices'].append({'run':root.name,'checkpoint':path.parent.name if path.parent != root else 'original',
                                    'outputs_sha256':digest(path.read_bytes()),'supports':slices(json.loads(path.read_bytes())['outputs'])})
    with args.out.open('xb') as stream:
        stream.write(encoded(report))
    print(json.dumps({'path':str(args.out),'sha256':digest(args.out.read_bytes()),'saved_passes':len(report['slices'])},sort_keys=True))


if __name__=='__main__':
    main()
