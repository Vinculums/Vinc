# laya-predicates-community-train-v1

Training pairs for the three predicates, built on 2026-10-10 from the document bodies of the physics and mathematics
starter packs. The owner chose Laya as the only judge, and a Laya that customers reach may only be tuned on text we are
allowed to ship. Every tuned checkpoint so far saw personal graph text; this set is the shareable replacement for that
part of the training data. Method: `vinc.plan.laya-test-method-2026-10-07`.

**Status: made at the owner's direction on 2026-10-10.** The pairs were written and labelled by an AI; no person read
them pair by pair. No model was run and no training is authorized by this directory.

## 505 training pairs from 35 documents

| language | supports | contradicts | same_as | none | total |
|---|---|---|---|---|---|
| en | 33 | 34 | 34 | 65 | 166 |
| mixed | 30 | 34 | 34 | 70 | 168 |
| ko | 33 | 34 | 35 | 69 | 171 |

Same row shape as the other sets. `gold.split.json` marks every pair `calib`, which is the value
`rag_eval.laya.build_train` reads as trainable; nothing here is calibration or measurement.

Each pair holds at least one statement grounded in a pack document: one sentence that states a fact the document makes.
The other side is a second grounded statement, or a statement written against it (evidence, a counter-claim, a
paraphrase, a translation, a same-topic bystander). `authoring-spec.md` is the specification the writers followed.

## Kept apart from the other community sets

| set | role | pack nodes |
|---|---|---|
| `laya-predicates-community-v1` | measurement | art, economics |
| `laya-predicates-community-calib-v1` | calibration | 26 physics and mathematics nodes |
| this set | training | the other 35 physics and mathematics nodes |

No pack node and no text is shared between them (`verify.py` checks it). Three bridges that reach into art or economics
were left out of training so that the measurement domains stay unseen.

## How it was made and checked

Thirteen writing sessions each read three documents and wrote 15 pairs per document to a fixed plan of language, label
and kind: 570 pairs. 45 from the three excluded bridges were dropped. Ten blind sessions relabelled the remaining 525
from the texts alone; 12 disagreed and were removed, no label was changed. 8 more were removed because they talked
about the notes themselves. The specifying session then read 39 kept pairs and found no wrong label. `disputed.jsonl`
holds the 20 removed pairs with the reason.

## Limits

- Written and labelled by one model family. Agreement between writer and blind reader is a consistency filter.
- 28 of the 35 nodes are physics, most of them quantum gravity. The relations are general; the
  vocabulary is not.
- Every Korean statement was written by the model. The packs hold no Korean text.
- Grounded statements restate the documents; they were not checked against outside sources.

Run `python verify.py` from any directory. It needs no model.
