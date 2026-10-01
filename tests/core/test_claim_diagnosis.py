# -*- coding: utf-8 -*-
"""R5 — `vinc_diagnose(kind='claims')`: 어긋난 주장 짝과 닻 없는 주장.

[[vinc.ops.graph-claim-decay]] §6.1 이 지목한 것은 규약의 부재가 아니라 **러너의 부재**였다.
"조인 키가 있으면 질의 하나면 된다"고 써 놓고 누가 언제 그 질의를 도는지는 아무 데도
없었고, 남은 것은 읽는 사람의 성실함이었다. §1 에서 실패한 것이 바로 그 성실함이다.

여기서 고정하는 것:
  ① R4 의 정규화가 실제로 조인을 성립시킨다 — 표기가 다른 두 기록이 한 짝이 된다.
  ② 판정하지 않는다. 짝만 내놓는다(어느 쪽이 옳은지는 경로를 열어야 안다).
  ③ 짝짓기는 최신 대 나머지다. n² 가 아니고, 방향이 "이 옛 주장이 아직 서 있나"다.
  ④ 코드를 말하면서 경로가 없는 기록은 셀 수 있다 — 줄일 수 있는 수가 된다.
  ⑤ algo 확장이 없어도 답한다(나머지 세 kind 와 다른 점)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment as frag  # noqa: E402
from api import query as q  # noqa: E402

_BS = chr(92)

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass


class PathTokenTests(unittest.TestCase):
    """닻 없는 주장을 고르는 판별자. 너무 넓으면 목록이 소음이 되어 아무도 안 줄인다."""

    def _hint(self, text: str) -> str:
        return q._path_hint({"note": text}, "제목")

    def test_경로를_잡는다(self):
        for t in ("api/sync.py", "app/src/surfaces/view3d.ts",
                  "scripts/gate_forbidden.py"):
            self.assertEqual(self._hint(f"측정: {t} 를 읽었다"), t)

    def test_경로가_아닌_것은_안_잡는다(self):
        for t in ("and/or 둘 중 하나", "2026-08-17 에 쟀다", "경로 없음",
                  "read https://vincs.io/docs/x.html today"):
            self.assertEqual(self._hint(t), "", t)

    def test_제목에만_있어도_잡는다(self):
        self.assertEqual(q._path_hint({}, "api/sync.py carries created"),
                         "api/sync.py")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ClaimDiagnosisE2E(unittest.TestCase):
    OWNER = "user:a"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ingest(self, nodes, owner=None):
        return frag.merge_fragment({"owner": owner or self.OWNER, "nodes": nodes},
                                   store=self.store)

    def _run(self, **kw):
        return q.claim_diagnosis(owner=self.OWNER, store=self.store, **kw)

    def test_다른_표기의_두_주장이_한_짝이_된다(self):
        """R4 의 쓰기 시점 정규화가 없으면 이 테스트는 0건을 내면서 통과한다 —
        그래서 두 표기를 일부러 다르게 넣는다."""
        self._ingest([
            {"id": "record:old", "type": "record", "title": "옛 측정",
             "props": {"subject_path": "api" + _BS + "sync.py",
                       "measured_at": "2026-08-17", "measured_ref": "branch-x"}},
            {"id": "record:new", "type": "record", "title": "새 측정",
             "props": {"subject_path": "./api/sync.py",
                       "measured_at": "2026-08-24", "measured_ref": "main"}}])
        out = self._run()
        self.assertTrue(out["executed"], out)
        self.assertEqual(out["conflict_count"], 1, out["conflicts"])
        c = out["conflicts"][0]
        self.assertEqual(c["subject_path"], "api/sync.py")
        self.assertEqual(c["newer"]["id"], "record:new")
        self.assertEqual(c["older"]["id"], "record:old")
        self.assertEqual(c["newer"]["measured_ref"], "main")

    def test_판정하지_않는다(self):
        """옳고 그름을 담은 필드가 없어야 한다 — 있으면 그것이 읽히고, 경로를 여는
        일이 사라진다. 그 열어 보기가 이 도구가 대신 못 하는 잔여다(§7.5)."""
        self._ingest([
            {"id": "record:a", "type": "record", "title": "A",
             "props": {"subject_path": "api/x.py", "measured_at": "2026-01-01"}},
            {"id": "record:b", "type": "record", "title": "B",
             "props": {"subject_path": "api/x.py", "measured_at": "2026-02-01"}}])
        c = self._run()["conflicts"][0]
        for banned in ("verdict", "winner", "correct", "stale", "resolved"):
            self.assertNotIn(banned, c, banned)

    def test_최신_대_나머지로_짝짓는다(self):
        """셋이면 짝은 둘이다(n² 면 셋). 물어야 할 것은 '옛 주장이 아직 서 있나'이지
        '옛 주장 둘이 서로 맞나'가 아니다."""
        self._ingest([
            {"id": f"record:m{i}", "type": "record", "title": f"M{i}",
             "props": {"subject_path": "api/y.py", "measured_at": d}}
            for i, d in enumerate(("2026-01-01", "2026-02-01", "2026-03-01"))])
        out = self._run()
        self.assertEqual(out["conflict_count"], 2, out["conflicts"])
        self.assertEqual({c["newer"]["id"] for c in out["conflicts"]}, {"record:m2"})

    def test_같은_날의_두_측정은_어긋남이_아니다(self):
        self._ingest([
            {"id": "record:s1", "type": "record", "title": "S1",
             "props": {"subject_path": "api/z.py", "measured_at": "2026-05-05"}},
            {"id": "record:s2", "type": "record", "title": "S2",
             "props": {"subject_path": "api/z.py", "measured_at": "2026-05-05"}}])
        out = self._run()
        self.assertEqual(out["conflict_count"], 0, out["conflicts"])
        self.assertEqual(out["anchored_count"], 2)

    def test_날짜_없는_주장이_최신_자리를_뺏지_않는다(self):
        """빈 measured_at 이 정렬에서 앞에 오면 짝의 방향이 뒤집혀 '새 주장이 낡았다'가
        된다. 시간을 못 대는 주장은 맨 뒤다."""
        self._ingest([
            {"id": "record:dated", "type": "record", "title": "날짜 있음",
             "props": {"subject_path": "api/w.py", "measured_at": "2026-06-06"}},
            {"id": "record:undated", "type": "record", "title": "날짜 없음",
             "props": {"subject_path": "api/w.py"}}])
        c = self._run()["conflicts"][0]
        self.assertEqual(c["newer"]["id"], "record:dated")
        self.assertEqual(c["older"]["measured_at"], "")

    def test_혼자인_경로는_짝이_아니다(self):
        self._ingest([{"id": "record:lonely", "type": "record", "title": "L",
                       "props": {"subject_path": "api/only.py",
                                 "measured_at": "2026-07-07"}}])
        out = self._run()
        self.assertEqual(out["conflict_count"], 0)
        self.assertEqual(out["anchored_count"], 1)

    def test_코드를_말하는데_경로가_없으면_닻이_없다고_센다(self):
        self._ingest([
            {"id": "record:loose", "type": "record", "title": "느슨한 주장",
             "props": {"note": "api/sync.py 가 created 를 흘린다"}},
            {"id": "record:tight", "type": "record", "title": "닻 있는 주장",
             "props": {"subject_path": "api/sync.py", "measured_at": "2026-08-24"}},
            {"id": "record:notcode", "type": "record", "title": "코드 얘기가 아님",
             "props": {"note": "가격 정책은 월 단위다"}}])
        out = self._run()
        self.assertEqual([u["id"] for u in out["unanchored"]], ["record:loose"])
        self.assertEqual(out["unanchored"][0]["path_hint"], "api/sync.py")
        self.assertEqual(out["unanchored_count"], 1)
        self.assertEqual(out["anchored_count"], 1)
        self.assertEqual(out["scanned"], 3)

    def test_남의_기록은_안_본다(self):
        self._ingest([{"id": "record:mine", "type": "record", "title": "M",
                       "props": {"subject_path": "api/s.py",
                                 "measured_at": "2026-01-01"}}])
        self._ingest([{"id": "record:theirs", "type": "record", "title": "T",
                       "props": {"subject_path": "api/s.py",
                                 "measured_at": "2026-09-09"}}], owner="user:b")
        out = self._run()
        self.assertEqual(out["scanned"], 1)
        self.assertEqual(out["conflict_count"], 0)

    def test_목록이_잘리면_밝힌다(self):
        self._ingest([
            {"id": f"record:u{i}", "type": "record", "title": f"U{i}",
             "props": {"note": f"pkg/mod{i}.py 를 봤다"}} for i in range(8)])
        out = self._run(limit=3)
        self.assertEqual(len(out["unanchored"]), 3)
        self.assertEqual(out["unanchored_count"], 8)
        self.assertTrue(out["unanchored_truncated"])

    def test_기록이_없어도_실패가_아니다(self):
        out = self._run()
        self.assertTrue(out["executed"], out)
        self.assertEqual(out["scanned"], 0)


class DiagnoseContractTests(unittest.TestCase):
    """도구 계약에 kind 가 없으면 어시스턴트는 이 러너의 존재를 모른다."""

    def test_kind_enum_에_claims_가_있다(self):
        from vinc_mcp.tools import TOOLS
        spec = next(t for t in TOOLS if t["name"] == "vinc_diagnose")
        self.assertIn("claims", spec["inputSchema"]["properties"]["kind"]["enum"])
        self.assertIn("unanchored", spec["description"])

    def test_라우트가_claims_를_분기한다(self):
        import inspect
        from api import server
        src = inspect.getsource(server.VincApp.read_diagnose)
        self.assertIn('kind == "claims"', src)
        self.assertIn("claim_diagnosis", src)

    def test_모르는_kind_는_거절한다(self):
        """오타 하나가 전혀 다른 진단을 성공으로 받아 가던 자리. 성공이라 답하면서
        시킨 것을 안 하는 것이 이 PR 전체가 다루는 고장이라 같이 닫는다."""
        import inspect
        from api import server
        src = inspect.getsource(server.VincApp.read_diagnose)
        self.assertIn("unknown diagnose kind", src)


if __name__ == "__main__":
    unittest.main()
