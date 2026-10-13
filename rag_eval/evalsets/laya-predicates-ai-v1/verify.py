"""Verify the reference corpus without loading or running Laya."""
import collections
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent
PRED=('supports','contradicts','same_as')
def read(name):
    return [json.loads(line) for line in (ROOT/name).read_text(encoding='utf-8').splitlines() if line.strip()]
def check(ok, message):
    if not ok: raise SystemExit('FAIL: '+message)
rows=read('gold.jsonl')
split_doc=json.loads((ROOT/'gold.split.json').read_text(encoding='utf-8'))
split=split_doc['split']
check(len(rows)==300,'300 rows required')
check(len({r['pair_id'] for r in rows})==300,'duplicate pair ids')
check(len({tuple(sorted([r['a']['text'],r['b']['text']])) for r in rows})==300,'duplicate text pairs')
check(set(split)=={r['pair_id'] for r in rows},'split membership mismatch')
check(collections.Counter(split.values())=={'calib':150,'eval':150},'split size')
check(hashlib.sha256((ROOT/'gold.jsonl').read_bytes()).hexdigest()==split_doc['gold_sha256'],'split/gold digest mismatch')
for r in rows:
    check(set(r['labels'])==set(PRED),'predicate keys')
    check(all(type(v)is int and v in (0,1) for v in r['labels'].values()),'non-binary label')
    check(all(r['rationale'].get(p) for p in PRED),'missing label rationale')
    check(r['a']['text']!=r['b']['text'],'identical statement pair')
    check(all(0<len(r[k]['text'])<=400 for k in ('a','b')),'statement character budget')
    check(len(r.get('context') or '')<=400,'context character budget')
    check('AI' in r['author'],'AI author must be explicit')
    check(not(r['labels']['same_as'] and r['labels']['supports']),'restatement labeled as evidence')
    check(not(r['labels']['contradicts'] and (r['labels']['supports'] or r['labels']['same_as'])),'incompatible labels')
for p in PRED:
    for value in (0,1):check(sum(r['labels'][p]==value for r in rows)>=60,f'{p} class {value} quota')
for lang in ('ko','en','mixed'):check(sum(r['lang']==lang for r in rows)>=60,'language quota '+lang)
for family in {r['family_id'] for r in rows if r['synthetic']}:
    check(len({split[r['pair_id']] for r in rows if r['family_id']==family})==1,'authored family leakage')
neg=re.compile(r'\b(?:not|no|never|without|cannot|neither|nor)\b|없|않|아니|안 된다|못|무관',re.I)
semantic=[r for r in rows if r['hard_kind']=='semantic_reversal']
check(len(semantic)>=30 and all(r['labels']['contradicts']==1 and not neg.search(r['a']['text']+' '+r['b']['text']) for r in semantic),'semantic reversal challenge')
for name in ('calib','eval'):
    check(read(name+'.jsonl')==[r for r in rows if split[r['pair_id']]==name],name+' projection mismatch')
blind=read('blind-inputs.jsonl')
check(blind==[{k:r[k] for k in ('pair_id','a','b','context','lang')} for r in rows],'blind inputs contain extra fields or differ')
labels=read('reference-labels.jsonl')
check(labels==[{k:r[k] for k in ('pair_id','labels','rationale','author','labeled_at')} for r in rows],'reference labels projection mismatch')
manifest=ROOT/'manifest.json'
if manifest.exists():
    data=json.loads(manifest.read_text(encoding='utf-8'))
    for name,digest in data['sha256'].items():
        check(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,'hash mismatch '+name)
print('PASS: 300 pairs, 900 labels, class/language/challenge quotas, family split, projections, hashes.')
print('LIMIT: AI labels; source quotas differ from original M1 protocol; all positives are synthetic.')
print('No model inference or independent semantic review was performed by this validator.')
