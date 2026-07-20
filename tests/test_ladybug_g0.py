# -*- coding: utf-8 -*-
"""G0 — LadybugDB 오프라인 확장 번들(§9) 회귀 가드.

전환의 실질 관문은 ladybug 확장(vector/fts/algo)이 런타임 다운로드형이라는 점이다
("설치 즉시 오프라인" 불변식 §1 위반). 실측(2026-07-20): 캐시 없이 `LOAD EXTENSION
<name>` 은 실패하나 **`LOAD EXTENSION '<번들 경로>'` 는 네트워크 없이 로드 성공**하고
이후 벡터/FTS 인덱스가 정상 동작한다. 이 테스트가 그 메커니즘을 상시 고정한다:

  · `_bundled_ext_path` 경로 해석(순수 — ladybug 불요, CI 상시 실행)
  · ladybug 오프라인 로드(ladybug 미설치 개발 환경에서만 skip — v0.5.3 부터 유일 드라이버)
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import embedded_backend as eb  # noqa: E402


class BundledExtPathTests(unittest.TestCase):
    """`_bundled_ext_path` — 번들 확장 경로 해석(순수, 드라이버 무관)."""

    def test_resolves_from_env_dir(self):
        import tempfile
        d = Path(tempfile.mkdtemp())
        f = d / "libvector.lbug_extension"
        f.write_bytes(b"stub")
        old = os.environ.get("VINC_LBUG_EXT_DIR")
        os.environ["VINC_LBUG_EXT_DIR"] = str(d)
        try:
            got = eb._bundled_ext_path("vector")
            self.assertIsNotNone(got)
            self.assertTrue(got.endswith("libvector.lbug_extension"))
            self.assertNotIn("\\", got)  # Cypher 리터럴용 슬래시 정규화
        finally:
            if old is None:
                os.environ.pop("VINC_LBUG_EXT_DIR", None)
            else:
                os.environ["VINC_LBUG_EXT_DIR"] = old

    def test_none_when_absent(self):
        old = os.environ.get("VINC_LBUG_EXT_DIR")
        import tempfile
        os.environ["VINC_LBUG_EXT_DIR"] = tempfile.mkdtemp()  # 빈 dir
        try:
            self.assertIsNone(eb._bundled_ext_path("vector"))
        finally:
            if old is None:
                os.environ.pop("VINC_LBUG_EXT_DIR", None)
            else:
                os.environ["VINC_LBUG_EXT_DIR"] = old


class LadybugOfflineLoadTests(unittest.TestCase):
    """ladybug 설치 시: 캐시 없는 오프라인에서 번들 경로 로드 → 벡터/FTS 인덱스 동작.
    ladybug 미설치(개발 환경 등)면 전체 skip."""

    @classmethod
    def setUpClass(cls):
        try:
            import ladybug  # noqa: F401
        except Exception:
            raise unittest.SkipTest("ladybug 미설치 — 개발 환경 확인")

    def test_offline_bundled_load(self):
        import ladybug, shutil, tempfile
        home = Path.home()
        cache = home / ".lbdb"
        # 번들 준비: 확장이 캐시에 없으면 INSTALL(네트워크) 후 복사. 네트워크/설치 실패 시 skip.
        bundle = Path(tempfile.mkdtemp(prefix="g0bundle_"))
        try:
            _seed_bundle(ladybug, cache, bundle)
        except Exception as e:
            self.skipTest(f"확장 시드 실패(오프라인 CI 등) — {e}")

        moved = home / ".lbdb__g0test"
        if moved.exists():
            shutil.rmtree(moved)
        if cache.exists():
            shutil.move(str(cache), str(moved))
        old = os.environ.get("VINC_LBUG_EXT_DIR")
        os.environ["VINC_LBUG_EXT_DIR"] = str(bundle)
        try:
            db = ladybug.Database(os.path.join(tempfile.mkdtemp(), "g"))
            conn = ladybug.Connection(db)

            class _Cfg:
                data_dir = None

            be = eb.EmbeddedBackend(db, conn, "ladybug", _Cfg())
            # 오프라인(캐시 이동됨)에서 실 코드 경로로 로드 — 예외 없어야 통과
            be._load_ext("vector")
            be._load_ext("fts")
            conn.execute("CREATE NODE TABLE Chunk(id INT64, embedding FLOAT[3], text STRING, PRIMARY KEY(id))")
            conn.execute("CREATE (:Chunk {id:1, embedding:[0.1,0.2,0.3], text:'hello world'})")
            conn.execute("CALL CREATE_VECTOR_INDEX('Chunk','chunk_vec','embedding')")
            conn.execute("CALL CREATE_FTS_INDEX('Chunk','chunk_ft',['text'])")
        finally:
            if old is None:
                os.environ.pop("VINC_LBUG_EXT_DIR", None)
            else:
                os.environ["VINC_LBUG_EXT_DIR"] = old
            if cache.exists():
                shutil.rmtree(cache)
            shutil.move(str(moved), str(cache))


def _seed_bundle(ladybug, cache: Path, bundle: Path) -> None:
    """확장 3개를 bundle 로 확보 — 캐시에 있으면 복사, 없으면 INSTALL(네트워크) 후 복사."""
    import shutil
    need = ("vector", "fts", "algo")
    found = {e: sorted(cache.glob(f"extension/*/*/{e}/lib{e}.lbug_extension")) for e in need}
    if not all(found[e] for e in need):
        import tempfile
        db = ladybug.Database(os.path.join(tempfile.mkdtemp(), "seed"))
        conn = ladybug.Connection(db)
        for e in need:
            conn.execute(f"INSTALL {e}")
        found = {e: sorted(cache.glob(f"extension/*/*/{e}/lib{e}.lbug_extension")) for e in need}
    for e in need:
        if not found[e]:
            raise RuntimeError(f"{e} 확장 확보 실패")
        shutil.copy(found[e][-1], bundle / f"lib{e}.lbug_extension")


if __name__ == "__main__":
    unittest.main()
