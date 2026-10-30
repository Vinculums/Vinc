# -*- coding: utf-8 -*-
"""측정 도구의 계약 — 잰다고 적은 것을 실제로 재는가.

이 리포에는 게이트가 붙은 결함과 안 붙은 결함이 있었다. `retable_vectors` 와
`peak_rss_mb` 는 각자 회귀 테스트를 얻었지만(PR #185), 같은 커밋의 나머지 둘은
못 얻었다. 그 둘이 다시 드리프트하면 **오류 없이 틀린 수치**로 돌아간다. 여기가
그 자리다.

계약은 소스 문자열이 아니라 **동작**으로 잰다. `grep` 으로 지키면 함수를 옮기거나
이름을 바꾸는 것만으로 통과하고, 정작 값이 틀려지는 변경은 안 잡힌다. 그래서 각
테스트는 도구를 실제로 돌리고 그 출력이나 그 호출 인자를 본다.

각 테스트에 **되돌리면 왜 빨간가**를 적어 둔다. 그것이 없으면 통과가 무의미하다.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from api import docstore as ds                 # noqa: E402
from api import query as q                     # noqa: E402
from store.backend import open_store           # noqa: E402
from store.config import load_config           # noqa: E402


def _store_with_two_docs(owner="eval"):
    """평가 도구를 돌릴 수 있는 최소 스토어. 문서 둘, 소유자 하나.

    🔴 **벡터와 색인까지 만든다.** `put_doc` 만 하면 `ChunkVec` 이 비어 있고 색인은
    지연 재빌드 상태라(doc_put 이 `search indexes dropped` 를 알린다), 벡터 팔이
    `index_missing` 으로 떨어진다. 그러면 이 파일의 계약 대부분이 **실제 결함이 아니라
    픽스처 미비 때문에** 빨갛거나, 더 나쁘게는 강등된 창의 수치를 정상으로 읽는다.
    프로덕션에서 벡터를 만드는 유일한 경로가 `apply_embeddings` 이므로 그것을 쓴다
    (`rehearse_rebuild.reindex` 와 같은 순서다).
    """
    cfg = load_config({"data_dir": tempfile.mkdtemp() + "/db"})
    st = open_store(cfg)
    st.provision(dim=cfg.embedding_dim)
    ds.put_doc(st, owner=owner, text="Newtonian mechanics describes motion under forces.",
               doc_id="d/one", title="d/one", link=False)
    ds.put_doc(st, owner=owner, text="허브는 단일 라이터라 리비전 롤링 전환이 안 된다.",
               doc_id="d/two", title="d/two", link=False)
    while True:
        pend = ds.pending_embed_chunks(st, limit=16)
        if not pend:
            break
        by_doc: dict[str, list[dict]] = {}
        for row in pend:
            by_doc.setdefault(row["doc_id"], []).append(row)
        for doc_id, rows in by_doc.items():
            # 값은 무관하다. 이 파일은 순위 품질이 아니라 **계약**을 재고, 벡터 팔이
            # 실제로 도는 것만 필요하다. 문서마다 다른 상수를 줘 서로 구분되게 한다.
            ds.apply_embeddings(st, doc_id, {
                r["chunk_id"]: [0.01 * (1 + i)] * cfg.embedding_dim
                for i, r in enumerate(rows)})
    st.ensure_search_indexes(dim=cfg.embedding_dim)
    return st, cfg


def _evalset(path: Path, n=2):
    rows = [{"query": "Newtonian mechanics", "doc_id": "d/one", "lang": "en"},
            {"query": "단일 라이터", "doc_id": "d/two", "lang": "ko"}][:n]
    path.write_text(json.dumps({"version": 1, "queries": rows}, ensure_ascii=False),
                    encoding="utf-8")
    return path


class _SpyStore:
    """실 스토어를 감싸 `vector_search` 의 인자를 기록한다.

    호출을 막지 않고 지나가게 한다 — 계약은 "어떤 인자로 부르는가"이고, 그것을
    확인하려고 검색 자체를 죽이면 뒤의 단계가 다른 이유로 실패한다.

    ⚠ 이 스파이는 **평가 스크립트의 직접 호출만** 받아야 한다. `q.search` 도
    안에서 `store.vector_search` 를 부르므로, 같은 스파이를 넘기면 두 경로가 한
    카운터를 공유하고 "N번째 호출을 죽인다"가 어느 경로를 죽이는지 알 수 없게
    된다(실제로 그렇게 짰다가 RRF 조건이 죽어 다른 계약이 발동했다).
    아래 `_run_eval_search` 가 `q.search` 에 감싸지 않은 스토어를 준다.
    """

    def __init__(self, inner, fail_from=None):
        self._inner, self._fail_from = inner, fail_from
        self.vector_calls: list[tuple] = []

    def vector_search(self, vec, top_k, filters=None):
        self.vector_calls.append((len(vec) if vec is not None else None, top_k, filters))
        if self._fail_from is not None and len(self.vector_calls) >= self._fail_from:
            raise RuntimeError("의도된 벡터 팔 실패")
        return self._inner.vector_search(vec, top_k, filters)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _run_eval_search(monkey, *, fail_direct_from=None, search_hook=None, argv_extra=()):
    """`rag_eval/search.py` 를 실제로 돌리고 JSON 출력을 돌려준다."""
    from rag_eval import search as es

    st, cfg = _store_with_two_docs()
    spy = _SpyStore(st, fail_from=fail_direct_from)
    tmp = Path(tempfile.mkdtemp())
    fixture = _evalset(tmp / "evalset.json")

    monkey(es, "open_store", lambda _cfg: spy)
    # 인코더 창구를 띄우지 않는다 — 이 테스트가 재는 것은 검색 경로의 계약이고,
    # 실제 벡터 값은 무관하다. 차원만 스토어와 맞으면 된다.
    monkey(es, "encode_query", lambda text, url, timeout=20.0: [0.01] * cfg.embedding_dim)

    # `q.search` 에는 **감싸지 않은** 스토어를 준다. 그래야 스파이의 카운터가
    # 평가의 직접 호출만 세고, 계약 3이 재려는 경로가 딱 하나가 된다.
    inner_search = search_hook if search_hook is not None else es.q.search

    def routed(*a, **kw):
        return inner_search(*a, **{**kw, "store": st})

    monkey(es.q, "search", routed)

    argv = ["eval_search.py", "--data", "unused", "--owner", "eval",
            "--evalset", str(fixture), "--top-k", "5", "--json", *argv_extra]
    buf = io.StringIO()
    old_argv, sys.argv = sys.argv, argv
    try:
        with redirect_stdout(buf):
            rc = es.main()
    finally:
        sys.argv = old_argv
    return rc, json.loads(buf.getvalue()), spy


class EvalSearchContract(unittest.TestCase):
    """`rag_eval/search.py` — 프로덕션 검색 경로를 도는 유일한 도구."""

    def setUp(self):
        self._undo: list[tuple] = []
        import os
        self._old_encode = os.environ.get("VINC_EMBED_ENCODE")
        # 창구 주소가 없으면 도구가 벡터·RRF 조건을 **시도조차 하지 않는다**
        # (api/server.py 와 같은 규약). 아래 테스트 전부가 그 조건을 재므로 필수다.
        os.environ["VINC_EMBED_ENCODE"] = "http://127.0.0.1:1/unused"

    def tearDown(self):
        import os
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)
        if self._old_encode is None:
            os.environ.pop("VINC_EMBED_ENCODE", None)
        else:
            os.environ["VINC_EMBED_ENCODE"] = self._old_encode

    def _patch(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    # ── 계약 1 ────────────────────────────────────────────────────────────
    def test_it_reports_where_the_time_went(self):
        """되돌리면 빨간 이유: 시계를 떼면 `timing_ms` 키가 사라진다.

        이 도구는 프로덕션 경로를 도는 유일한 도구인데 `perf_counter` 가 0건이었다.
        구간 분해를 못 하는 계기로는 "무엇이 느린가"에 답할 수 없다.
        """
        rc, out, _ = _run_eval_search(self._patch)
        self.assertEqual(rc, 0)
        self.assertIn("timing_ms", out, "구간 시계가 없다")
        for leg in ("enc", "search_fts", "ann", "rrf_total"):
            self.assertIn(leg, out["timing_ms"], f"{leg} 구간이 없다")
            for k in ("n", "p50", "p95"):
                self.assertIn(k, out["timing_ms"][leg])
        self.assertGreater(out["timing_ms"]["rrf_total"]["n"], 0)

    # ── 계약 2 ────────────────────────────────────────────────────────────
    def test_the_vector_arm_is_called_with_production_parameters(self):
        """되돌리면 빨간 이유: `(top_k, None)` 로 부르면 인자가 어긋난다.

        프로덕션 `api/query.py::_chunk_arms` 는 후보 풀 상한
        :data:`api.query.SEARCH_POOL_CAP` 과 owner 필터로 부른다. 평가가 `top_k` 와
        `None` 로 부르면 **다른 일을 재고**, 그 값을 ANN 몫으로 인용하면 프로덕션의
        일부만 그린다. 리터럴 200 이 아니라 상수에 묶는 이유는, 프로덕션이 상한을
        바꾸면 이 테스트가 평가의 드리프트를 알려야 하기 때문이다.
        """
        _rc, _out, spy = _run_eval_search(self._patch)
        self.assertTrue(spy.vector_calls, "벡터 팔이 아예 안 불렸다")
        # 첫 호출은 워밍업이다(확장 LOAD·HNSW 첫 접촉을 표본에서 빼기 위한 것).
        # 워밍업도 같은 인자여야 한다 — 다른 인자로 워밍업하면 다른 경로를 덥힌다.
        for i, (_dim, top_k, filters) in enumerate(spy.vector_calls):
            self.assertEqual(top_k, q.SEARCH_POOL_CAP,
                             f"{i}번째 호출이 후보 풀 상한이 아니라 {top_k} 로 불렸다")
            self.assertEqual(filters, {"owner_id": "eval"},
                             f"{i}번째 호출에 owner 필터가 없다: {filters!r}")

    # ── 계약 3 ────────────────────────────────────────────────────────────
    def test_a_failed_vector_query_is_counted_not_erased(self):
        """되돌리면 빨간 이유: `except: pass` 는 실패한 질의를 분모에서 지운다.

        남은 질의만으로 계산된 평균이 **정상 모양의 표**로 나오는 것이 생존편향이고,
        이 리포가 반복해 물린 모양이다. 실패는 세어져야 하고, 세어진 수가 출력에
        남아야 한다.
        """
        # 평가의 직접 호출은 워밍업 1 + 질의 2 = 3회다(계약 6이 그 수를 지킨다).
        # 3번째부터 죽이면 두 번째 질의의 벡터 조건만 떨어진다.
        _rc, out, spy = _run_eval_search(self._patch, fail_direct_from=3)
        self.assertIn("vector_dropped", out, "떨어진 질의를 세는 자리가 없다")
        self.assertEqual(out["vector_dropped"], 1,
                         f"직접 호출 {len(spy.vector_calls)}회 중 하나가 떨어져야 한다")
        # 분모 대조: rrf 는 두 질의 다 돌았고 vector 는 하나만 성공했다.
        self.assertEqual(out["overall"]["rrf"]["n"], 2)
        self.assertEqual(out["overall"]["vector"]["n"], 1)

    # ── 계약 4 ────────────────────────────────────────────────────────────
    def test_a_failing_rrf_condition_stops_the_measurement(self):
        """되돌리면 빨간 이유: RRF 조건에 `error` 검사가 없으면 조용히 기록된다.

        전에는 `error` 검사가 FTS 호출에만 있었다. 벡터 팔이 죽은 창에서 나온
        **FTS 단독 결과가 "rrf" 라는 이름으로** 표에 들어가고, 그 표는 정상으로
        보인다. 검색이 나쁜 것과 검색이 죽은 것은 다른 사실이다.
        """
        real = q.search

        def hook(*a, **kw):
            if kw.get("query_embedding") is not None:
                return {"kind": "search", "error": "index_missing", "rows": []}
            return real(*a, **kw)

        with self.assertRaises(SystemExit) as cm:
            _run_eval_search(self._patch, search_hook=hook)
        self.assertIn("RRF", str(cm.exception))

    # ── 계약 5 ────────────────────────────────────────────────────────────
    def test_a_degraded_window_is_visible_in_the_output(self):
        """되돌리면 빨간 이유: `arms_census` 가 없으면 강등이 결과에서 안 보인다.

        라이브에서 벡터 팔이 붙지 않은 검색은 **정상 모양으로** 돌아온다. 그 상태로
        측정하면 "벡터를 쓴 수치"라고 이름 붙인 FTS 수치가 나온다. 어느 팔이 실제로
        붙었는지가 출력에 있어야 그 셀을 버릴 수 있다.
        """
        real = q.search

        def hook(*a, **kw):
            res = real(*a, **kw)
            if kw.get("query_embedding") is not None:
                res = {**res, "arms": "fts",
                       "vector_note": "TimeoutError: 의도된 강등"}
            return res

        _rc, out, _spy = _run_eval_search(self._patch, search_hook=hook)
        self.assertIn("arms_census", out, "어느 팔이 붙었는지를 세는 자리가 없다")
        keys = list(out["arms_census"])
        self.assertTrue(any(k.startswith("fts|") and "Timeout" in k for k in keys),
                        f"강등이 census 에 안 남았다: {keys!r}")

    # ── 계약 6 ────────────────────────────────────────────────────────────
    def test_the_first_query_is_not_in_the_sample(self):
        """되돌리면 빨간 이유: 워밍업을 지우면 직접 호출이 질의 수만큼만 남는다.

        확장 LOAD 와 HNSW 첫 접촉이 첫 질의에 통째로 실린다(실측 621ms 대 8.1ms).
        그것을 표본에 넣으면 **첫 질의 하나가 p95 를 혼자 정한다.** 그래서 루프
        앞에서 한 번 버리고 돌린다.

        계약 3이 이 호출 수에 의존하므로 여기서 따로 못박는다.
        """
        _rc, out, spy = _run_eval_search(self._patch)
        n_queries = out["overall"]["rrf"]["n"]
        self.assertEqual(len(spy.vector_calls), n_queries + 1,
                         "워밍업 호출이 없다 — 첫 질의의 색인 적재가 표본에 섞인다")
        # 그리고 워밍업은 표본에 들어가지 않아야 한다.
        self.assertEqual(out["timing_ms"]["ann"]["n"], n_queries,
                         "워밍업이 ann 표본에 섞였다")


class _FakeEncoder:
    """첫 호출만 느린 인코더 — 모델 적재의 형상이다.

    ``clock`` 을 주면 자지 않고 그 가상 시계를 첫 호출 시간만큼 민다.
    """

    def __init__(self, dim, first_call_s=0.45, clock=None):
        self.dim, self.first_call_s, self.calls, self.clock = dim, first_call_s, 0, clock

    def encode(self, texts, kind="passage"):
        self.calls += 1
        if self.calls == 1:
            if self.clock is not None:
                self.clock.now += self.first_call_s
            else:
                time.sleep(self.first_call_s)
        return [[0.01] * self.dim for _ in texts]


class _VirtualClock:
    """인코더가 민 만큼만 가는 시계. 스토어 쓰기처럼 기계 속도에 매인 시간은 0 으로 센다."""

    def __init__(self):
        self.now = 0.0

    def perf_counter(self):
        return self.now


class _TimeWithClock:
    """`time` 모듈 대신 끼운다. `perf_counter` 만 가상 시계이고 나머지는 진짜 `time` 이다."""

    def __init__(self, clock):
        self._clock = clock

    def __getattr__(self, name):
        return self._clock.perf_counter if name == "perf_counter" else getattr(time, name)


class ReindexTimerContract(unittest.TestCase):
    """`scripts/rehearse_rebuild.py::reindex` — 처리량을 내는 자리."""

    def setUp(self):
        self._undo: list[tuple] = []

    def tearDown(self):
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)

    def _patch(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def _seeded(self, docs=3):
        cfg = load_config({"data_dir": tempfile.mkdtemp() + "/db"})
        st = open_store(cfg)
        st.provision(dim=cfg.embedding_dim)
        for i in range(docs):
            ds.put_doc(st, owner="eval", text=f"chunk body number {i} " * 20,
                       doc_id=f"d/{i}", title=f"d/{i}", link=False)
        st.close()
        return cfg.data_dir, cfg.embedding_dim

    # ── 계약 6 ────────────────────────────────────────────────────────────
    def test_model_load_is_reported_separately_and_excluded_from_throughput(self):
        """되돌리면 빨간 이유(둘).

        ① 워밍업 줄을 지우면 `load_s` 키가 사라진다.
        ② 워밍업을 `t0` **안으로** 되돌리면 첫 호출의 0.45초가 `encode_s` 에 섞여
           들어가 아래 상한을 넘는다.

        왜 중요한가: `Encoder` 는 지연 로드라 ONNX 세션 빌드와 아티팩트 읽기(266MB ·
        543MB)가 타이머 안에서 일어났다. 고정비가 **모델마다 달라서** 절대치가 아니라
        모델 간 비율까지 흔든다.
        """
        import rehearse_rebuild as rr

        data, dim = self._seeded()
        # 벽시계로 재면 느린 러너에서는 스토어 쓰기만으로 아래 상한을 넘는다.
        # 그래서 인코더가 민 시간만 가는 시계로 잰다.
        clock = _VirtualClock()
        fake = _FakeEncoder(dim, first_call_s=0.45, clock=clock)
        self._patch(rr, "encoder_for", lambda model, onnx=None: fake)
        self._patch(rr, "time", _TimeWithClock(clock))

        out = rr.reindex(data, "e5")

        self.assertIn("load_s", out, "적재 비용을 따로 내는 자리가 없다")
        self.assertGreaterEqual(out["load_s"], 0.4,
                                "적재 비용이 load_s 에 안 잡혔다 — 워밍업이 없는 것이다")
        self.assertLess(out["encode_s"], 0.4,
                        f"적재 0.45초가 encode_s({out['encode_s']}) 에 섞였다 "
                        "— 워밍업이 타이머 안에 있다")
        self.assertGreater(out["chunks_embedded"], 0, "아무것도 인코딩하지 않았다")

    # ── 계약 7 ────────────────────────────────────────────────────────────
    def test_a_killed_run_still_reports_throughput_and_peak(self):
        """되돌리면 빨간 이유: `kill_after` 분기가 두 값을 계산 전에 return 했다.

        `--kill-after` 로 재도록 설계된 배터리가 답한다고 적은 값(`chunks_per_s` ·
        `peak_rss_mb`)이 실제로는 안 나오는 상태였다. 잘린 런도 그 둘을 내야 한다.
        """
        import rehearse_rebuild as rr

        data, dim = self._seeded(docs=4)
        self._patch(rr, "encoder_for",
                    lambda model, onnx=None: _FakeEncoder(dim, first_call_s=0.0))

        out = rr.reindex(data, "e5", kill_after=1)

        self.assertIn("killed_at_chunks", out)
        for k in ("chunks_per_s", "peak_rss_mb", "load_s"):
            self.assertIn(k, out, f"잘린 런이 {k} 를 안 낸다")
        self.assertGreater(out["chunks_per_s"], 0)
        # peak_rss_mb 의 계약은 test_peak_rss_never_lies.py 가 지킨다: 0.0 은 금지다.
        self.assertNotEqual(out["peak_rss_mb"], 0.0)


if __name__ == "__main__":
    unittest.main()
