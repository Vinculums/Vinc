"""삭제의 미리보기 → 적용과 쓰기 신호 — 소스 잠금(2026-10-16, 0.9.8 계약 채택).

- 되돌릴 수 없는 삭제는 확인 창을 열 때 `dry_run` 으로 미리보고, 확인하면 그 `preview_digest`
  와 `operation_id` 를 실어 적용한다. 그 사이 바뀌었으면 서버가 지우지 않는다.
- 응답을 잃은 실패만 같은 operation_id 로 한 번 더 보낸다(서버가 첫 결과를 돌려준다).
- `vinc:write` 는 데이터 평면 셋 모두에서 나고, 미리보기는 쓰기로 세지 않는다.
"""
from __future__ import annotations

import re
import unittest

from _src import func, read

API = read("api")
VIEW = read("view")
DOC_DELETE = read("doc-delete")


class Api(unittest.TestCase):
    def test_preview_is_a_dry_run_that_keeps_only_a_complete_digest(self):
        fn = func(API, "previewDelete")
        self.assertIn("dry_run: true", fn)
        self.assertIn("crypto.randomUUID()", fn)
        self.assertIn("p.preview_complete", fn)
        self.assertIn("guard.preview_digest = p.preview_digest", fn)

    def test_apply_carries_the_guard_and_retries_only_a_lost_response(self):
        fn = func(API, "applyDelete")
        self.assertIn("...(guard || {})", fn)
        self.assertIn("status === 0 || status === 502 || status === 504", fn)
        self.assertEqual(fn.count("send()"), 2)

    def test_every_delete_route_has_a_preview_and_a_guarded_apply(self):
        for path, preview, apply in (("/delete", "previewDeleteNode", "deleteNode"),
                                     ("/delete-edge", "previewDeleteEdge", "deleteEdge"),
                                     ("/doc/delete", "previewDocDelete", "docDelete")):
            p = re.escape(path)
            self.assertIsNotNone(re.search(rf'(?s){preview}: \(.*?previewDelete\("{p}"', API), path)
            self.assertIsNotNone(re.search(
                rf'(?s){apply}: \([^)]*guard\?: DeleteGuard.*?applyDelete\("{p}"', API), path)


class CallSites(unittest.TestCase):
    def test_node_and_edge_deletes_preview_while_the_confirmation_is_open(self):
        previews = [m.start() for m in re.finditer(r"api\.previewDelete(Node|Edge)\(", VIEW)]
        applies = [m.start() for m in re.finditer(r"api\.delete(Node|Edge)\(", VIEW)]
        # 3D 우클릭 메뉴의 지우기는 그 렌더러와 함께 빠졌다. 남은 셋(레일 · 카드 · 엣지)이 모두 미리본다
        self.assertEqual(len(previews), 3)
        self.assertEqual(len(applies), 3)
        self.assertEqual(len(re.findall(r"api\.delete(Node|Edge)\([^)]*await guard\)", VIEW)), 3)
        for p, a in zip(previews, applies):
            between = VIEW[p:a]
            self.assertLess(p, a)
            self.assertIn("confirmDestructive(", between)

    def test_document_delete_previews_after_saving_has_paused(self):
        fn = DOC_DELETE[DOC_DELETE.index("export async function deleteDocument("):]
        self.assertLess(fn.index("api.previewDocDelete("), fn.index("api.docDelete("))
        self.assertIn("api.docDelete(owner, id, scope, guard)", fn)


class WriteSignal(unittest.TestCase):
    def test_all_three_planes_announce_writes(self):
        account = func(API, "accountCall")
        self.assertIn('if (method === "POST") announceWrite(path, body);', account)
        local = func(API, "localCall")
        self.assertIn("if (isWrite) announceWrite(path, body);", local)
        # 웹 쓰기를 빼던 조건이 되살아나면 계정 평면이 다시 빠진다.
        self.assertNotIn("!WEB && !path.startsWith", local)

    def test_previews_and_sync_machinery_are_not_writes(self):
        fn = func(API, "announceWrite")
        self.assertIn("dry_run === true", fn)
        self.assertIn('path.startsWith("/sync")', fn)
        self.assertIn('path.startsWith("/connect/")', fn)
        self.assertIn('new CustomEvent("vinc:write")', fn)


if __name__ == "__main__":
    unittest.main()
