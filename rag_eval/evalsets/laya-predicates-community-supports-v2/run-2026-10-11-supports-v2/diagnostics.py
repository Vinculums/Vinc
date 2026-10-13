"""Independently check saved scores and frozen cuts without model inference."""
import json
import math
from pathlib import Path
try:
    from vin439_run import ROOT, OUT, CKPT, INPUT, sha, save
except ModuleNotFoundError:
    from execute import ROOT, OUT, CKPT, INPUT, sha, save

def read(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x and not x.startswith('#')]

def probability(logits, temperature=1.):
    a, b = [x / temperature for x in logits]
    peak = max(a, b)
    return math.exp(b-peak)/(math.exp(a-peak)+math.exp(b-peak))

def auc(ps, ys):
    positive = [p for p,y in zip(ps,ys) if y]
    negative = [p for p,y in zip(ps,ys) if not y]
    return sum((p>n) + .5*(p==n) for p in positive for n in negative)/(len(positive)*len(negative))

def ece(ps, ys):
    value = 0.
    bins = 15
    for i in range(bins):
        lo, hi = .5 + .5*i/bins, .5 + .5*(i+1)/bins
        entries = [(max(p,1-p),int((p>=.5)==bool(y))) for p,y in zip(ps,ys)
                   if (lo<=max(p,1-p)<=hi if i==0 else lo<max(p,1-p)<=hi)]
        if entries:
            value += abs(sum(p-y for p,y in entries))/len(ps)
    return value

def close(a,b):
    assert math.isclose(a,b,abs_tol=1e-12), (a,b)

def main():
    report = json.loads((OUT/'community-report.json').read_text(encoding='utf-8'))
    rows = read(OUT/'community.jsonl')
    dirs = {(r['pair_id'],r['predicate'],r['direction']):r for r in rows}
    diagnostics = []
    for t in report['table']:
        group = [r for r in rows if r['direction']=='ab' and r['predicate']==t['predicate'] and not r['truncated'] and (t['lang']=='all' or r['lang']==t['lang'])]
        ps, ys = [r['p_cal'] for r in group], [r['label'] for r in group]
        close(auc(ps,ys),t['auroc'])
        close(ece(ps,ys),t['ece'])
        close(sum((p-y)**2 for p,y in zip(ps,ys))/len(ps),t['brier'])
        totals = {k:0 for k in ['tp','fn','tn','fp']}
        covered = dict(totals)
        for r in group:
            p = r['p_cal']
            key = 'tp' if r['label'] and p>=.5 else 'fn' if r['label'] else 'fp' if p>=.5 else 'tn'
            totals[key] += 1
            ok = r['abstain_below'] is not None and round(max(p,1-p),4)>=r['abstain_below']
            if r['predicate'] in ['contradicts','same_as']:
                rev = dirs.get((r['pair_id'],r['predicate'],'ba'))
                ok = ok and rev is not None and not rev['truncated'] and abs(p-rev['p_cal'])<=.2
            if ok:
                covered[key] += 1
        count = sum(covered.values())
        assert count == t['frozen']['covered']
        if count:
            close((covered['fn']+covered['fp'])/count,t['frozen']['error'])
        diagnostics.append({'predicate':t['predicate'],'lang':t['lang'],'n':len(group),'positives':sum(ys),
            'confusion_at_0_5':totals,'covered_confusion':covered,
            'positive_recall_at_0_5':totals['tp']/(totals['tp']+totals['fn']),
            'covered_positive_recall':covered['tp']/(totals['tp']+totals['fn']),
            'positive_raw_p_le_0_05':sum(r['label'] and r['p_raw']<=.05 for r in group),
            'covered_errors':covered['fn']+covered['fp']})
    calibration_rows = read(OUT/'calib.jsonl')
    cuts=[]
    for path in sorted((OUT/'calib').glob('calib-*.json')):
        c=json.loads(path.read_text(encoding='utf-8'))
        group=[r for r in calibration_rows if not r['truncated'] and r['predicate']==c['predicate'] and ('en' if r['lang']=='mixed' else r['lang'])==c['lang']]
        pairs=[]
        for r in group:
            p=probability(r['logits'],c['temperature'])
            pairs.append((round(max(p,1-p),4),int((p>.5)==bool(r['label']))))
        safe=[]
        for th in sorted({p for p,_ in pairs}):
            admitted=[ok for p,ok in pairs if p>=th]
            risk=(len(admitted)-sum(admitted)+1)/(len(admitted)+1)
            if risk<=.05:
                safe.append({'threshold':th,'accepted':len(admitted),'errors':len(admitted)-sum(admitted),'conservative_risk':risk})
        assert (safe[0]['threshold'] if safe else 1.)==c['min_confidence']
        assert c['n']==len(group)
        cuts.append({'predicate':c['predicate'],'lang':c['lang'],'records':len(group),
            'positive_records':sum(r['label'] for r in group),'errors_at_0_5':sum(1-ok for _,ok in pairs),
            'temperature':c['temperature'],'status':c['status'],'min_confidence':c['min_confidence'],
            'underpowered':c['underpowered'],'selected_safe_cut':safe[0] if safe else None,'safe_cut_exists':bool(safe)})
    result={'model_inference_calls':0,'probability_metrics_independently_checked':True,
        'registered_gate':report['gate'],'confidence_decimals':4,'calibration_cuts_replayed':cuts,
        'confusion_tables':diagnostics,'new_admission_gates_added':False,
        'source_report_sha256':sha(OUT/'community-report.json')}
    save(OUT/'diagnostics.json',result)
    print(json.dumps({'aggregate':[r for r in diagnostics if r['lang']=='all'],'calibration':cuts}),flush=True)

if __name__=='__main__':
    main()
