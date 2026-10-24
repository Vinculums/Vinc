"""오른쪽 패널의 편집 문 — 문서 옆 칸의 주제, 리더의 제목·시점, 쓸 수 없는 자리의 편집 단추.

조사 정본: [[vinc.research.right-panel-editing-2026-10-24]]. 여기서는 소스에 박힌 길만 확인한다:
옆 칸은 편집기의 저장 길(`saveTopic`)로 주제를 적고, 문서 저장은 읽은 해시를 서버 조건으로 들고 가며,
리더는 편집 모드에서 제목 줄을 열고, 기본 정보·속성 편집 단추는 쓸 수 있는 스페이스에서만 선다.
"""
import pathlib
import unittest

SRC = pathlib.Path(__file__).resolve().parents[2] / "app" / "src"


def read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


class PanelEditing(unittest.TestCase):
    def test_doc_aside_saves_the_topic_through_the_editor(self):
        home = read("surfaces/home.ts")
        self.assertIn('<dd id="ha-topic"></dd>', home)
        self.assertIn("handle.saveTopic?.(next)", home)
        self.assertIn("canWriteHere()", home)

    def test_doc_put_carries_the_hash_it_read(self):
        api = read("api.ts")
        self.assertIn("expected_content_hash: expectedContentHash", api)
        ed = read("doc-editor.ts")
        self.assertIn("force ? undefined : hash || undefined", ed)
        self.assertIn('res.error === "write_conflict"', ed)

    def test_reader_opens_the_title_row_when_editing(self):
        view = read("surfaces/view.ts")
        self.assertEqual(view.count("hideTitle: !docWriting"), 2)
        self.assertIn("contentHash: savedHash", view)

    def test_edit_buttons_follow_write_access(self):
        view = read("surfaces/view.ts")
        self.assertIn("const canEdit = await canWriteHere();", view)
        self.assertIn("propsEditable(meta.kind) && meta.canEdit !== false", view)
        basics = read("node-basics.ts")
        self.assertIn("canEdit = true): Promise<void>", basics)
        api = read("api.ts")
        self.assertIn("export function canWriteHere(", api)


if __name__ == "__main__":
    unittest.main()
