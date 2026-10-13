"""진단 180개 대조와 고정 seed 가족 부트스트랩을 재현한다."""
from . import metrics as m
import collections
import hashlib
import json
import random
from pathlib import Path


def check(condition):
    if not condition:
        raise ValueError("saved audit differs from pinned report or rank AUROC")

def read(p):return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s and not s.startswith('#')]

def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    args = parser.parse_args(argv)
    BASE, RUN = args.dataset, args.run
    analysis=json.loads(args.analysis.read_text(encoding='utf-8'))
    gold={x['pair_id']:x for x in read(BASE/'eval.jsonl')}
    raw=read(RUN/'community.jsonl')
    report=json.loads((RUN/'community-report.json').read_text(encoding='utf-8'))
    checks=0
    for predicate in ['supports','contradicts','same_as']:
     for lang in ['all','ko','en','mixed']:
      rows=[x for x in raw if x['direction']=='ab' and x['predicate']==predicate and (lang=='all' or x['lang']==lang)]
      expected=next(x for x in report['table'] if x['predicate']==predicate and x['lang']==lang)
      check(abs(m.auroc([x['p_cal'] for x in rows],[x['label'] for x in rows])-expected['auroc'])<1e-12)
      observed=analysis['error_slices']['eval:'+predicate+':'+lang]
      check(sum(observed.get('covered_'+s,0) for s in ['TP','FP','TN','FN'])==expected['frozen']['covered'])
      check(abs(m.ece([x['p_cal'] for x in rows],[x['label'] for x in rows])-expected['ece'])<1e-12)
      checks+=3
    for s in ['train','calib','eval','dev']:
     for lang in ['all','ko','en','mixed']:
      rr=[x for x in read(BASE/(s+'.jsonl')) if lang=='all' or x['lang']==lang]
      for predicate in ['supports','contradicts','same_as']:
       for feat,fun in [('a_chars',lambda r:len(r['a']['text'])),('b_chars',lambda r:len(r['b']['text'])),('length_ratio',lambda r:len(r['a']['text'])/max(1,len(r['b']['text'])))]:
        rank=m.auroc([fun(x) for x in rr],[x['labels'][predicate] for x in rr])
        check(abs(rank-analysis['distributions'][s]['by_language'][lang]['features'][predicate][feat]['auc'])<1e-12)
        checks+=1
    rows=[x for x in raw if x['direction']=='ab' and x['predicate']=='supports']
    families=collections.defaultdict(list)
    for x in rows:families[gold[x['pair_id']]['family_id']].append(x)
    rng=random.Random(20261011);familylist=sorted(families);stats=collections.defaultdict(list)
    for _ in range(1000):
     draw=[x for f in rng.choices(familylist,k=len(familylist)) for x in families[f]]
     p=[x['p_cal'] for x in draw];y=[x['label'] for x in draw]
     stats['auroc'].append(m.auroc(p,y));stats['ece'].append(m.ece(p,y))
     stats['positive_recall_at_half'].append(sum(x['label'] and x['p_cal']>=.5 for x in draw)/sum(y))
     stats['coverage'].append(sum(round(max(x['p_cal'],1-x['p_cal']),4)>=x['abstain_below'] for x in draw)/len(draw))
    ci={k:[sorted(v)[24],sorted(v)[974]] for k,v in stats.items()}
    out={'checks_passed':checks,'method':'1000 family cluster bootstrap replicates, percentile order statistics 25 and 975; descriptive AI-reference uncertainty only; not a registered gate','seed':20261011,'families':len(families),'supports_interval_95':ci,'analysis_sha256':hashlib.sha256(args.analysis.read_bytes()).hexdigest(),'model_calls':0}
    dest=args.out
    with dest.open('x', encoding='utf-8', newline='\n') as stream:
     stream.write(json.dumps(out,indent=2))
    print(json.dumps(out,indent=2))


if __name__ == "__main__":
    main()
