# -*- coding: utf-8 -*-
"""A script may not point a reader at a script that does not exist.

Vinculums/Vinc#136 found `scripts/migrate_pk_namespacing.py` naming
`scripts/pkns_preflight.py` as the canonical place for the snapshot
procedure, twice, while the same docstring said a few lines up that the file
had been removed (#128). The file was gone, so an operator following the
migration procedure went looking for a procedure that was nowhere.

This is the shape of failure that survives review: the reference and the
deletion are true statements written months apart, and nothing reads both.
So a test reads both.

**The removal record is allowed to stay.** A line saying a script *was*
removed is history and is the reason a reader is not confused; a line
telling them to *go read it* is the defect. The two are told apart by the
markers below, not by guessing at the sentence.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

#: `…scripts/<이름>.py` 꼴의 인용. 코드의 import 가 아니라 산문 안의 경로를 찾는다.
#: 앞자리(`scripts/design/` 등)를 함께 잡는 이유는, 리포에 `scripts/` 가 둘이라
#: 꼬리만 보면 허브의 파일을 루트에 없다고 오판하기 때문이다.
_REF = re.compile(r"([A-Za-z0-9_./-]*?scripts/[A-Za-z0-9_]+\.py)")

#: 이 표지가 같은 줄에 있으면 "없어진 것을 없어졌다고 적은 줄"로 본다.
#: 늘리기 전에 생각할 것 — 표지를 늘리는 것은 예외를 늘리는 것이다.
_HISTORY = ("걷어냈다", "걷어낸", "삭제했다", "없다", "removed", "deleted")

#: 훑을 곳. 산문이 실려 있고 사람이 절차를 찾아 읽는 파일들이다.
#: 테스트는 팀 폴더(`tests/<패키지>/`)에 있다. `tests/*.py` 는 그 위의 헬퍼만 본다.
_SWEEP = ("scripts/*.py", "tests/*.py", "tests/*/test_*.py")

#: 이 파일 자신. 없어진 파일의 이름을 적는 것이 일이라 훑으면 제가 걸린다.
_SELF = Path(__file__).name


class ScriptReferencesResolve(unittest.TestCase):
    def test_인용된_스크립트가_전부_실재한다(self):
        dangling: list[str] = []
        for pattern in _SWEEP:
            for path in sorted(_ROOT.glob(pattern)):
                if path.name == _SELF:
                    continue
                text = path.read_text(encoding="utf-8")
                for lineno, line in enumerate(text.splitlines(), 1):
                    for ref in _REF.findall(line):
                        if (_ROOT / ref).exists():
                            continue
                        if any(marker in line for marker in _HISTORY):
                            continue
                        rel = path.relative_to(_ROOT).as_posix()
                        dangling.append(f"{rel}:{lineno} → {ref} (없는 파일)")
        self.assertEqual(
            dangling,
            [],
            "없는 스크립트를 정본으로 가리키는 줄이 있다. 그 파일을 되살리거나, "
            "절차를 인용하는 자리에 직접 적거나, 그 줄을 '걷어냈다'는 기록으로 "
            "고칠 것:\n  " + "\n  ".join(dangling),
        )

    def test_없어진_파일을_기록으로_적은_줄은_통과시킨다(self):
        """#136 이 남겨 둔 그 한 줄이 실제로 통과한다는 것을 못 박는다.

        이 검사가 없으면 다음 사람이 위 표지를 없애고 "전부 실재해야 한다"로
        조여서, 이 이관이 왜 두 번 측정될 뻔했는지를 적은 기록을 지우게 된다."""
        doc = (_ROOT / "scripts" / "migrate_pk_namespacing.py").read_text(
            encoding="utf-8"
        )
        removal = [
            ln for ln in doc.splitlines()
            if "pkns_preflight" in ln and "걷어냈다" in ln
        ]
        self.assertEqual(len(removal), 1, doc[:400])
        self.assertFalse((_ROOT / "scripts" / "pkns_preflight.py").exists())


if __name__ == "__main__":
    unittest.main()
