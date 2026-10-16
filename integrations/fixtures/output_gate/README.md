# Frozen output gate evidence

`hand-labelled.json` preserves the original fifteen exp01/02 and eight exp07
answers and their pre-existing hand labels. Source hashes were recorded before
porting the gate. The experiment scripts and model runners were not executed.

`test_original_output_gate.py` is an unchanged copy of the original ten synthetic
tests. Package tests retain those cases with a test-only adapter selecting the
corresponding structured role profile. Production does not parse legacy prose.

`proposed-role-checks.json` is the concrete configuration the owner ratified
(`decision:machine-role-gates-ratified-2026-10-04`); the live role nodes carry the
same gates (`agents/roles.json` snapshot). This file is a frozen test copy, not a
live roster snapshot. Actual runtime callers obtain fresh role props from the
role API instead.

Original cache: `D:/Temp/vinc-agent-lab-03c7780c/vinc-agent-lab`. The folder suffix
is not a verified Git revision. Hashes of the gate, synthetic tests, evaluation
sources and original roster are in `hand-labelled.json`.

Offline replay establishes deterministic agreement; it does not measure fresh
mini/strong model quality. No model call or package publication is part of replay.
