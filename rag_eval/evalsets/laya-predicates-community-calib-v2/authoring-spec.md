# How the pairs were written

This is the rule the author followed for `laya-predicates-community-v2` (measurement) and
`laya-predicates-community-calib-v2` (calibration). It is kept with the data so that a reader can judge the labels
against the rule. A blind reviewer must not read this file before labelling.

## The three questions

1. supports: "Does statement A give evidence or a reason for statement B?"
   true: A provides a measurement, result or argument that makes B more credible.
   false: A is unrelated to B, merely restates B, or argues against B. The question is directional.
2. contradicts: "Do statements A and B make claims that cannot both be true?"
   true: accepting A requires rejecting B, including when the opposition is in meaning rather than in wording.
   false: A and B can both hold, or they are about different things.
3. same_as: "Do statements A and B make the same claim about the same thing?"
   true: A and B assert the same fact or decision, possibly in different words or languages.
   false: they differ in subject, scope, time or verdict, or one adds a claim the other lacks.

Each pair carries one of four classes: supports only, contradicts only, same_as only, or none of the three.

## Source text

One excerpt per pack document, taken from the body of the document and not from its opening. The node one-liner,
which the older community sets use as a statement, was shown to the author as text to avoid. Tables, formulas and
images were stripped before reading.

- Measurement: the 57 art and economics documents. Economics documents carry eight pairs each; art documents four or five.
- Calibration: the documents of the 26 physics and mathematics nodes that `laya-predicates-community-calib-v1` already
  uses. Four or five pairs each.

## Plan

Language, class and kind were fixed per slot before any pair was written, by a seeded shuffle.

| set | language | supports | contradicts | same_as | none |
|---|---|---|---|---|---|
| measurement | en | 24 | 24 | 24 | 48 |
| measurement | ko | 18 | 18 | 18 | 36 |
| measurement | mixed | 18 | 18 | 18 | 36 |
| calibration | ko | 12 | 12 | 12 | 24 |
| calibration | en | 6 | 6 | 6 | 12 |
| calibration | mixed | 6 | 6 | 6 | 12 |

## The grounded statement

Every pair has at least one statement that restates a fact or claim of the document in one self-contained sentence
with an explicit subject, at most 300 characters, without formulas or markup. Each slot of a document uses a
different fact.

## Kinds

- supports, `grounded_supports_claim`: A is grounded; B is a written claim that A makes more credible.
- supports, `evidence_supports_grounded`: B is grounded; A is a written observation, result or example that is evidence for B.
- supports, `reason_supports_conclusion`: A is grounded and gives a cause or mechanism; B is the conclusion it leads to.
- contradicts, `negation`: one side denies the other.
- contradicts, `value_conflict`: a different date, number, name or order for the same thing.
- contradicts, `semantic_reversal`: the opposite direction, cause or outcome, written without leaning on a negation word.
- same_as, `paraphrase`: the same claim with different wording and structure, nothing added or dropped.
- same_as, `translation` (mixed pairs): the same claim in English and in Korean.
- none, `topical_only`: two statements on the same broad topic that do not bear on each other.
- none, `reversed_direction`: A is a recommendation and B is the grounded fact that would motivate it, so A is not evidence for B.
- none, `scope_shift`: a different person, object, year or case; both hold.
- none, `lexical_overlap`: the same sentence frame about a different thing; both hold.

A written counter-statement may be false. That is intended: the label describes the relation between the two
statements, not whether either is true.

## Language

- `en`: both statements in English. `ko`: both in Korean, with names and terms written in Hangul. `mixed`: one in each
  language, with the Korean side alternating between A and B.
- Every Korean statement was written by the author. The packs hold no Korean text.

## Hard rules

- A statement is at most 400 characters. A and B are never identical. No statement is used in two pairs.
- No em dash, en dash or markup in a statement.
- A supports pair is not a restatement. A same_as pair adds no claim. A none pair is not accidentally evidence, a
  contradiction or a restatement. A contradicts pair is about the same subject.


## Length

After all pairs were written, the summed length of the two statements separated the classes: restatement pairs were
long, and none and contradicts pairs were short. A judge could have scored on length alone. The short none and
contradicts pairs of the measurement set, and a few supports pairs, were therefore lengthened before any reader or
model saw them. A grounded side took a further detail of the same fact from the same document. A written side took a
detail from the author's general knowledge. No label, language or kind changed. `summary.json` reports what length
alone still predicts (`length_only_auroc`, where 0.5 means nothing). The calibration set was left as written.
