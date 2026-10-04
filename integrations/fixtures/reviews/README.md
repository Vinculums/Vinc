# Saved review failure

`p1-invalid.json` contains only the strong verifier's raw P1 output from the
original cached lab file `exp07_verifier/raw_strong/P1.json`. The recorded SHA-256
identifies that complete source file; its enclosing cache directory name is not
a verified Git revision. The invalid escaped quote outside a JSON string is
preserved without repair.

Tests replay this output twice and require `UNPARSED`, with no empty findings
result and no third request. Recovery tests use a recording response containing
two findings to exercise preservation, not to claim a new paid-model result.
