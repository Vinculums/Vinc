"""Agent 레일은 Agents 보드(#home/agents)를 열고, 역할은 보드 안의 편집 면(#home/agents/<id>)으로 연다.

그래프로 건너뛰던 흐름(레일 → 그래프 스코프, 역할 줄 → #view/read, 만든 뒤 → 노드 상세)과
역할을 띄움 시트로 고치던 흐름이 돌아오지 않게 소스에 박힌 길을 확인한다. 팀 꾸리기(이름 바꾸기 ·
해체 · 옮기기)는 보낸 키만 바꾸고 판을 대조해야 한다. 근거: [[vinc.canon.agent-roles]]
"""
import pathlib
import re
import unittest

SRC = pathlib.Path(__file__).resolve().parents[2] / "app" / "src"


def read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


class AgentRailOpensTheBoard(unittest.TestCase):
    def test_rail_goes_to_the_board(self):
        lnb = read("lnb.ts")
        self.assertIn('export const AGENTS_BOARD = "#home/agents";', lnb)
        self.assertIn('if (next === "agent") location.hash = AGENTS_BOARD;', lnb)

    def test_role_row_opens_the_sheet_not_the_graph(self):
        lnb = read("lnb.ts")
        block = lnb[lnb.index('nd.classList.contains("lnb-agent-role")'):]
        block = block[:block.index("return;") + 7]
        self.assertIn("openRoleComposer({ id })", block)
        self.assertNotIn("#view/read", block)

    def test_saving_a_role_stays_on_the_board(self):
        rc = read("role-composer.ts")
        save = rc[rc.index("async function save()"):]
        save = save[:save.index('form.addEventListener("submit"')]
        self.assertNotIn("#view", save)
        self.assertIn("location.hash = AGENTS_BOARD;", save)

    def test_the_editor_is_a_page_not_a_sheet(self):
        rc = read("role-composer.ts")
        self.assertNotIn("openSheet", rc)
        self.assertIn("export async function renderRoleEditor(", rc)
        self.assertIn("location.hash = roleHash(opts);", rc)
        home = read("surfaces/home.ts")
        self.assertIn("renderRoleEditor(el, live, parts.slice(2))", home)

    def test_team_changes_patch_only_the_team_keys_and_check_the_rev(self):
        board = read("surfaces/agents-board.ts")
        self.assertIn("props_patch: c.patch, expected_rev: revs[i]", board)
        self.assertNotRegex(board, r"\bprops:\s*\{")
        for act in ("new-team", "team-more", "more"):
            self.assertIn(f'data-act="{act}"', board)
        self.assertIn('addEventListener("drop"', board)

    def test_home_routes_the_board(self):
        home = read("surfaces/home.ts")
        self.assertIn('parts[1] === "agents" ? "agents"', home)
        self.assertTrue((SRC / "surfaces" / "agents-board.ts").exists())

    def test_edit_keeps_unknown_props_and_checks_the_rev(self):
        rc = read("role-composer.ts")
        self.assertRegex(rc, re.compile(r"\.\.\.baseProps,"))
        self.assertIn("node.expected_rev = baseRev", rc)


if __name__ == "__main__":
    unittest.main()
