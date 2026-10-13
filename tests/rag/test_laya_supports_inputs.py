import copy
import json
from types import SimpleNamespace

import pytest

from rag_eval.laya.community_inputs import sha
from rag_eval.laya.finetune import count_optimizer_steps, update_budget
from rag_eval.laya.supports_inputs import assert_roles, load_agreed, training_rows


def pair(pid, origin=None, text=None):
    return {"pair_id": pid, "a": {"id": pid + ":a", "text": text or pid + " A"},
            "b": {"id": pid + ":b", "text": pid + " B"}, "lang": "en",
            "provenance": {"a": {"node_id": origin}},
            "labels": {"supports": 1, "contradicts": 0, "same_as": 0}}


def test_predicate_balance_preserves_targets_and_direction_without_relabelling():
    pairs = [pair("positive"), pair("negative")]
    pairs[1]["labels"]["supports"] = 0
    before = copy.deepcopy(pairs)
    rows = training_rows(pairs)
    assert pairs == before
    for pred in ("supports", "contradicts", "same_as"):
        selected = [r for r in rows if pred in r["gold"]]
        assert len(selected) == 4
        if pred == "supports":
            assert all(r["meta"]["direction"] == "ab" for r in selected)
            assert sum(r["gold"][pred]["probabilities"]["true"] for r in selected) == 2
    assert all(set(r["questions"]) == set(r["gold"]) for r in rows)
    assert sum(len(r["gold"]) for r in rows) == 12


def test_item_budget_counts_questions_and_partial_accumulation_windows():
    assert update_budget(2510, 4) == {"micro_batches_per_epoch": 314,
                                     "opportunities_per_epoch": 40, "opportunities_total": 160}
    assert update_budget(3012, 8)["opportunities_total"] == 384
    assert update_budget(65, 1)["opportunities_total"] == 2


@pytest.mark.parametrize("args", [(0, 4), (2510, 0), (True, 4), (10, 1, 0, 8)])
def test_invalid_budget_is_refused(args):
    with pytest.raises(ValueError):
        update_budget(*args)


def test_development_can_share_measurement_pack_nodes_but_not_text():
    groups = {k: [pair(k, "shared" if k in ("dev", "eval") else k)]
              for k in ("train", "calib", "eval", "dev")}
    assert_roles(groups)
    groups["dev"][0]["a"]["text"] = groups["eval"][0]["a"]["text"]
    with pytest.raises(ValueError, match="development and measurement"):
        assert_roles(groups)


def test_training_rejects_calibration_origin_and_duplicate_combined_ids():
    groups = {k: [pair(k, k)] for k in ("train", "calib", "eval", "dev")}
    groups["train"][0]["provenance"]["a"]["node_id"] = "calib"
    with pytest.raises(ValueError, match="overlap"):
        assert_roles(groups)
    groups["train"][0]["provenance"]["a"]["node_id"] = "train"
    groups["calib"].append(copy.deepcopy(groups["calib"][0]))
    with pytest.raises(ValueError, match="duplicate pair id"):
        assert_roles(groups)


def test_only_frozen_agreement_partition_is_usable(tmp_path):
    gold = (json.dumps(pair("p")) + "\n").encode()
    (tmp_path / "gold.jsonl").write_bytes(gold)
    review = {"gold_sha256": sha(gold), "agreed": ["p"], "disagreed": []}
    path = tmp_path / "crossvendor-agreed.json"
    path.write_text(json.dumps(review))
    assert len(load_agreed(tmp_path)[0]) == 1
    review["disagreed"] = ["p"]
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="partition"):
        load_agreed(tmp_path)
    review.update(disagreed=[], gold_sha256="stale")
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="digest"):
        load_agreed(tmp_path)


def test_optimizer_counts_executed_steps_and_restores_factory_after_error():
    class Optimizer:
        def register_step_post_hook(self, hook):
            self.hook = hook
            return SimpleNamespace(remove=lambda: setattr(self, "hook", None))

        def step(self):
            self.hook(self, (), {})

    optim = SimpleNamespace(AdamW=Optimizer)
    with pytest.raises(RuntimeError):
        with count_optimizer_steps(optim) as counts:
            instance = optim.AdamW()
            instance.step()
            instance.step()
            assert counts["executed"] == 2
            raise RuntimeError("training failure")
    assert optim.AdamW is Optimizer
    assert instance.hook is None


def test_counter_works_with_real_cpu_adamw_and_scheduler():
    torch = pytest.importorskip("torch")
    weight = torch.nn.Parameter(torch.tensor([1.0]))
    original = torch.optim.AdamW
    with count_optimizer_steps(torch.optim) as counts:
        optimizer = torch.optim.AdamW([weight], lr=0.1)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=2)
        for _ in range(2):
            optimizer.zero_grad()
            weight.square().sum().backward()
            optimizer.step()
            scheduler.step()
        assert counts["executed"] == 2
    assert torch.optim.AdamW is original


def test_frozen_supports_inputs_regenerate():
    from pathlib import Path
    from rag_eval.laya.supports_inputs import main
    root = Path(__file__).resolve().parents[2] / "rag_eval/evalsets"
    out = root / "laya-predicates-community-supports-v2"
    assert main(["--root", str(root), "--out", str(out), "--check"]) is None


def test_training_digest_refused_before_torch_or_model_load(tmp_path):
    from rag_eval.laya.finetune import main
    data = tmp_path / "train.jsonl"
    data.write_text("{}\n")
    with pytest.raises(ValueError, match="training digest"):
        main(["--snapshot", str(tmp_path / "missing"), "--data", str(data),
              "--out", str(tmp_path / "output"), "--hf-home", str(tmp_path / "hf"),
              "--expected-data-sha256", "stale"])
