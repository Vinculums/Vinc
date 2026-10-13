"""사전 등록한 로컬 개발 실행. 근거: [[vinc.plan.laya-training-data-pipeline-2026-10-11]] §17."""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

from .finetune import count_optimizer_steps, freeze_embeddings, update_budget
from .metrics import auroc, brier, ece, softmax
from .public_sources import digest, encoded
from .intervention import SCHEMA, SELECTION, select_checkpoint

BASE_SHA = '9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204'


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def validate_registration(reg):
    if reg.get('schema') not in ('vinc.laya.local-development.v1', SCHEMA) or reg.get('base_sha256') != BASE_SHA:
        raise ValueError('published original-weight registration required')
    if reg.get('loss') not in ('rlcd', 'soft-ce') or reg.get('sealed_test') is not False:
        raise ValueError('development-only loss comparison required')
    if reg.get('epochs') != 2 or reg.get('item_cap') != 3012 or reg.get('scheduled_updates') != 96:
        raise ValueError('registered diagnostic budget changed')
    selection = SELECTION if reg['schema'] == SCHEMA else 'core_supports_nll_then_positive_recall'
    if type(reg.get('seed')) is not int or reg.get('selection') != selection:
        raise ValueError('fixed seed and development selection required')
    if reg['schema'] == SCHEMA:
        if reg.get('loss') != 'soft-ce' or reg.get('arm') not in ('core', 'mixed'):
            raise ValueError('fixed auxiliary comparison required')
        if reg.get('max_training_runs') != 2 or reg.get('no_new_paid_resource') is not True:
            raise ValueError('bounded local comparison required')
    if any(Path(r['path']).name.startswith(('eval', 'calib')) for r in reg['development']):
        raise ValueError('calibration or sealed file is not development')


def checked_rows(records):
    rows = []
    for record in records:
        path = Path(record['path'])
        if digest(path.read_bytes()) != record['sha256']:
            raise ValueError('registered input bytes changed')
        rows.extend(read_rows(path))
    return rows


def prepare_items(T, tok, rows, max_len, head_len, cap=None, seed=0):
    # encode_state가 자르기 전에 실제 토큰 수를 확인한다.
    for row in rows:
        raw = T.serialize_state(row['state']).replace(tok.mask_token, ' ')
        if len(T.encode_text(tok, raw, add_special_tokens=False)['input_ids']) > max_len:
            raise ValueError('state truncation refused')
    items, skipped = T.items_from_rows(tok, rows, max_len, head_len)
    if skipped:
        raise ValueError('training item skipped: ' + json.dumps(skipped))
    metadata = [dict(row.get('meta', {}), task=qid) for row in rows for qid in row['questions'] if qid in row['gold']]
    if len(metadata) != len(items):
        raise ValueError('question/metadata cardinality mismatch')
    for item in items:
        # 두 옵션의 marker와 mask가 온전히 남는지 직렬/병렬 표현은 실행 전 별도로 확인한다.
        if item['k'] != 2 or len(item['target']) != 2 or any(v not in (0.0, 1.0) for v in item['target']):
            raise ValueError('hard binary native/predicate targets required')
    if cap is not None:
        if len(items) < cap:
            raise ValueError('insufficient items for registered update budget')
        chosen = sorted(random.Random(seed).sample(range(len(items)), cap)) if len(items) > cap else list(range(len(items)))
        items, metadata = [items[i] for i in chosen], [metadata[i] for i in chosen]
    return items, metadata


def metrics(records, metadata):
    grouped = defaultdict(list)
    for (_, logits, target, k), meta in zip(records, metadata):
        if k != 2 or len(logits) != 2:
            raise ValueError('binary canonical logits required')
        p, y = softmax(list(map(float, logits)))[1], int(target[1])
        if not math.isfinite(p):
            raise ValueError('nonfinite development logits')
        grouped[meta['task']].append((p, y))
    result = {}
    for task, rows in sorted(grouped.items()):
        p, y = [r[0] for r in rows], [r[1] for r in rows]
        tp = sum(q >= .5 and t == 1 for q, t in rows)
        fp = sum(q >= .5 and t == 0 for q, t in rows)
        positive = sum(y)
        result[task] = {'n': len(rows), 'positives': positive, 'auroc': auroc(p,y), 'ece': ece(p,y),
                        'brier': brier(p,y), 'nll': -sum(math.log(max(1e-12, q if t else 1-q)) for q,t in rows)/len(rows),
                        'positive_recall': tp/positive if positive else None,
                        'positive_precision': tp/(tp+fp) if tp+fp else None,
                        'accuracy': sum((q>=.5)==bool(t) for q,t in rows)/len(rows)}
    return result


def evaluate(T, model, tok, items, metadata, device, cfg):
    records = T.calibration_records(model, tok, items, device, cfg['max_len'], cfg['head_max_len'],
                                    batch_size=8, parallel=T.uses_parallel_layout(cfg))
    return metrics(records, metadata), [{'meta': m, 'logits': list(map(float, r[1])), 'target': list(map(float, r[2]))}
                                      for r,m in zip(records,metadata)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--hf-home', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args(argv)
    raw = args.registration.read_bytes()
    reg = json.loads(raw)
    validate_registration(reg)
    if digest(Path(__file__).read_bytes()) != reg['development_module_sha256']:
        raise ValueError('registered development implementation changed')
    if reg['schema'] == SCHEMA:
        if digest(Path(__file__).with_name('intervention.py').read_bytes()) != reg['intervention_module_sha256']:
            raise ValueError('registered intervention implementation changed')
    if digest((args.snapshot/'model.safetensors').read_bytes()) != BASE_SHA:
        raise ValueError('retired or non-original checkpoint refused')
    os.environ.update(HF_HOME=str(args.hf_home), HF_HUB_OFFLINE='1')
    import torch
    from laya import train as T
    if not torch.cuda.is_available():
        raise ValueError('registered local GPU unavailable')
    torch.manual_seed(reg['seed'])
    model, tok, cfg = T.load_checkpoint(str(args.snapshot))
    if digest(Path(T.__file__).read_bytes()) != reg['laya_train_sha256']:
        raise ValueError('installed training implementation changed')
    train_rows, dev_rows = checked_rows(reg['training']), checked_rows(reg['development'])
    if reg['schema'] == SCHEMA:
        exposure_path = Path(reg['exposure']['path'])
        if digest(exposure_path.read_bytes()) != reg['exposure']['sha256']:
            raise ValueError('registered sampling exposure changed')
        exposure = json.loads(exposure_path.read_bytes())
        if exposure['arm'] != reg['arm'] or exposure['seed'] != reg['seed'] or len(train_rows) != reg['item_cap']:
            raise ValueError('registered sampling arm changed')
    items, meta = prepare_items(T,tok,train_rows,cfg['max_len'],cfg['head_max_len'],reg['item_cap'],reg['seed'])
    dev_items, dev_meta = prepare_items(T,tok,dev_rows,cfg['max_len'],cfg['head_max_len'])
    budget = update_budget(len(items),reg['epochs'])
    if budget['opportunities_total'] != reg['scheduled_updates']:
        raise ValueError('actual update budget mismatch')
    for item in items + dev_items:
        actual = T.encode_item(tok,item,cfg['max_len'],cfg['head_max_len'],parallel=T.uses_parallel_layout(cfg))
        batch = T.collate_items([[actual]], tok.pad_token_id)
        if int(batch['marker_mask'].sum()) != 2 or len(actual['markers']) != 2:
            raise ValueError('option marker/mask lost')
    preflight = {'registration_sha256': digest(raw), 'items':len(items), 'development_items':len(dev_items),
                 'item_identity_sha256':digest(encoded(meta)), 'task_counts':dict(__import__('collections').Counter(m['task'] for m in meta)),
                 'scheduled_updates':budget['opportunities_total'], 'state_truncations':0,'skipped_items':0,
                 'torch':torch.__version__,'laya_train_sha256':reg['laya_train_sha256'],'base_sha256':BASE_SHA,
                 'development_module_sha256':reg['development_module_sha256'],
                 'data_rows':len(train_rows),'development_rows':len(dev_rows), 'gpu':torch.cuda.get_device_name(0)}
    if reg['schema'] == SCHEMA:
        preflight['exposure'] = exposure
        preflight['task_class_counts'] = dict(__import__('collections').Counter(
            f"{m['task']}:{int(item['target'][1])}" for item, m in zip(items, meta)))
    if args.preflight:
        print(json.dumps(preflight,sort_keys=True));return
    # 실행을 시작하기 전에 시도 영수증을 독점 생성한다. 재실행은 다른 등록을 요구한다.
    args.out.mkdir(parents=True,exist_ok=False)
    (args.out/'attempt.json').write_bytes(encoded({'started_at':time.time(),'registration_sha256':digest(raw)}))
    (args.out/'preflight.json').write_bytes(encoded(preflight))
    (args.out/'registration.json').write_bytes(raw)
    device = torch.device('cuda')
    trainable,total = freeze_embeddings(model)
    model.to(device)
    baseline, outputs = evaluate(T,model,tok,dev_items,dev_meta,device,cfg)
    (args.out/'original-development.json').write_bytes(encoded({'metrics':baseline,'outputs':outputs}))
    config = T.TrainConfig(epochs=reg['epochs'],micro_batch=8,grad_accum=8,encoder_lr=2.5e-5,head_lr=1e-4,
                           amp=True,gradient_checkpointing=True,seed=reg['seed'],loss=reg['loss'],calib_frac=0.,calib_max=0,log_every=50)
    (args.out/'training-config.json').write_bytes(encoded(T.asdict(config)))
    history, micro, gradients = [], [], []
    ce_original, rl_original, clip_original = T.soft_ce_loss,T.rlcd_loss,torch.nn.utils.clip_grad_norm_

    def ce(logits,target,mask):
        loss=ce_original(logits,target,mask)
        value=float(loss.detach())
        micro.append({'ce':value,'combined':value,'rl':0.0,'n':int(mask.shape[0]),
                      'correct':int((logits.detach().argmax(-1)==target.argmax(-1)).sum()),
                      'positive':int((target.argmax(-1)==1).sum()),
                      'tp':int(((logits.detach().argmax(-1)==1)&(target.argmax(-1)==1)).sum())})
        if not math.isfinite(value):raise ValueError('nonfinite CE objective')
        return loss

    def rl(*a,**kw):
        loss=rl_original(*a,**kw)
        value=float(loss.detach())
        micro[-1].update(combined=value,rl=value-micro[-1]['ce'])
        if not math.isfinite(value):raise ValueError('nonfinite combined objective')
        return loss

    def clip(*a,**kw):
        norm=clip_original(*a,**kw)
        gradients.append(float(norm))
        return norm

    started=time.perf_counter()
    counters={'executed':0}

    def epoch_end(epoch,loss):
        checkpoint=args.out/f'epoch-{epoch+1}'
        T.save_checkpoint(model,tok,cfg,str(checkpoint))
        result,outputs=evaluate(T,model,tok,dev_items,dev_meta,device,cfg)
        (checkpoint/'development.json').write_bytes(encoded({'metrics':result,'outputs':outputs}))
        record={'epoch':epoch+1,'combined_loss':loss,'ce_mean':sum(r['ce'] for r in micro)/len(micro),
                'rl_mean':sum(r['rl'] for r in micro)/len(micro),'train_accuracy':sum(r['correct'] for r in micro)/sum(r['n'] for r in micro),
                'train_positive_recall':sum(r['tp'] for r in micro)/sum(r['positive'] for r in micro),
                'gradient_nonfinite':sum(not math.isfinite(g) for g in gradients),
                'gradient_norm_max_finite':max([g for g in gradients if math.isfinite(g)],default=None),
                'executed_updates_cumulative':counters['executed'],'development':result,
                'elapsed_s':time.perf_counter()-started,'peak_alloc_gib':torch.cuda.max_memory_allocated()/2**30}
        history.append(record)
        (args.out/'history.json').write_bytes(encoded(history))
        print(json.dumps({'epoch':epoch+1,'core_supports':result.get('supports'),'elapsed_s':record['elapsed_s']},sort_keys=True),flush=True)
        micro.clear();gradients.clear()
        model.train()

    with count_optimizer_steps(torch.optim) as counters, patch.object(T,'soft_ce_loss',ce), patch.object(T,'rlcd_loss',rl), patch.object(torch.nn.utils,'clip_grad_norm_',clip):
        T.train_model(model,tok,items,config,device,cfg['max_len'],cfg['head_max_len'],on_epoch_end=epoch_end,parallel=T.uses_parallel_layout(cfg))
    chosen=select_checkpoint(history,reg['selection'])
    summary={'registration_sha256':digest(raw),'model_sha256':digest((args.out/f'epoch-{chosen["epoch"]}'/'model.safetensors').read_bytes()),
             'selected_epoch':chosen['epoch'],'selection':reg['selection'],'loss':reg['loss'],'baseline':baseline,'selected':chosen['development'],
             'executed_updates':counters['executed'],'scheduled_updates':budget['opportunities_total'],
             'AMP_skips':budget['opportunities_total']-counters['executed'],'params_trainable':trainable,'params_total':total,
             'elapsed_s':time.perf_counter()-started,'production_approved':False,'sealed_test_opened':False}
    (args.out/'summary.json').write_bytes(encoded(summary))
    print(json.dumps(summary,sort_keys=True),flush=True)


if __name__=='__main__':
    main()
