# laya-predicates-ai-v2

A second AI-authored reference set for the three Laya predicates (`supports`, `contradicts`, `same_as`),
made on 2026-10-10 so that a third fine-tuning round has a fresh measurement split and a calibration pool
large enough for Korean. Method: `vinc.plan.laya-test-method-2026-10-07`. It sits beside `laya-predicates-ai-v1`
and uses the same row shape, so `rag_eval.laya.run_gold`, `fit_calib` and `report` read it unchanged.

**Status: not accepted.** The labels are written by an AI. The method asks for owner-written gold and v1 was
accepted as a one-set exception. Using this set for a ruling needs the same owner decision. No model was run
and no experiment is authorized by this directory.

## What is in it

350 pairs, 350 families, 418 distinct real graph nodes. Split: calib 177, eval 173.

| language | supports | contradicts | same_as | none | total |
|---|---|---|---|---|---|
| en | 24 | 23 | 24 | 43 | 114 |
| mixed | 22 | 24 | 24 | 47 | 117 |
| ko | 24 | 24 | 24 | 47 | 119 |

`language` is the harness rule (`pins.detect_lang`): `ko` means both sides are mostly Hangul, `en` both English,
`mixed` one side each or Korean prose carrying many Latin terms.

| construction | pairs |
|---|---|
| real_anchor_authored | 240 |
| real_real | 60 |
| real_content_both | 50 |

- `real_real`: both sides are real nodes, text frozen from the VIN-410 census copy.
- `real_content_both`: both sides carry real content, at least one as my Korean rendering of a real node or a
  verbatim excerpt of `CLAUDE.md`.
- `real_anchor_authored`: one side is real content, the other is a statement I wrote against it (a paraphrase,
  a counter-claim, a supported claim, a shifted variant).

Hard kinds: anchored_evidence 51, paraphrase 43, topical_only 40, semantic_reversal 36, negation_cue 35, lexical_overlap 34, translation 24, real_evidence 19, random 18, reversed_direction 16, scope_shift 15, time_shift 14, real_restatement 5.
Contradictions by negation cue under the report's rule baseline: {'one_side': 20, 'none': 48, 'both': 3}. A pair with a cue on one side only is
the kind the rule baseline solves.
Positives with real content on both sides: {'supports': 19, 'contradicts': 8, 'same_as': 29}.

## How v1's weak points are addressed

| v1 limit (method section 13) | here |
|---|---|
| all 180 positives were synthetic scenarios | every pair has a real node or a verbatim repository sentence on at least one side |
| frozen real negatives were memorised after training | no node of v1 gold or of the v1 and v2 training sets is used |
| `supports` ko had 15 calibration records, below `min_bucket_n` 30 | calibration records per bucket: supports:ko 60, supports:en 117, contradicts:ko 120, contradicts:en 234, same_as:ko 120, same_as:en 234 |
| contrastive triples made labels recoverable from design | one pair per family; no shared scenario across label classes |
| calib and eval shared nodes | no family and no real node crosses the split (`verify.py` checks it) |
| mixed-language `same_as` was weak and thin | 24 real English nodes with Korean renderings, plus cross-lingual negatives of every kind |

## Labelling rules

The frozen question wording and criteria of `questions.json` were applied as written. Three readings matter:

1. `supports` is directional. A conclusion placed before its evidence is 0 (`reversed_direction`).
2. Two statements about different dates, versions or subjects can both hold, so they are not a contradiction
   even when the values differ (`time_shift`, `scope_shift`). A contradiction needs the same subject and time.
3. `same_as` is strict: a statement that adds a substantive claim is not the same claim.

## Review

A second AI reader relabelled all 360 authored pairs blind (texts and the question wording only).
Pair agreement was 350/360. The 10 pairs with any disagreement were removed and are kept in `disputed.jsonl`
with both readings; no reference label was changed. Both readers are Anthropic models, so this is a
consistency filter and not a validation. `blind-inputs.jsonl` is ready for a relabel by another vendor's
model or by the owner, and that is the recommended first step.

## Audit of the VIN-410 same_as positives

`fill-same-as-audit.jsonl` holds a random sample of 90 pairs the v2 checkpoint accepted as `same_as`
positives in the personal-graph fill. I labelled 2 of 90 as true (Wilson 95 percent interval 0.6 to 7.7
percent), and both true cases are pairs whose two sides carry the identical text. 83 of the 90 pairs have a
title-only node on at least one side: the accepted positives are mostly document titles that share a topic.
The sample comes from the 243 pairs that touch no node of an earlier set, out of 814. These rows were selected
with model output, so they are usable as training negatives or as a precision check and never as measurement.

## Suggested use (needs approval before any run)

- Measurement: `eval.jsonl`, once. v1's measurement 150 has been read twice and can retire to training.
- Calibration only: hold out part of `calib.jsonl` (Korean included) before training, as v2 did.
- Training: the rest of `calib.jsonl`, all of v1, and the 88 audit negatives.
- `disputed.jsonl` stays out of training and measurement.

## Files

`gold.jsonl` `gold.split.json` `calib.jsonl` `eval.jsonl` `blind-inputs.jsonl` `reference-labels.jsonl`
`review-labels-blind.jsonl` `review.json` `disputed.jsonl` `fill-same-as-audit.jsonl` `audit-summary.json`
`gold-review.csv` `questions.json` `summary.json` `provenance.json` `manifest.json` `verify.py`

Run `python verify.py` from any directory. It needs no model.
