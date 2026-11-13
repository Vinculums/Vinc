# -*- coding: utf-8 -*-
"""R4 — 코드에 대한 주장은 문장만이 아니라 **구조화된 필드**다.

[[vinc.ops.graph-claim-decay]] §7.3. 산문 규약(§3: "측정으로 쓰고 경로를 적는다")은 사람이
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment as frag  # noqa: E402
from store import pkns  # noqa: E402

_BS = chr(92)   # 백슬래시. 리터럴로 쓰면 이 파일을 옮기는 층마다 한 겹씩 먹는다.

_DRIVER = None
try:
    from store.embedded_backend import import_driver
    _DRIVER, _ = import_driver()
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
            "props": {"subject_path": "vinc:." + _BS + "api" + _BS + "sync.py",
                      "measured_at": "2026-08-24", "measured_ref": "main"}})
        p = self._props(res, "record:claim1")
        self.assertEqual(p["subject_path"], "vinc:api/sync.py")
        self.assertEqual(p["measured_at"], "2026-08-24")
        self.assertEqual(p["measured_ref"], "main")

    def test_다른_표기로_들어온_두_기록이_같은_키를_공유한다(self):
        """R5 가 찾을 짝이 실제로 짝이 되는지 — 이 테스트가 R4 의 인수 조건이다."""
        self._ingest({"id": "record:a", "type": "record", "title": "A",
                      "props": {"subject_path": "vinc:api" + _BS + "sync.py"}})
        self._ingest({"id": "record:b", "type": "record", "title": "B",
                      "props": {"subject_path": "VINC:./api/sync.py"}})
        rows = self.store.run(
            "MATCH (r:Record) WHERE r.owner_id = $o RETURN r.record_id AS id, "
            "r.props AS props", {"o": self.OWNER})
        paths = {json.loads(r["props"]).get("subject_path") for r in rows}
        self.assertEqual(paths, {"vinc:api/sync.py"})

    def test_props_원본을_망가뜨리지_않는다(self):
        """부분 재전송 함정 — 예약 키 하나를 접느라 나머지를 잃으면 안 된다."""
        res = self._ingest({
            "id": "record:keep", "type": "record", "title": "K",
            "props": {"subject_path": "vinc:/api/sync.py", "note": "다른 값",
                      "ko": "한국어"}})
        p = self._props(res, "record:keep")
        self.assertEqual(p, {"subject_path": "vinc:api/sync.py", "note": "다른 값",
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
        for lang in ("en", "ko"):
            self.assertIn("subject_path", graphmd.build(None, lang=lang)["text"], lang)

    def test_ingest_스키마가_주장_필드를_선언한다(self):
        from vinc_mcp.tools import _FRAGMENT_SCHEMA
        desc = _FRAGMENT_SCHEMA["properties"]["nodes"]["items"]["properties"]["props"]
        self.assertIn("subject_path", json.dumps(desc, ensure_ascii=False))


class RepoQualifierTests(unittest.TestCase):
    """조인 키는 `repo:path` 다. 경로만으로는 부족하다.

    이 그래프는 리포 셋(Vinc·Vinc-homepage·Vincs-app)에 대한 기록을 갖는다. 닻이
    경로만 담으면 동명 파일 둘이 한 키에 묶여 **짝이 아닌 것이 짝으로** 제시되고,
    사람이 경로를 열어 아무 문제도 못 찾는 값을 치른다. 닻이 빠진 것보다 비싸다 —
    빠진 것은 목록에 뜨고 정직하다.

    실측(2026-08-25)으로 온 요구다: 닻 없는 43건의 힌트에 이미 `billing/service.ts`
    처럼 홈페이지 파일이 섞여 있었다."""

    def test_한정자를_보존하고_리포를_소문자로_접는다(self):
        self.assertEqual(frag._norm_subject_path("VINC:./api/sync.py"),
                         "vinc:api/sync.py")
        self.assertEqual(frag._norm_subject_path("vinc-homepage:./account/src/x.ts"),
                         "vinc-homepage:account/src/x.ts")

    def test_한정자가_있어도_경로부는_그대로_접힌다(self):
        want = "vinc:api/sync.py"
        for raw in ("vinc:" + _BS + "api" + _BS + "sync.py", "vinc:/api/sync.py",
                    "  vinc:./api/sync.py  "):
            self.assertEqual(frag._norm_subject_path(raw), want, raw)

    def test_윈도_드라이브_문자는_한정자가_아니다(self):
        """`D:/tmp/x.py` 를 리포 `d` 로 읽으면 경로가 통째로 다른 네임스페이스로 간다.
        한 글자짜리는 리포가 아니다."""
        self.assertEqual(frag._norm_subject_path("D:/tmp/x.py"), "D:/tmp/x.py")

    def test_한정자를_지어내지_않는다(self):
        """어느 리포인지는 값이 말해야 한다. 서버는 모르고, 모르는 것을 채우면
        그 순간 조용히 틀린 닻이 된다."""
        self.assertEqual(frag._norm_subject_path("api/sync.py"), "api/sync.py")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class RepoQualifierWarningTests(unittest.TestCase):
    """한정자가 빠지거나 오타 나면 **말한다.** 막지는 않는다 — 리포는 늘 수 있고
    목록은 낡을 수 있다. 오타 난 한정자가 특히 나쁜 이유는 조용히 자기만의
    네임스페이스를 만들면서 아무 오류도 안 내기 때문이다."""

    def _warn(self, path, known=("vinc", "vinc-homepage", "vincs-app")):
        w: list = []
        frag._claim_props({"subject_path": path}, "Record", "record:x", w,
                          known_repos=lambda: set(known))
        return w

    def test_맨_경로는_리포가_없다고_말한다(self):
        w = self._warn("api/sync.py")
        self.assertTrue(any("names no repository" in x for x in w), w)

    def test_모르는_리포는_말한다(self):
        w = self._warn("vnic:api/sync.py")
        self.assertTrue(any("is new to this graph" in x for x in w), w)

    def test_리포_목록은_코드가_아니라_그래프에서_온다(self):
        """vincos 처럼 새 리포도, 다른 사용자의 리포도 그 그래프가 이미 쓴 이름이면 조용하다."""
        self.assertEqual(self._warn("vincos:vincos/zones.py", known=("vinc", "vincos")), [])
        self.assertEqual(self._warn("acme:src/app.py", known=("acme",)), [])

    def test_첫_기록은_이름을_묻지_않는다(self):
        """그래프가 아직 아무 리포도 모르면 비교할 것이 없다. 형식만 본다."""
        self.assertEqual(self._warn("anything:src/x.py", known=()), [])

    def test_아는_리포는_조용하다(self):
        for good in ("vinc:api/sync.py", "vinc-homepage:account/src/x.ts",
                     "vincs-app:src/main.ts"):
            self.assertEqual(self._warn(good), [], good)


#: 아래 파생 클래스가 달고 다니는 설명.
_NS_DOC = """같은 검사 전부를 **PK 네임스페이싱을 켠 채** 다시 돈다.

이 자리는 실제로 물렸다(2026-08-25 CI): 되돌림표를 물리 키로 지었는데 읽기 훅이
돌려주는 값을 이미 벗겨서 주기 때문에 조회가 전부 빗나갔고, `stored` 가 조용히 빈
목록이 됐다. 기본 스위트는 플래그가 꺼져 있어 초록으로 통과했다.

그래서 검사를 새로 쓰지 않고 **있는 검사를 두 모드로 돌린다** — 한 모드에서만 참인
것을 못 쓰게 하는 것이 요점이고, 새 단언을 더하는 것으로는 그 성질이 안 온다."""


def _namespaced(base):
    """base 의 검사 전부를 플래그 켠 채 도는 클래스로 만든다."""

    class _Ns(base):
        def setUp(self):
            self._pk_prev = os.environ.get("VINC_PK_NAMESPACED")
            os.environ["VINC_PK_NAMESPACED"] = "on"
            super().setUp()

        def tearDown(self):
            try:
                super().tearDown()
            finally:
                if self._pk_prev is None:
                    os.environ.pop("VINC_PK_NAMESPACED", None)
                else:
                    os.environ["VINC_PK_NAMESPACED"] = self._pk_prev

    _Ns.__name__ = _Ns.__qualname__ = base.__name__ + "UnderNamespacing"
    _Ns.__doc__ = _NS_DOC
    return _Ns


ClaimPropsUnderNamespacing = _namespaced(ClaimPropsE2E)


if __name__ == "__main__":
    unittest.main()
