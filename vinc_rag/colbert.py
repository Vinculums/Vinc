"""ColBERT 토큰 벡터 — 노드 후보 재채점(VIN-845)의 계산 전부. numpy 만 쓴다.

한 곳에 모은 이유: 인코더(`vinc_rag/embed/worker.py`, 토큰을 만들어 pq64 로 줄인다)와
사이드카(`vinc_rag/retrieval.py`, 코드를 풀어 MaxSim 을 잰다)가 **같은 투영과 같은
코드북**을 써야 한다. 둘이 따로 구현하면 에러 없이 점수만 어긋난다.

계산은 연구 코드(`vinc-rag-assets` `results/cross-language-2026-10-28/node-set/bundle-fix/colbert.py`)
와 같아야 한다 — 그 점수로 brief 규칙의 상수를 맞췄기 때문이다:
  last_hidden_state 에서 CLS(0번)를 버리고 나머지 전부(끝의 </s> 포함) → colbert_linear
  (h @ W.T + b) → 행마다 L2 정규화. 문서 쪽은 pq64(16차원 부분공간 64개 × 중심 256개,
  부분공간마다 1바이트)로 저장하고, 풀 때 다시 L2 정규화한다. 질의 쪽은 압축하지 않는다.
  점수 = 질의 토큰마다 가장 가까운 문서 토큰과의 내적, 그 평균(MaxSim).
근거: [[vinc.plan.vin845-colbert-node-rescoring-implementation-2026-10-30]]
"""
from __future__ import annotations

import base64
import hashlib
import os
import pickle
import threading
import zipfile

import numpy as np

DIM = 1024
SUBSPACES = 64
CENTROIDS = 256
SUB_DIM = DIM // SUBSPACES

#: BAAI/bge-m3 의 colbert_linear 가 고정된 판. 파일 해시가 다르면 올리지 않는다 —
#: 다른 투영으로 만든 토큰은 기존 코드북·규칙 상수와 맞지 않는다.
LINEAR_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
LINEAR_SHA256 = "19bfbae397c2b7524158c919d0e9b19393c5639d098f0a66932c91ed8f5f9abb"


class _TorchState(pickle.Unpickler):
    """torch.save 의 zip 형식을 torch 없이 읽는다(허브 이미지에는 torch 가 없다).

    받는 것은 `OrderedDict` 와 `_rebuild_tensor_v2` 와 저장소 타입 둘뿐이다. 그 밖의
    전역은 거절한다 — 피클은 임의 코드를 부를 수 있으니 열린 목록으로 두지 않는다."""

    _STORAGE = {"HalfStorage": np.float16, "FloatStorage": np.float32}

    def __init__(self, fh, zf: zipfile.ZipFile, root: str):
        super().__init__(fh)
        self._zf, self._root = zf, root

    def find_class(self, module, name):
        if (module, name) == ("collections", "OrderedDict"):
            import collections
            return collections.OrderedDict
        if (module, name) == ("torch._utils", "_rebuild_tensor_v2"):
            return self._rebuild
        if module == "torch" and name in self._STORAGE:
            return self._STORAGE[name]
        raise pickle.UnpicklingError(f"허용하지 않은 전역: {module}.{name}")

    def persistent_load(self, pid):
        _tag, dtype, key, _loc, numel = pid
        raw = self._zf.read(f"{self._root}/data/{key}")
        arr = np.frombuffer(raw, dtype=np.dtype(dtype).newbyteorder("<"))
        if arr.size != numel:
            raise ValueError(f"저장소 {key} 크기가 어긋난다: {arr.size} != {numel}")
        return arr

    @staticmethod
    def _rebuild(storage, offset, size, stride, *_rest):
        n = int(np.prod(size)) if size else 1
        flat = storage[offset:offset + n]
        if tuple(stride) != tuple(int(np.prod(size[i + 1:])) for i in range(len(size))):
            raise ValueError("연속 배치가 아닌 텐서는 지원하지 않는다")
        return flat.reshape(size)


def load_linear(path: str) -> tuple[np.ndarray, np.ndarray]:
    """colbert_linear.pt → (W[out,in], b[out]) float32. 해시가 고정값과 다르면 거절한다."""
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    if digest != LINEAR_SHA256:
        raise ValueError(f"colbert_linear 해시가 고정값과 다르다: {digest}")
    with zipfile.ZipFile(path) as zf:
        pkl = next(n for n in zf.namelist() if n.endswith("/data.pkl"))
        root = pkl.rsplit("/", 1)[0]
        with zf.open(pkl) as fh:
            state = _TorchState(fh, zf, root).load()
    w = np.asarray(state["weight"], dtype=np.float32)
    b = np.asarray(state["bias"], dtype=np.float32)
    if w.shape != (DIM, DIM) or b.shape != (DIM,):
        raise ValueError(f"colbert_linear 모양이 어긋난다: {w.shape} {b.shape}")
    return w, b


def token_text(title: str, summary: str = "", one_liner: str = "", props=None) -> str:
    """노드의 ColBERT 입력. 제목 → 요약 → 한 줄 설명 → props 안의 모든 문자열(깊이 우선),
    앞뒤 공백을 걷고 같은 줄은 한 번만, 줄바꿈으로 잇는다. 512토큰 자르기는 토크나이저가 한다.

    ⚠ **dense 미러 텍스트(`store/node_mirror_state.py`)와 다르다.** 미러는 일부러 좁고, 재채점의
    근거는 이 넓은 텍스트로 쟀다. 순서나 고르는 필드를 바꾸면 규칙 상수가 맞춰진 점수가 바뀐다.
    근거: [[vinc.plan.vin845-colbert-node-rescoring-implementation-2026-10-30]]"""
    parts = [title or "", summary or "", one_liner or ""]

    def walk(x):
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(props or {})
    seen, keep = set(), []
    for s in parts:
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            keep.append(s)
    return "\n".join(keep)


def books_id(books: np.ndarray) -> str:
    """코드북의 정체 — 저장된 코드가 어느 코드북으로 만들어졌는지 대조하는 값."""
    return hashlib.sha256(np.ascontiguousarray(books, dtype=np.float32).tobytes()).hexdigest()[:16]


class Colbert:
    """투영 + 코드북. 인코더와 사이드카가 같은 파일 둘에서 같은 것을 만든다."""

    def __init__(self, linear_path: str, books_path: str):
        self.w, self.b = load_linear(linear_path)
        self.wt = np.ascontiguousarray(self.w.T)
        books = np.load(books_path).astype(np.float32)
        if books.shape != (SUBSPACES, CENTROIDS, SUB_DIM):
            raise ValueError(f"코드북 모양이 어긋난다: {books.shape}")
        self.books = books
        self.books_sq = (books ** 2).sum(axis=2)            # [64, 256]
        self.id = books_id(books)

    def project(self, hidden: np.ndarray) -> np.ndarray:
        """한 텍스트의 last_hidden_state[n, 1024](패딩 제외, CLS 포함) → 토큰 벡터[n-1, 1024]."""
        v = hidden[1:].astype(np.float32) @ self.wt + self.b
        norm = np.linalg.norm(v, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return (v / norm).astype(np.float32)

    def encode_codes(self, vecs: np.ndarray) -> np.ndarray:
        """토큰 벡터[n, 1024] → pq64 코드[n, 64] uint8."""
        codes = np.empty((len(vecs), SUBSPACES), dtype=np.uint8)
        for s in range(SUBSPACES):
            sub = vecs[:, s * SUB_DIM:(s + 1) * SUB_DIM]
            codes[:, s] = np.argmin(self.books_sq[s][None] - 2 * sub @ self.books[s].T, axis=1)
        return codes

    def decode(self, codes: np.ndarray) -> np.ndarray:
        """pq64 코드[n, 64] → 토큰 벡터[n, 1024], 다시 L2 정규화(연구 코드와 같다)."""
        out = np.empty((len(codes), DIM), dtype=np.float32)
        for s in range(SUBSPACES):
            out[:, s * SUB_DIM:(s + 1) * SUB_DIM] = self.books[s][codes[:, s]]
        norm = np.linalg.norm(out, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return out / norm


def maxsim(q: np.ndarray, d: np.ndarray) -> float:
    """질의 토큰마다 가장 가까운 문서 토큰과의 내적, 그 평균."""
    if not len(q) or not len(d):
        return float("-inf")
    return float((q @ d.T).max(axis=1).mean())


def pack_codes(codes: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(codes, dtype=np.uint8).tobytes()).decode("ascii")


def unpack_codes(text: str) -> np.ndarray:
    raw = np.frombuffer(base64.b64decode(text), dtype=np.uint8)
    if raw.size % SUBSPACES:
        raise ValueError("pq64 코드 길이가 64의 배수가 아니다")
    return raw.reshape(-1, SUBSPACES)


def pack_query(vecs: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(vecs, dtype="<f4").tobytes()).decode("ascii")


def unpack_query(text: str) -> np.ndarray:
    raw = np.frombuffer(base64.b64decode(text), dtype="<f4")
    if raw.size % DIM:
        raise ValueError("질의 토큰 길이가 1024의 배수가 아니다")
    return raw.reshape(-1, DIM).astype(np.float32)


def from_env() -> Colbert | None:
    """`VINC_COLBERT_LINEAR`·`VINC_COLBERT_BOOKS` 가 둘 다 있을 때만 올린다. 없으면 None —
    부르는 쪽은 재채점 없이 기존 순서를 쓴다(데스크탑에는 인코더도 이 파일도 없다)."""
    lp = os.environ.get("VINC_COLBERT_LINEAR", "").strip()
    bp = os.environ.get("VINC_COLBERT_BOOKS", "").strip()
    if not lp or not bp:
        return None
    return Colbert(lp, bp)


class _Cache:
    lock = threading.Lock()
    tried = False
    value: "Colbert | None" = None


def cached() -> Colbert | None:
    """:func:`from_env` 를 프로세스당 한 번만 올린다(가중치 4MB·코드북 1MB). 실패하면 None 을 기억하고
    사유를 한 줄 남긴다 — 재채점과 토큰 채우기가 함께 꺼진다."""
    with _Cache.lock:
        if not _Cache.tried:
            _Cache.tried = True
            try:
                _Cache.value = from_env()
            except Exception as exc:
                import sys
                print(f"vinc: colbert 적재 실패 — {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return _Cache.value


def rescore_on() -> bool:
    """`VINC_NODE_COLBERT` — 노드 후보 재채점 스위치. 기본 꺼짐: 토큰이 미러 전체에 찰 때까지 켜지 않는다."""
    from store.envflag import truthy
    return truthy(os.environ.get("VINC_NODE_COLBERT"))
