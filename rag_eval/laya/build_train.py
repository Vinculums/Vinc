# -*- coding: utf-8 -*-
"""학습셋 조립. 근거: [[vinc.plan.laya-test-method-2026-10-07]] §7.1."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from .pins import PREDICATES, QUESTIONS, SYMMETRIC, detect_lang, render_state

BOOK_SLUGS = ["01-human-memory", "02-working-with-ai", "03-distribution", "04-sharing", "05-graph-md", "06-space",
              "07-agents", "08-trust", "09-boundaries", "10-architecture", "11-memory-model", "12-canonical-graph",
              "13-ontology", "14-graph-md", "15-document-pipeline", "16-search", "17-embedding", "18-retrieval",
              "19-brief", "20-mcp", "21-agents", "21b-role-contract", "22-provenance", "23-temporal", "24-view",
              "25-space", "26-sync", "27-infrastructure", "28-performance", "29-call-efficiency", "30-embedding-eval",
              "31-migration", "32-failure-cases", "33-measurement-before-decision", "34-cost", "35-cycle"]

REJECTIONS = [  # vinc_merge_rejections 2026-10-08, 30 쌍
    ("concept:account-backend", "concept:inprocess-state"), ("concept:agent-copy-owner", "concept:agent-copy-writer"),
    ("concept:agent-design", "concept:agent-infra"), ("concept:agent-design", "concept:agent-marketing"),
    ("concept:agent-design", "concept:agent-ops"), ("concept:agent-design", "concept:agent-orchestrator"),
    ("concept:agent-design", "concept:agent-release"), ("concept:agent-design", "concept:agent-security"),
    ("concept:agent-dev-account", "concept:agent-dev-app"), ("concept:agent-dev-account", "concept:agent-dev-core"),
    ("concept:agent-dev-app", "concept:agent-dev-core"), ("concept:agent-infra", "concept:agent-marketing"),
    ("concept:agent-infra", "concept:agent-ops"), ("concept:agent-infra", "concept:agent-orchestrator"),
    ("concept:agent-infra", "concept:agent-release"), ("concept:agent-infra", "concept:agent-security"),
    ("concept:agent-marketing", "concept:agent-ops"), ("concept:agent-marketing", "concept:agent-orchestrator"),
    ("concept:agent-marketing", "concept:agent-release"), ("concept:agent-marketing", "concept:agent-security"),
    ("concept:agent-ops", "concept:agent-orchestrator"), ("concept:agent-ops", "concept:agent-release"),
    ("concept:agent-ops", "concept:agent-security"), ("concept:agent-orchestrator", "concept:agent-release"),
    ("concept:agent-orchestrator", "concept:agent-security"), ("concept:agent-release", "concept:agent-security"),
    ("concept:agent-reviewer-checklist", "concept:agent-reviewer-judgement"),
    ("concept:fail-open-degradation", "concept:세-fail-open"), ("concept:그래프-시간", "concept:세계-시간과-그래프-시간"),
    ("concept:세계-시간", "concept:세계-시간과-그래프-시간"),
]


# same_as 음성으로 받는 출처 규칙. 읽어서 붙인 라벨과 규칙으로 붙인 라벨을 섞지 않으려고 이름을 고정한다.
# 근거: [[vinc.plan.laya-test-method-2026-10-07]] §7.1 (교사 LLM 라벨을 쓰지 않는다)
NEGATIVE_RULES = ("pack-linked-distinct-concepts", "audited-model-accepted-pair")


def gold_dir_pairs(gold_dir: Path) -> tuple[list[dict], list[dict], str]:
    """골드 디렉터리 하나를 (보정 쌍, 측정 쌍, gold.jsonl sha256) 으로 읽는다."""
    raw = (gold_dir / "gold.jsonl").read_bytes()
    gold = [json.loads(l) for l in raw.decode("utf-8").splitlines() if l.strip()]
    split = json.loads((gold_dir / "gold.split.json").read_text(encoding="utf-8"))["split"]
    return ([g for g in gold if split[g["pair_id"]] == "calib"], [g for g in gold if split[g["pair_id"]] == "eval"],
            hashlib.sha256(raw).hexdigest())


def hub_node(token: str, nid: str, base: str) -> dict | None:
    req = urllib.request.Request(f"{base.rstrip(chr(47))}/v1/nodes/{urllib.parse.quote(nid, safe=':')}",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def node_text(n: dict) -> str:
    node = n.get("node", n)
    title = (node.get("title") or "").strip()
    one = (node.get("one_liner") or "").strip()
    text = title if not one else f"{title}. {one.split('. ')[0].rstrip('.')}."
    return text[:400]


def rows_for_pair(a_text: str, b_text: str, ctx: str | None, labels: dict, source: str, lang: str) -> list[dict]:
    """run_gold 와 같은 두 state(ab, ba). 라벨이 None 인 술어는 gold 에서 뺀다(items_from_rows 가 건너뛴다)."""
    out = []
    for direction, (x, y), preds in (("ab", (a_text, b_text), PREDICATES), ("ba", (b_text, a_text), SYMMETRIC)):
        gold = {p: {"probabilities": {"true": float(labels[p]), "false": 1.0 - float(labels[p])}}
                for p in preds if labels.get(p) is not None}
        if not gold:
            continue
        out.append({"state": render_state(x, y, ctx), "questions": {p: QUESTIONS[p] for p in preds},
                    "gold": gold, "meta": {"source": source, "direction": direction, "lang": lang}})
    return out


def pair_builder(eval_pairs):
    eval_texts = {g[k]["text"] for g in eval_pairs for k in ("a", "b")}
    eval_ids = {g[k]["id"] for g in eval_pairs for k in ("a", "b")}
    seen_pairs = set()
    rows: list[dict] = []
    counts: Counter = Counter()
    excluded: Counter = Counter()

    def add_pair(a_id, b_id, a_text, b_text, ctx, labels, source, lang):
        key = frozenset((a_text, b_text))
        # 보정 150 은 방식 §7.1 이 학습에 허용한 분할이라 통째로 넣는다(측정 쌍과 노드 텍스트를 공유하는 쌍이
        # 있고 그 수는 기록한다). 그래프에서 따로 뽑은 것들만 측정 쌍의 텍스트·노드 id 와 겹치면 뺀다.
        if source == "calib":
            if a_text in eval_texts or b_text in eval_texts or a_id in eval_ids or b_id in eval_ids:
                counts["calib:shares_node_with_eval"] += 1
        elif a_text in eval_texts or b_text in eval_texts or a_id in eval_ids or b_id in eval_ids:
            excluded[f"{source}:eval_leak"] += 1
            return
        if key in seen_pairs:
            excluded[f"{source}:duplicate"] += 1
            return
        seen_pairs.add(key)
        new = rows_for_pair(a_text, b_text, ctx, labels, source, lang)
        rows.extend(new)
        counts[source] += 1
        counts[f"{source}:rows"] += len(new)
        counts[f"{source}:items"] += sum(len(r["gold"]) for r in new)

    return rows, counts, excluded, add_pair


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gold-dir', type=Path, required=True)
    p.add_argument('--candidates', type=Path, required=True)
    p.add_argument('--typed-decisions', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--base-training', type=Path)
    p.add_argument('--holdout', type=Path)
    p.add_argument('--same-as-pairs', type=Path)
    p.add_argument('--same-as-negatives', type=Path, action='append', default=[],
                   help='same_as 음성 쌍(JSONL). provenance.rule 이 NEGATIVE_RULES 에 있어야 한다. 여러 번 줄 수 있다')
    p.add_argument('--train-gold-dir', type=Path, action='append', default=[],
                   help='보정 쌍을 학습에 보태는 또 다른 골드 디렉터리. 그 측정 쌍은 막는다. 여러 번 줄 수 있다')
    p.add_argument('--block-gold-dir', type=Path, action='append', default=[],
                   help='측정 쌍의 텍스트와 노드 id 만 막는 골드 디렉터리(측정 전용 셋). 여러 번 줄 수 있다')
    p.add_argument('--hub', default=os.environ.get('VINC_HUB', 'https://mcp.vincs.io'))
    p.add_argument('--seed', type=int, default=20261007)
    p.add_argument('--typed-decisions-rows', type=int, default=600)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    GOLD_DIR, CAND, TD, OUT = args.gold_dir, args.candidates, args.typed_decisions, args.out
    SEED, TD_ROWS = args.seed, args.typed_decisions_rows
    if OUT.exists() or OUT.with_suffix('.summary.json').exists():
        raise FileExistsError(OUT)
    token = os.environ.get("VINC_RO")
    if not token and not args.base_training:
        print("VINC_RO is not set", file=sys.stderr)
        return 2
    gold = [json.loads(l) for l in (GOLD_DIR / "gold.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    split = json.loads((GOLD_DIR / "gold.split.json").read_text(encoding="utf-8"))["split"]
    eval_pairs = [g for g in gold if split[g["pair_id"]] == "eval"]
    calib_pairs = [g for g in gold if split[g["pair_id"]] == "calib"]
    # 다른 골드 디렉터리의 측정 쌍도 같은 그물로 막는다. 한 체크포인트를 여러 셋에서 재려면 어느 셋의 측정 쌍도
    # 학습에 들어가면 안 된다.
    extra_train, gold_dirs = [], {}
    for d in args.train_gold_dir:
        c, e, digest = gold_dir_pairs(d)
        extra_train.append((d.name, c))
        eval_pairs = eval_pairs + e
        gold_dirs[d.name] = {"role": "train", "gold_sha256": digest, "calib": len(c), "eval_blocked": len(e)}
    for d in args.block_gold_dir:
        _, e, digest = gold_dir_pairs(d)
        eval_pairs = eval_pairs + e
        gold_dirs[d.name] = {"role": "block", "gold_sha256": digest, "eval_blocked": len(e)}
    eval_texts = {t for g in eval_pairs for t in (g["a"]["text"], g["b"]["text"])}
    eval_ids = {i for g in eval_pairs for i in (g["a"]["id"], g["b"]["id"])}
    holdout_ids = set(json.loads(args.holdout.read_text(encoding='utf-8'))['holdout']) if args.holdout else set()
    if not holdout_ids <= {g['pair_id'] for g in calib_pairs}:
        raise ValueError('Holdout must be a subset of calib')
    holdout_pairs = [g for g in calib_pairs if g['pair_id'] in holdout_ids]
    held_texts = {g[k]['text'] for g in holdout_pairs for k in ('a', 'b')}
    held_ids = {g[k]['id'] for g in holdout_pairs for k in ('a', 'b')}
    rows, counts, excluded, original_add_pair = pair_builder(eval_pairs)

    def add_pair(a_id, b_id, a_text, b_text, ctx, labels, source, lang):
        if a_id in held_ids or b_id in held_ids or a_text in held_texts or b_text in held_texts:
            excluded[f'{source}:holdout_leak'] += 1
            return
        original_add_pair(a_id, b_id, a_text, b_text, ctx, labels, source, lang)

    # 1. 보정 150
    for g in calib_pairs:
        add_pair(g["a"]["id"], g["b"]["id"], g["a"]["text"], g["b"]["text"], g.get("context"),
                 {p: g["labels"][p] for p in PREDICATES}, "calib", g["lang"])

    # 1-1. 다른 골드의 보정 쌍. 출처 이름이 "calib" 이 아니라서 측정 쌍과 텍스트나 노드 id 가 겹치면 빠진다
    # (주 골드의 보정 쌍에만 방식 §7.1 의 공유 허용이 걸린다).
    for name, pairs in extra_train:
        for g in pairs:
            add_pair(g["a"]["id"], g["b"]["id"], g["a"]["text"], g["b"]["text"], g.get("context"),
                     {p: g["labels"][p] for p in PREDICATES}, f"calib-{name}", g["lang"])

    for path in args.same_as_negatives:
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            g = json.loads(line)
            if g['provenance']['rule'] not in NEGATIVE_RULES or g['labels'].get('same_as') != 0:
                raise ValueError('Negative pairs need a registered provenance rule and same_as 0')
            # 이 파일이 말하는 것은 same_as 하나다. 다른 술어 라벨이 실려 있어도 싣지 않는다.
            add_pair(g['a']['id'], g['b']['id'], g['a']['text'], g['b']['text'], None,
                     {'supports': None, 'contradicts': None, 'same_as': 0}, g['source'], g['lang'])

    if args.same_as_pairs:
        for line in args.same_as_pairs.read_text(encoding='utf-8').splitlines():
            g = json.loads(line)
            if g['provenance']['rule'] not in ('same-node-title-one-liner', 'declared-document-language-mirror'):
                raise ValueError('Additional labels must have deterministic graph provenance')
            add_pair(g['a']['id'], g['b']['id'], g['a']['text'], g['b']['text'], None,
                     {'supports': None, 'contradicts': 0, 'same_as': 1}, g['source'], g['lang'])

    misses = []
    if args.base_training:
        # v1에서 측정 id·텍스트 제외를 검증한 동결 입력을 재사용한다. 보정 행은 위에서 재구성한다.
        base = [json.loads(l) for l in args.base_training.read_text(encoding='utf-8').splitlines() if l.strip()]
        for r in base:
            source = r['meta']['source']
            if source == 'calib':
                continue
            if source != 'typed-decisions' and any(t in r['state'] for t in held_texts | eval_texts):
                excluded[f'{source}:blocked_text'] += 1
                continue
            rows.append(r)
            counts[source + ':rows'] += 1
            counts[source + ':items'] += len(r['gold'])
    else:
        # 2. 책 미러
        misses = []
        for slug in BOOK_SLUGS:
            ko = hub_node(token, f"concept:doc.vinc.book.{slug}", args.hub)
            en = hub_node(token, f"concept:doc.vinc.book.{slug}.en", args.hub)
            if not ko or not en:
                misses.append(slug)
                continue
            a, b = node_text(ko), node_text(en)
            if len(a) < 6 or len(b) < 6:
                misses.append(slug)
                continue
            add_pair(f"concept:doc.vinc.book.{slug}", f"concept:doc.vinc.book.{slug}.en", a, b, None,
                     {"supports": 0, "contradicts": 0, "same_as": 1}, "book-mirror", detect_lang(a, b))

        # 3. 머지 거부
        for a_id, b_id in REJECTIONS:
            na, nb = hub_node(token, a_id, args.hub), hub_node(token, b_id, args.hub)
            if not na or not nb:
                misses.append(f"{a_id}|{b_id}")
                continue
            a, b = node_text(na), node_text(nb)
            add_pair(a_id, b_id, a, b, None, {"supports": None, "contradicts": 0, "same_as": 0}, "merge-reject",
                     detect_lang(a, b))

        # 4. 후보 random
        cand = [json.loads(l) for l in CAND.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
        for c in cand:
            if c.get("source") == "random":
                add_pair(c["a"]["id"], c["b"]["id"], c["a"]["text"], c["b"]["text"], c.get("context"),
                         {"supports": 0, "contradicts": 0, "same_as": 0}, "random", c["lang"])

        # 5. typed-decisions noul
        import pandas as pd
        df = pd.read_parquet(TD)
        rng = random.Random(SEED)
        idx = list(range(len(df)))
        rng.shuffle(idx)
        td_items = 0
        for i in idx[:TD_ROWS]:
            r = df.iloc[i]
            qs = json.loads(r["questions"]) if isinstance(r["questions"], str) else r["questions"]
            gd = json.loads(r["gold"]) if isinstance(r["gold"], str) else r["gold"]
            noul_qs = {k: v for k, v in qs.items() if v.get("type") == "noul" and k in gd}
            if not noul_qs:
                continue
            state = r["state"] if isinstance(r["state"], str) else json.dumps(r["state"], ensure_ascii=False)
            gold_q = {k: {"probabilities": gd[k]["probabilities"]} for k in noul_qs}
            rows.append({"state": state, "questions": noul_qs, "gold": gold_q,
                         "meta": {"source": "typed-decisions", "id": r["id"], "workflow": r["workflow"], "lang": "en"}})
            counts["typed-decisions"] += 1
            td_items += len(noul_qs)
        counts["typed-decisions:items"] = td_items

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
    summary = {"rows": len(rows), "counts": dict(counts), "excluded": dict(excluded), "misses": misses,
               "eval_pairs_excluded_by": {"texts": len(eval_texts), "node_ids": len(eval_ids)},
               "typed_decisions_rows_sampled": TD_ROWS, "seed": SEED, "sha256": sha, "out": str(OUT),
               "base_training_sha256": hashlib.sha256(args.base_training.read_bytes()).hexdigest() if args.base_training else None,
               "holdout_pairs": len(holdout_ids),
               "holdout_sha256": hashlib.sha256(args.holdout.read_bytes()).hexdigest() if args.holdout else None,
               "same_as_pairs_sha256": hashlib.sha256(args.same_as_pairs.read_bytes()).hexdigest() if args.same_as_pairs else None,
               "same_as_negatives_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in args.same_as_negatives},
               "extra_gold_dirs": gold_dirs,
               "gold_sha256": hashlib.sha256((GOLD_DIR / "gold.jsonl").read_bytes()).hexdigest()}
    (OUT.with_suffix(".summary.json")).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
