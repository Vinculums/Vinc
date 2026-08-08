# -*- coding: utf-8 -*-
"""WAL 이 무한정 자라지 않는가 — 2026-08-08 데이터 유실 사고의 재발 방지.

무슨 일이 있었나. DB 파일 mtime 06:38 · WAL mtime 11:56(11.9MB) — 그 5시간 반의
모든 쓰기가 WAL 에만 있었고, 사이드카가 죽자 그 WAL 은 **재생 불가**였다(네이티브
크래시 0xC0000409, 로컬 재현 확인). 5시간 반이 통째로 사라졌다.

⚠ 최초 진단은 틀렸다. "엔진은 close 때만 체크포인트한다"고 봤고 그래서 잡 큐에
"N개마다 CHECKPOINT" 를 넣었다. 그러나 ladybug 0.18.0 의 `SystemConfig` 는
``autoCheckpoint=true, checkpointThreshold=16MB`` 가 기본이라 **close 없이도 스스로
체크포인트한다**. 11.9MB 는 그 16MB 에 못 미쳤을 뿐이다 — 버그가 아니라 설정값
그대로의 동작이었다. 그래서 잡 카운터를 걷어내고 **임계값 자체**를 내렸다.

여기서 지키는 것 둘:

1. 설정한 임계값이 **실제로 WAL 을 그 근처로 묶는다**(설정이 전달되긴 했다가 아니라
   효과가 있다 — 어제 "호출됐다" 수준의 검증이 오진을 못 걸렀다).
2. 임계값을 안 주면 드라이버 기본값을 건드리지 않는다.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.config import StoreConfig, load_config                # noqa: E402
from store.embedded_backend import _driver_kwargs, _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


class DriverKwargs(unittest.TestCase):
    """드라이버 없이도 도는 계약 검사 — 모든 열기 경로가 이 하나를 쓴다."""

    def test_threshold_is_passed_in_bytes(self):
        cfg = StoreConfig(checkpoint_threshold_mb=4)
        self.assertEqual(_driver_kwargs(cfg)["checkpoint_threshold"], 4 * 1024 * 1024)

    def test_zero_leaves_the_driver_default_alone(self):
        cfg = StoreConfig(checkpoint_threshold_mb=0)
        self.assertNotIn("checkpoint_threshold", _driver_kwargs(cfg),
                         "0 이면 인자를 넣지 않아야 한다 — 드라이버 기본값 유지")

    def test_default_config_lowers_the_threshold(self):
        # 기본이 드라이버 기본(16MB)이면 이 사고가 그대로 재현된다.
        self.assertGreater(StoreConfig.checkpoint_threshold_mb, 0)
        self.assertLess(StoreConfig.checkpoint_threshold_mb, 16,
                        "기본 임계값이 16MB 이상이면 사고 당시와 같은 손실 창이다")

    def test_env_override(self):
        old = os.environ.get("VINC_CHECKPOINT_THRESHOLD_MB")
        os.environ["VINC_CHECKPOINT_THRESHOLD_MB"] = "1"
        try:
            self.assertEqual(load_config().checkpoint_threshold_mb, 1)
        finally:
            if old is None:
                os.environ.pop("VINC_CHECKPOINT_THRESHOLD_MB", None)
            else:
                os.environ["VINC_CHECKPOINT_THRESHOLD_MB"] = old


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버 미설치")
class ThresholdBoundsTheWal(unittest.TestCase):
    """실엔진 — 임계값을 주면 WAL 이 정말 그 근처에서 잘리는가.

    이것이 이 파일의 핵심이다. 설정이 전달됐는지가 아니라 **WAL 이 실제로 안 자라는
    것**을 본다. 사고를 막는 것은 전자가 아니라 후자다."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
                     "MEMORY_EMBEDDING_MODEL": "local-en",
                     "VINC_CHECKPOINT_THRESHOLD_MB": "1"}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _wal_size(self, data_dir: str) -> int:
        wal = Path(data_dir + ".wal")
        return wal.stat().st_size if wal.exists() else 0

    def test_wal_stays_bounded_without_any_explicit_checkpoint(self):
        from store.backend import open_store
        cfg = load_config()
        self.assertEqual(cfg.checkpoint_threshold_mb, 1)
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        data_dir = cfg.data_dir

        peak = 0
        # 1MB 임계값을 여러 번 넘길 만큼 쓴다. **CHECKPOINT 를 한 번도 부르지 않는다** —
        # 엔진이 스스로 하는지가 쟁점이다.
        with store.session() as s:
            for i in range(4000):
                s.run("MERGE (c:Concept {concept_id:$id}) SET c.owner_id='u', "
                      "c.title=$t, c.domain='x'", id=f"n{i}", t="x" * 200)
                if i % 250 == 0:
                    peak = max(peak, self._wal_size(data_dir))
        peak = max(peak, self._wal_size(data_dir))
        store.close()

        self.assertGreater(peak, 0, "쓰기 뒤에는 WAL 이 있어야 한다 "
                                    "(없다면 이 엔진은 WAL 을 안 쓰고 이 사고도 불가능하다)")
        # 임계값은 "넘으면 그때 반영"이라 정확히 1MB 에서 잘리지는 않는다. 넉넉히
        # 4배를 한도로 둔다 — 사고 당시의 11.9MB(임계 미달로 한 번도 안 돈 상태)와
        # 명확히 구분되면 충분하다.
        self.assertLess(peak, 4 * 1024 * 1024,
                        f"WAL 최고치가 {peak}B — 임계값 1MB 를 주고도 자유롭게 자랐다. "
                        f"그러면 자동 체크포인트가 안 돌고 있고 사고가 재현된다")
