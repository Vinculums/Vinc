# -*- coding: utf-8 -*-
"""6엔진 full main() e2e — 코퍼스 픽스처를 실제 엔진 main() 으로 임베디드 스토어에
적재하고 read API(recall/domains/graph/learner/activate) + 스코프 필터 계약을
검증한다. §11 P1 게이트를 실제 엔진 실행으로 닫는다.

**embed 엔진 경계:** embed_pipeline.main() 은 chonkie/sentence-transformers +
HuggingFace 모델 다운로드를 요구해 오프라인·프록시 차단 환경에서 실행 불가하다.
embed 의 데이터 경로(벡터/FTS/RRF)는 합성 임베딩으로 test_embedded_backend 가
이미 e2e 커버한다 — 여기서는 청킹/임베딩 생성이 필요 없는 5개 엔진의 main()
(recall·concepts·bridges·skill·learner)을 실제로 돌린다.

드라이버(ladybug/kuzu) 미설치 시 skip. 순차 open/close 라 인프로세스로 동작.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()

_OWNER = "user:e2e"

# ── 코퍼스 픽스처 (각 엔진의 파서 규약에 맞춘 최소 입력) ──────────────────────
_SESSION_MD = """---
title: Refund policy planning
owner: user:e2e
domain: math
workspace: planning
mode: advisor
---
# Refund policy planning

## 세션 요약
환불 정책 초안을 논의하고 선호 신호를 정리했다.
"""

_CONCEPT_01 = """---
status: active
---
# Group Theory

## 한 줄 핵심
군은 결합법칙을 만족하는 이항연산을 가진 집합이다. 다음은 [[02-rings]].
"""

_CONCEPT_02 = """---
status: active
---
# Ring Theory

## 한 줄 핵심
환은 두 이항연산을 가진 대수 구조다.
"""

_BRIDGE_MD = """---
status: active
---
# Symmetry in visual art
"""

# learner user-model: | **{code} {name}** — {desc} | {domain} | {obs} | {ex} |
_USER_MODEL_MD = """# user-model

| pattern | domain | n | examples |
|---|---|---|---|
| **U-1 structure-first** — 배경 골격부터 정리한 뒤 세부로 | 전체 | 12 | ex |
| **U-2 cross-domain** — 도메인 간 유추로 흡수 | math | 7 | ex |
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug/kuzu) 미설치 — e2e skip")
class FullPipelineE2E(unittest.TestCase):
    def setUp(self):
        self.hub = Path(tempfile.mkdtemp())
        # 픽스처 배치
        _write(self.hub / "CONTEXT/memory/recall/2026-07-01/session-01.md", _SESSION_MD)
        _write(self.hub / "WORKSPACES/learning/math/algebra/01-groups.md", _CONCEPT_01)
        _write(self.hub / "WORKSPACES/learning/math/algebra/02-rings.md", _CONCEPT_02)
        _write(self.hub / "WORKSPACES/learning/math/art-bridges/bridge-01.md", _BRIDGE_MD)
        _write(self.hub / "user-model.md", _USER_MODEL_MD)

        self._cwd = os.getcwd()
        os.chdir(self.hub)   # recall 은 상대경로(CONTEXT/…)를 CWD 기준으로 읽는다
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": str(self.hub / "store"),
            "MEMORY_EMBEDDING_MODEL": "local-en",
            "MEMORY_DEFAULT_OWNER": _OWNER,
            "MEMORY_HUB_ROOT": str(self.hub),
            "MEMORY_BRIDGE_SOURCE": "math",
        }.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v

    def tearDown(self):
        os.chdir(self._cwd)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _run_engine(self, module_name: str, argv: list[str]) -> None:
        """엔진 main() 을 실제 argv 로 실행(모듈 캐시된 HUB 는 --hub-root 로 재설정)."""
        mod = importlib.import_module(f"engines.{module_name}")
        old = sys.argv
        sys.argv = [f"{module_name}.py", *argv]
        try:
            mod.main()
        finally:
            sys.argv = old

    def test_five_engines_load_then_read_apis(self):
        from store.config import load_config
        from store.backend import open_store
        from api import query as q

        cfg = load_config()
        # 스키마 선행(엔진은 provision 하지 않음, §5)
        store = open_store(cfg)
        self.assertIsNotNone(store, "임베디드 스토어 open 실패")
        store.provision(dim=cfg.embedding_dim)
        store.close()

        # ── 5개 엔진 full main() 순차 실행 ──────────────────────────────────
        self._run_engine("recall_to_neo4j", [])
        self._run_engine("concepts_to_neo4j", ["--hub-root", str(self.hub)])
        self._run_engine("bridges_to_neo4j", ["--hub-root", str(self.hub)])
        self._run_engine("learner_to_neo4j", ["--source", str(self.hub / "user-model.md"),
                                              "--owner", _OWNER])
        self._run_engine("skill_tracker", ["--action", "log", "--skill", "planning",
                                           "--workspace", "planning", "--mode", "PM_PLANNING",
                                           "--outcome", "success", "--duration-min", "5"])

        # ── read API 검증(§11 P1 게이트) ───────────────────────────────────
        r = q.recall(owner=_OWNER)
        self.assertTrue(r["executed"] and len(r["rows"]) > 0, "recall 미적재")

        d = q.domains(owner=_OWNER)
        self.assertTrue(d["executed"])
        self.assertIn("math", {row["domain"] for row in d["rows"]})

        g = q.graph(owner=_OWNER)
        self.assertTrue(g["executed"])
        self.assertGreaterEqual(len(g["nodes"]), 2)        # Group·Ring
        self.assertGreaterEqual(len(g["edges"]), 1)        # 01→02 [[link]] RELATED_TO

        lm = q.learner_model(owner=_OWNER)
        self.assertTrue(lm["executed"])
        self.assertEqual({p["code"] for p in lm["rows"]}, {"U-1", "U-2"})

        from api import activate as act
        ap = act.activate(owner=_OWNER)
        self.assertTrue(ap["executed"])
        self.assertGreater(ap["episode_count"], 0)

        # ── 스코프 필터 계약(§1): 다른 owner 는 0행 ────────────────────────
        other = q.recall(owner="user:stranger")
        self.assertEqual(other["rows"], [], "스코프 밖 owner 데이터 혼입 — §1 위반")

        # ── 노드 카운트(적재 커버리지) ─────────────────────────────────────
        store = open_store(cfg)
        try:
            counts = {lbl: store.run(f"MATCH (n:{lbl}) RETURN count(n) AS c")[0]["c"]
                      for lbl in ("Episode", "Concept", "Bridge", "LearnerPattern",
                                  "SkillExecution")}
        finally:
            store.close()
        self.assertEqual(counts["Episode"], 1)
        self.assertEqual(counts["Concept"], 2)
        self.assertGreaterEqual(counts["Bridge"], 1)
        self.assertEqual(counts["LearnerPattern"], 2)
        self.assertEqual(counts["SkillExecution"], 1)


if __name__ == "__main__":
    unittest.main()
