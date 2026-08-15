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
import unittest.mock
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

    def test_meipass_is_mirrored_off_the_volatile_dir(self):
        """frozen 추출 dir 에서 찾은 확장은 **그 경로를 그대로 주면 안 된다**.

        `LOAD EXTENSION '<절대경로>'` 는 그 경로를 WAL 에 평문으로 남기고, 다음 열기의
        WAL 재생이 같은 경로로 LOAD 를 재실행한다. `_MEIPASS` 는 실행마다 바뀌는
        `%TEMP%/_MEI<랜덤>` 이라, 그대로 주면 급사한 뒤 그 디렉터리가 사라지는 순간
        스토어가 **영구히** 안 열린다(2026-08-14 실증). 이 가드가 그 회귀를 막는다."""
        import tempfile
        mei = Path(tempfile.mkdtemp()) / "_MEI99999"
        (mei / "lbug_extensions").mkdir(parents=True)
        src = mei / "lbug_extensions" / "libvector.lbug_extension"
        src.write_bytes(b"stub-payload")
        old_mei = getattr(sys, "_MEIPASS", None)
        old_env = os.environ.pop("VINC_LBUG_EXT_DIR", None)
        home = Path(tempfile.mkdtemp())
        sys._MEIPASS = str(mei)
        try:
            with unittest.mock.patch.object(Path, "home", staticmethod(lambda: home)):
                got = eb._bundled_ext_path("vector")
            self.assertIsNotNone(got)
            self.assertNotIn("_MEI99999", got)          # 휘발성 경로를 넘기지 않는다
            self.assertTrue(got.startswith(str(home).replace("\\", "/")))
            self.assertEqual(Path(got).read_bytes(), b"stub-payload")   # 실제로 복사됐다
            self.assertNotIn("\\", got)
        finally:
            if old_mei is None:
                del sys._MEIPASS
            else:
                sys._MEIPASS = old_mei
            if old_env is not None:
                os.environ["VINC_LBUG_EXT_DIR"] = old_env

    def test_wal_scan_flags_a_volatile_ext_path(self):
        """레거시 WAL 이 물고 있는 `_MEI…` 경로를 **아직 열리는 동안** 잡아낸다.

        `_mirror_to_stable` 은 앞으로 심기는 것만 막는다. 2026-08-14 이전 패키징본이
        급사하며 남긴 기록은 그대로 남아 있고, 그 디렉터리가 사라지는 순간 스토어가
        영구히 안 열린다. 정상 종료 한 번이면 체크포인트가 지우므로, 살아 있는 동안
        말해 주는 것이 유일하게 값싼 시점이다."""
        import tempfile
        d = Path(tempfile.mkdtemp())
        # 길이 접두(널)로 감싼 실제 WAL 모양 — 앞뒤 바이너리에서 경로만 떨어져야 한다.
        (d / "db.wal").write_bytes(
            b"\xd9B\xb1>\x00\x00\x00\x00"
            b"C:/Users/u/AppData/Local/Temp/_MEI16322/lbug_extensions/libfts.lbug_extension"
            b"\xbd*\x0cQ\xdf")
        note = eb._volatile_wal_note(str(d / "db"))
        self.assertIn("_MEI16322", note)
        self.assertIn("libfts.lbug_extension", note)
        self.assertIn("유실 1건", note)      # 그 경로는 실재하지 않는다 → 곧 깨진다

    def test_wal_scan_is_quiet_when_clean(self):
        """무장되지 않았으면 **아무 말도 안 한다**. 상시 뜨는 경고는 곧 안 읽히는 경고다."""
        import tempfile
        d = Path(tempfile.mkdtemp())
        self.assertEqual(eb._volatile_wal_note(str(d / "db")), "")   # WAL 자체가 없다
        (d / "db.wal").write_bytes(b"\x00\x01ordinary wal bytes\x00")
        self.assertEqual(eb._volatile_wal_note(str(d / "db")), "")

    def test_wal_scan_never_breaks_open(self):
        # 진단이 기동을 막으면 안 된다 — 읽을 수 없는 WAL 은 조용히 ""(경고 없음).
        # ⚠ WAL 이 **실재해야** 이 경로를 탄다: 없으면 os.path.exists 에서 먼저 빠져
        # 읽기 자체를 안 하므로, 파일 없이 부르면 통과해도 아무것도 검증하지 못한다.
        import tempfile
        d = Path(tempfile.mkdtemp())
        (d / "db.wal").write_bytes(b"\x00")
        with unittest.mock.patch("builtins.open", side_effect=PermissionError("locked")):
            self.assertEqual(eb._volatile_wal_note(str(d / "db")), "")

    def test_mirror_falls_back_to_source_when_copy_fails(self):
        """복사가 안 되면(읽기전용 홈 등) **원본 경로로 강등**한다 — None 이 아니다.

        None 을 주면 확장이 통째로 강등돼 벡터·FTS 가 죽는다. 취약한 경로라도 로드되는
        편이 낫다: 이 함정은 급사 + 디렉터리 소멸이 겹쳐야 물리지만, 강등은 즉시 손해다."""
        import tempfile
        src = Path(tempfile.mkdtemp()) / "libfts.lbug_extension"
        src.write_bytes(b"stub")
        with unittest.mock.patch.object(eb, "_stable_ext_dir",
                                        side_effect=OSError("read-only home")):
            self.assertEqual(eb._mirror_to_stable(str(src), src.name), str(src))


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
