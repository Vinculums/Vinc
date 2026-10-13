# -*- coding: utf-8 -*-
"""임베딩 동결 학습. 근거: [[vinc.plan.laya-test-method-2026-10-07]] §7.1."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import math
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


def update_budget(items, epochs, micro_batch=8, grad_accum=8):
    if any(type(v) is not int or v < 1 for v in (items, epochs, micro_batch, grad_accum)):
        raise ValueError('items, epochs and batch sizes must be positive integers')
    batches = math.ceil(items / micro_batch)
    per_epoch = math.ceil(batches / grad_accum)
    return {'micro_batches_per_epoch': batches, 'opportunities_per_epoch': per_epoch,
            'opportunities_total': per_epoch * epochs}


@contextmanager
def count_optimizer_steps(optim):
    counts = {'executed': 0}
    original = optim.AdamW
    handles = []

    def factory(*args, **kwargs):
        optimizer = original(*args, **kwargs)

        def on_step(*unused):
            counts['executed'] += 1

        handles.append(optimizer.register_step_post_hook(on_step))
        return optimizer

    try:
        with patch.object(optim, 'AdamW', factory):
            yield counts
    finally:
        for handle in handles:
            handle.remove()

def freeze_embeddings(model):
    frozen = 0
    for name, parameter in model.named_parameters():
        if 'embeddings' in name:
            parameter.requires_grad_(False)
            frozen += parameter.numel()
    if not frozen:
        raise ValueError('No embedding parameters found')
    return (sum(p.numel() for p in model.parameters() if p.requires_grad),
            sum(p.numel() for p in model.parameters()))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--hf-home', type=Path, required=True)
    p.add_argument('--epochs', type=int, default=4)
    p.add_argument('--seed', type=int, default=20261007)
    p.add_argument('--expected-items', type=int)
    p.add_argument('--expected-updates', type=int)
    p.add_argument('--expected-data-sha256')
    p.add_argument('--expected-base-sha256')
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    SNAP, DATA, OUT = str(args.snapshot), args.data, args.out
    SEED, EPOCHS = args.seed, args.epochs
    LOG = OUT.parent / (OUT.name + '.log.json')
    if OUT.exists() or LOG.exists():
        raise FileExistsError(OUT)
    if EPOCHS < 1:
        raise ValueError('epochs must be positive')
    data_sha = hashlib.sha256(DATA.read_bytes()).hexdigest()
    if args.expected_data_sha256 and data_sha != args.expected_data_sha256:
        raise ValueError('training digest mismatch')
    if args.expected_base_sha256 and hashlib.sha256((args.snapshot / 'model.safetensors').read_bytes()).hexdigest() != args.expected_base_sha256:
        raise ValueError('base checkpoint digest mismatch')
    os.environ['HF_HOME'] = str(args.hf_home)
    os.environ['HF_HUB_OFFLINE'] = '1'
    import torch
    from laya import train as T
    assert torch.cuda.is_available(), "CUDA 가 없다"
    device = torch.device("cuda")
    torch.manual_seed(SEED)
    model, tok, cfg = T.load_checkpoint(SNAP)
    max_len, head_max_len = cfg["max_len"], cfg["head_max_len"]
    rows = T.read_jsonl(str(DATA))
    items, skipped = T.items_from_rows(tok, rows, max_len, head_max_len)
    budget = update_budget(len(items), EPOCHS)
    if args.expected_items is not None and (len(items) != args.expected_items or skipped):
        raise ValueError('training item count mismatch or skipped items')
    if args.expected_updates is not None and budget['opportunities_total'] != args.expected_updates:
        raise ValueError('training update budget mismatch')
    trainable, total = freeze_embeddings(model)
    config = T.TrainConfig(epochs=EPOCHS, micro_batch=8, grad_accum=8, encoder_lr=2.5e-5, head_lr=1e-4,
                           amp=True, gradient_checkpointing=True, seed=SEED, log_every=50,
                           calib_max=0, calib_frac=0.0)
    header = {"base_snapshot": SNAP, "data": str(DATA), "data_sha256": data_sha, "update_budget": budget,
              "rows": len(rows), "items": len(items), "skipped": skipped, "params_total": total,
              "params_trainable": trainable, "frozen": "encoder embeddings (names containing 'embeddings')",
              "config": T.asdict(config), "max_len": max_len, "head_max_len": head_max_len,
              "torch": torch.__version__, "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name(0),
              "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    print(json.dumps(header, ensure_ascii=False), flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps({"header": header, "epochs": []}, ensure_ascii=False, indent=1), encoding="utf-8")

    history: list[dict] = []
    t0 = time.perf_counter()
    optimizer_steps = {'executed': 0}

    def on_epoch_end(epoch: int, mean_loss: float) -> None:
        history.append({"epoch": epoch + 1, "mean_loss": mean_loss, "elapsed_s": round(time.perf_counter() - t0, 1),
                        "peak_alloc_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
                        "optimizer_updates_executed_cumulative": optimizer_steps['executed']})
        LOG.write_text(json.dumps({"header": header, "epochs": history}, ensure_ascii=False, indent=1), encoding="utf-8")
        T.save_checkpoint(model, tok, dict(cfg, max_len=max_len, head_max_len=head_max_len),
                          str(OUT / "checkpoint_latest"))

    with count_optimizer_steps(torch.optim) as optimizer_steps:
        T.train_model(model, tok, items, config, device, max_len, head_max_len, on_epoch_end=on_epoch_end,
                      parallel=T.uses_parallel_layout(cfg))

    out_cfg = dict(cfg, max_len=max_len, head_max_len=head_max_len, fine_tuned=True)
    out_cfg["training"] = dict(out_cfg.get("training") or {}, laya_train=T.asdict(config),
                               vinc_m1_8={"base_revision": "e4e9ddf21a7b1903b7acffd8814ad4307bf63a67",
                                          "data_sha256": header["data_sha256"], "frozen": header["frozen"],
                                          "items": len(items), "history": history,
                                          "update_budget": budget, "optimizer_updates_executed": optimizer_steps['executed']})
    T.save_checkpoint(model, tok, out_cfg, str(OUT))
    sha = hashlib.sha256((OUT / "model.safetensors").read_bytes()).hexdigest()
    done = {"out": str(OUT), "model_sha256": sha, "history": history,
            "optimizer_updates_executed": optimizer_steps['executed'],
            "optimizer_updates_skipped": budget['opportunities_total'] - optimizer_steps['executed'],
            "wall_min": round((time.perf_counter() - t0) / 60, 1),
            "peak_alloc_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2)}
    LOG.write_text(json.dumps({"header": header, "epochs": history, "done": done}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(json.dumps(done, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
