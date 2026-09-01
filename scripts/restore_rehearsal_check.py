# -*- coding: utf-8 -*-
"""복원 리허설 판정 — 스냅샷 사본을 **실제로 열어** 라벨별 행 수를 센다.

열어보지 않은 백업은 백업이 아니라 추측이다: `db.recovered3.bad-20260809` 는
복사가 성공했고 크기도 맞았는데 열면 죽었다(FTS 손상 — [[vinc.ops.fts-index-drift]]).
그래서 판정은 "파일이 있다"가 아니라 세 관문이다:
  ① 열린다(WAL 재생·확장 로드 포함 — 재생 불가 WAL 이 P5 를 통째로 되감았다)
  ② 노드 테이블이 있다
  ③ 행이 있다 — 임베디드 스토어는 없는 경로를 열면 **빈 스토어를 새로 만들어
    성공**하므로, 0 카운트를 통과로 읽으면 그 사고를 "성공"으로 보고하게 된다

restore-rehearsal.yml 이 부른다. 단독 실행: python scripts/restore_rehearsal_check.py <db 파일 경로>
(스토어는 단일 파일 `db` + `db.wal` — 인자는 디렉터리가 아니라 그 **파일**이다.)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.backend import open_store          # noqa: E402
from store.config import load_config          # noqa: E402

#: show_tables 가 어떤 이유로든 실패할 때의 폴백 — 전용 6종 + 캐리어 + 문서 + 툼스톤.
_FALLBACK_LABELS = ("Concept", "Record", "Episode", "Decision", "Domain", "Bridge",
                    "Entity", "Chunk", "_Tombstone", "_EdgeTombstone")


def _node_labels(store) -> list[str]:
    try:
        rows = store.run("CALL show_tables() RETURN *")
        names = [str(r.get("name", "")) for r in rows
                 if str(r.get("type", "")).upper() == "NODE"]
        if names:
            return sorted(n for n in names if n)
    except Exception as exc:                   # 폴백은 판정을 약화시키므로 사유를 남긴다
        print(f"show_tables unavailable ({exc}) — falling back to fixed label list",
              file=sys.stderr)
    return list(_FALLBACK_LABELS)


def main() -> int:
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        print(f"usage: {sys.argv[0]} <db 파일>  (존재하는 파일이어야 한다 — "
              "없는 경로는 엔진이 빈 스토어를 새로 만들어 '성공'해 버린다)", file=sys.stderr)
        return 2
    store = open_store(load_config({"data_dir": sys.argv[1]}))
    if store is None:
        print("FAIL: store driver unavailable (ladybug 미설치?)")
        return 1
    try:
        total = 0
        for label in _node_labels(store):
            try:
                c = int(store.run(f"MATCH (n:`{label}`) RETURN count(n) AS c")[0]["c"])
            except Exception:
                continue                       # 폴백 목록의 미존재 라벨은 조용히 넘어간다
            total += c
            print(f"{label}\t{c}")
        print(f"TOTAL\t{total}")
        if total == 0:
            print("FAIL: opened but holds zero rows — 경로가 틀렸거나 속 빈 사본이다")
            return 1
    finally:
        store.close()
    print("OK: the copy opens and holds data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
