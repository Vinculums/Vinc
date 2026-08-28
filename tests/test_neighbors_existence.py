# -*- coding: utf-8 -*-
"""`vinc_neighbors` 의 존재 판정은 PK 조회여야 한다 — 유사도 검색이 아니라.

이 파일이 존재하는 이유는 결함의 모양이다. 엣지가 하나라도 있으면 부착 축 플랜이
노드를 잡아 주므로, 존재 판정이 틀려도 **아무도 모른다.** 틀린 판정이 드러나는 것은
엣지가 0인 Episode·Record·Bridge 뿐이고, 그 상태는 노드를 막 쓰고 아직 잇지 않은
순간이다 — 즉 방금 쓴 것을 읽어 확인하는 바로 그 절차에서 "없다"고 답했다.

정본: [[record:neighbors-unknown-node-for-edgeless-attachments-2026-08-28]].
같은 결함을 결정(Decision)이 v12 에서 먼저 겪었고 거기서는 `_SEMANTIC_PK` 승격으로
막았다. 나머지 세 라벨은 그 처방 밖에 남아 있었다.

여기서 고정하는 것 셋:
  ① 엣지 0인 Record·Episode·Bridge 가 `found:true` 다(구 동작에서는 unknown).
  ② 진짜 없는 id 는 여전히 `found:false` 이고 이유를 말한다 — 고치면서 반대쪽으로
     넘어가 아무거나 있다고 답하면 그것도 같은 크기의 거짓말이다.
  ③ 남의 노드는 없는 것과 같이 보인다(SEC-1). 존재 판정을 PK 로 옮기면서 owner
     술어가 빠지면 **id 를 찍어 남의 그래프를 탐지**할 수 있게 된다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import fragment as frag        # noqa: E402
from api import query as q              # noqa: E402

OWNER = "user:nbr"
OTHER = "user:other"


def _fresh_store():
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR", "MEMORY_EMBEDDING_MODEL")
    keep = {k: os.environ.get(k) for k in keys}
    os.environ["MEMORY_STORE_BACKEND"] = "embedded"
    os.environ["MEMORY_DATA_DIR"] = os.path.join(tempfile.mkdtemp(), "store")
    os.environ["MEMORY_EMBEDDING_MODEL"] = "local-en"
    from store.backend import open_store
    from store.config import load_config
    cfg = load_config()
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    return store, keep


def _restore(keep):
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


_DRIVER_MISSING = None
try:                                  # 드라이버가 없으면 이 파일은 통째로 skip
    import ladybug  # noqa: F401
except Exception as exc:              # noqa: BLE001
    _DRIVER_MISSING = f"{type(exc).__name__}: {exc}"


@unittest.skipIf(_DRIVER_MISSING, f"임베디드 드라이버 미설치 — {_DRIVER_MISSING}")
class EdgelessNodesExist(unittest.TestCase):
    """엣지를 하나도 안 단 노드도 실재한다."""

    def setUp(self):
        self.store, self._keep = _fresh_store()
        # 제목은 **일부러 슬러그와 안 겹치게** 쓴다. 겹치면 옛 유사도 폴백도 우연히
        # 통과해서, 이 테스트가 회귀를 못 잡는 상태로 초록이 된다.
        frag.merge_fragment({"owner": OWNER, "nodes": [
            {"id": "record:zzz-1", "type": "record", "title": "A measurement with prose only"},
            {"id": "episode:zzz-2", "type": "episode", "title": "Something that happened once"},
            {"id": "bridge:zzz-3", "type": "bridge", "title": "A junction between two areas"},
            {"id": "concept:zzz-4", "type": "concept", "title": "An idea worth reusing"},
        ]}, store=self.store)

    def tearDown(self):
        try:
            self.store.close()
        finally:
            _restore(self._keep)

    def test_엣지_0인_노드가_존재로_보고된다(self):
        broken = []
        for nid in ("record:zzz-1", "episode:zzz-2", "bridge:zzz-3", "concept:zzz-4"):
            r = q.neighbors(nid, owner=OWNER, store=self.store)
            if not r.get("found"):
                broken.append(f"{nid}: found={r.get('found')} error={r.get('error')!r}")
            elif r.get("count"):
                broken.append(f"{nid}: 이웃이 0이어야 하는데 {r.get('count')}")
        self.assertEqual(broken, [], "엣지 0인 노드가 없는 것으로 보고됐다:\n  "
                                     + "\n  ".join(broken))

    def test_이웃이_없다는_사실은_그대로_전달된다(self):
        """존재를 고치면서 '이웃 0'을 숨기면 안 된다 — 그건 다른 사실이다."""
        r = q.neighbors("record:zzz-1", owner=OWNER, store=self.store)
        self.assertTrue(r["found"])
        self.assertEqual(r["count"], 0)
        self.assertEqual(r["neighbors"], [])
        self.assertIsNone(r.get("error"))
        self.assertIn("no relations yet", r.get("hint", ""))

    def test_진짜_없는_id_는_여전히_없다고_한다(self):
        r = q.neighbors("record:does-not-exist-at-all", owner=OWNER, store=self.store)
        self.assertFalse(r["found"])
        self.assertIn("unknown node", r.get("error") or "")

    def test_남의_노드는_없는_것과_같이_보인다(self):
        """SEC-1. PK 조회로 옮기면서 owner 술어가 빠지면 존재가 새어 나간다."""
        r = q.neighbors("record:zzz-1", owner=OTHER, store=self.store)
        self.assertFalse(r["found"], f"남의 노드가 보인다: {r}")


if __name__ == "__main__":
    unittest.main()
