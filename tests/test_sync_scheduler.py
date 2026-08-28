# -*- coding: utf-8 -*-
"""자동 동기화 스케줄러(v0.8.7 §8)의 계약.

JS 러너가 없어 소스를 텍스트로 못 박는다(`test_mcp_icons` 와 같은 방식). 여기 있는
것들은 전부 **조용히** 고장난다. 스케줄러는 실패를 화면에 안 그리므로, 망가져도
아무도 모르고 그래프만 갈라진다.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = (ROOT / "app/src/sync-scheduler.ts").read_text(encoding="utf-8")
API_TS = (ROOT / "app/src/api.ts").read_text(encoding="utf-8")
MAIN_TS = (ROOT / "app/src/main.ts").read_text(encoding="utf-8")
SERVER_PY = (ROOT / "api/server.py").read_text(encoding="utf-8")


class SchedulerContractTest(unittest.TestCase):

    def test_쓰기_신호가_자기_자신을_다시_부르지_않는다(self):
        """`/sync` 가 낸 쓰기로 다시 sync 를 걸면 스케줄러가 자기를 먹는다.

        무한 루프가 아니라 **영구 반복 동기화**로 나타나므로 화면상 증상이 없다."""
        self.assertIn('!path.startsWith("/sync")', API_TS)
        self.assertIn('!path.startsWith("/connect/")', API_TS)
        # 읽기까지 신호를 내면 화면을 넘기는 것만으로 동기화가 돈다.
        self.assertIn("if (isWrite &&", API_TS)

    def test_스케줄러가_실제로_마운트된다(self):
        """부팅에서 안 부르면 파일만 있고 아무 일도 일어나지 않는다."""
        self.assertIn("mountSyncScheduler()", MAIN_TS)
        self.assertIn('from "./sync-scheduler"', MAIN_TS)

    def test_동시_실행_가드는_사이드카에만_있다(self):
        """클라는 in-flight 가드를 두지 않고 409 를 삼킨다.

        커서 경합 방지는 서버 불변식이다(`submit_sync`). 클라에 사본을 두면 둘이
        어긋나는 날 조용히 안 돈다. 그러니 **서버 쪽이 사라지면 이 설계가 무너진다** —
        그것을 여기서 지킨다."""
        self.assertIn("self._sync_running", SERVER_PY)
        self.assertIn("a sync is already in progress", SERVER_PY)
        # 클라가 가드를 다시 만들었는지 본다(스스로 "도는 중"을 기억하려는 시도).
        self.assertNotIn("running", SCHEDULER.split("export function syncSoon")[1])

    def test_들어오는_쪽_주기가_쓰기_디바운스보다_길다(self):
        """반대면 안전망이 디바운스를 계속 앞질러 쓰기 합침이 사라진다."""
        self.assertIn("const WRITE_DEBOUNCE_MS = 20_000;", SCHEDULER)
        self.assertIn("const INBOUND_MS = 10 * 60_000;", SCHEDULER)

    def test_실패는_삼킨다(self):
        """오프라인은 정상이다. 던지면 부팅 경로에서 처리되지 않은 거절이 된다."""
        self.assertIn("api.syncNow(key, cfg.owner).catch(() => {})", SCHEDULER)
        self.assertIn('accountKey().catch(() => "")', SCHEDULER)


if __name__ == "__main__":
    unittest.main()
