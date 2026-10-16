# -*- coding: utf-8 -*-
"""스토어가 내용만큼 큰가, 아니면 부풀었나 — 논리 재구축 전후의 크기를 잰다.

왜 필요한가. 2026-09-02 실측에서 스냅샷 1회가 **167MB** 를 복사하는데, 그 안의
내용은 문서 197건·청크 약 1,500개·노드 1,131개로 텍스트와 속성이 10MB 안팎이었다.
스냅샷은 매 더러운 60초마다 스토어를 **통째로** SMB 로 밀므로 파일 크기가 곧
트랜잭션량이고, 그것이 3주 만에 3.5배가 됐다(16k → 105k/일).

가설은 죽은 공간이다: `doc_put` 이 편집마다 청크를 지우고 다시 넣고 검색 색인을
떨궜다 다시 세우는데 엔진에 VACUUM 이 없다. 하지만 **가설은 측정이 아니다.**
파일이 큰 이유가 죽은 공간인지 진짜 내용인지는 논리 재구축(export → 새 스토어 →
import)을 해서 크기를 비교해야만 갈린다. 이 스크립트가 그 실험이다.

읽는 법:
  ratio 가 1.0 에 가깝다  → 내용이 진짜 그만큼이다. 스냅샷 주기로만 다뤄야 한다
  ratio 가 크다(예: 10x) → 죽은 공간이다. 주기적 재구축이 파일을 되돌린다

**행 수를 함께 센다.** 작아진 파일이 행을 잃은 것이면 그것은 압축이 아니라 손실이고,
크기만 보면 그 둘이 똑같아 보인다 — 리허설 판정이 배운 것과 같은 함정이다
(근거: [[vinc.ops.deployment]]).

벡터는 **버리지 않는다**. `rebuild_store` 는 차원을 바꾸려고 ChunkVec 을 덤프에서
빼지만, 여기서 그러면 줄어든 크기의 원인이 죽은 공간인지 버린 벡터인지 갈리지
않는다. 그래서 이 스크립트는 재구축 경로가 아니라 그 아래의 export/import 를
직접 쓴다(같은 파일의 같은 함수들이다).

단독 실행:
    python -X utf8 scripts/store_size_probe.py <db 파일> [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

try:
    from scripts._root import ROOT  # noqa: F401
except ImportError:  # 직접 실행: 리포 루트가 아직 sys.path 에 없다
    from _root import ROOT  # noqa: F401

from store.backend import open_store              # noqa: E402
from store.config import load_config              # noqa: E402
from store.migrate import export_store, import_store, vector_dim   # noqa: E402


def _bytes(db_path: str) -> int:
    """스토어가 차지하는 바이트 — 본 파일 + 있으면 .wal. 스냅샷이 복사하는 것과 같은 집합."""
    total = 0
    for p in (db_path, db_path + ".wal"):
        if os.path.isfile(p):
            total += os.path.getsize(p)
        elif os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return total


def _counts(store) -> dict[str, int]:
    """라벨별 행 수. 크기가 줄었을 때 '압축'과 '손실'을 가르는 유일한 재료다."""
    out: dict[str, int] = {}
    try:
        rows = store.run("CALL show_tables() RETURN *")
        labels = sorted(str(r.get("name", "")) for r in rows
                        if str(r.get("type", "")).upper() == "NODE" and r.get("name"))
    except Exception as exc:
        print(f"show_tables unavailable ({exc})", file=sys.stderr)
        return out
    for label in labels:
        try:
            out[label] = int(store.run(f"MATCH (n:`{label}`) RETURN count(n) AS c")[0]["c"])
        except Exception:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="논리 재구축 전후의 스토어 크기를 비교한다")
    ap.add_argument("db", help="원본 db 파일 (디렉터리가 아니라 파일)")
    ap.add_argument("--out", help="재구축 대상 경로(생략하면 임시 디렉터리, 끝나고 지운다)")
    ap.add_argument("--keep", action="store_true", help="재구축 결과를 남긴다")
    a = ap.parse_args()

    if not Path(a.db).exists():
        # 없는 경로를 열면 엔진이 **빈 스토어를 새로 만들어 성공한다** — 0 을 통과로
        # 읽으면 "완벽히 압축됨"이라는 거짓 보고가 나온다.
        print(f"FAIL: {a.db} 가 없다", file=sys.stderr)
        return 2

    src_bytes = _bytes(a.db)
    src_cfg = load_config({"data_dir": a.db})
    src = open_store(src_cfg)
    if src is None:
        print("FAIL: store driver unavailable (ladybug 미설치?)", file=sys.stderr)
        return 1

    tmp = None
    try:
        src_counts = _counts(src)
        if sum(src_counts.values()) == 0:
            print("FAIL: 원본이 열렸는데 행이 0이다 — 경로가 틀렸거나 속 빈 사본이다")
            return 1
        # 벡터 폭은 원본 열에서 읽는다. 설정 기본값(768)을 쓰면 1024 인 허브 스냅샷에서
        # 형 변환 예외로 죽는다(2026-09-30 복원 리허설 run 36628686796).
        src_dim = vector_dim(src)
        t0 = time.time()
        dump = export_store(src)
    finally:
        src.close()

    try:
        if a.out:
            dst_path = a.out
        else:
            tmp = tempfile.mkdtemp(prefix="store-size-probe-")
            dst_path = os.path.join(tmp, "db")
        dst_cfg = load_config({"data_dir": dst_path})
        dst = open_store(dst_cfg)
        if dst is None:
            print("FAIL: 대상 스토어를 열 수 없다", file=sys.stderr)
            return 1
        try:
            dim = src_dim or dst_cfg.embedding_dim
            stats = import_store(dst, dump, dim=dim)
            # 색인까지 세워야 공정한 비교다 — 원본에는 색인이 있고, 색인은 파일의
            # 일부다. 안 세우면 줄어든 몫에 "색인이 없어서"가 섞인다.
            dst.ensure_search_indexes(dim=dim)
            dst_counts = _counts(dst)
        finally:
            dst.close()
        elapsed = time.time() - t0
        dst_bytes = _bytes(dst_path)

        lost = {k: (src_counts.get(k, 0), dst_counts.get(k, 0))
                for k in sorted(set(src_counts) | set(dst_counts))
                if src_counts.get(k, 0) != dst_counts.get(k, 0)}

        ratio = (src_bytes / dst_bytes) if dst_bytes else 0.0
        print(json.dumps({
            "src_bytes": src_bytes, "dst_bytes": dst_bytes,
            "src_mb": round(src_bytes / 1e6, 1), "dst_mb": round(dst_bytes / 1e6, 1),
            "ratio": round(ratio, 2),
            "reclaimed_mb": round((src_bytes - dst_bytes) / 1e6, 1),
            "rebuild_seconds": round(elapsed, 1),
            "imported": stats,
            "rows_src_total": sum(src_counts.values()),
            "rows_dst_total": sum(dst_counts.values()),
            "row_mismatch": lost,
        }, ensure_ascii=False, indent=2))

        if lost:
            # 여기서 떨어지는 것이 이 스크립트의 존재 이유의 절반이다. 행을 잃고
            # 작아진 파일은 압축이 아니라 손실이고, 크기만 보면 구별이 안 된다.
            print("FAIL: 재구축본의 행 수가 원본과 다르다 — 압축이 아니라 손실이다")
            return 1
        verdict = ("죽은 공간이 지배적이다 — 주기적 재구축이 파일을 되돌린다"
                   if ratio >= 2.0 else
                   "파일은 대체로 내용이다 — 재구축으로 줄지 않는다")
        print(f"OK: ratio {ratio:.2f}x · {verdict}")
        return 0
    finally:
        if tmp and not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
