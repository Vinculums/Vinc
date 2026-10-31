"""vinc_rag/colbert.py — torch 없는 가중치 읽기, pq64 왕복, MaxSim, 토큰 텍스트."""
import io
import pickle
import sys
import types
import zipfile

import pytest

np = pytest.importorskip("numpy")  # 네임스페이스 잡에는 numpy 가 없다

from vinc_rag import colbert as cb


def _fake_torch_zip(w: np.ndarray, b: np.ndarray) -> bytes:
    """torch.save 가 쓰는 zip 모양(OrderedDict + _rebuild_tensor_v2 + HalfStorage)을 torch 없이 만든다."""
    mods = {"torch": types.ModuleType("torch"), "torch._utils": types.ModuleType("torch._utils")}

    class HalfStorage:
        pass

    def _rebuild_tensor_v2(*_a):
        pass

    HalfStorage.__module__, HalfStorage.__qualname__ = "torch", "HalfStorage"
    _rebuild_tensor_v2.__module__, _rebuild_tensor_v2.__qualname__ = "torch._utils", "_rebuild_tensor_v2"
    mods["torch"].HalfStorage = HalfStorage
    mods["torch._utils"]._rebuild_tensor_v2 = _rebuild_tensor_v2

    class Ref:
        def __init__(self, key, numel):
            self.key, self.numel = key, numel

    class T:
        def __init__(self, ref, size):
            self.ref, self.size = ref, size

        def __reduce__(self):
            stride = tuple(int(np.prod(self.size[i + 1:])) for i in range(len(self.size)))
            return (_rebuild_tensor_v2, (self.ref, 0, self.size, stride, False, None))

    class P(pickle.Pickler):
        def persistent_id(self, obj):
            if isinstance(obj, Ref):
                return ("storage", HalfStorage, obj.key, "cpu", obj.numel)
            return None

    import collections
    state = collections.OrderedDict([("weight", T(Ref("0", w.size), w.shape)), ("bias", T(Ref("1", b.size), b.shape))])
    saved = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    try:
        buf = io.BytesIO()
        P(buf, protocol=2).dump(state)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("colbert_linear/data.pkl", buf.getvalue())
        zf.writestr("colbert_linear/data/0", w.astype("<f2").tobytes())
        zf.writestr("colbert_linear/data/1", b.astype("<f2").tobytes())
    return out.getvalue()


def test_load_linear_reads_half_storage_without_torch(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    w = rng.standard_normal((cb.DIM, cb.DIM)).astype(np.float16)
    b = rng.standard_normal(cb.DIM).astype(np.float16)
    p = tmp_path / "colbert_linear.pt"
    p.write_bytes(_fake_torch_zip(w, b))
    with pytest.raises(ValueError, match="해시"):
        cb.load_linear(str(p))                                  # 고정 해시가 아니면 거절
    import hashlib
    monkeypatch.setattr(cb, "LINEAR_SHA256", hashlib.sha256(p.read_bytes()).hexdigest())
    gw, gb = cb.load_linear(str(p))
    assert gw.dtype == np.float32 and np.array_equal(gw, w.astype(np.float32))
    assert np.array_equal(gb, b.astype(np.float32))


def test_unpickler_refuses_other_globals(tmp_path):
    payload = pickle.dumps(print, protocol=2)                   # builtins.print
    with zipfile.ZipFile(tmp_path / "x.pt", "w") as zf:
        zf.writestr("x/data.pkl", payload)
    with zipfile.ZipFile(tmp_path / "x.pt") as zf, zf.open("x/data.pkl") as fh:
        with pytest.raises(pickle.UnpicklingError):
            cb._TorchState(fh, zf, "x").load()


def _colbert(tmp_path, monkeypatch, seed=1):
    rng = np.random.default_rng(seed)
    books = rng.standard_normal((cb.SUBSPACES, cb.CENTROIDS, cb.SUB_DIM)).astype(np.float32)
    np.save(tmp_path / "books.npy", books)
    monkeypatch.setattr(cb, "load_linear", lambda _p: (np.eye(cb.DIM, dtype=np.float32), np.zeros(cb.DIM, np.float32)))
    return cb.Colbert("unused", str(tmp_path / "books.npy")), books


def test_pq_round_trip_returns_the_nearest_centroids(tmp_path, monkeypatch):
    c, books = _colbert(tmp_path, monkeypatch)
    pick = np.random.default_rng(2).integers(0, cb.CENTROIDS, size=(5, cb.SUBSPACES))
    exact = np.concatenate([books[s][pick[:, s]] for s in range(cb.SUBSPACES)], axis=1)
    codes = c.encode_codes(exact)
    assert np.array_equal(codes, pick.astype(np.uint8))
    back = c.decode(cb.unpack_codes(cb.pack_codes(codes)))
    assert np.allclose(back, exact / np.linalg.norm(exact, axis=1, keepdims=True), atol=1e-6)


def test_project_drops_cls_and_normalises(tmp_path, monkeypatch):
    c, _ = _colbert(tmp_path, monkeypatch)
    h = np.random.default_rng(3).standard_normal((7, cb.DIM)).astype(np.float32)
    v = c.project(h)
    assert v.shape == (6, cb.DIM)
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5)
    assert np.allclose(v[0], h[1] / np.linalg.norm(h[1]), atol=1e-5)


def test_maxsim_and_query_packing():
    q = np.eye(4, cb.DIM, dtype=np.float32)
    d = np.eye(4, cb.DIM, dtype=np.float32)[[0, 1]]
    assert cb.maxsim(q, d) == pytest.approx(0.5)               # 두 토큰은 1, 두 토큰은 0
    assert cb.maxsim(q, np.zeros((0, cb.DIM), np.float32)) == float("-inf")
    assert np.array_equal(cb.unpack_query(cb.pack_query(q)), q)


def test_token_text_order_and_dedup():
    t = cb.token_text("Title", "Summary", "One", {"claim": "A", "nested": {"x": ["B", "A"], "n": 3}, "s": " Title "})
    assert t == "Title\nSummary\nOne\nA\nB"


def test_encode_service_colbert_payload(tmp_path, monkeypatch):
    """`/encode` 의 colbert 갈래: 질의는 f32, 문서는 pq64. 설정이 없으면 dense 만 주고 사유를 남긴다."""
    from vinc_rag.embed import worker as w
    c, _ = _colbert(tmp_path, monkeypatch)

    class Emb:
        def encode(self, texts, query=False):
            return [[1.0, 0.0] for _ in texts]

        def encode_with_hidden(self, texts, query=False):
            h = np.random.default_rng(4).standard_normal((5, cb.DIM)).astype(np.float32)
            return [[1.0, 0.0] for _ in texts], [h for _ in texts]

    monkeypatch.setattr(w, "_COLBERT", type("S", (), {"lock": w.threading.Lock(), "tried": True, "value": None})())
    vecs, extra = w._encode_colbert(Emb(), ["a"], query=True)
    assert vecs == [[1.0, 0.0]] and extra == {"colbert_error": "colbert not configured"}

    w._COLBERT.value = c
    _, q = w._encode_colbert(Emb(), ["a"], query=True)
    assert q["colbert_format"] == "f32" and cb.unpack_query(q["colbert"][0]).shape == (4, cb.DIM)
    _, d = w._encode_colbert(Emb(), ["a", "b"], query=False)
    assert d["colbert_format"] == "pq64" and d["colbert_books"] == c.id
    assert [cb.unpack_codes(x).shape for x in d["colbert"]] == [(4, cb.SUBSPACES)] * 2


class _Rows:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def run(self, q, params=None):
        self.calls.append(params)
        return [r for r in self.rows if r["v"] in (params or {}).get("v", [])]


def test_rescore_reorders_the_head_and_skips_when_any_token_is_missing(tmp_path, monkeypatch):
    from vinc_rag import retrieval as R
    c, books = _colbert(tmp_path, monkeypatch)
    monkeypatch.setattr(cb, "cached", lambda: c)
    codes = {i: np.random.default_rng(10 + i).integers(0, cb.CENTROIDS, (6, cb.SUBSPACES)).astype(np.uint8)
             for i in range(3)}
    q = c.decode(codes[2])[:3]                              # 질의는 2번 노드의 토큰 일부 → 2번이 1위
    rows = [{"v": f"o#n{i}", "c": cb.pack_codes(codes[i]), "b": c.id} for i in range(3)]
    hits = [{"id": f"n{i}"} for i in range(3)] + [{"id": "tail"}]
    out, st = R._colbert_rescore(_Rows(rows), [dict(h) for h in hits], cb.pack_query(q), "o")
    # tail 은 토큰이 없으니 상위 20 안에 든 이상 통째로 건너뛴다
    assert st == {"state": "skipped", "reason": "tokens missing", "missing": 1}
    assert [h["id"] for h in out] == ["n0", "n1", "n2", "tail"]
    out, st = R._colbert_rescore(_Rows(rows), [dict(h) for h in hits[:3]], cb.pack_query(q), "o")
    assert st == {"state": "applied", "n": 3} and out[0]["id"] == "n2" and out[0]["colbert"] == pytest.approx(1.0, abs=1e-5)
    other = [{**r, "b": "other"} for r in rows]
    _, st = R._colbert_rescore(_Rows(other), [dict(h) for h in hits[:3]], cb.pack_query(q), "o")
    assert st["state"] == "skipped"


def test_rescore_keeps_production_order_on_ties(tmp_path, monkeypatch):
    from vinc_rag import retrieval as R
    c, _ = _colbert(tmp_path, monkeypatch)
    monkeypatch.setattr(cb, "cached", lambda: c)
    k = np.random.default_rng(5).integers(0, cb.CENTROIDS, (4, cb.SUBSPACES)).astype(np.uint8)
    rows = [{"v": f"o#n{i}", "c": cb.pack_codes(k), "b": c.id} for i in range(3)]
    out, st = R._colbert_rescore(_Rows(rows), [{"id": f"n{i}"} for i in range(3)], cb.pack_query(c.decode(k)[:2]), "o")
    assert st["state"] == "applied" and [h["id"] for h in out] == ["n0", "n1", "n2"]
