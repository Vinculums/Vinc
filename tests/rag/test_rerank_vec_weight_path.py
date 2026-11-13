# -*- coding: utf-8 -*-
"""`rag_eval/rerank.py` 의 `--vec-weight` 대조군 경로가 끝까지 돌고, 관례 청크를 융합 전에 빼는가.

모델·네트워크·스토어 엔진 없이 `main()` 을 실제로 돌린다. 리랭커와 인코더는 가짜이고,
스토어는 두 팔과 청크→문서 매핑만 답하는 가짜다. 재는 것은 순위 품질이 아니라 경로다:
두 팔 맨 위에 관례 청크(구 고정 id 와 owner 파생 id)를 두고, 대조군에서 정답 문서가
1위로 오는지 본다. 거름이 빠지면 정답은 3위로 밀리고, 거름이 없는 이름을 읽으면
`main()` 이 그 자리에서 죽는다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rag_eval import rerank as er  # noqa: E402

#: 구 고정 id 의 청크와 owner 파생 id 의 청크. 둘 다 관례로 걸러져야 한다.
_LEGACY_CONV = "graph.md#00000"
_OWNER_CONV = "graph.md~0123456789abcdef#00000"


class _Store:
    """대조군 경로가 부르는 것만 답한다. 두 팔 모두 관례 청크가 맨 위다."""

    def __init__(self):
        self.closed = False

    def fulltext_search(self, text, top_k, filters=None):
        return [{"id": _OWNER_CONV}, {"id": _LEGACY_CONV}, {"id": "d/one#00000"},
                {"id": "d/two#00000"}]

    def vector_search(self, vec, top_k, filters=None):
        return [{"id": _OWNER_CONV}, {"id": _LEGACY_CONV}, {"id": "d/one#00000"},
                {"id": "d/two#00000"}]

    def run(self, cypher, params=None):
        # `rag_eval.search._docs_of` 의 청크→문서 질의. chunk_id 는 `{doc_id}#{n}` 이다.
        if params and "ids" in params:
            return [{"i": i, "d": i.rsplit("#", 1)[0]} for i in params["ids"]]
        return [{"c": 0}]

    def close(self):
        self.closed = True


class _Reranker:
    """모델을 읽지 않는다. 후보 순서를 그대로 지키는 점수를 준다."""

    load_s = 0.0
    piece_tokens = 512

    def __init__(self, name, path, max_tokens=512):
        pass

    def warm(self):
        return 0.0

    def score_texts(self, text, texts, window="first", seqs=None):
        return [1.0 - 0.01 * i for i in range(len(texts))], len(texts)


def _search(text, query_embedding=None, top_k=10, **_kw):
    return {"rows": [{"id": "d/one#00000", "text": "one"}, {"id": "d/two#00000", "text": "two"}],
            "arms": "fts+semantic" if query_embedding is not None else "fts"}


def test_vec_weight_runs_and_drops_conventions_chunks_before_fusion(monkeypatch, tmp_path):
    store = _Store()
    monkeypatch.setenv("VINC_EMBED_ENCODE", "http://127.0.0.1:1/unused")
    monkeypatch.setattr(er, "load_config", lambda _arg=None: SimpleNamespace())
    monkeypatch.setattr(er, "open_store", lambda _cfg: store)
    monkeypatch.setattr(er, "Reranker", _Reranker)
    monkeypatch.setattr(er.q, "search", _search)
    monkeypatch.setattr(er.es, "encode_query", lambda text, url, timeout=20.0: [0.1, 0.2])
    monkeypatch.setattr(er.es, "assert_gold_present", lambda *_a, **_k: None)
    monkeypatch.setattr(er.es, "run_manifest", lambda *_a, **_k: {})

    evalset = tmp_path / "evalset.json"
    evalset.write_text(json.dumps({"version": 1, "queries": [
        {"query": "뉴턴 역학은", "doc_id": "d/one", "lang": "ko"}]}, ensure_ascii=False),
        encoding="utf-8")
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", [
        "rerank.py", "--data", "unused", "--owner", "eval", "--evalset", str(evalset),
        "--rerank", "stub=unused", "--depth", "2", "--vec-weight", "0.5", "--ko-strip",
        "--bootstrap", "10", "--out", str(out)])

    assert er.main() == 0
    got = json.loads(out.read_text(encoding="utf-8"))
    rec = got["per_query"][0]
    # 관례 청크가 남으면 정답 문서는 관례 문서 둘 뒤의 3위다.
    assert rec["rrf_w0.5"] == 1
    assert rec["fts_ko"] == 1 and rec["rrf_w0.5_ko"] == 1
    assert got["vec_weights"] == [0.5] and "rrf_w0.5" in got["overall"]
    assert store.closed
