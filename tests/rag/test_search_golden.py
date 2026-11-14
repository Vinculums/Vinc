# -*- coding: utf-8 -*-
"""`vinc_rag.retrieval.search` 의 응답 전체를 표로 고정한다(골든 스냅숏).

`_search` 를 단계마다 함수로 나누는 리팩터(findings-rag F4) 앞에 둔다. 단계는 같은 응답 dict 를
차례로 고치고, 몇몇 키(`validity_filtered` 등)는 마지막에 쓴 단계가 이긴다. 그래서 키 하나하나가
아니라 **응답 전체**를 케이스마다 고정한다. 이 표가 바뀌면 검색 응답이 바뀐 것이다. 단계를 옮기는
리팩터는 표를 바꾸지 않아야 하고, 의도한 변경이면 바뀐 케이스를 PR 본문에 적는다.

스토어는 합성이다(4차원 결정론 벡터, 실 엔진). 소유자 둘, 개념·결정·기록, 유효구간이 지난 것과
아직 오지 않은 것, 청크 여러 개짜리 문서, 언급 링크, 노드 미러를 한 번 심고, 케이스는 그 위에서
읽기만 한다. 벡터 색인을 비우는 케이스(`vector rebuilding`)와 노드 미러가 빈 케이스만 따로 심은
스토어를 쓴다.

기대값은 `tests/fixtures/search_golden.json` 에 있다. `plain` 은 PK 네임스페이싱을 끈 모드,
`namespaced` 는 켠 모드에서 **결과가 다른 케이스만** 담는다. 다시 기록하는 법(끈 모드를 먼저):

    VINC_PK_NAMESPACED=0 python tests/rag/test_search_golden.py --record
    VINC_PK_NAMESPACED=1 python tests/rag/test_search_golden.py --record

정규화: HLC 시각은 `<hlc>`, 오늘과 내일 날짜(UTC)는 `<today>`·`<tomorrow>`, 스토어 경로는 `<store>`. 순서는 그대로 둔다
(순위가 계약이다). 키 순서는 정렬해서 적는다.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from store import embedded_backend as eb  # noqa: E402

_DRIVER, _NAME = eb.import_driver()
pytestmark = pytest.mark.skipif(_DRIVER is None, reason="임베디드 드라이버(ladybug) 미설치")

GOLDEN = _ROOT / "tests" / "fixtures" / "search_golden.json"
A = "user:golden-a"
B = "user:golden-b"
QUERY = "harbor lighthouse signal"
NEAR = [1.0, 0.0, 0.0, 0.0]
RUNTIME = "golden-runtime"
MODEL = "golden-model"


def vec(key: str) -> list[float]:
    """이름에서 뽑는 결정론 4차원 단위 벡터. `NEAR` 와의 거리는 이름에 따라 고르게 퍼진다."""
    h = hashlib.sha256(key.encode()).digest()
    raw = [1.0 + h[0] / 64.0, (h[1] - 128) / 128.0, (h[2] - 128) / 128.0, (h[3] - 128) / 128.0]
    norm = sum(x * x for x in raw) ** 0.5
    return [round(x / norm, 6) for x in raw]


# ── 심는 것 ────────────────────────────────────────────────────────────────────
NODES_A = [
    {"id": "concept:harbor-lighthouse", "type": "concept", "title": "Harbor lighthouse",
     "domain": "golden/sea", "one_liner": "The lighthouse that guards the harbor signal"},
    {"id": "concept:signal-lamp", "type": "concept", "title": "Signal lamp", "domain": "golden/sea"},
    {"id": "concept:harbor-pier", "type": "concept", "title": "Harbor pier", "domain": "golden/land"},
    {"id": "concept:keeper-log", "type": "concept", "title": "Keeper log", "domain": "golden/land"},
    # 질의와 겹치는 낱말이 없고 문서도 언급하지 않는다. 의미 팔로만 닿는다(미러 벡터가 `NEAR`).
    {"id": "concept:beacon-tower", "type": "concept", "title": "Beacon tower", "domain": "golden/sky"},
    {"id": "decision:lighthouse-lens", "type": "decision", "title": "Lighthouse lens choice",
     "domain": "golden/sea", "valid_from": "2000", "valid_until": "2002"},
    {"id": "decision:signal-schedule", "type": "decision", "title": "Signal schedule for the harbor",
     "domain": "golden/sea", "valid_from": "2001"},
    {"id": "record:lighthouse-future", "type": "record", "title": "Lighthouse future survey",
     "domain": "golden/sea", "valid_from": "2099"},
]
EDGES_A = [
    {"from": "decision:lighthouse-lens", "to": "concept:harbor-lighthouse", "type": "about",
     "evidence": "the lens belongs to the lighthouse"},
    {"from": "decision:signal-schedule", "to": "concept:signal-lamp", "type": "about",
     "evidence": "the schedule drives the lamp"},
]
NODES_B = [
    {"id": "concept:harbor-lighthouse", "type": "concept", "title": "Harbor lighthouse of B",
     "domain": "golden/other"},
    {"id": "concept:fog-horn", "type": "concept", "title": "Fog horn signal", "domain": "golden/other"},
]
DOCS = [
    (A, "golden.guide", "Harbor guide",
     "# Harbor guide\n\nThe Harbor lighthouse sends a signal every night. The Signal lamp turns.\n\n"
     + "".join(f"## Night {i}\n\nHarbor lighthouse signal note {i}. The Keeper log records the lamp.\n\n"
               for i in range(5)), {}),
    (A, "golden.pier", "Pier notes", "# Pier notes\n\nThe Harbor pier is near the lighthouse.\n", {}),
    (A, "golden.old", "Old lens memo", "# Old lens memo\n\nThe lighthouse signal used an old lens.\n",
     {"valid_from": "2000", "valid_until": "2002"}),
    (A, "golden.future", "Future survey", "# Future survey\n\nA harbor lighthouse survey is planned.\n",
     {"valid_from": "2099"}),
    (B, "golden.b", "Other harbor", "# Other harbor\n\nThe Fog horn signal of the other harbor lighthouse.\n", {}),
]
PROFILE = {"version": 1, "id": "golden-profile", "encoder_model": "local-en", "encoder_runtime": RUNTIME,
           "corpus_revision": "golden-rev", "evidence_sha256": "0" * 64, "status": "approved",
           "owner_id": A, "max_distance": {"chunk": 0.6, "node": 0.6}}


def seed(store, *, node_mirror_rows: bool = True) -> None:
    from api import docembed, docstore, node_mirror
    from api import fragment as frag
    store.provision(dim=4)
    for owner, nodes, edges in ((A, NODES_A, EDGES_A), (B, NODES_B, [])):
        res = frag.merge_fragment({"owner": owner, "nodes": copy.deepcopy(nodes),
                                   "edges": copy.deepcopy(edges)}, store=store)
        assert res.get("executed"), res
    for owner, doc_id, title, text, extra in DOCS:
        res = docstore.put_doc(store, owner=owner, doc_id=doc_id, title=title, text=text,
                               created_at="2000-01-01T00:00:00Z", updated_at="2000-01-01T00:00:00Z",
                               max_chars=160, **extra)
        assert not res.get("error"), res
    for _ in range(4):
        pending = docembed.pending_embed_chunks(store, limit=16)
        if not pending:
            break
        by_doc: dict[str, dict] = {}
        for row in pending:
            by_doc.setdefault(row["doc_id"], {"hash": row["content_hash"], "emb": {}})
            by_doc[row["doc_id"]]["emb"][row["chunk_id"]] = vec(row["chunk_id"])
        for doc_id in sorted(by_doc):
            docembed.apply_embeddings(store, doc_id, by_doc[doc_id]["emb"], RUNTIME,
                                      content_hash=by_doc[doc_id]["hash"], model=MODEL)
    rows = []
    for owner in ((A, B) if node_mirror_rows else ()):
        got, _ = node_mirror.collect(store, owner)
        rows.extend(got)
    rows.sort(key=lambda r: (str(r.get("owner_id")), str(r.get("id"))))
    for r in rows:
        r["emb"] = vec(f"{r.get('owner_id')}|{r.get('id')}")
        if r.get("id") == "concept:beacon-tower" and r.get("owner_id") == A:
            r["emb"] = list(NEAR)
    if rows:
        assert node_mirror.replace(store, rows, MODEL, RUNTIME)["index_ok"]
    ensure = getattr(store, "ensure_search_indexes", None)
    if callable(ensure):
        ensure(dim=4)


# ── 케이스 ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Case:
    name: str
    kwargs: dict
    page2: bool = False          # 첫 페이지의 next_cursor 로 두 번째 페이지를 읽어 그것을 기록한다
    # 따로 심은 스토어: "drop_vector_index" 는 청크 벡터 색인을 비우고, "no_node_mirror" 는 노드 미러를 안 심는다
    fresh_store: str = ""
    env: tuple = ()              # 이 케이스에서만 거는 환경((이름, 값), ...)


E = {"query_embedding": NEAR, "query_runtime": RUNTIME, "query_model": MODEL}
CASES: tuple[Case, ...] = (
    Case("general, no embedding", {"owner": A, "top_k": 3}),
    Case("general, with embedding", {"owner": A, "top_k": 3, **E}),
    Case("wide window, with embedding", {"owner": A, "top_k": 8, **E}),
    Case("mode current", {"owner": A, "top_k": 3, "mode": "current", **E}),
    Case("mode historical", {"owner": A, "top_k": 3, "mode": "historical", **E}),
    Case("as_of 2001", {"owner": A, "top_k": 3, "as_of": "2001-06", **E}),
    Case("known_at before everything", {"owner": A, "top_k": 3, "known_at": "2001-01-01", **E}),
    Case("kinds decision", {"owner": A, "top_k": 3, "kinds": ["decision"], **E}),
    Case("cursor page 2", {"owner": A, "top_k": 2, **E}, page2=True),
    Case("owner B", {"owner": B, "top_k": 3, **E}),
    Case("owner unscoped", {"owner": None, "top_k": 3}),
    Case("detail full", {"owner": A, "top_k": 2, "detail": "full", **E}),
    Case("runtime mismatch", {"owner": A, "top_k": 2, "query_embedding": NEAR, "query_runtime": "other-runtime",
                              "query_model": MODEL}),
    Case("relevance profile active", {"owner": A, "top_k": 3, "relevance_profile": PROFILE,
                                      "relevance_corpus_revision": "golden-rev", **E}),
    Case("relevance profile unverified", {"owner": A, "top_k": 3, "relevance_profile": PROFILE, **E}),
    Case("vector rebuilding", {"owner": A, "top_k": 3, **E}, fresh_store="drop_vector_index"),
    Case("node mirror model mismatch", {"owner": A, "top_k": 3, "query_embedding": NEAR,
                                        "query_runtime": RUNTIME, "query_model": "other-model"}),
    Case("node mirror empty", {"owner": A, "top_k": 3, **E}, fresh_store="no_node_mirror"),
    Case("semantic arm off", {"owner": A, "top_k": 3, **E}, env=(("VINC_NODE_SEMANTIC_ARM", "off"),)),
    Case("semantic rank off", {"owner": A, "top_k": 3, **E}, env=(("VINC_NODE_SEM_RANK", "off"),)),
    Case("invalid kinds", {"owner": A, "kinds": ["bridge"]}),
    Case("invalid as_of", {"owner": A, "as_of": "not-a-date"}),
    Case("invalid cursor", {"owner": A, "cursor": "not-a-cursor"}),
)


# ── 실행과 정규화 ──────────────────────────────────────────────────────────────
_HLC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z(?:/\d+)?(?:#[0-9a-f]+)?")


def _value(v, store_dir: str):
    if isinstance(v, dict):
        return {str(k): _value(x, store_dir) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_value(x, store_dir) for x in v]
    if isinstance(v, set):
        return sorted(_value(x, store_dir) for x in v)
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, str):
        # 검색의 시계는 UTC 날짜다(`store.search_temporal`). current 의 기간 끝은 다음 날이다.
        today = datetime.now(timezone.utc).date()
        s = v.replace(store_dir, "<store>") if store_dir else v
        s = _HLC.sub(lambda m: m.group(0) if m.group(0).startswith("2000-01-01") else "<hlc>", s)
        return (s.replace(today.isoformat(), "<today>")
                 .replace((today + timedelta(days=1)).isoformat(), "<tomorrow>"))
    return v


def run_case(case: Case, store, store_dir: str) -> dict:
    from unittest import mock
    from vinc_rag import retrieval as R
    kwargs = copy.deepcopy(case.kwargs)
    with mock.patch.dict(os.environ, dict(case.env)):
        res = R.search(QUERY, store=store, **kwargs)
        if case.page2:
            first = res
            cursor = first.get("next_cursor")
            assert cursor, first
            res = {"page1": _value(first, store_dir),
                   "page2": R.search(QUERY, store=store, cursor=cursor, **kwargs)}
    return _value(res, store_dir)


def _mode() -> str:
    from store.pkns import enabled
    return "namespaced" if enabled() else "plain"


def _env():
    return {"MEMORY_STORE_BACKEND": "embedded", "VINC_KEEP_VECTOR_INDEX": "off",
            "MEMORY_EMBEDDING_MODEL": "local-en", "VINC_SEARCH_RELEVANCE_PROFILE": "",
            "VINC_NODE_COLBERT": "", "VINC_NODE_SEMANTIC_ARM": "", "VINC_NODE_SEM_RANK": ""}


def _open_store(data_dir: str):
    from store.backend import open_store
    from store.config import load_config
    return open_store(load_config({"data_dir": data_dir}))


class _Stores:
    """케이스가 함께 읽는 심은 스토어 하나와, 따로 쓰는 스토어들."""

    def __init__(self, root: str):
        self.root = root
        self.shared = None

    def get(self, case: Case):
        if case.fresh_store:
            path = os.path.join(self.root, case.fresh_store.replace(" ", "-"), "db")
            st = _open_store(path)
            seed(st, node_mirror_rows=case.fresh_store != "no_node_mirror")
            if case.fresh_store == "drop_vector_index":
                st.drop_vector_index()
            return st, path, True
        if self.shared is None:
            path = os.path.join(self.root, "shared", "db")
            self.shared = (_open_store(path), path)
            seed(self.shared[0])
        return self.shared[0], self.shared[1], False

    def close(self):
        if self.shared is not None:
            self.shared[0].close()


@pytest.fixture(scope="module")
def stores(tmp_path_factory):
    saved = {k: os.environ.get(k) for k in _env()}
    os.environ.update(_env())
    s = _Stores(str(tmp_path_factory.mktemp("search-golden")))
    yield s
    s.close()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@functools.lru_cache(maxsize=1)
def _golden_text() -> str:
    return GOLDEN.read_text(encoding="utf-8")


def _expected(name: str):
    golden = json.loads(_golden_text())
    return golden.get(_mode(), {}).get(name, golden["plain"][name])


def test_케이스_이름이_겹치지_않고_기록과_맞는다():
    names = [c.name for c in CASES]
    assert len(names) == len(set(names))
    golden = json.loads(_golden_text())
    assert sorted(golden["plain"]) == sorted(names)
    assert set(golden.get("namespaced", {})) <= set(names)


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_응답이_기록과_같다(case, stores):
    st, path, own = stores.get(case)
    try:
        got = run_case(case, st, os.path.dirname(path))
    finally:
        if own:
            st.close()
    assert got == _expected(case.name)


def _dump(golden: dict) -> str:
    return json.dumps(golden, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def _record() -> None:
    os.environ.update(_env())
    golden = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {"plain": {}, "namespaced": {}}
    mode = _mode()
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        stores = _Stores(tmp)
        try:
            for case in CASES:
                st, path, own = stores.get(case)
                try:
                    out[case.name] = run_case(case, st, os.path.dirname(path))
                finally:
                    if own:
                        st.close()
        finally:
            stores.close()
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
