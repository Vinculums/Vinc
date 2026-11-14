# -*- coding: utf-8 -*-
"""`docstore.put_doc`(`_put_doc_once`)의 현재 동작을 표로 고정한다(특성 테스트).

입력은 아래 `CASES` 의 한 줄이고, 결과는 공개 입구 `docstore.put_doc` 을 실 엔진 스토어에 한 번
돌려 얻는다. 기록하는 것은 셋이다: 응답(경고를 뺀 나머지), 경고 목록(순서 그대로), 호출 뒤
스토어의 문서 메타·청크·청크의 MENTIONS·`concept:doc.*` 미러·문서 삭제 표식 전부. 기대값은
`tests/fixtures/put_doc_characterization.json` 에 있다. `plain` 은 PK 네임스페이싱을 끈 모드,
`namespaced` 는 켠 모드에서 **결과가 다른 케이스만** 담는다.

이 표가 바뀌면 그것은 문서 쓰기 경로의 동작 변경이다. `_put_doc_once` 를 나누는 리팩터는 표를
바꾸지 않아야 하고, 의도한 변경이면 바뀐 케이스를 PR 본문에 적는다. 다시 기록하는 법(끈 모드를
먼저):

    VINC_PK_NAMESPACED=0 python tests/core/test_put_doc_characterization.py --record
    VINC_PK_NAMESPACED=1 python tests/core/test_put_doc_characterization.py --record

정규화: HLC 시각은 `<hlc>`, 오늘 날짜는 `<today>`, 120자를 넘는 문자열은 `<str N>` 로 적는다.
"""
from __future__ import annotations

import copy
import functools
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api import docstore  # noqa: E402
from api import fragment as frag  # noqa: E402
from api import graphmd  # noqa: E402
from store import pkns  # noqa: E402
from store import querybase as _qb  # noqa: E402

GOLDEN = _ROOT / "tests" / "fixtures" / "put_doc_characterization.json"

A = "user:alice"
B = "user:bob"

BODY = "# Graph Store\n\nGraph Store keeps nodes. The Vector Index ranks chunks.\n"
BODY2 = BODY + "\n## Write Path\n\nThe Write Path commits once and Graph Store answers.\n"
LONG = "".join(f"## Part {i}\n\nGraph Store paragraph {i} with enough words to fill a chunk.\n\n"
               for i in range(6))
FM_ID = "---\ndoc_id: vinc.other.name\n---\n# Declared\n\nBody with a declared id.\n"
FM_DOMAIN = "---\ndomain: vinc/test\nrelated: [doc:x]\n---\n# Front\n\nFront matter body.\n"


# ── 입력 표 ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Case:
    """케이스 하나. ``seed`` 는 앞서 넣을 `put_doc` 인자들이고 그 결과는 기록하지 않는다(실패하면
    멈춘다). ``nodes`` 는 앞서 넣을 개념(언급 링크 재료)이다."""
    name: str
    call: dict
    seed: tuple = ()
    nodes: tuple = ()
    seed_may_fail: bool = False


def P(**kw):
    return {"owner": A, "text": BODY, "doc_id": "doc-a", **kw}


GRAPH = ({"id": "concept:graph-store", "type": "concept", "title": "Graph Store"},
         {"id": "concept:vector-index", "type": "concept", "title": "Vector Index"},
         {"id": "concept:write-path", "type": "concept", "title": "Write Path"})

CASES: tuple[Case, ...] = (
    # 입구 거절
    Case("거절: owner 없음", {"owner": "", "text": BODY}),
    Case("거절: owner 가 문자열이 아니다", {"owner": 5, "text": BODY}),
    Case("거절: text 가 문자열이 아니다", {"owner": A, "text": None}),
    Case("거절: 제목에 U+001F", P(title="a\x1fb")),
    Case("거절: doc_id 에 U+001F", P(doc_id="doc\x1fa")),
    Case("거절: valid_from 형식", P(valid_from="2026/01/01")),
    Case("거절: valid_until 형식", P(valid_until="soon")),
    Case("거절: superseded_by 가 자기 자신", P(superseded_by="doc-a")),
    Case("거절: 남의 문서", P(), seed=(dict(P(), owner=B),)),
    Case("거절: create_only 인데 이미 있다", P(create_only=True), seed=(P(),)),
    Case("거절: 기대 해시가 다르다", P(text=BODY2, expected_content_hash="0" * 64), seed=(P(),)),
    # 새 문서
    Case("신규: id 파생", {"owner": A, "text": BODY, "title": "Graph"}),
    Case("신규: id 지정", P(title="Graph")),
    Case("신규: 제목 없음", P()),
    Case("신규: 언급 링크", P(title="Graph"), nodes=GRAPH),
    Case("신규: 링크 끔", P(title="Graph", link=False), nodes=GRAPH),
    Case("신규: 여러 청크", P(text=LONG, max_chars=120), nodes=GRAPH),
    Case("신규: 외부 경계", P(text=BODY2, boundaries=[0, 30])),
    Case("신규: 레이어", P(layer="reference")),
    Case("신규: 출처", P(origin="notes/graph.md")),
    Case("신규: 만든 시각 지정", P(created_at="2026-01-02T03:04:05.000Z")),
    Case("신규: 고친 시각 지정", P(updated_at="2026-01-02T03:04:05.000Z")),
    Case("신규: 내용 시점", P(content_time="2026-03")),
    Case("신규: 내용 시점 형식 위반", P(content_time="March")),
    Case("신규: 유효구간", P(valid_from="2026-01", valid_until="2026-12-31", superseded_by="doc-b")),
    Case("신규: 주제", P(title="Graph", topic="vinc/test"), nodes=GRAPH),
    Case("신규: create_only", P(create_only=True)),
    Case("신규: 본문이 다른 doc_id 를 선언", P(text=FM_ID)),
    Case("신규: 프런트매터 주제·관계에 미러 없음", P(text=FM_DOMAIN)),
    Case("신규: 프런트매터 주제·관계에 미러 있음", P(text=FM_DOMAIN, topic="vinc/test")),
    Case("신규: 옛 관례 id 빈 자리", {"owner": A, "text": BODY, "doc_id": graphmd.LEGACY_CONVENTIONS_DOC_ID}),
    Case("신규: 옛 관례 id 남이 점유", {"owner": A, "text": BODY, "doc_id": graphmd.LEGACY_CONVENTIONS_DOC_ID},
         seed=({"owner": B, "text": BODY2, "doc_id": graphmd.LEGACY_CONVENTIONS_DOC_ID},)),
    Case("거절: 옛 관례 id 남이 점유하고 조건부", {"owner": A, "text": BODY, "create_only": True,
                                       "doc_id": graphmd.LEGACY_CONVENTIONS_DOC_ID},
         seed=({"owner": B, "text": BODY2, "doc_id": graphmd.LEGACY_CONVENTIONS_DOC_ID},)),
    # 다시 쓰기
    Case("같은 본문: 건너뜀", P(title="Graph"), seed=(P(title="Graph"),)),
    Case("같은 본문: 제목 생략은 보존하고 건너뜀", P(), seed=(P(title="Graph"),)),
    Case("같은 본문: 제목 비움은 다시 쓴다", P(title=""), seed=(P(title="Graph"),)),
    Case("같은 본문: 새 제목은 다시 쓴다", P(title="Store"), seed=(P(title="Graph"),), nodes=GRAPH),
    Case("같은 본문: 주제만", P(topic="vinc/test"), seed=(P(title="Graph"),)),
    Case("같은 본문: 출처만 바뀜", P(origin="notes/new.md"), seed=(P(origin="notes/old.md"),), nodes=GRAPH),
    Case("같은 본문: 유효구간만 바뀜", P(valid_until="2026-06"), seed=(P(),)),
    Case("같은 본문: 내용 시점만 바뀜", P(content_time="2025"), seed=(P(content_time="2026"),)),
    Case("같은 본문: 내용 시점 형식 위반", P(content_time="later"), seed=(P(content_time="2026"),)),
    Case("같은 본문: 유효구간 생략은 보존", P(), seed=(P(valid_from="2026-01"),)),
    Case("같은 본문: 기대 해시가 맞다", P(expected_content_hash=docstore._sha(BODY)), seed=(P(),)),
    Case("편집: 청크가 는다", P(text=LONG, max_chars=120), seed=(P(title="Graph"),), nodes=GRAPH),
    Case("편집: 본문이 길어진다", P(text=BODY2), seed=(P(title="Graph"),), nodes=GRAPH),
    Case("편집: 청크가 준다", P(text=BODY), seed=(P(text=LONG, max_chars=120),), nodes=GRAPH),
    Case("편집: 제목 생략은 보존", P(text=BODY2), seed=(P(title="Graph"),)),
    Case("편집: 만든 시각은 보존", P(text=BODY2, created_at="2020-01-01"),
         seed=(P(created_at="2026-01-02T03:04:05.000Z"),)),
    Case("편집: 기대 해시가 맞다", P(text=BODY2, expected_content_hash=docstore._sha(BODY)), seed=(P(),)),
    Case("편집: 주제와 함께", P(text=BODY2, topic="vinc/other"), seed=(P(topic="vinc/test"),), nodes=GRAPH),
)


# ── 정규화 ────────────────────────────────────────────────────────────────────

_HLC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")


def _text(s: str) -> str:
    return _HLC.sub("<hlc>", s).replace(_qb._today(), "<today>")


def _value(v):
    if isinstance(v, str):
        if _HLC.fullmatch(v) and v != "2026-01-02T03:04:05.000Z":
            return "<hlc>"
        v = _text(v) if v != "2026-01-02T03:04:05.000Z" else v
        return v if len(v) <= 120 else f"<str {len(v)}>"
    if isinstance(v, dict):
        return {k: _value(x) for k, x in sorted(v.items())}
    if isinstance(v, (list, tuple)):
        return [_value(x) for x in v]
    return v


def _rows(store, q: str, params=None) -> list:
    return sorted((_value(r) for r in store.run(q, params or {}) or []),
                  key=lambda r: json.dumps(r, ensure_ascii=False, sort_keys=True))


def snapshot(store) -> dict:
    """문서 메타·청크·청크 언급·미러 개념·문서 삭제 표식 전부."""
    return {
        "meta": _rows(store, "MATCH (d:_DocMeta) RETURN d.doc_id AS doc_id, d.owner_id AS owner, "
                             "d.title AS title, d.origin AS origin, d.content_hash AS hash, "
                             "d.chunk_count AS chunks, d.needs_embed AS needs_embed, "
                             "d.created_at AS created_at, d.updated_at AS updated_at, "
                             "d.content_time AS content_time, d.valid_from AS valid_from, "
                             "d.valid_until AS valid_until, d.superseded_by AS superseded_by, "
                             "d.sync_seq AS sync_seq"),
        "chunks": _rows(store, "MATCH (c:Chunk) RETURN c.chunk_id AS id, c.doc_id AS doc, "
                               "c.owner_id AS owner, c.source_file AS origin, c.section_title AS sec, "
                               "c.chunk_index AS idx, c.layer AS layer, c.char_start AS cs, "
                               "c.char_end AS ce, c.char_count AS cc, c.text AS text"),
        "mentions": _rows(store, "MATCH (c:Chunk)-[m:MENTIONS]->(n:Concept) RETURN c.chunk_id AS chunk, "
                                 "n.concept_id AS concept, m.method AS method"),
        "mirrors": _rows(store, "MATCH (n:Concept) WHERE n.concept_id CONTAINS 'concept:doc.' "
                                "RETURN n.concept_id AS id, n.title AS title, n.domain AS domain, "
                                "n.owner_id AS owner"),
        "tombstones": _rows(store, "MATCH (t:_DocTombstone) RETURN t.doc_id AS doc_id"),
    }


def run_case(case: Case, store) -> dict:
    if case.nodes:
        for owner in (A,):
            res = frag.merge_fragment({"owner": owner, "nodes": copy.deepcopy(list(case.nodes))},
                                      store=store)
            assert res.get("executed") is True, (case.name, res)
    for seed in case.seed:
        res = docstore.put_doc(store, **copy.deepcopy(seed))
        assert res.get("executed") is True or case.seed_may_fail, (case.name, res)
    try:
        res = docstore.put_doc(store, **copy.deepcopy(case.call))
    except Exception as exc:          # 입구 밖으로 나가는 예외도 동작이다
        res = {"raises": type(exc).__name__}
    warnings = res.pop("warnings", None)
    out = {"result": _value(res)}
    if warnings:
        out["warnings"] = [_text(w) for w in warnings]
    out.update(snapshot(store))
    return out


def _mode() -> str:
    return "namespaced" if pkns.enabled() else "plain"


def _open_store(data_dir: str):
    from store.backend import open_store
    from store.config import load_config
    cfg = load_config({"data_dir": data_dir})
    st = open_store(cfg)
    st.provision(dim=cfg.embedding_dim)
    return st


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    st = _open_store(str(tmp_path / "db"))
    if st is None:
        pytest.skip("임베디드 드라이버(ladybug) 미설치")
    try:
        yield st
    finally:
        st.close()


@functools.lru_cache(maxsize=1)
def _golden_cached() -> str:
    return GOLDEN.read_text(encoding="utf-8")


def _golden() -> dict:
    return json.loads(_golden_cached())


def _expected(golden: dict, name: str) -> dict:
    if _mode() == "namespaced" and name in golden["namespaced"]:
        return golden["namespaced"][name]
    return golden["plain"][name]


# ── 테스트 ────────────────────────────────────────────────────────────────────

def test_케이스_이름이_겹치지_않고_기록과_맞는다():
    names = [c.name for c in CASES]
    assert len(names) == len(set(names))
    golden = _golden()
    assert sorted(golden["plain"]) == sorted(names)
    assert set(golden["namespaced"]) <= set(names)


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_동작이_기록과_같다(case, store):
    assert run_case(case, store) == _expected(_golden(), case.name)


# ── 다시 기록하기 ──────────────────────────────────────────────────────────────

def _dump(golden: dict) -> str:
    """케이스마다 몇 줄로 적는다. 행 하나는 한 줄이라 차이가 케이스 단위로 읽힌다."""
    def one(v):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)

    lines = ["{"]
    sections = sorted(golden.items())
    for si, (section, cases) in enumerate(sections):
        lines.append(f" {one(section)}: {{")
        items = sorted(cases.items())
        for ci, (name, out) in enumerate(items):
            lines.append(f"  {one(name)}: {{")
            keys = sorted(out)
            for ki, k in enumerate(keys):
                v = out[k]
                tail = "," if ki < len(keys) - 1 else ""
                if isinstance(v, list) and v:
                    lines.append(f"   {one(k)}: [")
                    lines.extend(f"    {one(x)}" + ("," if xi < len(v) - 1 else "")
                                 for xi, x in enumerate(v))
                    lines.append("   ]" + tail)
                else:
                    lines.append(f"   {one(k)}: {one(v)}{tail}")
            lines.append("  }" + ("," if ci < len(items) - 1 else ""))
        lines.append(" }" + ("," if si < len(sections) - 1 else ""))
    lines.append("}")
    return "\n".join(lines) + "\n"


def _record() -> None:
    os.environ.setdefault("MEMORY_STORE_BACKEND", "embedded")
    os.environ.setdefault("MEMORY_EMBEDDING_MODEL", "local-en")
    golden = _golden() if GOLDEN.exists() else {"plain": {}, "namespaced": {}}
    mode = _mode()
    out = {}
    for case in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            st = _open_store(os.path.join(tmp, "db"))
            try:
                out[case.name] = run_case(case, st)
            finally:
                st.close()
    if mode == "plain":
        golden["plain"] = out
        golden["namespaced"] = {k: v for k, v in golden.get("namespaced", {}).items() if k in out}
    else:
        golden["namespaced"] = {k: v for k, v in out.items() if v != golden["plain"].get(k)}
    GOLDEN.write_text(_dump(golden), encoding="utf-8")
    print(f"recorded {len(out)} cases in {mode} mode -> {GOLDEN}")


if __name__ == "__main__":
    if "--record" in sys.argv:
        _record()
    else:
        sys.exit(pytest.main([__file__, "-q"]))
