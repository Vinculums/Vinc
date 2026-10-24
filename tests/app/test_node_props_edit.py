"""노드 편집(속성 · 기본 정보) — 소스 잠금. 2026-09-26 첫 판, 2026-10-16 0.9.8 계약으로 다시 씀.

동작(형식 변환 · 검사 · merge patch 생성)은 `app/dev/props-edit-check.ts` 가 브라우저 없이
검사한다. 여기서는 그 동작이 기대는 구조가 조용히 풀리지 않게 붙잡는다:

- 필드 이력이 없다. 편집을 연 판(rev)을 `expected_rev` 로 실어 서버가 쓰기 안에서 대조하게
  한다. 클라이언트가 먼저 읽어 비교하던 방식은 읽기와 쓰기 사이가 열려 있었다.
- 서버는 `props` 를 통째로 교체한다. 바뀐 키만 `props_patch`(JSON Merge Patch)로 싣는다.
- 판이 달라 멈춘 쓰기(`status:"conflict"`)는 실패가 아니라 충돌 문구와 새로 불러오기로 말한다.
- 판 확인은 owner 스코프로 읽으므로 쓰기에 보기의 owner 를 싣는다(계정·웹은 허브가 바꾼다).
- props 가 저장되지 않는 종(Entity 캐리어)에 편집 단추를 내면 값이 조용히 사라진다.

근거: [[decision:node-props-are-edited-in-the-document-pane-as-a-whole-and-checked-against-the-read-revision-2026-09-26]]
· [[record:guarded-node-edits-carry-the-view-owner-2026-10-16]]
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
VIEW = (APP / "src" / "surfaces" / "view.ts").read_text(encoding="utf-8")
EDIT = (APP / "src" / "props-edit.ts").read_text(encoding="utf-8")
COPY = (APP / "src" / "copy.ts").read_text(encoding="utf-8")
API = (APP / "src" / "api.ts").read_text(encoding="utf-8")
BASICS = (APP / "src" / "node-basics.ts").read_text(encoding="utf-8")


def _fn(src: str, name: str, nxt: str) -> str:
    a = src.index(f"function {name}(")
    return src[a:src.index(nxt, a)]


EDITOR = _fn(VIEW, "openPropsEditor", "function propsDl(")
SAVE = EDITOR[EDITOR.index("async function save()"):]
BASICS_SAVE = BASICS[BASICS.index("async function save()"):]


class Contract(unittest.TestCase):
    def test_edit_node_sends_the_read_revision_and_the_view_owner(self):
        m = re.search(r"editNode: \(node: .*?\n(.*?)\n", API, re.S)
        assert m, "api.editNode 가 없다"
        body = m.group(1)
        self.assertIn('call("/ingest/fragment"', body)
        self.assertIn("expected_rev: rev", body)
        self.assertIn("owner: cfg.owner", body)

    def test_conflict_reads_both_the_200_envelope_and_a_409(self):
        fn = _fn(API, "isWriteConflict", "\n}\n")
        for needle in ('r?.status === "conflict"', "r?.status === 409",
                       '"write_conflict"', '"preview_conflict"'):
            self.assertIn(needle, fn)


class Save(unittest.TestCase):
    def test_the_server_checks_the_revision_not_a_client_reread(self):
        self.assertNotIn("api.nodeContext(", SAVE)
        self.assertNotIn("api.ingestFragment(", SAVE)
        self.assertIn("meta.rev ?? 0", SAVE)
        self.assertIn("api.editNode(", SAVE)

    def test_sends_a_patch_of_the_changed_keys_with_id_and_kind_and_no_owner(self):
        self.assertIn("propsPatchOf(props, next)", SAVE)
        call = SAVE[SAVE.index("api.editNode("):SAVE.index("meta.rev ?? 0")]
        self.assertIn("props_patch: patch", call)
        # patch 로 못 싣는 편집(값으로서의 null)만 전체를 같은 판 조건으로 싣는다.
        self.assertIn("{ props: next }", call)
        self.assertIn("type: meta.kind", call)
        self.assertNotIn("owner", call)   # owner 는 api.editNode 한 곳이 싣는다

    def test_a_conflict_offers_reload_instead_of_failing(self):
        self.assertIn("V.propsConflict", EDITOR)
        self.assertIn("if (isWriteConflict(res)) { conflict(); return; }", SAVE)
        self.assertIn("if (isWriteConflict(err)) { conflict(); return; }", SAVE)

    def test_reads_merged_instead_of_trusting_the_call(self):
        self.assertIn("res?.merged?.nodes?.matched", EDITOR)
        self.assertIn("res?.warnings", EDITOR)

    def test_redraws_from_a_fresh_read_and_refreshes_the_agent_list(self):
        self.assertIn("ctxCache = null;", EDITOR)
        self.assertIn("refreshLnb({ quiet: true })", EDITOR)


class Basics(unittest.TestCase):
    def test_the_note_goes_as_a_single_key_patch(self):
        self.assertIn("node.props_patch = { note: next.oneLiner || null }", BASICS_SAVE)
        # 지금 props 전체를 읽어 합쳐 보내던 길이 남아 있으면 경합이 되살아난다.
        self.assertNotIn("fresh.props", BASICS_SAVE)
        self.assertNotIn("node.props =", BASICS_SAVE)

    def test_the_server_checks_the_revision(self):
        self.assertNotIn("api.nodeContext(", BASICS_SAVE)
        self.assertIn("api.editNode(node, src.rev ?? 0)", BASICS_SAVE)
        self.assertIn("if (isWriteConflict(res)) { conflict(); return; }", BASICS_SAVE)
        self.assertIn("COPY.ui.viewx.propsConflict", BASICS_SAVE)


class Editor(unittest.TestCase):
    def test_escape_folds_only_the_editor(self):
        self.assertIn('e.key === "Escape") { e.stopPropagation(); cancel(); }', EDITOR)

    def test_listeners_are_released_with_the_box(self):
        self.assertIn("box.replaceWith(fresh)", EDITOR)

    def test_detail_passes_kind_and_revision(self):
        self.assertIn("drawNodeProps(nd, res.props, { kind: res.node_kind, rev: res.rev, canEdit })", VIEW)
        self.assertIn("node_kind?: string; rev?: number;", API)


class Kinds(unittest.TestCase):
    def test_only_kinds_that_keep_props_are_editable(self):
        m = re.search(r"PROPS_EDITABLE_KINDS = \[([^\]]*)\]", EDIT)
        assert m
        kinds = sorted(re.findall(r'"(\w+)"', m.group(1)))
        self.assertEqual(kinds, ["concept", "decision", "record"])

    def test_module_is_import_free(self):
        # app/dev/props-edit-check.ts 가 node 로 직접 돌리는 성립 조건.
        self.assertIsNone(re.search(r"^import ", EDIT, re.M))
        self.assertTrue((APP / "dev" / "props-edit-check.ts").exists())


class Copy(unittest.TestCase):
    KEYS = ("propsEdit", "propsEditTip", "propsEmpty", "propsAdd", "propsSave", "propsCancel",
            "propsSaving", "propsKeyPh", "propsListHint", "propsKindLabel", "propsKinds",
            "propsYes", "propsNo", "propsRemove", "propsErr", "propsNoChange",
            "propsConflict", "propsReload", "propsFail")

    def test_both_decks_carry_every_key(self):
        for k in self.KEYS:
            self.assertEqual(len(re.findall(rf"\b{k}:", COPY)), 2, k)

    def test_no_dash_on_screen(self):
        # 화면 문구에 대시 금지(§1). 두 덱 각각 propsEdit 부터 propsFail 줄 끝까지.
        for a in (m.start() for m in re.finditer(r"propsEdit:", COPY)):
            b = COPY.index("\n", COPY.index("propsFail:", a))
            chunk = COPY[a:b]
            self.assertNotIn("—", chunk)
            self.assertNotIn("–", chunk)


if __name__ == "__main__":
    unittest.main()
