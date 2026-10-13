"""고유 증거 출처를 늘리고 supports 검토 후보를 분리한다. 근거: [[vinc.plan.laya-training-data-pipeline-2026-10-11]] §20."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from . import public_auxiliary as A
from .data_contracts import normalized
from .intervention import jsonl_bytes
from .public_sources import digest, encoded, verify as verify_sources


def expand(eligible, parent, per_label=1000):
    if type(per_label) is not int or per_label < 1:
        raise ValueError('positive source-class cap required')
    by_id={r['pair_id']:r for r in eligible if r['role']=='train'}
    if len({r['pair_id'] for r in parent}) != len(parent):
        raise ValueError('duplicate parent pair')
    for r in parent:
        if by_id.get(r['pair_id']) != r:
            raise ValueError('parent source row changed or became ineligible')
    selected={r['pair_id']:r for r in parent}
    for source in ('scifact','vitaminc'):
        for label in (0,1):
            pool=[r for r in by_id.values() if r['source']==source and r['task']=='fact'
                  and r['targets']['aux_fact_supports']==label]
            retained=[r for r in parent if r['source']==source and r['targets']['aux_fact_supports']==label]
            if len(retained)>per_label:
                raise ValueError('cap would discard parent data')
            seen={r['independence_group'] for r in retained}
            extras=[r for r in pool if r['pair_id'] not in selected]
            extras.sort(key=lambda r:digest(r['pair_id'].encode()))
            limit=len(pool) if source=='scifact' else min(per_label,len(pool))
            first=[]
            later=[]
            for r in extras:
                if r['independence_group'] not in seen:
                    first.append(r);seen.add(r['independence_group'])
                else:
                    later.append(r)
            for r in (first+later)[:limit-len(retained)]:
                selected[r['pair_id']]=r
    return sorted(selected.values(),key=lambda r:r['pair_id'])


def candidates(train):
    result=[]
    for r in train:
        if r['task']!='fact' or r['targets']['aux_fact_supports']!=1:
            continue
        measurement=bool(re.search(r'\b(observed|measured|experiment|results|study|found|showed)\b',r['a'],re.I))
        result.append({'candidate_id':r['pair_id'],'source':r['source'],'language':r['language'],
                       'evidence':r['a'],'claim':r['b'],'locator':r['locator'],
                       'independence_group':r['independence_group'],'source_native_label':r['native_label'],
                       'review_flags':['native_support_is_not_vinc_gold',
                           'measurement_word_present' if measurement else 'measurement_word_absent',
                           'numeric_literal_present' if re.search(r'\d',r['a']) else 'numeric_literal_absent'],
                       'predicate_labels':{'supports':None,'contradicts':None,'same_as':None},
                       'annotation_events':[],'vinc_predicate_gold':False})
    return result


def validation(train, dev, parent):
    report=A.validate_exports(train,dev)
    by_id={r['pair_id']:r for r in train}
    if any(by_id.get(r['pair_id']) != r for r in parent):
        raise ValueError('parent training pair lost or changed')
    if [r for r in train if r['task']!='fact'] != sorted([r for r in parent if r['task']!='fact'],key=lambda r:r['pair_id']):
        raise ValueError('nonfact task pool changed')
    return dict(report,parent_pairs_preserved=len(parent),
                fact_support_positives=sum(r['task']=='fact' and r['targets']['aux_fact_supports']==1 for r in train),
                new_pairs=len(train)-len(parent),new_vinc_gold_pairs=0)


def review_queue(review,parent):
    old_ids={r['pair_id'] for r in parent}
    old_groups={r['independence_group'] for r in parent}
    result=[]
    for source in ('scifact','vitaminc'):
        seen=set()
        ordered=sorted([r for r in review if r['source']==source],key=lambda r:(
            r['candidate_id'] in old_ids,r['independence_group'] in old_groups,
            'measurement_word_absent' in r['review_flags'],digest(r['candidate_id'].encode())))
        for r in ordered:
            if r['independence_group'] in seen:continue
            seen.add(r['independence_group']);result.append(r['candidate_id'])
            if len(seen)==30:break
    return result


def build(sources,paws,parent_root,out,per_label=1000):
    parent_manifest=A.verify(parent_root)
    freezes=[verify_sources(sources),verify_sources(paws)]
    rows=list(A.scifact(sources))+list(A.vitaminc(sources))+list(A.klue(sources))+list(A.pawsx(paws))
    eligible,exclusions=A.separate(rows)
    parent=[json.loads(l) for l in (parent_root/'train-pairs.jsonl').read_text(encoding='utf8').splitlines()]
    dev=[json.loads(l) for l in (parent_root/'dev-pairs.jsonl').read_text(encoding='utf8').splitlines()]
    train=expand(eligible,parent,per_label)
    report=validation(train,dev,parent)
    review=candidates(train)
    # 후보 신원만 묶는다. 단어·숫자 표시는 정답 라벨을 만들지 않는다.
    queue=review_queue(review,parent)
    original_ids={r['pair_id'] for r in parent}
    manifest={'schema':'vinc.laya.evidence-expansion.v1','source_freezes':freezes,
              'parent_manifest_sha256':parent_manifest['manifest_sha256'],
              'per_label_cap':per_label,'parsed_native_pairs':len(rows),'exclusions':exclusions,'validation':report,
              'question_sha256':digest(encoded(A.QUESTIONS)),
              'classes':dict(Counter(f"{r['source']}:{r['native_label']}" for r in train)),
              'source_components':{s:len({r['independence_group'] for r in train if r['source']==s}) for s in ('scifact','vitaminc')},
              'new_positive_pairs':sum(r['candidate_id'] not in original_ids for r in review),
              'unlabeled_review_candidates':len(review),'review_queue':queue,
              'review_queue_policy':'new_component_then_new_pair_then_parent_with_measurement_word_flag',
              'new_human_reviews':0,'teacher_calls':0,'production_weights_approved':False,
              'new_korean_fact_evidence':0,'new_mixed_language_gold':0,'files':{}}
    out.mkdir(parents=True,exist_ok=False)
    contents={'train-pairs.jsonl':jsonl_bytes(train),'training.jsonl':jsonl_bytes([A.training_row(r) for r in train]),
              'dev-pairs.jsonl':(parent_root/'dev-pairs.jsonl').read_bytes(),
              'development.jsonl':(parent_root/'development.jsonl').read_bytes(),
              'supports-review-candidates.jsonl':jsonl_bytes(review),
              'parent-train-pairs.jsonl':(parent_root/'train-pairs.jsonl').read_bytes()}
    for name,raw in contents.items():
        (out/name).write_bytes(raw);manifest['files'][name]=digest(raw)
    (out/'manifest.json').write_bytes(encoded(manifest))
    return manifest


def verify(root):
    m=json.loads((root/'manifest.json').read_bytes())
    if m['schema']!='vinc.laya.evidence-expansion.v1' or m['question_sha256']!=digest(encoded(A.QUESTIONS)):
        raise ValueError('evidence task contract changed')
    from .public_sources import safe_path
    for name,sha in m['files'].items():
        if digest(safe_path(root,name).read_bytes())!=sha:raise ValueError('evidence export hash changed')
    def read(name):return [json.loads(l) for l in (root/name).read_text(encoding='utf8').splitlines()]
    train,dev,parent=read('train-pairs.jsonl'),read('dev-pairs.jsonl'),read('parent-train-pairs.jsonl')
    if validation(train,dev,parent)!=m['validation']:raise ValueError('evidence validation changed')
    if read('training.jsonl')!=[A.training_row(r) for r in train] or read('development.jsonl')!=[A.training_row(r) for r in dev]:
        raise ValueError('evidence native labels changed')
    if read('supports-review-candidates.jsonl')!=candidates(train):raise ValueError('review candidate authority changed')
    if m['review_queue']!=review_queue(candidates(train),parent):raise ValueError('review queue identity changed')
    if m['production_weights_approved'] is not False or m['new_human_reviews']!=0 or m['teacher_calls']!=0:
        raise ValueError('evidence authority changed')
    return {'verified':True,**m['validation'],'manifest_sha256':digest((root/'manifest.json').read_bytes())}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path);p.add_argument('--paws',type=Path);p.add_argument('--parent',type=Path)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--per-label',type=int,default=1000)
    a=p.parse_args(argv)
    if any((a.sources,a.paws,a.parent)) and not all((a.sources,a.paws,a.parent)):p.error('all source/parent freezes required')
    print(json.dumps(build(a.sources,a.paws,a.parent,a.out,a.per_label) if a.sources else verify(a.out),sort_keys=True))


if __name__=='__main__':main()
