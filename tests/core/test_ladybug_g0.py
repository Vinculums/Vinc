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

import hashlib
import os
import platform
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from store import embedded_backend as eb  # noqa: E402

#: 번들에 싣는 확장. 해시 목록(`packaging/lbug-ext-<판>.sha256`)의 확장 이름과 같다.
_NEED = ("vector", "fts", "algo")


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
        import ladybug, shutil
        home = Path.home()
        cache = home / ".lbdb"
        # 번들 준비: 엔진 판의 핀과 해시가 같은 바이트만 쓴다(상류에서 받지 않는다).
        # 리눅스에서 핀과 다르면 판 불일치로 실패하고, 그 밖의 기기에서 맞는 파일이 없으면 skip.
        bundle = Path(tempfile.mkdtemp(prefix="g0bundle_"))
        try:
            sources = _seed_bundle(ladybug, cache, bundle)
        except EngineMismatch as e:
            self.fail(str(e))
        except BundleUnavailable as e:
            self.skipTest(f"핀과 같은 확장이 이 기기에 없다: {e}")

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
            # 오프라인(캐시 이동됨)에서 실 코드 경로로 로드 — 예외 없어야 통과.
            # 실패하면 엔진 판과 번들 출처·해시를 함께 말한다(재실행으로 가려지지 않게).
            for ext in ("vector", "fts"):
                try:
                    be._load_ext(ext)
                except Exception as e:
                    self.fail(f"{ext} 번들 로드 실패: 엔진 ladybug "
                              f"{getattr(ladybug, '__version__', '?')} · 출처 {sources[ext]} · {e}")
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
            # 캐시가 처음부터 없던 기기(깨끗한 러너)에서는 되돌릴 것이 없다.
            if moved.exists():
                shutil.move(str(moved), str(cache))


class EngineMismatch(Exception):
    """확장 바이트가 설치된 엔진 판의 핀과 다르다. 심볼 오류로 떨어지기 전에 판 불일치로 말한다."""


class BundleUnavailable(Exception):
    """이 기기에 핀과 같은 확장 바이트가 없다(리눅스 밖의 개발 기기). 테스트는 skip 한다."""


def _platform_tag() -> str:
    """해시 목록이 쓰는 플랫폼 이름. 목록에 없는 조합이면 빈 문자열."""
    machine = platform.machine().lower()
    if sys.platform.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux_amd64"
    if sys.platform == "win32" and machine in ("x86_64", "amd64"):
        return "win_amd64"
    if sys.platform == "darwin" and machine in ("arm64", "aarch64"):
        return "osx_arm64"
    return ""


def _pinned_hashes(version: str, plat: str, repo: Path = _REPO) -> dict[str, str]:
    """`packaging/lbug-ext-<엔진 판>.sha256` 에서 이 플랫폼 확장의 해시를 읽는다. 없으면 {}."""
    path = repo / "packaging" / f"lbug-ext-{version}.sha256"
    if not plat or not path.is_file():
        return {}
    prefix, suffix = f"{plat}-lib", ".lbug_extension"
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith(prefix) and parts[1].endswith(suffix):
            out[parts[1][len(prefix):-len(suffix)]] = parts[0]
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_bundle(ladybug, cache: Path, bundle: Path, *, plat: str | None = None,
                 repo: Path = _REPO) -> dict[str, str]:
    """확장 3개를 bundle 로 확보하고 {확장: "출처 경로 sha256=<앞 12자>"} 를 돌려준다.

    상류 확장 서버에서는 받지 않는다. 상류는 같은 판 경로의 파일을 제자리에서 다시 빌드해
    올리고, 엣지 캐시는 옛 빌드를 몇 주씩 내준다. 그래서 `INSTALL` 로 받은 바이트는 러너가
    닿은 엣지에 따라 달라지고, 설치된 엔진과 맞는다는 보장도 없다. 출처는 엔진 판의 핀과
    해시가 같은 파일뿐이다:

      · linux_amd64: 리포의 `packaging/lbug_extensions` (허브 이미지와 CI 가 싣는 바이트).
        핀이 없거나 해시가 다르면 `EngineMismatch` (테스트 실패).
      · 그 밖: `~/.lbdb` 캐시에서 해시가 핀과 같은 파일. 없으면 `BundleUnavailable` (skip).

    근거: [[record:upstream-rebuilt-the-ladybug-0-21-vector-and-fts-too-and-cache-edges-serve-either-build-2026-10-22]]
    """
    import shutil
    version = str(getattr(ladybug, "__version__", ""))
    plat = _platform_tag() if plat is None else plat
    pins = _pinned_hashes(version, plat, repo)
    pin_name = f"packaging/lbug-ext-{version}.sha256"
    sources: dict[str, str] = {}
    for e in _NEED:
        want = pins.get(e, "")
        if plat == "linux_amd64":
            src = repo / "packaging" / "lbug_extensions" / f"lib{e}.lbug_extension"
            got = _sha256(src) if src.is_file() else "파일 없음"
            if not want or got != want:
                raise EngineMismatch(
                    f"판 불일치: 엔진 ladybug {version} ({plat}) 의 핀 {pin_name} 에 "
                    f"{e} 해시가 {want or '없다'}, 리포 번들 {src} 는 {got}. 엔진 핀과 "
                    f"확장 핀·번들은 같은 판으로 함께 올린다.")
        else:
            if not want:
                raise BundleUnavailable(
                    f"엔진 ladybug {version} ({plat or sys.platform}) 의 핀({pin_name})이 없다")
            cands = sorted(cache.glob(f"extension/*/{plat}/{e}/lib{e}.lbug_extension"))
            src = next((c for c in cands if _sha256(c) == want), None)
            if src is None:
                raise BundleUnavailable(
                    f"{e}: 핀 해시 {want[:12]} 와 같은 캐시 파일이 없다(후보 {len(cands)}개)")
        shutil.copy(src, bundle / f"lib{e}.lbug_extension")
        sources[e] = f"{src} sha256={want[:12]}"
    return sources


class SeedBundleTests(unittest.TestCase):
    """`_seed_bundle` 은 핀과 같은 바이트만 번들에 싣는다(순수, 드라이버 무관)."""

    class _Lb:
        __version__ = "0.21.2"

    def _repo(self, payload: bytes, pin: bytes | None) -> Path:
        repo = Path(tempfile.mkdtemp(prefix="g0repo_"))
        (repo / "packaging" / "lbug_extensions").mkdir(parents=True)
        lines = []
        for e in _NEED:
            data = payload + e.encode()
            (repo / "packaging" / "lbug_extensions" / f"lib{e}.lbug_extension").write_bytes(data)
            digest = hashlib.sha256(pin + e.encode() if pin is not None else data).hexdigest()
            lines.append(f"{digest}  linux_amd64-lib{e}.lbug_extension")
        (repo / "packaging" / "lbug-ext-0.21.2.sha256").write_text("\n".join(lines) + "\n",
                                                                     encoding="utf-8")
        return repo

    def test_linux_copies_the_pinned_repo_bundle(self):
        repo = self._repo(b"pinned-", None)
        bundle = Path(tempfile.mkdtemp())
        got = _seed_bundle(self._Lb, Path(tempfile.mkdtemp()), bundle,
                           plat="linux_amd64", repo=repo)
        self.assertEqual(sorted(got), sorted(_NEED))
        self.assertEqual((bundle / "libvector.lbug_extension").read_bytes(), b"pinned-vector")

    def test_linux_names_a_version_mismatch_instead_of_loading(self):
        """핀과 다른 바이트는 로드하기 전에 판 불일치로 멈춘다. 심볼 오류는 원인을 숨긴다."""
        repo = self._repo(b"rebuilt-", b"pinned-")
        with self.assertRaises(EngineMismatch) as ctx:
            _seed_bundle(self._Lb, Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()),
                         plat="linux_amd64", repo=repo)
        msg = str(ctx.exception)
        self.assertIn("판 불일치", msg)
        self.assertIn("ladybug 0.21.2", msg)
        self.assertIn("lbug-ext-0.21.2.sha256", msg)

    def test_linux_without_a_pin_for_the_engine_is_a_mismatch(self):
        repo = self._repo(b"pinned-", None)

        class _Newer:
            __version__ = "0.21.3"

        with self.assertRaises(EngineMismatch):
            _seed_bundle(_Newer, Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()),
                         plat="linux_amd64", repo=repo)

    def test_elsewhere_takes_only_cache_files_with_the_pinned_hash(self):
        repo = Path(tempfile.mkdtemp(prefix="g0repo_"))
        (repo / "packaging").mkdir()
        cache = Path(tempfile.mkdtemp(prefix="g0cache_"))
        lines = []
        for e in _NEED:
            good = cache / "extension" / "0.21.0" / "win_amd64" / e / f"lib{e}.lbug_extension"
            good.parent.mkdir(parents=True)
            good.write_bytes(b"pinned-" + e.encode())
            lines.append(f"{_sha256(good)}  win_amd64-lib{e}.lbug_extension")
        # 같은 자리 옆 판 디렉터리의 다시 빌드된 바이트는 집지 않는다.
        stray = cache / "extension" / "0.21.2" / "win_amd64" / "vector" / "libvector.lbug_extension"
        stray.parent.mkdir(parents=True)
        stray.write_bytes(b"rebuilt-vector")
        (repo / "packaging" / "lbug-ext-0.21.2.sha256").write_text("\n".join(lines) + "\n",
                                                                     encoding="utf-8")
        bundle = Path(tempfile.mkdtemp())
        _seed_bundle(self._Lb, cache, bundle, plat="win_amd64", repo=repo)
        self.assertEqual((bundle / "libvector.lbug_extension").read_bytes(), b"pinned-vector")

    def test_elsewhere_without_a_matching_file_skips(self):
        repo = self._repo(b"pinned-", None)
        with self.assertRaises(BundleUnavailable):
            _seed_bundle(self._Lb, Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()),
                         plat="osx_arm64", repo=repo)


if __name__ == "__main__":
    unittest.main()
