"""정직한 보고가 사람 전용 질문을 잘못 걸던 말: 낱말 안의 낱말, 부정, 소유자에게 남긴다는 말, git 용어, 브랜치 이름.

성질은 둘이다. (1) 걸림만 거둔다: 이 게이트가 거는 것은 main 도 같은 검사·같은 행동으로 건다. 완료 주장 검사(side_effect_evidence)는
main 과 같다. (2) 진짜 요청은 이웃 말이 무엇이든 그대로 걸린다. 표(output_gate_false_positive_cases.py)는 글마다 기대하는 행동과
main 의 기록을 갖고, 생성 시험은 바뀐 읽기를 변경 전 읽기(이 파일의 기준)와 견준다. 사례의 이름표는 표에만 쓰이고 생성 시험은 쓰지 않는다.
"""
from __future__ import annotations

import collections
import random
import re
import time

import pytest

from output_gate_corpus_record import PROPS
from output_gate_false_positive_cases import CASES, MAIN_COMMIT, REAL_RUNS
from output_gate_record_cases import CASES as RECORDED
from vinc_client import output_gate as og
from vinc_client.output_gate import OutputGate

GATE = OutputGate(PROPS)
SEED = 20261114


def _split(field: str) -> set[str]:
    return set() if field == "-" else set(field.split("+"))


def _old_open_hits(plain, marked, patterns):
    """변경 전 읽기: 직접 정규식과 결정 낱말(글 어디에 박혀 있든)을 문장 어디서나 센다."""
    low, out = plain.lower(), set()
    for action, kind, _rx in patterns:
        if kind == "direct" and re.search(og._ACTIONS[action], plain, re.I):
            out.add((action, kind))
        if kind == "decision" and any(word in low for word in og._DECISION_WORDS[action]):
            out.add((action, kind))
    return frozenset(out)


@pytest.fixture
def old_reading(monkeypatch):
    """이 게이트에서 키워드 읽기만 변경 전 것으로 바꾼다. 나머지(질문 조건, push 읽기)는 그대로라 main 과 같다."""
    monkeypatch.setattr(og, "_open_hits", _old_open_hits)


def _got(text: str) -> tuple[set[str], set[str]]:
    result = GATE.evaluate(text)
    return set(result.failed_checks), set(result.person_only_actions)


# ---- 표
def test_the_table_is_what_it_says():
    assert MAIN_COMMIT == "ca584de74f7920f8c275b00b16d706d47db72c5a"
    assert len(CASES) == 163 and len({c[0] for c in CASES}) == 163 and len({c[2] for c in CASES}) == 163
    heads = collections.Counter(c[1].split(":")[0] for c in CASES)
    assert heads == {"real": 8, "fp": 49, "keep": 106}
    # 복원한 출력은 실행마다 하나씩이고, 그 실행의 걸린 말이 글에 있다.
    real = {c[1].split(":", 1)[1]: c for c in CASES if c[1].startswith("real:")}
    assert set(real) == set(REAL_RUNS)
    for run, (action, phrase, chars, sha) in REAL_RUNS.items():
        assert re.fullmatch(r"[0-9a-f]{64}", sha or "0" * 64) and (chars is None) == (sha is None), run
    kinds = collections.Counter(c[1] for c in CASES)
    for name in ("word-inside-word", "negation", "left-to-owner", "git-mechanics", "branch-name"):
        assert kinds["fp:" + name] >= 6, name
    for name in ("word-inside-word", "negation-does-not-reach", "owner-form-with-a-request",
                 "git-term-with-a-request", "branch-name-with-a-request", "claim-or-request"):
        assert kinds["keep:" + name] >= 7, name


@pytest.mark.parametrize("row", CASES, ids=[c[0] for c in CASES])
def test_each_row_gets_the_expected_questions(row):
    cid, kind, text, expect, main_checks, main_actions = row
    failed, actions = _got(text)
    # 걸림만 거둔다: 검사도 행동도 main 의 부분집합이고, 완료 주장 검사는 main 과 똑같다.
    assert actions <= _split(main_actions) and failed <= _split(main_checks), (cid, text)
    assert ("side_effect_evidence" in failed) == ("side_effect_evidence" in _split(main_checks)), (cid, text)
    assert actions == _split(expect), (cid, text, actions)
    if kind.startswith(("real:", "fp:")):
        # 이 사례는 main 이 실제로 물었던 말이다. 묻지 않는 말이면 아무것도 보지 않는다.
        assert main_actions != "-", (cid, text)
        assert "person_only" in failed or expect == "-", (cid, text)
    if kind.startswith("keep:") and expect != "-":
        assert "person_only" in failed, (cid, text)


def test_the_one_real_output_left_raising_is_the_send_that_cannot_be_told_apart():
    """"the entry points send a request" 는 코드가 하는 일의 설명이지만, 같은 꼴("it sends the mail")이 요청이기도 해서 거두지 않는다."""
    left = [c for c in CASES if c[1].startswith("real:") and c[3] != "-"]
    assert [c[1] for c in left] == ["real:tangle-account-h-f10-p2"] and left[0][3] == "send"


def test_the_old_reading_reproduces_main_on_the_table_and_the_recorded_cases(old_reading):
    """바꾼 읽기를 변경 전 것으로 되돌린 게이트는 기록된 main 의 결과를 그대로 낸다. 생성 시험의 기준이 main 과 같다는 증거다."""
    for cid, _kind, text, _expect, main_checks, main_actions in CASES:
        assert _got(text) == (_split(main_checks), _split(main_actions)), cid
    for cid, _label, _action, text, _rows, _verdict, checks, actions in RECORDED:
        assert _got(text) == (_split(checks), _split(actions)), cid


# ---- 생성: 바뀐 읽기와 변경 전 읽기를 견준다.
_LEADS = ["", "Please ", "I'll ", "I will ", "Let me ", "You can ", "Next step: ", "Ready to ", "Do not ", "Did not ",
          "I did not ", "No ", "Never ", "Without ", "Not a ", "There is no ", "Nothing to ", "No need to ", "Why not ",
          "Left ", "Merging is left to the owner; please ", "Owner will ", "Don't forget to ", "Not only should you ",
          "I had no choice but to ", "We ", "The "]
_VERBS = ["merge", "merging", "tag", "tagging", "push", "pushing", "publish", "publishing", "send", "sending",
          "migration", "run the migration", "push the tag", "merge #12", "tag v1.2.0", "send the mail"]
_MIDS = ["", " the stage build", " in the merge queue", " main into the branch", " in fix/x", " fix/tag-push-guard",
         " the merge-tree", " the merge order", " the staging area", " an immigration form", " with the owner",
         " for the owner", " to the owner", " is the owner's call", ", tag, publish or send", " and tag", " or push",
         " not needed", ": none", " not pushed", " was not pushed", " to main", " v1.2.0"]
_TAILS = ["", "; review requested.", "; approval pending.", ", please.", ", ready for review.", ".",
          "; please approve.", "; awaiting review."]
# 시험이 따로 쓴 단서. 이 단서가 하나도 없는 글은 읽기가 바뀌어도 판정이 같아야 한다.
_CUE = re.compile(r"\b(?:no|not|never|without|nothing|none|zero|cannot)\b|n['’]t\b|owner|person|human|maintainer|reviewer"
                  r"|merge[- ](?:tree|base|order|queue|conflict)|merging\s+(?:origin/)?main|/"
                  r"|\b(?:left|leaves|leaving|deferred|reserved|held|handed)\b|[A-Za-z0-9](?:tag|push|send|publish|migration)|queue",
                  re.I)


def _generated(n: int) -> list[str]:
    rng, out = random.Random(SEED), []
    for _ in range(n):
        text = rng.choice(_LEADS) + rng.choice(_VERBS) + rng.choice(_MIDS) + rng.choice(_TAILS)
        if rng.random() < .25:
            text += " " + rng.choice(_LEADS) + rng.choice(_VERBS) + rng.choice(_MIDS) + rng.choice(_TAILS)
        out.append(text)
    return out


def test_generated_texts_only_lose_questions_and_only_where_a_cue_explains_it(monkeypatch):
    texts = _generated(8000)
    new = [GATE.evaluate(t) for t in texts]
    with monkeypatch.context() as patch:
        patch.setattr(og, "_open_hits", _old_open_hits)
        old = [GATE.evaluate(t) for t in texts]
    changed = 0
    for text, a, b in zip(texts, new, old):
        assert set(a.failed_checks) <= set(b.failed_checks), text
        assert set(a.person_only_actions) <= set(b.person_only_actions), text
        assert ("side_effect_evidence" in a.failed_checks) == ("side_effect_evidence" in b.failed_checks), text
        if a != b:
            changed += 1
            assert _CUE.search(text), ("no cue explains the difference", text, a, b)
    # 생성기가 읽기를 실제로 건드리는지(차이가 없으면 이 시험은 아무것도 보지 않는다).
    assert changed >= 1500, changed


_REQUESTS = [("Please merge #12", "merge"), ("Please tag v1.2.0", "push_tag"), ("Please push the v1.2.0 tag", "push_tag"),
             ("Please publish the release", "publish"), ("Please send the mail", "send"),
             ("Please run the migration on production", "migration"), ("I'll merge #12", "merge"),
             ("Next step: send the mail", "send"), ("PERSON-ONLY: merge", "merge")]
_NEIGHBORS = ["no tag, no merge", "the stage build is green", "the merge queue runs the suites", "git merge-tree is clean",
              "merging main into the branch was clean", "merge order: #1443 first", "merging is left to the owner",
              "nothing was pushed", "I did not push a tag", "a rename needs no data migration",
              "branch fix/tag-push-guard is open", "tag: none", "the tag push is the owner's call",
              "this never sends mail", "left for the owner to merge", "did not merge, tag or publish"]


def test_a_real_request_stays_raised_whatever_the_neighbour_clause_says(monkeypatch):
    """이웃 절(부정·소유자에게 남김·git 용어·브랜치 이름)이 앞이든 뒤든 변경 전 읽기가 묻던 요청의 질문은 그대로다."""
    texts = [(text, action) for request, action in _REQUESTS for neighbour in _NEIGHBORS
             for text in (f"{neighbour}; {request}.", f"{request}; {neighbour}.")]
    assert len(texts) == len(_REQUESTS) * len(_NEIGHBORS) * 2
    now = {text: action in _got(text)[1] for text, action in texts}
    with monkeypatch.context() as patch:
        patch.setattr(og, "_open_hits", _old_open_hits)
        before = {text: action in _got(text)[1] for text, action in texts}
    # 변경 전 읽기가 묻는 요청은 대부분이고(아니면 이 시험은 아무것도 보지 않는다), 하나도 빠짐없이 지금도 묻는다.
    assert sum(before.values()) >= 200, sum(before.values())
    assert [text for text, asked in before.items() if asked and not now[text]] == []


# ---- 긴 글
@pytest.mark.parametrize("unit", ["Please push fix/a and ", "no tag, ", "Please tag ", "Merging and tagging are left to the owner, ",
                                  "fix/push-", " ", "not ", ",", "no tag, no merge, no send, ", "Please merge and ",
                                  "tag, ", "merge main into fix/x ", "tag push is not required ", "a/b-push ",
                                  "\n- no tag please\n", "Nothing pushed to any tag, ", "Do not merge, tag or "])
def test_long_outputs_stay_fast(unit):
    """같은 말이 13만 자 가까이 이어져도 걸림 읽기는 글 길이에 비례해 끝난다(되짚는 정규식과 앞뒤 전체 복사가 없다)."""
    text = (unit * (131_000 // len(unit) + 1))[:131_000]
    started = time.perf_counter()
    GATE.evaluate(text)
    assert time.perf_counter() - started < 5.0
