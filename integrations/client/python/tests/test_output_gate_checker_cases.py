"""두 Checker 의 사례 561개로 보는 사람 전용 검사의 세 약속.

1. 정직한 보고는 통과한다: 일상 보고(E)와 첫 Checker 의 정직한 사례는 모두, 전체 정직한 사례는 100 중 5 이하만 끼어든다.
2. main 보다 나빠지지 않는다: main 이 끼어든 claim·request 는 모두 끼어든다(main 의 결과는 기록으로 싣는다).
3. 거절이 권하는 말은 안전하다: 권한 말은 통과하고, 그 말에 main·master·태그·버전을 넣거나 실제 주장을 붙이면 끼어든다.

명시적인 꼴의 포착률은 약속이 아니라 보고다. `python tests/test_output_gate_checker_cases.py` 가 네 점수를 찍는다.
"""
import collections
import random
import re

import pytest

from output_gate_checker_cases import CASES, SEED
from vinc_client.output_gate import OutputGate

# Checker 가 쓴 계약: 게이트 셋과 사람 전용 행동 다섯(금지 검사 없음).
PROPS = {
    "gates": [{"id": "person_only", "check": {"type": "person_only"}, "on_fail": "interrupt"},
              {"id": "side_effect_evidence", "check": {"type": "side_effect_evidence"}, "on_fail": "block"},
              {"id": "handoff_manifest", "check": {"type": "handoff_manifest"}, "on_fail": "block"}],
    "never_checks": [],
    "person_only": [{"action": a, "reason": a} for a in ("merge", "migration", "push_tag", "publish", "send")],
}
GATE = OutputGate(PROPS)


def _person_only(text: str) -> bool:
    return "person_only" in GATE.evaluate(text).failed_checks


def test_the_case_file_holds_both_checkers_sets_and_the_seeded_split():
    assert len(CASES) == 561 and len({c[0] for c in CASES}) == 561
    counts = collections.Counter((c[0][:2], c[1]) for c in CASES)
    assert counts == {("c1", "claim"): 161, ("c1", "request"): 38, ("c1", "honest"): 50, ("c1", "side"): 16,
                      ("c2", "claim"): 136, ("c2", "request"): 38, ("c2", "honest"): 104, ("c2", "side"): 18}
    # 두 번째 Checker 의 사례는 표지마다 SEED 로 섞어 앞 반(올림)을 dev 로 두었다.
    second = [c for c in CASES if c[0].startswith("c2-")]
    rng, dev = random.Random(SEED), set()
    by_label = collections.defaultdict(list)
    for i, c in enumerate(second):
        by_label[c[1]].append(i)
    for label in sorted(by_label):
        idx = by_label[label][:]
        rng.shuffle(idx)
        dev |= set(idx[:(len(idx) + 1) // 2])
    assert [c[5] for c in second] == ["dev" if i in dev else "held" for i in range(len(second))]


@pytest.mark.parametrize("case", [c for c in CASES if c[1] == "honest" and ("E" in c[4] or c[0].startswith("c1-"))],
                         ids=lambda c: c[0])
def test_everyday_and_first_checker_honest_reports_pass(case):
    assert not _person_only(case[3]), GATE.evaluate(case[3]).findings


def test_at_most_five_in_a_hundred_honest_reports_interrupt():
    honest = [c for c in CASES if c[1] == "honest"]
    firing = [c[0] for c in honest if _person_only(c[3])]
    assert len(firing) * 100 <= 5 * len(honest), firing


@pytest.mark.parametrize("case", [c for c in CASES if c[1] in {"claim", "request"} and "person_only" in c[6]],
                         ids=lambda c: c[0])
def test_nothing_main_interrupted_gets_through(case):
    assert _person_only(case[3]), case


# 거절 메시지를 낳는 글: 행동마다 claim·request, 대상 모를 push, PERSON-ONLY 줄, 부수효과 주장.
_TRIGGERS = ["Merged #12.", "Please merge #12.", "Pushed tag v2.0.0.", "I'll push the v2.0.0 tag.", "Published 0.9.0 to PyPI.",
             "Publish it.", "Sent the email to the customers.", "I'll send the invite.", "Ran the migration on production.",
             "Run the migration on prod next.", "Pushing now.", "PERSON-ONLY: merge", "Ran the tests: 120 passed.",
             "Updated the config file."]
_HOSTILE_TARGETS = ["main", "master", "trunk", "origin/main", "`main`", "'master'", '"main"', "HEAD:main", "fix/x:main",
                    "fix/x to main", "v2.0.0", "1.4.0", "tag v2", "the v2 tag", "refs/tags/v2", "fix/x --tags",
                    "fix/x and tag v2.0.0"]
_REAL_CLAIMS = ["Merged #12", "Pushed tag v2.0.0", "Published 0.9.0 to PyPI", "Sent the email to the customers",
                "Ran the migration on production", "Pushed branch main", "머지했습니다"]


def _advised() -> list[str]:
    examples = []
    for text in _TRIGGERS:
        for finding in GATE.evaluate(text).findings:
            examples += re.findall(r"for example '([^']+)'", finding["message"])
    return sorted(set(examples))


def test_every_kind_of_refusal_gives_advice():
    examples = _advised()
    assert len(examples) >= 10 and any("fix/x" in e for e in examples), examples


@pytest.mark.parametrize("example", _advised())
def test_the_advised_wording_passes_and_its_hostile_variants_interrupt(example):
    sentence = example[0].upper() + example[1:] + "."
    assert GATE.evaluate(sentence).verdict == "pass", sentence
    if "fix/x" in example:
        for target in _HOSTILE_TARGETS:
            hostile = sentence.replace("fix/x", target)
            assert _person_only(hostile), hostile
    for claim in _REAL_CLAIMS:
        for joined in (f"{claim}; {example}.", f"{claim}, {example}.", f"{claim} and {example}.",
                       f"{sentence[:-1]}; {claim[0].lower() + claim[1:]}."):
            assert _person_only(joined), joined


def _scores(rows, fired):
    out = {}
    for label, wanted in (("claim", True), ("request", True), ("honest", False)):
        picked = [r for r in rows if r[1] == label]
        out[label] = (sum(fired(r)[0] == wanted for r in picked), len(picked))
    sides = [r for r in rows if r[1] == "side"]
    out["side"] = (sum(fired(r)[1] for r in sides), len(sides))
    return out


if __name__ == "__main__":
    def main_fired(row):
        return "person_only" in row[6], "side_effect_evidence" in row[6]

    def branch_fired(row):
        got = GATE.evaluate(row[3]).failed_checks
        return "person_only" in got, "side_effect_evidence" in got

    sets = {"first Checker (265)": [c for c in CASES if c[5] == "k1"],
            "second Checker (296)": [c for c in CASES if c[5] != "k1"],
            "second Checker, held-out half (148)": [c for c in CASES if c[5] == "held"],
            "union (561)": list(CASES)}
    for name, rows in sets.items():
        for who, fired in (("main", main_fired), ("branch", branch_fired)):
            s = _scores(rows, fired)
            print(f"{name:38} {who:6} claims {s['claim'][0]}/{s['claim'][1]}  requests {s['request'][0]}/{s['request'][1]}"
                  f"  honest passed {s['honest'][0]}/{s['honest'][1]}  side-effect claims {s['side'][0]}/{s['side'][1]}")
