# Writing statement pairs for three relations

You write pairs of statements (A, B) for training a small classifier. Each pair has one of four labels. The
classifier will be asked three yes/no questions about every pair:

1. supports: "Does statement A give evidence or a reason for statement B?"
   true: A provides a measurement, result or argument that makes B more credible.
   false: A is unrelated to B, merely restates B, or argues against B.
   This is directional: A as evidence for B, not B for A.
2. contradicts: "Do statements A and B make claims that cannot both be true?"
   true: accepting A requires rejecting B, including when the opposition is in meaning rather than in wording.
   false: A and B can both hold, or they are about different things.
3. same_as: "Do statements A and B make the same claim about the same thing?"
   true: A and B assert the same fact or decision, possibly in different words or languages.
   false: they differ in subject, scope, time or verdict, or one adds a claim the other lacks.

Labels: S = supports only. C = contradicts only. E = same_as only. N = none of the three.

## Your input

A batch file with `docs` (three documents, each with `doc_key`, `title`, `text`) and `plan` (15 slots per
document). Each slot names a `lang`, a `label` and a `kind`. Write exactly one pair for every slot.

## The grounded statement

Every pair has at least one **grounded statement**: one sentence that faithfully states a fact or claim the
document itself makes. Rules for it:

- Self-contained, with an explicit subject. Never "this note", "the document", "it", "the above".
- One sentence, at most 300 characters. No formulas, no LaTeX, no markdown, no asterisks, no bullet fragments.
- Use a different fact of the document for each of its 15 slots. Do not reuse a grounded statement.
- Prefer concrete facts (who showed what, what a concept says, what follows from what) over vague summaries.

## Language

- `en`: both statements in English.
- `ko`: both statements in Korean. Write names and technical terms in Hangul (아인슈타인, 엔트로피, 끈 이론). Do not
  leave Latin letters in Korean statements, except unavoidable symbols; write numbers as digits.
- `mixed`: one statement in English and the other in Korean. Alternate which side (A or B) is Korean.

## The kinds

**S, kind `grounded_supports_claim`**: A is the grounded statement. B is a claim you write that A makes more
credible (a consequence, a generalisation it is evidence for). B must not restate A.

**S, kind `evidence_supports_grounded`**: B is the grounded statement. A is a specific observation, experiment,
result or example you write that is evidence for B. A must not restate B.

**C, kind `contradiction`**: one side is the grounded statement, the other is a statement you write about the
same subject and the same time that cannot be true if the grounded one is. In about half of your C pairs,
avoid plain negation words (not, never, no; 아니다, 없다, 않는다) and contradict by meaning: a different value,
date, direction, cause or outcome. The written statement may be false; that is intended.

**E, kind `paraphrase`**: the grounded statement and a restatement with clearly different wording and sentence
structure that asserts exactly the same claim. Add nothing and drop nothing.

**E, kind `translation`** (mixed only): the grounded statement in English and its faithful Korean translation.

**N, kind `topical_only`**: the grounded statement and a true, ordinary statement on the same broad topic that
is neither evidence for it, nor the same claim, nor in conflict with it.

**N, kind `reversed_direction`**: A is a recommendation or conclusion you write ("Courses should ...",
"Researchers should not ..."), and B is the grounded statement that would be the reason for it. Because the
question asks whether A is evidence for B, the answer is no.

**N, kind `scope_shift`**: the grounded statement and a statement about a different person, object, year or
case, such that both hold and they are not the same claim. Not a contradiction.

**N, kind `lexical_overlap`**: the grounded statement and a statement that reuses its sentence frame and several
of its words but is about a different thing, so that both hold and neither supports the other.

## Hard rules

- Each statement is at most 400 characters and is never empty. A and B are never identical.
- No em dash or en dash characters anywhere. No markdown.
- An S pair must not also be a restatement. An E pair must not add a claim. An N pair must not accidentally be
  evidence, a contradiction or a restatement. A C pair must be about the same subject, not a different one.
- Do not copy these instructions' examples. Do not write about the task itself.

## Output

Write a JSON Lines file, one object per slot, in plan order, with exactly these fields:

{"doc_key": "d01", "slot": 1, "a": "...", "b": "...", "grounded": "a" | "b" | "both", "why": "one short sentence: why this label holds"}

`grounded` says which side is the grounded statement (`both` for E pairs where both sides state the document's
fact, and for translations).

Read each document yourself and write each pair yourself. Do not generate pairs with a script or a template.
When done, reply with the number of lines written.
