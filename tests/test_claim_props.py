# -*- coding: utf-8 -*-
"""R4 — 코드에 대한 주장은 문장만이 아니라 **구조화된 필드**다.

[[d4ab94561078498e3]] §7.3. 산문 규약(§3: "측정으로 쓰고 경로를 적는다")은 사람이
읽어야 비교된다. 두 문서가 같은 파일을 두고 어긋난 주장을 하는 것을 기계가 찾으려면
조인할 키가 있어야 하고, 그 키가 `props.subject_path` 다.

여기서 고정하는 것은 **필드의 존재가 아니라 값의 비교 가능성**이다. `api\\sync.py` 와
`./api/sync.py` 와 `api/sync.py` 가 서로 다른 주제로 남으면 R5 의 질의는 조용히 0건을
낸다 — 그러면 필드를 넣고도 R4 는 성립하지 않은 것이다."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import fragment as frag  # noqa: E402
from store import pkns  # noqa: E402

_BS = chr(92)   # 백슬래시. 리터럴로 쓰면 이 파일을 옮기는 층마다 한 겹씩 먹는다.

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass


class SubjectPathFoldingTests(unittest.TestCase):
    """조인 키는 한 형태로 접혀야 한다 — 이것이 R4 의 전부다."""

    def test_같은_파일의_여러_표기가_한_키로_접힌다(self):
        want = "api/sync.py"
        for raw in ("." + _BS + "api" + _BS + "sync.py",
                    "api" + _BS + "sync.py",
                    "./api/sync.py", "/api/sync.py", "  api/sync.py  ",
                    "api/sync.py"):
            self.assertEqual(frag._norm_subject_path(raw), want, raw)

    def test_빈_값은_빈_문자열이다(self):
        for raw in (None, "", "   ", "./"):
            self.assertEqual(frag._norm_subject_path(raw), "")

    def test_디렉터리와_확장자_없는_경로도_그대로_접힌다(self):
        self.assertEqual(frag._norm_subject_path(".\\app\\src"), "app/src")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ClaimPropsE2E(unittest.TestCase):
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

    def _ingest(self, node):
        return frag.merge_fragment({"owner": self.OWNER, "nodes": [node]},
                                   store=self.store, read_back=True)

    def _props(self, res, nid):
        return next(n for n in res["stored"] if n["id"] == nid)["props"]

    def test_저장된_subject_path_가_정규화된_형태다(self):
        """되읽기(R3)로 확인한다 — 정규화가 응답에서 보이는 것이 R3 가 준 것이다."""
        res = self._ingest({
            "id": "record:claim1", "type": "record", "title": "측정",
            "props": {"subject_path": "." + _BS + "api" + _BS + "sync.py",
                      "measured_at": "2026-08-24", "measured_ref": "main"}})
        p = self._props(res, "record:claim1")
        self.assertEqual(p["subject_path"], "api/sync.py")
        self.assertEqual(p["measured_at"], "2026-08-24")
        self.assertEqual(p["measured_ref"], "main")

    def test_다른_표기로_들어온_두_기록이_같은_키를_공유한다(self):
        """R5 가 찾을 짝이 실제로 짝이 되는지 — 이 테스트가 R4 의 인수 조건이다."""
        self._ingest({"id": "record:a", "type": "record", "title": "A",
                      "props": {"subject_path": "api" + _BS + "sync.py"}})
        self._ingest({"id": "record:b", "type": "record", "title": "B",
                      "props": {"subject_path": "./api/sync.py"}})
        rows = self.store.run(
            "MATCH (r:Record) WHERE r.owner_id = $o RETURN r.record_id AS id, "
            "r.props AS props", {"o": self.OWNER})
        paths = {json.loads(r["props"]).get("subject_path") for r in rows}
        self.assertEqual(paths, {"api/sync.py"})

    def test_props_원본을_망가뜨리지_않는다(self):
        """부분 재전송 함정 — 예약 키 하나를 접느라 나머지를 잃으면 안 된다."""
        res = self._ingest({
            "id": "record:keep", "type": "record", "title": "K",
            "props": {"subject_path": "/api/sync.py", "note": "다른 값",
                      "ko": "한국어"}})
        p = self._props(res, "record:keep")
        self.assertEqual(p, {"subject_path": "api/sync.py", "note": "다른 값",
                             "ko": "한국어"})

    def test_기록이_아닌_종류에_실으면_경고한다(self):
        """개념은 서술하고 결정은 고른다. 측정하는 것은 기록이다 — 다른 데 실린
        주장 필드는 저장은 되지만 주장 검토가 읽지 않으므로, 말해 줘야 한다."""
        res = self._ingest({
            "id": "concept:c", "type": "concept", "title": "C",
            "props": {"subject_path": "api/sync.py"}})
        self.assertTrue(any("claim field" in w for w in res["warnings"]),
                        res["warnings"])
        # 저장은 된다 — 자유 속성을 서버가 버리지 않는다.
        self.assertEqual(self._props(res, "concept:c")["subject_path"], "api/sync.py")

    def test_날짜_모양이_아닌_measured_at_은_경고하되_지우지_않는다(self):
        res = self._ingest({
            "id": "record:bad", "type": "record", "title": "B",
            "props": {"subject_path": "api/sync.py", "measured_at": "어제"}})
        self.assertTrue(any("measured_at" in w for w in res["warnings"]),
                        res["warnings"])
        self.assertEqual(self._props(res, "record:bad")["measured_at"], "어제")

    def test_예약_키가_없으면_아무것도_안_한다(self):
        res = self._ingest({"id": "record:plain", "type": "record", "title": "P",
                            "props": {"note": "그냥 기록"}})
        self.assertEqual(res["warnings"], [])
        self.assertEqual(self._props(res, "record:plain"), {"note": "그냥 기록"})


class ConventionsTemplateTests(unittest.TestCase):
    """규약이 문서에만 있으면 규약이 아니다 — 쓰기 직전에 읽히는 자리에 있어야 한다."""

    def test_기본_관례_템플릿이_주장_필드를_말한다(self):
        from api import graphmd
        for name, body in (("en", graphmd._TPL_EN), ("ko", graphmd._TPL_KO)):
            self.assertIn("subject_path", body, name)

    def test_ingest_스키마가_주장_필드를_선언한다(self):
        from vinc_mcp.tools import _FRAGMENT_SCHEMA
        desc = _FRAGMENT_SCHEMA["properties"]["nodes"]["items"]["properties"]["props"]
        self.assertIn("subject_path", json.dumps(desc, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
