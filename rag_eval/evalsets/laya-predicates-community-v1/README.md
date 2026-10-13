# laya-predicates-community-v1

Out-of-domain material for the three Laya predicates, built on 2026-10-10 from the four official starter packs
in `starter-packs/`. Every other Laya set so far is Vinc's own engineering text; this one is art, economics,
physics and mathematics. Method: `vinc.plan.laya-test-method-2026-10-07`. Reasoning and limits live in the graph
record named in the pull request; this page only says what the files are.

**Status: accepted by the owner on 2026-10-10** as the out-of-domain measurement of the third fine-tuning round
(`decision:laya-predicates-ai-v2-and-community-v1-accepted-as-gold-and-v3-run-ordered-2026-10-10`). The labels were written by an AI and no person read them pair by pair. No model was run by this
directory.

## The pack split

| side | packs | what it is for | file |
|---|---|---|---|
| measure | art, economics (57 nodes) | measurement only, never training or calibration | `gold.jsonl` |
| train | physics, mathematics | `same_as` hard negatives for training | `train-negatives.jsonl` |

No node and no text is on both sides (`verify.py` checks it). The two bridges that join both sides are unused.

## gold.jsonl: 140 measurement pairs

| language | supports | contradicts | same_as | none | total |
|---|---|---|---|---|---|
| en | 12 | 12 | 12 | 24 | 60 |
| mixed | 8 | 8 | 8 | 16 | 40 |
| ko | 8 | 8 | 8 | 16 | 40 |

Same row shape as `laya-predicates-ai-v1` and `-v2`, so `run_gold`, `fit_calib` and `report` read it unchanged.
`gold.split.json` marks every pair `eval`. Run it with the calibration frozen on the in-domain set: the point is
to see whether that calibration and those thresholds carry over to another field.

One side of every pair is a pack node's one-liner as shipped (English). The other side is another pack one-liner,
my Korean rendering of one, or a statement I wrote against it. The packs hold no Korean text, so every Korean
side is mine.

## train-negatives.jsonl: 404 pairs

Two different concept nodes that the pack links (`relates_to`, `derives_from`, `prerequisite_of`), labelled
`same_as` 0 and nothing else. The label is a rule, not a reading: a random 36 were read and all 36 held.
Bridge-to-concept links are left out because a bridge can restate its endpoint.
`build_train --same-as-negatives` reads this file (VIN-421). The third round uses a frozen sample of 120 of them, not all 404,
so that negatives do not swamp the `same_as` positives; see `vinc.plan.laya-finetune-v3-2026-10-10`.

## Review

A second AI reader relabelled all 140 authored pairs blind and agreed on all of them, so `disputed.jsonl` is empty.
Both readers are the same vendor, and full agreement mostly shows the labels are easy to recover. Treat it as weak evidence.

## VIN-57

VIN-57's community corpus v2 is built from the same four packs. No retrieval question or gold node was read or
written here. A checkpoint trained on `train-negatives.jsonl` has seen physics and mathematics node text, so if
Laya is later measured as a resolver on VIN-57 gold, only art and economics questions are clean for it.

Run `python verify.py` from any directory. It needs no model.
