# -*- coding: utf-8 -*-
"""Episode 시간 필드 계약 (F9 · 스키마 v9) — `props.date` 승격과 기존 행 백필.

배경(실측 2026-08-08, 호스티드 그래프): `vinc_recall` 이 `ORDER BY e.date DESC` 로
정렬하는데 에피소드 55건 중 16건의 `date` 열이 비어 있었다. 그 16건은 전부 `props`
안에만 날짜를 갖고 있었고(`props.date`), 나머지 39건은 반대로 `props` 가 비고 열이
차 있었다 — 두 모집단이 정확히 갈린다. 원인은 `vinc_ingest` 의 노드 계약에 `date`
가 선언돼 있지 않아 상류가 날짜를 실을 자리가 `props{}` 밖에 없었던 것이다.

여기서 못박는 것:
  1. **쓰기 승격**(`api.fragment._merge_node`) — props 에만 온 date/summary 를 열로.
     top-level 이 오면 그것이 이긴다. 문자열이 아닌 값은 승격하지 않는다.
  2. **백필**(`store.embedded_backend.backfill_episode_time_fields`) — 기존 행.
     멱등 · 비파괴 · **없는 날짜를 지어내지 않음** · 동기화 스탬프 무변경.
  3. **배선** — 구 버전 스토어를 열면 `migrate()` 가 백필을 실제로 돌린다.
"""
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
from store.embedded_backend import (_import_driver,  # noqa: E402
                                    backfill_episode_time_fields)

_DRIVER, _NAME = _import_driver()


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class EpisodeDateE2E(unittest.TestCase):
    """실엔진 왕복. 데이터 디렉토리는 tempdir 로 격리한다 — 공용 스토어를 오염시키면
    'DB 없음'을 전제하는 다른 테스트가 깨진다(실측 사고)."""

    OWNER = "user:a"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
        }.items():
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

    # ── helpers ──────────────────────────────────────────────────────────
    def _pk(self, nid: str) -> str:
        """PK 네임스페이싱이 켜졌을 때의 물리 키. 이 테스트는 API 를 거치지
        않고 스토어를 **직접** 조회하므로 물리 층의 말로 물어야 한다."""
        return pkns.key(self.OWNER, nid)

    def _row(self, eid: str) -> dict:
        rows = self.store.run(
            "MATCH (e:Episode) WHERE e.episode_id = $id "
            "RETURN e.date AS date, e.summary AS summary, e.props AS props, "
            "e.rev AS rev, e.updated_at AS updated_at, e.sync_seq AS sync_seq",
            {"id": self._pk(eid)})
        self.assertTrue(rows, f"{eid} 가 없다")
        return rows[0]

    def _ingest(self, node: dict) -> dict:
        return frag.merge_fragment({"owner": self.OWNER, "nodes": [node]},
                                   store=self.store)

    def _seed_legacy(self, eid: str, props: dict, **cols) -> None:
        """마이그레이션 **이전** 상태를 직접 만든다 — props 만 있고 date 열은 비어 있는 행.
        승격 코드를 타지 않도록 fragment 경로를 쓰지 않고 스토어에 바로 쓴다."""
        # 물리 키로 쓰고 owner_id 도 찍는다 — 백필이 행의 owner 로 키를 합성하므로
        # (store/embedded_backend), 둘이 어긋나면 백필이 자기 행을 못 찾는다.
        sets = ["e.props = $props", "e.title = $title", "e.owner_id = $owner"]
        params = {"id": self._pk(eid), "props": json.dumps(props, ensure_ascii=False),
                  "title": eid, "owner": self.OWNER}
        for c, v in cols.items():
            sets.append(f"e.{c} = ${c}")
            params[c] = v
        self.store.run(
            f"MERGE (e:Episode {{episode_id: $id}}) SET {', '.join(sets)}", params)

    # ── 1. 쓰기 승격 ──────────────────────────────────────────────────────
    def test_props_의_date_가_열로_승격된다(self):
        """이 검사가 F9 의 본체다 — 승격 전 코드에서는 date 가 null 로 남는다."""
        self._ingest({"id": "episode:p1", "type": "episode", "title": "t",
                      "props": {"date": "2026-08-07", "note": "n"}})
        row = self._row("episode:p1")
        self.assertEqual(row["date"], "2026-08-07")
        # props 원본은 깎지 않는다 — 상류가 보낸 자유 속성의 보존 계약.
        self.assertEqual(json.loads(row["props"]), {"date": "2026-08-07", "note": "n"})

    def test_props_의_summary_도_열로_승격된다(self):
        self._ingest({"id": "episode:p2", "type": "episode", "title": "t",
                      "props": {"summary": "한 문단 요약"}})
        self.assertEqual(self._row("episode:p2")["summary"], "한 문단 요약")

    def test_top_level_이_props_보다_이긴다(self):
        """기존 계약(top-level date)은 무변경 — props 는 **키가 없을 때만** 보는 폴백."""
        self._ingest({"id": "episode:p3", "type": "episode", "title": "t",
                      "date": "2026-01-01", "summary": "top",
                      "props": {"date": "1999-12-31", "summary": "props"}})
        row = self._row("episode:p3")
        self.assertEqual(row["date"], "2026-01-01")
        self.assertEqual(row["summary"], "top")

    def test_날짜가_아닌_props_값은_승격하지_않는다(self):
        """props 는 자유 형태다. dict/list 를 str() 로 뭉개 날짜 열에 앉히지 않는다
        (값은 props 에 그대로 남으므로 유실이 아니다)."""
        self._ingest({"id": "episode:p4", "type": "episode", "title": "t",
                      "props": {"date": {"y": 2026}, "summary": ["a"]}})
        row = self._row("episode:p4")
        self.assertFalse(row["date"], row["date"])
        self.assertFalse(row["summary"], row["summary"])

    def test_props_에_date_가_없으면_아무것도_지어내지_않는다(self):
        self._ingest({"id": "episode:p5", "type": "episode", "title": "t",
                      "props": {"note": "날짜 없음"}})
        self.assertFalse(self._row("episode:p5")["date"])

    def test_공백뿐인_props_date_는_승격하지_않는다(self):
        self._ingest({"id": "episode:p6", "type": "episode", "title": "t",
                      "props": {"date": "   "}})
        self.assertFalse(self._row("episode:p6")["date"])

    def test_승격된_date_는_recall_정렬에_실제로_실린다(self):
        """열이 비면 recall 정렬(`q.RECALL_ORDER`, 날짜 내림차순 · 날짜 없는 행은 끝)이
        그 행을 맨 뒤로 보낸다 — 승격이 열을 채워야 제자리에 선다. 날짜 없는 행의
        위치 자체는 `test_recall_filters_and_cursor.py` 가 못박는다."""
        from api import query as q
        for eid, d in (("episode:s-old", "2026-01-01"), ("episode:s-new", "2026-08-07")):
            self._ingest({"id": eid, "type": "episode", "title": eid,
                          "domain": "d", "props": {"date": d}})
        rows = q.recall(owner=self.OWNER, store=self.store, limit=10)["rows"]
        self.assertEqual([r["id"] for r in rows][:2],
                         ["episode:s-new", "episode:s-old"])
        self.assertEqual(rows[0]["date"], "2026-08-07")

    # ── 2. 백필 ──────────────────────────────────────────────────────────
    def test_백필이_props_만_가진_기존_행을_고친다(self):
        self._seed_legacy("episode:L1", {"date": "2026-08-07", "summary": "s"})
        self.assertEqual(backfill_episode_time_fields(self.store), 1)
        row = self._row("episode:L1")
        self.assertEqual(row["date"], "2026-08-07")
        self.assertEqual(row["summary"], "s")

    def test_백필은_멱등이다(self):
        self._seed_legacy("episode:L2", {"date": "2026-08-07"})
        self.assertEqual(backfill_episode_time_fields(self.store), 1)
        first = self._row("episode:L2")
        # 두 번째 실행은 아무것도 고치지 않는다(0) — 결과도 같다.
        self.assertEqual(backfill_episode_time_fields(self.store), 0)
        self.assertEqual(self._row("episode:L2")["date"], first["date"])

    def test_백필이_이미_있는_날짜를_덮지_않는다(self):
        self._seed_legacy("episode:L3", {"date": "1999-01-01"}, date="2026-08-07")
        self.assertEqual(backfill_episode_time_fields(self.store), 0)
        self.assertEqual(self._row("episode:L3")["date"], "2026-08-07")

    def test_백필이_없는_날짜를_지어내지_않는다(self):
        """props 는 있지만 date 키가 없는 행 · date 가 문자열이 아닌 행 — 둘 다 그대로."""
        self._seed_legacy("episode:L4", {"note": "날짜 없음"})
        self._seed_legacy("episode:L5", {"date": 20260807})
        self.assertEqual(backfill_episode_time_fields(self.store), 0)
        self.assertFalse(self._row("episode:L4")["date"])
        self.assertFalse(self._row("episode:L5")["date"])

    def test_깨진_props_JSON_이_백필을_죽이지_않는다(self):
        self.store.run("MERGE (e:Episode {episode_id: $id}) "
                       "SET e.props = '{not json', e.title = 't', "
                       "e.owner_id = $owner",
                       {"id": self._pk("episode:L6"), "owner": self.OWNER})
        self._seed_legacy("episode:L7", {"date": "2026-08-07"})
        self.assertEqual(backfill_episode_time_fields(self.store), 1)
        self.assertEqual(self._row("episode:L7")["date"], "2026-08-07")

    def test_백필이_동기화_스탬프를_건드리지_않는다(self):
        """파생값 채우기는 사용자의 편집이 아니다. 스탬프를 찍으면 모든 피어의 다음
        pull 에 '그 날 전 에피소드를 고쳤다'는 가짜 델타가 실린다(S0 무스탬프 규칙)."""
        self._seed_legacy("episode:L8", {"date": "2026-08-07"},
                          rev=3, updated_at="2026-08-01T00:00:00.000Z", sync_seq=7)
        before = self._row("episode:L8")
        self.assertEqual(backfill_episode_time_fields(self.store), 1)
        after = self._row("episode:L8")
        self.assertEqual(after["date"], "2026-08-07")
        for col in ("rev", "updated_at", "sync_seq"):
            self.assertEqual(after[col], before[col], col)

    # ── 3. 배선(버전 게이트) ──────────────────────────────────────────────
    def test_구버전_스토어를_열면_마이그레이션이_백필을_돌린다(self):
        """setUp 의 provision 이 이미 v9 를 찍었으므로 v8 로 되돌려 재현한다."""
        self.store.run("MERGE (m:_SchemaMeta {key: 'schema_version'}) SET m.value = '8'")
        self._seed_legacy("episode:L9", {"date": "2026-08-07"})
        res = self.store.migrate(dim=4)
        self.assertEqual(res["migrated_from"], 8)
        self.assertEqual(res["episode_time_backfilled"], 1)
        self.assertEqual(self._row("episode:L9")["date"], "2026-08-07")

    def test_최신_버전_스토어는_백필을_다시_돌리지_않는다(self):
        self._seed_legacy("episode:L10", {"date": "2026-08-07"})
        res = self.store.migrate(dim=4)          # 이미 v9 — 게이트가 막는다
        self.assertEqual(res["episode_time_backfilled"], 0)
        self.assertFalse(self._row("episode:L10")["date"])


class FragmentSchemaContractTests(unittest.TestCase):
    """도구 계약에도 date 가 있어야 한다 — 없으면 상류는 props 로 되돌아간다(F9 근본)."""

    def test_vinc_ingest_노드_스키마가_date_와_summary_를_선언한다(self):
        from vinc_mcp.tools import _FRAGMENT_SCHEMA
        props = _FRAGMENT_SCHEMA["properties"]["nodes"]["items"]["properties"]
        for key in ("date", "summary"):
            self.assertIn(key, props, key)
            self.assertTrue(props[key].get("description"), key)


if __name__ == "__main__":
    unittest.main()
