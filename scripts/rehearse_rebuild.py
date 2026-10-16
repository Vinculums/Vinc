#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M3 재구축 리허설 — 데스크탑 스토어 하나를 실제로 재구축해 보고 값을 남긴다.

**이것은 결정을 내리는 도구가 아니라 결정의 재료를 만드는 도구다.** 묻는 것 하나:
전 사용자 벡터 스토어 재구축(e5 768 → bge-m3 1024)이 안전한가, 안 그러면 무엇이 막나.

🔴 **왜 `switch_embed_model` 로는 안 되나.** 그 함수는 옛 벡터를 지우고 전 문서를
다시 큐에 넣지만 **벡터 열의 차원은 손대지 않는다**. 차원은 `CREATE NODE TABLE
ChunkVec(... embedding FLOAT[768] ...)` 로 박히고, `provision()`→`migrate()` 는
`_exec_idem` 이 'already exists' 를 삼키므로 **1024 로 다시 불러도 조용히 아무 일도
안 한다**(실측: 열이 FLOAT[768] 그대로). 그 뒤 1024 벡터를 쓰면 그때서야
``Unsupported casting LIST with incorrect list entry to ARRAY. Expected: 768,
Actual: 1024`` 가 난다 — **스토어를 이미 비운 다음에.** 그래서 차원이 바뀌는
모델 교체에는 재구축 경로가 따로 필요하다.

그 경로의 정본은 **`store.migrate.rebuild_store`** 다(제품 코드). 이 파일은 그것을
불러서 **재기만** 한다 — 리허설이 자기만의 최소 경로를 들고 있으면 재는 대상이
프로덕션이 아니게 되고, 실제로 그래서 메타 이관이 리허설에만 있고 제품에는 없는
상태가 한동안 유지됐다.

단계(발주자 지정):
    seed → rebuild → reindex → eval(전/후) → rollback → concurrent

사용:
    # 준비(모델 둘 다 로컬에 있어야 한다 — 네트워크 없이 도는 것이 이 리포의 불변식)
    VINC_EVAL_ROOT=... python scripts/rehearse_rebuild.py seed --data D --model e5 --docs 20
    python scripts/rehearse_rebuild.py rebuild --data D --out D2 --model m3
    python scripts/rehearse_rebuild.py reindex --data D2 --model m3
    python scripts/rehearse_rebuild.py eval    --data D2 --model m3
    python scripts/rehearse_rebuild.py concurrent --data D --model m3 --n 3

⚠ **측정 타당성**(W16 이 물린 자리). 수치를 읽기 전에 경합 프로세스를 확인할 것.
그리고 재구축 전후는 인덱스 상태가 다르므로("빨라졌다"가 인덱스 차이일 수 있다)
각 단계는 인덱스 빌드까지 포함한 벽시계를 따로 낸다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import docstore as ds                      # noqa: E402
from store.backend import open_store                # noqa: E402
from store.config import EMBEDDING_DIM_BY_MODEL, load_config   # noqa: E402
from store.migrate import rebuild_store as migrate_rebuild  # noqa: E402

#: 짧은 이름 → HF id. 리허설 인자를 짧게 쓰려는 것뿐이고 차원의 정본은 store.config 다.
MODELS = {"e5": "intfloat/multilingual-e5-base", "m3": "BAAI/bge-m3"}


def _hf(name: str) -> str:
    return MODELS.get(name, name)


def peak_rss_mb() -> float:
    """이 프로세스의 피크 워킹셋(MB). psutil 없으면 Windows API 로 직접 묻는다 —
    측정 도구 하나 때문에 의존성을 늘리지 않는다."""
    try:
        import psutil
        return psutil.Process().memory_info().peak_wset / 1e6   # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        # 🔴 `restype` 을 지정해야 한다. 기본값은 `c_int` 라 64비트에서 의사 핸들
        # (HANDLE)-1 이 32비트로 잘려 0x00000000FFFFFFFF 로 건네지고, 호출이 **실패**
        # 한다. 그런데 반환값을 안 보면 0 으로 채워진 구조체가 그대로 나가서
        # `peak_rss_mb()` 가 **0.0 을 답한다** — "메모리를 안 썼다"로 읽히는 "못 쟀다"다.
        # 실측 2026-09-03: 고치기 전 반환 0 · peak 0.0MB, 고친 뒤 반환 1 · peak 14.3MB.
        # psutil 이 있는 환경에서는 위 분기가 먼저 잡혀서 이 결함이 안 보였다.
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi   # type: ignore[attr-defined]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                               ctypes.POINTER(_PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        c = _PMC()
        c.cb = ctypes.sizeof(_PMC)
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return -1.0
        # `PeakWorkingSetSize` 는 워킹셋이라 시스템 메모리 압력에서 **OS 가 트림한다** —
        # 바쁜 기계가 더 작은 값을 내고, 그 작은 값이 "2GiB 에 들어간다"로 읽히고,
        # 프로덕션에서 SIGKILL 이 난다. 커밋 피크(`PeakPagefileUsage`)와 큰 쪽을 쓴다.
        return max(c.PeakWorkingSetSize, c.PeakPagefileUsage) / 1e6
    except Exception:
        return -1.0


def open_at(data_dir: str, model: str):
    """이 모델의 차원으로 스토어를 연다(없으면 만든다)."""
    cfg = load_config({"data_dir": data_dir, "embedding_model": _hf(model)})
    store = open_store(cfg)
    if store is None:
        raise SystemExit("임베디드 스토어를 열 수 없다(드라이버 미설치?)")
    store.provision(dim=cfg.embedding_dim)
    return store, cfg


def encoder_for(model: str, onnx: str | None = None):
    from vinc_rag.embed.encoder import Encoder
    return Encoder(_hf(model), onnx_path=onnx, batch=8)


# ── 1단계: 작은 Desktop store ────────────────────────────────────────────────

def _queries_of(fixtures: list[str]) -> list[dict]:
    rows: list[dict] = []
    for f in fixtures:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        rows.extend(d if isinstance(d, list) else (d.get("queries") or d.get("rows") or []))
    return rows


def _gold_of(fixtures: list[str]) -> set[str]:
    return {q["doc_id"] for q in _queries_of(fixtures) if q.get("doc_id")}


def cmd_seed(a) -> int:
    """실제 코퍼스로 데스크탑 형상의 스토어를 만든다(수기 조작 없음·재현 가능)."""
    from bench_models import load_corpus

    t0 = time.perf_counter()
    store, cfg = open_at(a.data, a.model)
    # 🔴 정답 문서를 **반드시** 코퍼스에 넣는다. 안 그러면 그 질의는 필연 miss 인데
    # 점수는 그냥 낮게 나올 뿐이라 "모델이 나쁘다"로 오독된다(조용한 0점).
    gold = _gold_of(a.fixture)
    texts, owners = load_corpus(max_chars=2000, gold=gold, per_lang_docs=a.docs)
    missing = gold - set(owners)
    if missing:
        raise SystemExit(f"정답 문서 {len(missing)}/{len(gold)}개가 코퍼스에 없다 "
                         f"— 평가가 무효다. 예: {sorted(missing)[:2]}")
    # 같은 doc_id 의 청크를 한 문서로 되모은다 — load_corpus 는 청크 단위로 낸다.
    by_doc: dict[str, list[str]] = {}
    for t, d in zip(texts, owners):
        by_doc.setdefault(d, []).append(t)
    if not by_doc:
        raise SystemExit("코퍼스가 비었다 — VINC_EVAL_ROOT 를 워크스페이스 루트로 줄 것")

    # ⚠ `put_doc(embed=...)` 를 **쓰지 않는다.** 그 경로는 벡터를 v4 레거시 열
    # `Chunk.embedding` 에 쓰고 `needs_embed=0` 으로 내린다. 그런데 v8 이후 검색이
    # 읽는 것은 `ChunkVec` 이고 그 표는 비어 있으므로, 결과는 **벡터 검색이 빈 채로
    # 다시 채워질 일도 없는 스토어**다(실측: docs 36 · chunks 283 · vecs 0 · pending 0).
    # 프로덕션에서 벡터를 만드는 유일한 경로는 워커의 apply_embeddings 다 — 그래서
    # 여기서는 needs_embed=1 로 남기고 `reindex` 가 채운다(= 데스크탑의 실제 순서).
    docs = 0
    for doc_id, parts in sorted(by_doc.items()):
        ds.put_doc(store, owner=a.owner, text="\n\n".join(parts), doc_id=doc_id,
                   title=doc_id, link=False)
        docs += 1
    # 모델 식별자를 기록해 둔다 — 이게 없으면 다음 기동이 "바뀌었다"고 판정한다.
    ds.switch_embed_model(store, _hf(a.model))
    st = _stats(store)
    store.close()
    st.update(phase="seed", model=_hf(a.model), dim=cfg.embedding_dim, docs=docs,
              wall_s=round(time.perf_counter() - t0, 2), peak_rss_mb=round(peak_rss_mb(), 1))
    print(json.dumps(st, ensure_ascii=False))
    return 0


def _stats(store) -> dict:
    def one(q):
        try:
            r = store.run(q)
            return int(r[0]["c"]) if r else 0
        except Exception:
            return -1
    return {"docs": one("MATCH (d:_DocMeta) RETURN count(d) AS c"),
            "chunks": one("MATCH (c:Chunk) RETURN count(c) AS c"),
            "vecs": one("MATCH (v:ChunkVec) RETURN count(v) AS c"),
            "pending": one("MATCH (d:_DocMeta) WHERE d.needs_embed = 1 RETURN count(d) AS c"),
            "vec_dim": _vec_dim(store)}


def _vec_dim(store) -> int:
    try:
        for r in store.run("CALL TABLE_INFO('ChunkVec') RETURN *"):
            if r.get("name") == "embedding":
                return int(str(r.get("type")).split("[")[1].rstrip("]"))
    except Exception:
        pass
    return -1


# ── 2단계: 실제 rebuild(차원이 바뀐다) ───────────────────────────────────────

def rebuild_store(src_dir: str, dst_dir: str, *, model: str) -> dict:
    """디렉터리 두 개를 열어 **제품 경로**(`store.migrate.rebuild_store`)에 맡기고 잰다.

    ⚠ 이 함수는 한때 재구축을 **직접** 했다. 그게 리허설의 결함이었다 — 리허설이
    자기만의 최소 경로를 쓰면 재는 것은 "그 최소 경로"지 프로덕션이 아니다. 실제로
    그때 메타 이관을 여기서 손으로 하고 있었고, 제품 코드는 여전히 `_DocMeta` 를
    잃은 채였다. 지금은 같은 함수를 부르므로 여기서 잰 수치가 곧 제품의 수치다.

    남는 일은 계측뿐이다: export/import/색인 구간을 갈라 재고 전후 형상을 찍는다."""
    out: dict = {"phase": "rebuild", "src": src_dir, "dst": dst_dir, "model": _hf(model)}
    t0 = time.perf_counter()
    if os.path.exists(dst_dir):
        raise SystemExit(f"대상이 이미 있다: {dst_dir}")
    src_cfg = load_config({"data_dir": src_dir})
    src = open_store(src_cfg)
    dst_cfg = load_config({"data_dir": dst_dir, "embedding_model": _hf(model)})
    dst = open_store(dst_cfg)
    if src is None or dst is None:
        raise SystemExit("스토어를 열 수 없다")
    out["before"] = _stats(src)
    try:
        res = migrate_rebuild(src, dst, model=_hf(model), dim=dst_cfg.embedding_dim,
                              requeue=ds.switch_embed_model)
        out["after"] = _stats(dst)
    finally:
        src.close()
        dst.close()
    out.update(res, wall_s=round(time.perf_counter() - t0, 2),
               peak_rss_mb=round(peak_rss_mb(), 1))
    if out["docs"] != out["requeued"]:
        raise SystemExit(f"재큐가 문서 수와 다르다({out['requeued']} != {out['docs']}) "
                         "— 재임베딩이 안 일어난다")
    return out


def retable_vectors(data_dir: str, *, model: str) -> dict:
    """벡터 표만 **제자리에서** 새 차원으로 다시 만든다(DROP + CREATE).

    이 경로가 성립하는 조건은 하나다: **버릴 벡터밖에 없을 것.** 그런데 데스크탑이
    정확히 그 형상이다 — 사이드카 번들이 인코더 스택(onnx·tokenizers·transformers)을
    제외하므로 데스크탑은 벡터를 만들 수단이 없고, 계정 동기화도 텍스트만 가져온다
    (`api/sync.py::pull_docs` — "embedding 은 여기서 재생성하지 않는다"). 그래서
    데스크탑 스토어의 `ChunkVec` 은 구조적으로 늘 비어 있다.

    비어 있으면 export/import 를 할 이유가 없다. 표 하나를 갈아 끼우면 끝이고,
    문서·청크·노드·엣지·메타는 **손도 대지 않으므로** 잃을 것이 없다.
    ⚠ 벡터가 있는 스토어에 쓰면 그 벡터는 사라진다 — 그래서 세는 것을 먼저 한다.
    """
    from store import embedded_schema as _schema

    out: dict = {"phase": "retable", "data": data_dir, "model": _hf(model)}
    t0 = time.perf_counter()
    store, cfg = open_at(data_dir, "e5")     # 현재 차원 그대로 연다(재프로비전 회피)
    before = _stats(store)
    out["before"] = before
    if before["vecs"] > 0:
        out["warning"] = f"벡터 {before['vecs']}건이 사라진다 — 재임베딩이 필요하다"
    try:
        store.drop_vector_index()
    except Exception as exc:                 # 인덱스가 없으면 그만이다
        out["drop_index_note"] = f"{type(exc).__name__}"
    store.run("DROP TABLE ChunkVec")
    dim = EMBEDDING_DIM_BY_MODEL[_hf(model)]
    spec = _schema._NODE_TABLES["ChunkVec"]
    cols = ", ".join(f"{c} {t.replace('{dim}', str(dim))}" for c, t in spec["cols"])
    store.run(f"CREATE NODE TABLE ChunkVec({cols}, PRIMARY KEY({spec['pk']}))")
    sw = ds.switch_embed_model(store, _hf(model))
    after = _stats(store)
    store.close()
    out.update(after=after, requeued=sw.get("requeued"),
               wall_s=round(time.perf_counter() - t0, 3),
               peak_rss_mb=round(peak_rss_mb(), 1))
    # 🔴 **지운 뒤에 재큐가 안 되면 벡터는 영영 안 돌아온다.** `switch_embed_model` 은
    # 저장된 모델 식별자가 그대로면 "바뀐 게 없다"고 보고 재큐를 건너뛰는데, 위에서
    # ChunkVec 은 **이미 DROP 했다.** 그 조합이 남기는 것은 벡터 0 · pending 0 인
    # 스토어다 — 의미 검색이 죽었고, 죽었다는 사실을 아무것도 말하지 않으며, 워커도
    # 할 일이 없다고 판단한다. 실측 2026-09-03: e5 스토어에 `retable --model e5` 를
    # 걸었더니 481벡터가 사라지고 `requeued: null` 만 남았다.
    # `rebuild_store` 에는 같은 가드가 이미 있다(docs != requeued → SystemExit).
    if before["vecs"] > 0 and not after["pending"]:
        raise SystemExit(
            f"벡터 {before['vecs']}건을 지웠는데 재큐가 0이다(requeued={sw.get('requeued')}) "
            f"— 이 스토어는 의미 검색이 죽은 채로 남는다. 같은 모델로 retable 하면 "
            f"`switch_embed_model` 이 재큐를 건너뛴다. 다른 모델로 부르거나 "
            f"`rebuild` 경로를 쓸 것.")
    return out


def cmd_retable(a) -> int:
    print(json.dumps(retable_vectors(a.data, model=a.model), ensure_ascii=False))
    return 0


def cmd_rebuild(a) -> int:
    print(json.dumps(rebuild_store(a.data, a.out, model=a.model), ensure_ascii=False))
    return 0


# ── 3단계: 재색인(전량 재임베딩) ─────────────────────────────────────────────

def reindex(data_dir: str, model: str, *, onnx: str | None = None,
            batch_docs: int = 8, kill_after: int = 0, split: bool = False) -> dict:
    """`needs_embed` 를 비운다 — 허브 워커(`vinc_rag/embed/worker.py`)와 같은 순서:
    pending_embed_chunks → 인코딩 → apply_embeddings. 다른 것은 사이드카 HTTP 를
    거치지 않는 것뿐이라, 여기서 재는 것은 **인코딩 + 스토어 쓰기**의 비용이다."""
    # ⚠ `split=False`(기본) 는 청크를 **통째로 한 벡터**로 만든다. 프로덕션 워커는
    # ≤500 토큰 조각마다 벡터를 만들어 자식으로 펴므로(청크당 2.08개, 라이브 실측)
    # 기본값은 프로덕션 형상이 아니다. 검색 품질을 잴 스토어는 `split=True` 로 만든다.
    # 두 모드의 chunks/s 는 서로 비교 불가다(조각 수가 다르다). 성능 정본 §3 의
    # 처리량은 전부 split=False 로 잰 것이라 기본값을 바꾸지 않는다.
    out: dict = {"phase": "reindex", "model": _hf(model), "split": bool(split)}
    store, cfg = open_at(data_dir, model)
    enc = encoder_for(model, onnx)
    # 🔴 `Encoder` 는 지연 로드다. 이 줄이 없으면 ONNX 세션 빌드와 아티팩트 파일 읽기
    # (e5 279MB · m3 570MB, 이 기계는 D: 가 5400rpm)가 t0 **뒤**의 첫 encode 안에서
    # 일어나 처리량에 섞인다. 실측 고정비 e5 2.40s · m3 2.98s 이고, 짧은 런에서는
    # e5 12.9% · m3 3.9% 를 먹는다 — 오염 크기가 모델마다 달라서 **비율까지 흔든다**
    # (고정비 제거 시 스레드 스케일링이 e5 2.56 → 2.97 로 옮겨 갔다).
    _t = time.perf_counter()
    enc.encode(["warmup"], kind="passage")
    load_s = round(time.perf_counter() - _t, 2)
    t0 = time.perf_counter()
    chunks = docs = vec_rows = 0
    while True:
        pend = ds.pending_embed_chunks(store, limit=batch_docs)
        if not pend:
            break
        by_doc: dict[str, list[dict]] = {}
        for row in pend:
            by_doc.setdefault(row["doc_id"], []).append(row)
        for doc_id, rows in by_doc.items():
            if split:
                pieces_of = [enc.split_for_embed(r["text"]) for r in rows]
                flat = enc.encode([p for ps in pieces_of for p in ps], kind="passage")
                emb: dict = {}
                at = 0
                for r, ps in zip(rows, pieces_of):
                    emb[r["chunk_id"]] = flat[at:at + len(ps)]
                    at += len(ps)
                vec_rows += sum(len(ps) for ps in pieces_of)
            else:
                vecs = enc.encode([r["text"] for r in rows], kind="passage")
                emb = {r["chunk_id"]: v for r, v in zip(rows, vecs)}
                vec_rows += len(rows)
            ds.apply_embeddings(store, doc_id, emb)
            chunks += len(rows)
            docs += 1
            if kill_after and chunks >= kill_after:
                # W14 stall 알람 검증용 — 워커가 중간에 죽는 상황을 그대로 만든다.
                _e = time.perf_counter() - t0
                # 잘린 런도 처리량과 피크를 낸다. 전에는 이 둘을 안 실어서,
                # `--kill-after` 로 재는 배터리가 답한다고 적은 값이 안 나왔다.
                out.update(killed_at_chunks=chunks, load_s=load_s,
                           encode_s=round(_e, 2),
                           chunks_per_s=round(chunks / _e, 3) if _e else 0,
                           peak_rss_mb=round(peak_rss_mb(), 1), **_stats(store))
                store.close()
                return out
    enc_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    store.ensure_search_indexes(dim=cfg.embedding_dim)
    idx_s = time.perf_counter() - t1
    out.update(_stats(store))
    store.close()
    out.update(docs_embedded=docs, chunks_embedded=chunks, vectors_made=vec_rows,
               load_s=load_s,
               encode_s=round(enc_s, 2), index_s=round(idx_s, 2),
               chunks_per_s=round(chunks / enc_s, 2) if enc_s else 0,
               wall_s=round(enc_s + idx_s, 2), peak_rss_mb=round(peak_rss_mb(), 1))
    return out


def cmd_reindex(a) -> int:
    print(json.dumps(reindex(a.data, a.model, onnx=a.onnx, kill_after=a.kill_after,
                             split=a.split),
                     ensure_ascii=False))
    return 0


# ── 4단계: retrieval regression ──────────────────────────────────────────────

def evaluate(data_dir: str, model: str, *, onnx: str | None = None,
             fixture: list[str], top_k: int = 10, limit: int = 0) -> dict:
    """같은 스토어·같은 질의로 재구축 전/후를 댄다.

    ⚠ **절대치를 W10·W16 과 비교하지 말 것.** 코퍼스 규모가 다르면 교란 문서 수가
    달라져 점수가 통째로 옮겨 간다. 여기서 유효한 것은 이 리허설 안의 전/후 차이뿐이다."""
    import math

    rows = _queries_of(fixture)
    if limit:
        rows = rows[:limit]
    store, _ = open_at(data_dir, model)
    enc = encoder_for(model, onnx)
    hits = ndcg = 0.0
    n = 0
    lat: list[float] = []
    for r in rows:
        gold = r.get("doc_id")
        q = r.get("query")
        if not (gold and q):
            continue
        t = time.perf_counter()
        vec = enc.encode([q], kind="query")[0]
        got = store.vector_search(vec, top_k) or []
        lat.append((time.perf_counter() - t) * 1000)
        # 자식 벡터가 여러 개라 doc 단위로 접는다(순위는 최초 등장 위치).
        # ⚠ `vector_search` 는 `doc_id` 를 **주지 않는다** — 돌려주는 키는
        # `id`(=chunk_id) · `text` · `distance` 뿐이다. 처음에 `g.get("doc_id")` 로
        # 읽었다가 전 질의가 miss 로 세어져 recall 0.0 이 나왔다(모델이 아니라 자가
        # 없는 것을 재고 있었다). chunk_id 는 `{doc_id}#{index}` 라 뒤를 자른다.
        seen: list[str] = []
        for g in got:
            cid = g.get("id") or ""
            d = g.get("doc_id") or (cid.rsplit("#", 1)[0] if "#" in cid else cid)
            if d and d not in seen:
                seen.append(d)
        rank = seen.index(gold) + 1 if gold in seen else 0
        hits += 1.0 if rank else 0.0
        ndcg += (1.0 / math.log2(rank + 1)) if rank else 0.0
        n += 1
    store.close()
    lat.sort()
    return {"phase": "eval", "model": _hf(model), "queries": n,
            "recall_at_k": round(hits / n, 4) if n else 0,
            "ndcg_at_k": round(ndcg / n, 4) if n else 0,
            "top_k": top_k,
            "query_p50_ms": round(lat[len(lat) // 2], 2) if lat else 0}


def cmd_eval(a) -> int:
    print(json.dumps(evaluate(a.data, a.model, onnx=a.onnx, fixture=a.fixture,
                              top_k=a.top_k, limit=a.limit), ensure_ascii=False))
    return 0


# ── 6단계: 여러 Desktop 동시 수행 ────────────────────────────────────────────

def _copy_store(src: Path, dst: Path) -> None:
    """스토어 하나를 통째로 복사한다.

    ⚠ 임베디드 스토어는 **디렉터리가 아니라 파일**이다 — `data_dir` 로 준 경로 자체가
    DB 파일이고, 옆에 `<경로>.wal` 이 따로 산다. `copytree` 는 여기서
    `NotADirectoryError` 로 죽고, 파일만 복사하면 WAL 에만 있는 쓰기를 잃는다."""
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    wal = Path(str(src) + ".wal")
    if wal.exists():
        shutil.copy2(wal, Path(str(dst) + ".wal"))


def cmd_concurrent(a) -> int:
    """스토어 N개를 같은 기계에서 **동시에** 재구축+재색인한다.

    각각을 별 프로세스로 돌린다 — 데스크탑 N대의 형상이 그것이고, 한 프로세스
    안에서 스레드로 돌리면 ONNX 세션이 공유돼 메모리 합이 실제보다 작게 나온다."""
    base = Path(a.data)
    work = Path(a.work or (str(base) + "-conc"))
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    srcs = []
    for i in range(a.n):
        d = work / f"s{i}"
        _copy_store(base, d)
        srcs.append(d)
    t0 = time.perf_counter()
    procs = []
    for i, d in enumerate(srcs):
        cmd = [sys.executable, "-X", "utf8", __file__, "one",
               "--data", str(d), "--out", str(work / f"o{i}"), "--model", a.model]
        if a.onnx:
            cmd += ["--onnx", a.onnx]
        procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8", errors="replace"))
    results = []
    for p in procs:
        so, se = p.communicate()
        results.append({"rc": p.returncode, "out": so.strip().splitlines()[-1] if so.strip() else "",
                        "err": se.strip()[-400:]})
    print(json.dumps({"phase": "concurrent", "n": a.n,
                      "wall_s": round(time.perf_counter() - t0, 2),
                      "results": results}, ensure_ascii=False))
    return 0


def cmd_one(a) -> int:
    """재구축 + 재색인 한 벌(동시 수행의 단위 작업)."""
    rb = rebuild_store(a.data, a.out, model=a.model)
    ri = reindex(a.out, a.model, onnx=a.onnx)
    print(json.dumps({"phase": "one", "rebuild": rb, "reindex": ri}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, *, model_default="e5"):
        p.add_argument("--data", required=True)
        p.add_argument("--model", default=model_default)
        p.add_argument("--onnx", default=None, help="ONNX 파일 경로(외부 가중치 모델용)")

    p = sub.add_parser("seed"); common(p)
    p.add_argument("--docs", type=int, default=10, help="언어별 문서 수(0=전량)")
    p.add_argument("--owner", default="user:local")
    p.add_argument("--fixture", action="append", default=[],
                   help="정답 문서를 코퍼스에 강제 포함시킬 평가셋(반복 가능)")
    p.set_defaults(fn=cmd_seed)

    p = sub.add_parser("rebuild"); common(p, model_default="m3")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_rebuild)

    p = sub.add_parser("retable"); common(p, model_default="m3")
    p.set_defaults(fn=cmd_retable)

    p = sub.add_parser("reindex"); common(p, model_default="m3")
    p.add_argument("--split", action="store_true",
                   help="워커처럼 청크를 ≤500 토큰 조각으로 나눠 조각마다 벡터(프로덕션 형상)")
    p.add_argument("--kill-after", type=int, default=0,
                   help="이만큼 청크를 채우고 중단(정체 알람 검증용)")
    p.set_defaults(fn=cmd_reindex)

    p = sub.add_parser("eval"); common(p)
    p.add_argument("--fixture", action="append", required=True)
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(fn=cmd_eval)

    p = sub.add_parser("one"); common(p, model_default="m3")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_one)

    p = sub.add_parser("concurrent"); common(p, model_default="m3")
    p.add_argument("--n", type=int, default=3)
    p.add_argument("--work", default=None)
    p.set_defaults(fn=cmd_concurrent)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
