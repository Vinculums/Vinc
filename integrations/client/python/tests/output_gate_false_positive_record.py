"""거짓 걸림 표(output_gate_false_positive_cases.py)에 main 게이트의 결과를 적는다. --check 는 적힌 결과와 같은지만 본다.

    git show <커밋>:integrations/client/python/src/vinc_client/output_gate.py > main_gate.py
    python -X utf8 integrations/client/python/tests/output_gate_false_positive_record.py main_gate.py <커밋> [--check]

시험 환경에는 git 이력이 없으므로 main 의 결과는 이 스크립트가 데이터로 적고, 시험은 그 데이터를 읽는다.
글과 이름표와 기대(둘째~넷째 칸)는 바꾸지 않는다. main 의 결과 두 칸과 커밋만 다시 적는다.
"""
from __future__ import annotations

import pprint
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import output_gate_corpus_record as base  # noqa: E402

DATA = HERE / "output_gate_false_positive_cases.py"


def _header() -> str:
    return DATA.read_text(encoding="utf-8").split("MAIN_COMMIT = ", 1)[0]


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--check"]
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    gate, commit = base._load(args[0]), args[1]
    import output_gate_false_positive_cases as table
    rows = [(cid, kind, text, expect, *base.recorded(gate, text)) for cid, kind, text, expect, *_ in table.CASES]
    if "--check" in sys.argv:
        changed = [r for r, old in zip(rows, table.CASES) if tuple(r) != tuple(old)]
        for row in changed:
            print("differs:", row, file=sys.stderr)
        same = not changed and table.MAIN_COMMIT == commit
        print(f"{len(rows)} cases, {len(changed)} differ from the record, commit "
              f"{'matches' if table.MAIN_COMMIT == commit else 'differs'}")
        return 0 if same else 1
    DATA.write_text(_header() + f"MAIN_COMMIT = {commit!r}\n\nCASES = " + pprint.pformat(rows, width=120) + "\n",
                    encoding="utf-8")
    print(f"wrote {len(rows)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
