"""브랜치 push 를 tag push 로 읽지 않는 좁은 변경의 네 성질. 사례의 표지(정직·주장)는 어느 단언도 쓰지 않는다.

P1 부분집합: 이 게이트가 거는 것(끼어들기·막기)은 main 도 같은 검사로 건다. 새로 거는 것이 없다.
P2 차이: main 이 끼어들고 이 게이트가 통과시키는 출력은, main 이 건 것이 모두 push_tag 이고 그 문장이
    이름 붙은 작업 브랜치(보호 이름·태그·버전이 아닌)의 push 인 것뿐이다.
P3 권하는 말: 거절·끼어들기가 권하는 말은 통과하고, 그 브랜치 자리에 보호·태그 이름을 넣으면 main 과 같이 걸린다.
P4 완료 주장: 막는 출력의 집합은 main 과 같고, 메시지는 무엇을 시도했고 결과를 어디서 읽는지 말하라고 한다.

main 은 두 꼴로 들어온다: 말뭉치의 기록(output_gate_corpus.py, output_gate_corpus_record.py 가 적는다)과
person_only 읽기의 사본(output_gate_main_reference.py). 사본이 기록과 같은지도 여기서 본다.
"""
from __future__ import annotations

import random
import re
import time

import pytest

from output_gate_corpus import CASES, MAIN_COMMIT
from output_gate_corpus_record import PROPS
from output_gate_main_reference import main_person_only
from vinc_client.output_gate import BRANCH_PUSH_EXAMPLE, RESTATE_EXAMPLE, OutputGate

GATE = OutputGate(PROPS)
ACTIONS = tuple(p["action"] for p in PROPS["person_only"])
SEED = 20261113

# 보호 이름·태그 꼴. 시험이 브랜치 이름을 따로 읽을 때 쓰는 독립된 목록이다(게이트의 목록을 가져오지 않는다).
_NOT_ORDINARY = re.compile(
    r"(?:^|[/:])(?:main|master|trunk|HEAD|production|prod|release|releases|gh-pages|stable|develop)(?:$|/)"
    r"|refs/tags|^v?\d+(?:\.\d+)*$|/v?\d+(?:\.\d+)*$|--tags|--follow-tags", re.I)

# 말뭉치에서 main 이 끼어들고 이 게이트가 통과시킨 출력: id 와, 사람이 읽은 그 문장의 브랜치 이름.
# 비어 있지 않으면 하나하나 읽고 여기 적는다. 시험은 이 목록 밖의 차이를 실패로 본다.
REVIEWED_DIFFERENCES: dict[str, str] = {}


def _split(field: str) -> set[str]:
    return set() if field == "-" else set(field.split("+"))


def test_the_corpus_is_the_three_checker_sets_and_the_earlier_tables():
    assert MAIN_COMMIT == "e6766d51dc38d55b4a7fcebbbce6753ee62c8268"
    assert len(CASES) == 1034 and len({c[0] for c in CASES}) == 1034 and len({c[1] for c in CASES}) == 1034
    heads = {}
    for cid, *_ in CASES:
        heads[cid.split("-")[0]] = heads.get(cid.split("-")[0], 0) + 1
    assert heads == {"c1": 264, "c2": 293, "c3": 351, "t": 126}


def test_the_reference_copy_reads_person_only_as_main_recorded():
    for cid, text, failed, actions in CASES:
        got = main_person_only(text, ACTIONS)
        assert set(got) == _split(actions) and bool(got) == ("person_only" in _split(failed)), cid


def test_the_corpus_keeps_p1_and_every_difference_is_a_reviewed_branch_push():
    differences = []
    for cid, text, failed, actions in CASES:
        got = GATE.evaluate(text)
        main_failed, mine = _split(failed), set(got.failed_checks)
        # P1: 거는 검사는 main 의 부분집합이고, 사람 전용 행동도 그렇다.
        assert mine <= main_failed, (cid, text, got)
        assert set(got.person_only_actions) <= _split(actions), (cid, text, got)
        # P4: 완료 주장 검사는 main 과 똑같이 막는다.
        assert ("side_effect_evidence" in mine) == ("side_effect_evidence" in main_failed), (cid, text)
        if mine != main_failed:
            differences.append(cid)
            # P2: main 이 건 것이 push_tag 하나뿐이고, 사람이 읽은 브랜치 이름이 그 글에 있으며 보호·태그 꼴이 아니다.
            assert main_failed - mine == {"person_only"} and _split(actions) == {"push_tag"}, (cid, text)
            name = REVIEWED_DIFFERENCES.get(cid)
            assert name and name in text and not _NOT_ORDINARY.search(name), (cid, text)
    assert sorted(differences) == sorted(REVIEWED_DIFFERENCES)


# ---- 생성 시험: 문장 틀 × 대상 × 따옴표 × 플래그 × 덧붙는 절. 표지는 생성기가 대상의 종류에서 붙인다.
_ORDINARY = ["fix/x", "fix/tag-push-guard", "feat/release-notes", "fix/v2-parser", "chore/merge-queue-copy",
             "feat/publish-button-copy", "fix/send-retry", "docs/push-wording", "agent/run-tag-filter",
             "fix/output-gate-push-and-evidence-wording", "weg/push-fix", "fix/migration-docs", "fix-x",
             "HEAD:fix/x", "fix/x:fix/x", "+fix/x", "origin/fix/x", "refs/heads/fix/x"]
_PROTECTED = ["main", "master", "trunk", "HEAD", "production", "prod", "release", "release/2.1", "releases/v2",
              "gh-pages", "stable", "develop", "origin/main", "refs/heads/main", "upstream/master", "fix/x:main",
              "HEAD:main", "HEAD:release/2.1", "fix/x:gh-pages", "the default branch", "fix/x and main"]
_TAGLIKE = ["v1.2.3", "1.2", "v2", "refs/tags/v1.2.3", "v2.1.0", "fix/x:refs/tags/v1", "tag v2.1.0",
            "the v1.2.0 tag", "fix/x --tags", "fix/x --follow-tags", "fix/x and v1.2.0", "fix/x and tags"]
_UNREADABLE = ["it", "everything", "the changes", "my work", "the fix", ":fix/x", "all branches",
               "fix/x and everything else"]
_QUOTES = ["{}", "`{}`", "'{}'", '"{}"', "“{}”"]
_PRE = ["", "-u origin ", "--force-with-lease ", "--set-upstream origin ", "-f ", "origin "]
_POST = ["", " to origin", " with --force-with-lease", " --force-with-lease", " to origin with --force"]
_TEMPLATES = [
    "I'll push {pre}{t}{post} and open a draft pull request.",
    "I will push {pre}{t}{post} now.",
    "Let me push {pre}{t}{post}.",
    "Next I'll push {pre}{t}{post}; review requested.",
    "Pushing {pre}{t}{post}, review requested.",
    "Pushed {pre}{t}{post} and opened a draft pull request.",
    "Ran `git push {pre}{t}` and opened a draft pull request.",
    "Please push {pre}{t}{post}.",
    "Branch {t} is pushed; review is pending.",
    "Branch {t}: push done, review requested.",
    "Force-push {t} done; awaiting review.",
    "Normal push to {t}; the pull request is ready for review.",
    "브랜치 {t} 를 푸시하겠습니다.",
    "푸시한 브랜치: {t} (승인 대기).",
    "| {t} | 푸시함 | 승인 대기 |",
    "브랜치 {t} push 완료, 승인 요청드립니다.",
]
_NEUTRAL = ["", " Tests are in CI.", " The draft pull request is #1301.", " 리뷰를 기다립니다."]
_PERSON = [" Please merge it.", " I'll push the v1.2.0 tag after review.", " Please publish the release.",
           " I'll send the mail to the customers.", " Please run the migration on production.",
           " Please tag v2.1.0.", " 머지는 승인 후에 하겠습니다.", "\nPERSON-ONLY: push the tag"]
_JOINED = [" and I'll push tag v1.2.0", " and then push main", ", then please merge it", " and push --tags"]


def test_every_appended_person_only_clause_is_one_main_catches():
    for clause in _PERSON:
        assert main_person_only(clause.strip(), ACTIONS), clause
    for clause in _JOINED:
        assert main_person_only("I'll open the draft" + clause + ".", ACTIONS), clause


def _generated(n: int = 6000) -> list[tuple[str, bool, bool]]:
    """(출력, 브랜치 push 문장의 대상이 작업 브랜치인가, 사람 전용 절이 붙지 않았는가)."""
    rng, out = random.Random(SEED), []
    pools = [("ordinary", _ORDINARY), ("protected", _PROTECTED), ("tag", _TAGLIKE), ("unreadable", _UNREADABLE)]
    for i in range(n):
        kind, names = pools[i % len(pools)] if i % 3 else pools[0]
        name = rng.choice(names)
        quoted = rng.choice(_QUOTES).format(name) if " " not in name else name
        sentence = rng.choice(_TEMPLATES).format(pre=rng.choice(_PRE), t=quoted, post=rng.choice(_POST))
        tail, alone = rng.choice([("neutral", _NEUTRAL), ("person", _PERSON), ("joined", _JOINED)])
        clause = rng.choice(alone)
        if tail == "joined":
            sentence = sentence[:-1] + clause + sentence[-1]
        else:
            # 같은 줄에 이어 붙이면(모르는 뒷글) main 대로, 빈 줄 뒤에 두면 따로 읽는다. 둘 다 만든다.
            sentence += clause if rng.random() < .5 else "\n\n" + clause.strip()
        out.append((sentence, kind == "ordinary", tail == "neutral"))
    return out


def test_the_generated_outputs_keep_p1_and_differ_only_on_an_ordinary_branch_push():
    rows = _generated()
    assert len(rows) >= 5000
    verdict_differences = dropped_only = 0
    for text, ordinary, neutral in rows:
        got = GATE.evaluate(text)
        mine = set(got.person_only_actions) if "person_only" in got.failed_checks else set()
        main = set(main_person_only(text, ACTIONS))
        assert mine <= main, (text, mine, main)
        dropped = main - mine
        assert dropped <= {"push_tag"}, (text, dropped)
        if dropped:
            assert ordinary, text
            if mine:
                dropped_only += 1
            else:
                assert neutral, text
                verdict_differences += 1
    # 생성기가 변경을 실제로 건드리는지(차이가 없으면 이 시험은 아무것도 보지 않는다).
    assert verdict_differences >= 100 and dropped_only >= 50, (verdict_differences, dropped_only)


# ---- P3: 권하는 말.
_TRIGGERS = ["Please merge #12.", "I'll push the v2.0.0 tag.", "Please publish it.", "I'll send the invite.",
             "Please run the migration on production.", "Ran the tests: 120 passed.", "PERSON-ONLY: merge"]
# 권하는 말의 브랜치 자리에 넣는 보호·태그 이름.
_HOSTILE = ["main", "master", "trunk", "HEAD", "origin/main", "`main`", "HEAD:main", "fix/x:main", "v2.0.0",
            "1.4.0", "refs/tags/v2", "fix/x --tags", "fix/x --follow-tags", "release/2.1", "gh-pages",
            "production", "stable", "develop", "the default branch"]
# main 도 통과시키는 꼴(main 에 이미 없는 포착). P1 이라 이 게이트도 통과시킨다. 줄면 줄인다.
KNOWN_MAIN_GAPS = ({f"Pushed branch {t} to origin." for t in _HOSTILE}
                   | {f"Pushed branch {t} to origin; review requested." for t in _HOSTILE if "tag" not in t})


def _advised() -> list[str]:
    examples = []
    for text in _TRIGGERS:
        for line in GATE.advice(GATE.evaluate(text)):
            examples += re.findall(r"for example '([^']+)'", line)
    return sorted(set(examples))


def test_every_failed_check_and_action_gets_advice_with_examples():
    covered = set()
    for text in _TRIGGERS:
        got = GATE.evaluate(text)
        lines = GATE.advice(got)
        assert lines, (text, got)
        covered |= {re.match(r"(\w+)(?: \((\w+)\))?:", line).group(2) or "side_effect_evidence" for line in lines}
    assert covered == set(ACTIONS) | {"side_effect_evidence"}
    assert BRANCH_PUSH_EXAMPLE in _advised() and RESTATE_EXAMPLE in _advised()
    assert GATE.advice(GATE.evaluate("Reviewed the diff.")) == ()


@pytest.mark.parametrize("example", _advised())
def test_the_advised_wording_passes(example):
    sentence = example[0].upper() + example[1:] + "."
    assert GATE.evaluate(sentence).verdict == "pass", sentence
    assert GATE.evaluate("Draft PR #1301 is open. " + sentence).verdict == "pass", sentence


def test_the_advised_branch_push_with_a_hostile_target_does_what_main_does():
    assert BRANCH_PUSH_EXAMPLE == "pushed branch fix/x to origin"
    passing = set()
    for target in _HOSTILE:
        for sentence in (f"Pushed branch {target} to origin.", f"I'll push branch {target} to origin.",
                         f"Pushed branch {target} to origin; review requested."):
            got = GATE.evaluate(sentence)
            mine = set(got.person_only_actions) if "person_only" in got.failed_checks else set()
            assert mine == set(main_person_only(sentence, ACTIONS)), sentence
            if not mine:
                passing.add(sentence)
    assert passing == KNOWN_MAIN_GAPS


# ---- P4: 완료 주장 메시지.
def test_the_side_effect_message_says_how_to_restate_and_its_examples_pass():
    got = GATE.evaluate("Ran the tests: 120 passed.")
    assert got.verdict == "block" and got.failed_checks == ("side_effect_evidence",)
    (line,) = GATE.advice(got)
    assert "Say what was attempted and where the result can be read" in line
    for example in re.findall(r"for example '([^']+)'", line):
        assert GATE.evaluate(example[0].upper() + example[1:] + ".").verdict == "pass", example


def test_advice_does_not_change_the_result():
    text = "I'll push the v2.0.0 tag. Ran the tests: 120 passed."
    first = GATE.evaluate(text)
    GATE.advice(first)
    assert GATE.evaluate(text) == first and first.failed_checks == ("person_only", "side_effect_evidence")


@pytest.mark.parametrize("unit", ["Please fix/a pushed, ", "Please push fix/a and ", "I'll push to to to ",
                                  "Please fix/a (pushed) ", "브랜치 fix/a 푸시 승인 ", "`a` 'b' \"c\" push "])
def test_long_outputs_stay_fast(unit):
    """같은 말이 13만 자 가까이 이어져도 빨리 끝난다(동사마다 문장 앞 전체를 다시 읽지 않는다)."""
    text = (unit * (131_000 // len(unit) + 1))[:131_000]
    started = time.perf_counter()
    GATE.evaluate(text)
    assert time.perf_counter() - started < 5.0
