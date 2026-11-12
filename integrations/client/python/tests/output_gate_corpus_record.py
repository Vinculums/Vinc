"""말뭉치(output_gate_corpus.py)에 main 게이트의 결과를 적는다. --check 는 적힌 결과와 같은지만 본다.

    git show <커밋>:integrations/client/python/src/vinc_client/output_gate.py > main_gate.py
    python -X utf8 integrations/client/python/tests/output_gate_corpus_record.py main_gate.py <커밋> [--check]

시험 환경에는 git 이력이 없으므로 main 의 결과는 이 스크립트가 데이터로 적고, 시험은 그 데이터를 읽는다.
글(CASES 의 둘째 칸)은 바꾸지 않는다. 결과 두 칸과 커밋만 다시 적는다.
"""
from __future__ import annotations

import importlib.util
import pprint
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "output_gate_corpus.py"

# 말뭉치 시험과 같은 계약: 게이트 셋, 사람 전용 행동 다섯, 금지 검사 없음.
PROPS = {
    "gates": [{"id": "person_only", "check": {"type": "person_only"}, "on_fail": "interrupt"},
              {"id": "side_effect_evidence", "check": {"type": "side_effect_evidence"}, "on_fail": "block"},
              {"id": "handoff_manifest", "check": {"type": "handoff_manifest"}, "on_fail": "block"}],
    "never_checks": [],
    "person_only": [{"action": a, "reason": a} for a in ("merge", "migration", "push_tag", "publish", "send")],
}

HEADER = '''"""출력 게이트 말뭉치: 세 Checker 의 사례와 이전 표의 글, 그리고 main 게이트가 낸 결과. 생성 파일이다.

한 줄은 (id, 글, main 의 실패 검사, main 의 사람 전용 행동)이다. 검사·행동은 + 로 잇고, 없으면 - 다.
id 머리: c1 첫 Checker, c2 둘째 Checker, c3 셋째 Checker, t 이전 표. 글이 같은 사례는 한 번만 싣는다.
결과는 MAIN_COMMIT 의 게이트를 output_gate_corpus_record.PROPS 계약으로 돌린 것이다(그 스크립트가 다시 적는다).
"""
'''


def _load(path: str):
    spec = importlib.util.spec_from_file_location("_main_output_gate", path)
    module = importlib.util.module_from_spec(spec)
    # dataclass 가 모듈을 sys.modules 에서 찾으므로 실행 전에 올려 둔다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def recorded(gate_module, text: str) -> tuple[str, str]:
    got = gate_module.OutputGate(PROPS).evaluate(text)
    return "+".join(got.failed_checks) or "-", "+".join(got.person_only_actions) or "-"


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--check"]
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    gate, commit = _load(args[0]), args[1]
    sys.path.insert(0, str(HERE))
    import output_gate_corpus as corpus
    rows = [(cid, text, *recorded(gate, text)) for cid, text, *_ in corpus.CASES]
    if "--check" in sys.argv:
        changed = [r for r, old in zip(rows, corpus.CASES) if tuple(r) != tuple(old)]
        for row in changed:
            print("differs:", row, file=sys.stderr)
        same = not changed and corpus.MAIN_COMMIT == commit
        print(f"{len(rows)} cases, {len(changed)} differ from the record, commit "
              f"{'matches' if corpus.MAIN_COMMIT == commit else 'differs'}")
        return 0 if same else 1
    body = HEADER + f"MAIN_COMMIT = {commit!r}\n\nCASES = " + pprint.pformat(rows, width=120) + "\n"
    DATA.write_text(body, encoding="utf-8")
    print(f"wrote {len(rows)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
