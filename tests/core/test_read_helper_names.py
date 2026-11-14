# -*- coding: utf-8 -*-
"""읽기 화면 도구·revstamp 메타·CLI 도구의 공개 이름과 사설 별칭이 같은 객체이고, 다른 모듈은 공개
이름으로 부르는가.

잠그는 것:
- `events` 의 사설 이름(`_run`·`_props`·`_title`·`_StoreFailed`·`_superseded_by`)은 `readkit` 의
  공개 이름과 같은 객체다. `revstamp._meta_get`·`_meta_set`·`_fmt` 와 `cli_tools._tool_request`·
  `_confirm` 도 같다. 사본이 생기면 한쪽만 고친 규칙이 조용히 갈라진다.
- 세 읽기 모듈의 `clamp_limit` 은 각자 경계만 다르고 검사는 `readkit.clamp_limit` 한 벌이다.
- `sync`·`runs`·`agent_writes`·`eval_cards`·`cli_account` 는 그 사설 이름을 부르지 않는다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import cli_tools, eval_cards, events, readkit, revstamp, runs  # noqa: E402

ALIASES = [
    (events, "_run", readkit, "run_plan"),
    (events, "_props", readkit, "props"),
    (events, "_title", readkit, "title"),
    (events, "_StoreFailed", readkit, "StoreFailed"),
    (events, "_superseded_by", readkit, "superseded_by"),
    (revstamp, "_meta_get", revstamp, "meta_get"),
    (revstamp, "_meta_set", revstamp, "meta_set"),
    (revstamp, "_fmt", revstamp, "fmt"),
    (cli_tools, "_tool_request", cli_tools, "tool_request"),
    (cli_tools, "_confirm", cli_tools, "confirm"),
]


@pytest.mark.parametrize("mod,private,owner,public", ALIASES,
                         ids=[f"{m.__name__}.{p}" for m, p, _, _ in ALIASES])
def test_사설_이름은_공개_이름과_같은_객체다(mod, private, owner, public):
    assert getattr(mod, private) is getattr(owner, public)


@pytest.mark.parametrize("mod", [events, runs, eval_cards], ids=lambda m: m.__name__)
def test_clamp_limit_은_한_벌의_검사를_쓴다(mod):
    lo, hi = mod.DEFAULT_LIMIT, mod.MAX_LIMIT
    for raw in (None, "", "x", 0, -3, 1, hi, hi + 1, str(hi + 5), 2.7):
        try:
            want = readkit.clamp_limit(raw, default=lo, maximum=hi)
        except Exception as exc:
            with pytest.raises(type(exc)):
                mod.clamp_limit(raw)
            continue
        assert mod.clamp_limit(raw) == want


def test_fmt_는_밀리초_ISO_다():
    assert revstamp.fmt(1_700_000_000_123) == "2023-11-14T22:13:20.123Z"


#: 사설 이름 → 그것을 부르면 안 되는 모듈.
_PRIVATE_USE = {
    r"revstamp\._(meta_get|meta_set|fmt)\b": ["api/sync.py"],
    r"events\._(run|props|title|StoreFailed|superseded_by)\b": [
        "api/runs.py", "api/agent_writes.py", "api/eval_cards.py"],
    r"cli_tools\._(tool_request|confirm)\b|import[^\n]*\b_(tool_request|confirm)\b": [
        "api/cli_account.py"],
}


@pytest.mark.parametrize("pattern", sorted(_PRIVATE_USE))
def test_다른_모듈은_공개_이름으로_부른다(pattern):
    hits = []
    for rel in _PRIVATE_USE[pattern]:
        for i, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(pattern, code) and "`" not in code:
                hits.append(f"{rel}:{i}: {line.strip()}")
    assert hits == []
