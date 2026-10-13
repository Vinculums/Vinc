"""저장된 예측의 분포와 오류를 재현한다. 모델 및 네트워크 호출은 없다."""
import collections
import hashlib
import json
import re
import statistics
from pathlib import Path


def read(p):
    return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip() and not s.startswith('#')]

def count(xs):
    return dict(collections.Counter(xs).most_common())

def auc(xs, ys):
    pos = [x for x, y in zip(xs, ys) if y]
    neg = [x for x, y in zip(xs, ys) if not y]
    return sum((p > n) + .5 * (p == n) for p in pos for n in neg) / (len(pos)*len(neg)) if pos and neg else None

def tokens(s):
    return set(re.findall(r'[a-z0-9가-힣]+', s.lower()))


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    BASE, RUN = args.dataset, args.run
    sets = {s:read(BASE / (s+'.jsonl')) for s in ('train','calib','eval','dev')}
    result = {'mode':'posthoc_saved_outputs_only_no_model_execution','files':{},'distributions':{},'source_overlap':{},'error_slices':{},'supports_positive_cases':[]}
    for s, rows in sets.items():
        ids = {x['pair_id'] for x in rows}
        if len(ids) != len(rows):
            raise ValueError(f"duplicate pair id: {s}")
        result['files'][s] = hashlib.sha256((BASE/(s+'.jsonl')).read_bytes()).hexdigest()
        source = [r['provenance'][end].get('node_id') for r in rows for end in ('a','b') if r['provenance'][end].get('node_id')]
        d = {'pairs':len(rows),'families':len({r['family_id'] for r in rows}),'source_nodes':len(set(source)), 'top_source_nodes':count(source), 'lang':count(r['lang'] for r in rows),'context_present':sum(bool(r.get('context')) for r in rows), 'label_vectors':count(str(r['labels']) for r in rows),'by_language':{}}
        for lang in ('all','ko','en','mixed'):
            rr=[r for r in rows if lang=='all' or r['lang']==lang]
            ld={'n':len(rr),'classes':count(r['label_class'] for r in rr), 'hard_kind':count(r['hard_kind'] for r in rr),'features':{}}
            for pred in ('supports','contradicts','same_as'):
                y=[r['labels'][pred] for r in rr]
                feats={'a_chars':[len(r['a']['text']) for r in rr],'b_chars':[len(r['b']['text']) for r in rr],'length_ratio':[len(r['a']['text'])/max(1,len(r['b']['text'])) for r in rr],'jaccard':[len(tokens(r['a']['text'])&tokens(r['b']['text']))/max(1,len(tokens(r['a']['text'])|tokens(r['b']['text']))) for r in rr]}
                ld['features'][pred]={k:{'auc':auc(v,y),'positive_mean':statistics.mean(x for x,z in zip(v,y) if z),'negative_mean':statistics.mean(x for x,z in zip(v,y) if not z)} for k,v in feats.items()}
            d['by_language'][lang]=ld
        result['distributions'][s]=d

    for s,a in sets.items():
        for t,b in sets.items():
            if s>=t: continue
            def get(rows,key):
                if key=='family':return {r['family_id'] for r in rows}
                if key=='text':return {r[end]['text'].strip() for r in rows for end in ('a','b')}
                return {r['provenance'][end].get(key) for r in rows for end in ('a','b')} - {None}
            result['source_overlap'][s+'__'+t]={k:len(get(a,k)&get(b,k)) for k in ('family','text','node_id','doc_id')}

    for split, file in [('calib','calib.jsonl'),('eval','community.jsonl')]:
        rows = read(RUN/file)
        gold = {r['pair_id']:r for r in sets[split]}
        by = {(r['pair_id'],r['predicate'],r['direction']):r for r in rows}
        for pred in ('supports','contradicts','same_as'):
            for lang in ('all','ko','en','mixed'):
                rr=[r for r in rows if r['direction']=='ab' and r['predicate']==pred and (lang=='all' or r['lang']==lang)]
                c=collections.Counter()
                cases=[]
                for r in rr:
                    p=r.get('p_cal') if r.get('p_cal') is not None else r['p_raw']
                    correct=(p>=.5)==bool(r['label'])
                    kind=('T' if correct else 'F')+('P' if p>=.5 else 'N')
                    c[kind]+=1
                    threshold=r.get('abstain_below')
                    covered=threshold is not None and round(max(p,1-p),4)>=threshold
                    if pred!='supports':
                        rev=by[(r['pair_id'],pred,'ba')]
                        rp=rev.get('p_cal') if rev.get('p_cal') is not None else rev['p_raw']
                        covered=covered and abs(p-rp)<=.2
                    if covered:c['covered_'+kind]+=1
                    if r['label']:
                        g=gold[r['pair_id']]
                        cases.append({'pair_id':r['pair_id'],'family':g['family_id'],'lang':r['lang'],'hard_kind':g['hard_kind'],'domain':g['provenance'].get('pack_domain'),'p_raw':r['p_raw'],'p':p,'kind':kind,'covered':covered,'source_node':g['provenance']['a'].get('node_id')})
                        if pred=='supports':
                            result['supports_positive_cases'].append(dict(cases[-1],split=split,a=g['a']['text'],b=g['b']['text'],rationale=g.get('rationale',{}).get(pred))) if lang=='all' else None
                result['error_slices'][f'{split}:{pred}:{lang}']={'n':len(rr),**dict(c),'positive_cases':cases}

    out=args.out
    with out.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(out),'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'distributions':{s:{k:v for k,v in d.items() if k not in ('top_source_nodes','by_language')} for s,d in result['distributions'].items()},'overlap':result['source_overlap'],'supports':{k:{a:b for a,b in v.items() if a!='positive_cases'} for k,v in result['error_slices'].items() if ':supports:' in k}},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
