# -*- coding: utf-8 -*-
"""데스크탑 릴리스의 ladybug 확장은 상류 서버가 아니라 이 리포의 릴리스에서 온다(VIN-135).

상류 확장 서버는 최근 다섯 판만 내려 준다. 핀한 판이 빠지는 날 릴리스의 시드 단계가
깨지므로, 세 플랫폼 확장 9개를 Vinc 리포 릴리스 `ladybug-ext-<ver>` 에 두고 리포에는
해시 목록 `packaging/lbug-ext-<ver>.sha256` 만 둔다. 이 테스트는 그 세 자리(시드 단계 ·
해시 목록 · 버전 핀)가 같은 판을 가리키는지 잠근다. 한 곳만 판을 올리면 해시 대조가
빌드를 세우거나, 테스트한 엔진과 다른 확장이 실린다.
근거: [[decision:ladybug-extensions-ship-from-a-vinc-release-not-the-repo-2026-09-26]]
"""
from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
PLATS = ("linux_amd64", "win_amd64", "osx_arm64")
EXTS = ("algo", "fts", "vector")


def _seed_step() -> str:
    at = RELEASE.index("- name: Seed LadybugDB extensions")
    return RELEASE[at:RELEASE.index("- name: Build sidecar binary", at)]


def _ver() -> str:
    m = re.search(r'LBUG_VER: "([0-9.]+)"', _seed_step())
    assert m, "시드 단계에 LBUG_VER 가 없다"
    return m.group(1)


class LadybugVendorTests(unittest.TestCase):
    def test_해시_목록이_세_플랫폼_아홉_개를_담는다(self):
        sums = ROOT / "packaging" / f"lbug-ext-{_ver()}.sha256"
        rows = [ln.split() for ln in sums.read_text(encoding="utf-8").splitlines() if ln.strip()]
        names = {name for _, name in rows}
        self.assertEqual(names, {f"{p}-lib{e}.lbug_extension" for p in PLATS for e in EXTS})
        for digest, name in rows:
            self.assertRegex(digest, r"^[0-9a-f]{64}$", name)

    def test_시드_단계는_릴리스에서_받아_해시를_대조한다(self):
        step = _seed_step()
        self.assertIn('gh release download "ladybug-ext-$LBUG_VER"', step)
        self.assertIn("packaging/lbug-ext-$LBUG_VER.sha256", step)
        self.assertIn("-c -", step, "받은 파일을 해시 목록과 대조하지 않는다")
        self.assertIn(r"tr -d '\r'", step, "CRLF 목록은 perl shasum 의 해시 대조를 깬다")
        self.assertNotIn("extension.ladybugdb.com", step, "상류 서버에서 다시 받는다")
        self.assertIn("shasum -a 256", step, "맥 러너에는 sha256sum 이 없다")

    def test_엔진_핀과_확장_판이_같다(self):
        ver = _ver()
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'embedded = ["ladybug=={ver}"]', pyproject)
        self.assertIn(f"pip install ladybug=={ver}", RELEASE)

    def test_허브의_리눅스_사본이_릴리스와_같은_바이트다(self):
        """허브(Dockerfile.hub)와 CI 는 리포의 `packaging/lbug_extensions/` 를 쓴다. 그 사본이
        릴리스의 리눅스 자산과 달라지면 허브와 데스크탑이 서로 다른 확장을 싣는다."""
        sums = dict(
            (name, digest) for digest, name in (
                ln.split() for ln in (ROOT / "packaging" / f"lbug-ext-{_ver()}.sha256")
                .read_text(encoding="utf-8").splitlines() if ln.strip()))
        for e in EXTS:
            data = (ROOT / "packaging" / "lbug_extensions" / f"lib{e}.lbug_extension").read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),
                             sums[f"linux_amd64-lib{e}.lbug_extension"], e)


if __name__ == "__main__":
    unittest.main()
